package rendercontrol

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"net/netip"
	"time"

	"github.com/Wei-Shaw/sub2api/internal/rendercontrol/controlclient"
)

type Store struct {
	db    *sql.DB
	vault vault
}

func (*Store) String() string   { return "rendercontrol.Store{redacted}" }
func (*Store) GoString() string { return "rendercontrol.Store{redacted}" }

func NewStore(db *sql.DB, activeKey string, keys map[string][]byte) (*Store, error) {
	if db == nil {
		return nil, ErrInvalid
	}
	vault, err := newVault(activeKey, keys)
	if err != nil {
		return nil, err
	}
	return &Store{db, vault}, nil
}

func begin(ctx context.Context, db *sql.DB) (*sql.Tx, error) {
	tx, err := db.BeginTx(ctx, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, ErrStorage
	}
	if _, err = tx.ExecContext(ctx, `SELECT set_config('lock_timeout','2s',true), set_config('statement_timeout','5s',true)`); err != nil {
		_ = tx.Rollback()
		return nil, ErrStorage
	}
	return tx, nil
}

func commit(tx *sql.Tx) error {
	if tx.Commit() != nil {
		// The server may have committed before the connection/response was lost.
		return ErrUncertain
	}
	return nil
}

const columns = `id, instance, COALESCE(target_gateway_id,''), user_id, api_key_id, host(client_ip), operation, request_hash, task_type,
 provider_request_id, authorization_ref, price_version, unit_price::text, max_units, hold_amount::text,
 state, dispatch_count, COALESCE(admission_id,''), COALESCE(remote_job_id,''), COALESCE(terminal_event_id,''),
 COALESCE(terminal_hash,''), COALESCE(outcome,''), COALESCE(actual_amount,0)::text, COALESCE(ledger_ref,''), image_count, video_milliseconds, audio_milliseconds, COALESCE(public_model,''), created_at, COALESCE(client_request_hash,request_hash), COALESCE(usage_account_id,0), COALESCE(billing_group_id,0), rate_multiplier::text,rate_source,billing_type,COALESCE(subscription_id,0),COALESCE(catalog_revision,0),COALESCE(catalog_digest,'')`

type scanner interface{ Scan(...any) error }

func scanIntent(row scanner) (Intent, error) {
	var i Intent
	var ip, unit, hold, actual, rate string
	var catalogRevision int64
	var catalogDigest string
	if err := row.Scan(&i.ID, &i.Instance, &i.TargetGatewayID, &i.UserID, &i.KeyID, &ip, &i.Operation, &i.RequestHash, &i.TaskType,
		&i.ProviderRequestID, &i.AuthorizationRef, &i.PriceVersion, &unit, &i.MaxUnits, &hold, &i.State, &i.DispatchCount,
		&i.AdmissionID, &i.RemoteJobID, &i.TerminalEventID, &i.TerminalHash, &i.Outcome, &actual, &i.LedgerRef, &i.ImageCount, &i.VideoMilliseconds, &i.AudioMilliseconds, &i.Model, &i.CreatedAt, &i.ClientRequestHash, &i.UsageAccountID, &i.BillingGroupID, &rate, &i.RateSource, &i.BillingType, &i.SubscriptionID, &catalogRevision, &catalogDigest); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return Intent{}, ErrNotFound
		}
		return Intent{}, ErrStorage
	}
	var err error
	if catalogRevision != 0 {
		i.Catalog = &controlclient.CatalogStamp{InstanceID: i.Instance, Revision: catalogRevision, Digest: catalogDigest}
		if !i.Catalog.Valid() {
			return Intent{}, ErrStorage
		}
	} else if catalogDigest != "" {
		return Intent{}, ErrStorage
	}
	if i.TrustedIP, err = netip.ParseAddr(ip); err != nil {
		return Intent{}, ErrStorage
	}
	if i.UnitPrice, err = ParseMoney(unit); err != nil {
		return Intent{}, ErrStorage
	}
	if i.HoldAmount, err = ParseMoney(hold); err != nil {
		return Intent{}, ErrStorage
	}
	if i.ActualAmount, err = ParseMoney(actual); err != nil {
		return Intent{}, ErrStorage
	}
	if i.RateMultiplier, err = parseRate(rate); err != nil {
		return Intent{}, ErrStorage
	}
	return i, nil
}

func lockOwner(ctx context.Context, tx *sql.Tx, userID, keyID int64, admitting bool) error {
	var userActive, keyActive bool
	if err := tx.QueryRowContext(ctx, `SELECT status = 'active' AND deleted_at IS NULL FROM users WHERE id=$1 FOR NO KEY UPDATE`, userID).Scan(&userActive); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return ErrNotFound
		}
		return ErrStorage
	}
	var owner int64
	var groupID sql.NullInt64
	if err := tx.QueryRowContext(ctx, `SELECT user_id, group_id, status='active' AND deleted_at IS NULL AND (expires_at IS NULL OR expires_at>NOW()) FROM api_keys WHERE id=$1 FOR NO KEY UPDATE`, keyID).Scan(&owner, &groupID, &keyActive); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return ErrNotFound
		}
		return ErrStorage
	}
	if owner != userID || admitting && (!userActive || !keyActive) {
		return ErrDenied
	}
	if admitting {
		if !groupID.Valid {
			return ErrUnsupported
		}
		var mode string
		var groupActive bool
		if err := tx.QueryRowContext(ctx, `SELECT subscription_type, status='active' AND deleted_at IS NULL FROM groups WHERE id=$1 FOR SHARE`, groupID.Int64).Scan(&mode, &groupActive); err != nil {
			return ErrStorage
		}
		if !groupActive {
			return ErrDenied
		}
		if mode != "standard" && mode != "subscription" {
			return ErrUnsupported
		}
	}
	return nil
}

// lockIntent discovers immutable ownership, then locks user -> Key -> subscription -> intent
// order. No external network call may be made while this transaction is open.
func lockIntent(ctx context.Context, tx *sql.Tx, id string) (Intent, error) {
	var userID, keyID, subscriptionID, groupID int64
	err := tx.QueryRowContext(ctx, `SELECT user_id, api_key_id,COALESCE(subscription_id,0),COALESCE(billing_group_id,0) FROM render_control_intents WHERE id=$1`, id).Scan(&userID, &keyID, &subscriptionID, &groupID)
	if errors.Is(err, sql.ErrNoRows) {
		return Intent{}, ErrNotFound
	}
	if err != nil {
		return Intent{}, ErrStorage
	}
	if err = lockOwner(ctx, tx, userID, keyID, false); err != nil {
		return Intent{}, err
	}
	if subscriptionID > 0 {
		if _, _, err = lockSubscription(ctx, tx, subscriptionID, userID, groupID, false); err != nil {
			return Intent{}, err
		}
	}
	return scanIntent(tx.QueryRowContext(ctx, `SELECT `+columns+` FROM render_control_intents WHERE id=$1 FOR UPDATE`, id))
}

func addEffect(ctx context.Context, tx *sql.Tx, i Intent, kind string) error {
	var reference any
	if kind == "terminal" {
		reference = i.LedgerRef
	}
	if _, err := tx.ExecContext(ctx, `INSERT INTO render_control_effects(intent_id,kind,user_id,api_key_id,actual_amount,ledger_ref) VALUES ($1,$2,$3,$4,$5::numeric,$6)`, i.ID, kind, i.UserID, i.KeyID, i.ActualAmount.String(), reference); err != nil {
		return ErrStorage
	}
	return nil
}

func (s *Store) Reserve(ctx context.Context, subject Subject, target Target, idempotency string, request Request, quote Quote) (Intent, error) {
	if _, err := subject.GatewaySubject(); err != nil || !gatewayPattern.MatchString(target.gatewayID) || len(idempotency) < 1 || len(idempotency) > 128 || len(request.body) == 0 || request.hash == "" {
		return Intent{}, ErrInvalid
	}
	ctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	tx, err := begin(ctx, s.db)
	if err != nil {
		return Intent{}, err
	}
	defer tx.Rollback()
	if err = lockOwner(ctx, tx, subject.userID, subject.keyID, true); err != nil {
		return Intent{}, err
	}
	idem := digest([]byte(idempotency))
	existing, err := scanIntent(tx.QueryRowContext(ctx, `SELECT `+columns+` FROM render_control_intents WHERE instance=$1 AND api_key_id=$2 AND operation=$3 AND idempotency_hash=$4 FOR UPDATE`, subject.instance, subject.keyID, request.operation, idem))
	if err == nil {
		if existing.UserID != subject.userID || existing.ClientRequestHash != request.inputHash {
			return Intent{}, ErrConflict
		}
		return existing, commit(tx)
	}
	if !errors.Is(err, ErrNotFound) {
		return Intent{}, err
	}
	if err = lockReservationCatalog(ctx, tx, subject, target, request, quote); err != nil {
		return Intent{}, err
	}
	if request.requireAssets {
		if err = requireAssetBindings(ctx, tx, subject, target, request.body); err != nil {
			return Intent{}, err
		}
	}
	if !labelPattern.MatchString(quote.Version) || quote.MaxUnits != request.units {
		return Intent{}, ErrInvalid
	}
	groupID, rate, rateSource, billing, err := loadPricing(ctx, tx, subject.userID, subject.keyID, request.taskType)
	if err != nil {
		return Intent{}, err
	}
	hold, err := pricedAmount(quote.UnitPrice, quote.MaxUnits, rate)
	if err != nil {
		return Intent{}, err
	}
	// All Provider holds for this Key count conservatively in every currently
	// active window, including holds created before that window began.
	var allowed bool
	err = tx.QueryRowContext(ctx, `WITH pending AS (
		SELECT COALESCE(SUM(hold_amount),0) AS amount FROM render_control_intents
		WHERE api_key_id=$1 AND state IN ('reserved','dispatching','admitted'))
		SELECT (quota<=0 OR quota_used+pending.amount+$2::numeric<=quota)
		AND (rate_limit_5h<=0 OR (CASE WHEN window_5h_start IS NOT NULL AND window_5h_start+INTERVAL '5 hours'<=NOW() THEN 0 ELSE usage_5h END)+pending.amount+$2::numeric<=rate_limit_5h)
		AND (rate_limit_1d<=0 OR (CASE WHEN window_1d_start IS NOT NULL AND window_1d_start+INTERVAL '24 hours'<=NOW() THEN 0 ELSE usage_1d END)+pending.amount+$2::numeric<=rate_limit_1d)
		AND (rate_limit_7d<=0 OR (CASE WHEN window_7d_start IS NOT NULL AND window_7d_start+INTERVAL '7 days'<=NOW() THEN 0 ELSE usage_7d END)+pending.amount+$2::numeric<=rate_limit_7d)
		FROM api_keys CROSS JOIN pending WHERE id=$1`, subject.keyID, hold.String()).Scan(&allowed)
	if err != nil {
		return Intent{}, ErrStorage
	}
	if !allowed {
		return Intent{}, ErrQuota
	}
	i := Intent{Instance: subject.instance, TargetGatewayID: target.gatewayID, UserID: subject.userID, KeyID: subject.keyID, TrustedIP: subject.ip, Operation: request.operation, RequestHash: request.hash, TaskType: request.taskType, PriceVersion: quote.Version, UnitPrice: quote.UnitPrice, MaxUnits: quote.MaxUnits, HoldAmount: hold, State: "reserved", BillingGroupID: groupID, RateMultiplier: rate, RateSource: rateSource, BillingType: billing}
	i.Model = request.model
	i.ClientRequestHash = request.inputHash
	i.UsageAccountID = request.usageAccountID
	if request.catalog != nil {
		stamp := *request.catalog
		i.Catalog = &stamp
	}
	if billing == 1 {
		if i.SubscriptionID, err = reserveSubscription(ctx, tx, i.UserID, groupID, hold); err != nil {
			return Intent{}, err
		}
	}
	if i.UsageAccountID != 0 {
		if err = checkUsageAccount(ctx, tx, i.UsageAccountID); err != nil {
			return Intent{}, err
		}
	}
	if i.ID, err = newID("rc"); err != nil {
		return Intent{}, err
	}
	if i.ProviderRequestID, err = newID("rcr"); err != nil {
		return Intent{}, err
	}
	if i.AuthorizationRef, err = newID("rca"); err != nil {
		return Intent{}, err
	}
	encrypted, err := s.vault.seal(i, request.body)
	if err != nil {
		return Intent{}, err
	}
	if billing == 0 {
		result, err := tx.ExecContext(ctx, `UPDATE users SET balance=balance-$1::numeric,frozen_balance=frozen_balance+$1::numeric,updated_at=NOW() WHERE id=$2 AND balance >= $1::numeric AND deleted_at IS NULL`, hold.String(), subject.userID)
		if err != nil {
			return Intent{}, ErrStorage
		}
		if affected, err := result.RowsAffected(); err != nil || affected != 1 {
			return Intent{}, ErrBalance
		}
	}
	var revision, catalogDigest any
	if i.Catalog != nil {
		revision, catalogDigest = i.Catalog.Revision, i.Catalog.Digest
	}
	err = tx.QueryRowContext(ctx, `INSERT INTO render_control_intents(id,instance,user_id,api_key_id,client_ip,operation,idempotency_hash,request_hash,request_cipher,cipher_key_id,task_type,provider_request_id,authorization_ref,price_version,unit_price,max_units,hold_amount,target_gateway_id,public_model,client_request_hash,usage_account_id,billing_group_id,rate_multiplier,rate_source,billing_type,subscription_id,catalog_revision,catalog_digest)
		VALUES ($1,$2,$3,$4,$5::inet,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15::numeric,$16,$17::numeric,$18,$19,$20,NULLIF($21,0),$22,$23::numeric,$24,$25,NULLIF($26,0),$27,$28) RETURNING created_at`, i.ID, i.Instance, i.UserID, i.KeyID, i.TrustedIP.String(), i.Operation, idem, i.RequestHash, encrypted, s.vault.active, i.TaskType, i.ProviderRequestID, i.AuthorizationRef, i.PriceVersion, i.UnitPrice.String(), i.MaxUnits, i.HoldAmount.String(), i.TargetGatewayID, i.Model, i.ClientRequestHash, i.UsageAccountID, i.BillingGroupID, i.RateMultiplier.String(), i.RateSource, i.BillingType, i.SubscriptionID, revision, catalogDigest).Scan(&i.CreatedAt)
	if err != nil {
		return Intent{}, ErrStorage
	}
	if err = addEffect(ctx, tx, i, "reserved"); err != nil {
		return Intent{}, err
	}
	if _, err = tx.ExecContext(ctx, `INSERT INTO render_control_recovery(intent_id) VALUES ($1)`, i.ID); err != nil {
		return Intent{}, ErrStorage
	}
	return i, commit(tx)
}

// Load is the customer-scoped metadata lookup; it does not decrypt requests.
func (s *Store) Load(ctx context.Context, subject Subject, id string) (Intent, error) {
	if !validID("rc", id) {
		return Intent{}, ErrNotFound
	}
	ctx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	return scanIntent(s.db.QueryRowContext(ctx, `SELECT `+columns+` FROM render_control_intents WHERE id=$1 AND instance=$2 AND user_id=$3 AND api_key_id=$4`, id, subject.instance, subject.userID, subject.keyID))
}

// Recover is internal service-only access. HTTP handlers must use Load instead.
func (s *Store) Recover(ctx context.Context, id string) (Intent, controlclient.JobRequest, error) {
	if !validID("rc", id) {
		return Intent{}, controlclient.JobRequest{}, ErrInvalid
	}
	ctx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	tx, err := begin(ctx, s.db)
	if err != nil {
		return Intent{}, controlclient.JobRequest{}, err
	}
	defer tx.Rollback()
	i, err := lockIntent(ctx, tx, id)
	if err != nil {
		return Intent{}, controlclient.JobRequest{}, err
	}
	var encrypted []byte
	var keyID string
	if err = tx.QueryRowContext(ctx, `SELECT request_cipher,cipher_key_id FROM render_control_intents WHERE id=$1`, id).Scan(&encrypted, &keyID); err != nil {
		return Intent{}, controlclient.JobRequest{}, ErrStorage
	}
	body, err := s.vault.open(i, keyID, encrypted)
	if err != nil {
		return Intent{}, controlclient.JobRequest{}, err
	}
	var request controlclient.JobRequest
	decoder := json.NewDecoder(bytes.NewReader(body))
	decoder.UseNumber()
	if decoder.Decode(&request) != nil {
		return Intent{}, controlclient.JobRequest{}, ErrCipher
	}
	return i, request, commit(tx)
}

// Pending is bounded service-only recovery metadata. It never claims an intent
// or authorizes an HTTP send; MarkDispatch must commit before each send.
func (s *Store) Pending(ctx context.Context, limit int) ([]Intent, error) {
	if limit < 1 || limit > 100 {
		return nil, ErrInvalid
	}
	ctx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	rows, err := s.db.QueryContext(ctx, `SELECT `+columns+` FROM render_control_intents WHERE state IN ('reserved','dispatching','admitted') ORDER BY updated_at,id LIMIT $1`, limit)
	if err != nil {
		return nil, ErrStorage
	}
	defer rows.Close()
	result := make([]Intent, 0, limit)
	for rows.Next() {
		i, err := scanIntent(rows)
		if err != nil {
			return nil, err
		}
		result = append(result, i)
	}
	if rows.Err() != nil {
		return nil, ErrStorage
	}
	return result, nil
}

// FinalizeGatewaySettlement derives the instance from this ledger's persisted
// intent. A service event cannot choose a different target or customer scope.
func (s *Store) FinalizeGatewaySettlement(ctx context.Context, target Target, event controlclient.Settlement) (Intent, error) {
	if !gatewayPattern.MatchString(target.gatewayID) || !validID("rc", event.ExternalTaskRef) {
		return Intent{}, ErrNotFound
	}
	ctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	var instance string
	err := s.db.QueryRowContext(ctx, `SELECT instance FROM render_control_intents WHERE id=$1 AND target_gateway_id=$2`, event.ExternalTaskRef, target.gatewayID).Scan(&instance)
	if errors.Is(err, sql.ErrNoRows) {
		return Intent{}, ErrNotFound
	}
	if err != nil {
		return Intent{}, ErrStorage
	}
	return s.Finalize(ctx, target, instance, event)
}
