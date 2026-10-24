package rendercontrol

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"strconv"
	"strings"
	"time"
)

// BillingInvalidator is implemented by the pinned upstream BillingCache. All
// operations must honor the supplied context and may safely be replayed.
type BillingInvalidator interface {
	InvalidateUserBalance(context.Context, int64) error
	InvalidateAPIKeyRateLimit(context.Context, int64) error
	InvalidateSubscriptionCache(context.Context, int64, int64) error
}

type accountReader interface {
	QueryRowContext(context.Context, string, ...any) *sql.Row
}

func checkUsageAccount(ctx context.Context, db accountReader, id int64) error {
	if id < 1 {
		return ErrUnsupported
	}
	var allowed bool
	err := db.QueryRowContext(ctx, `SELECT platform='openai' AND NOT schedulable AND deleted_at IS NULL FROM accounts WHERE id=$1 FOR SHARE`, id).Scan(&allowed)
	if errors.Is(err, sql.ErrNoRows) || err == nil && !allowed {
		return ErrUnsupported
	}
	if err != nil {
		return ErrStorage
	}
	return nil
}

type EffectConsumer struct {
	store    *Store
	cache    BillingInvalidator
	instance string
	owner    string
}

func NewEffectConsumer(store *Store, cache BillingInvalidator, instance, owner string) (*EffectConsumer, error) {
	if store == nil || cache == nil || !instancePattern.MatchString(instance) || !labelPattern.MatchString(owner) {
		return nil, ErrInvalid
	}
	return &EffectConsumer{store: store, cache: cache, instance: instance, owner: owner}, nil
}

func (*EffectConsumer) String() string   { return "rendercontrol.EffectConsumer{redacted}" }
func (*EffectConsumer) GoString() string { return "rendercontrol.EffectConsumer{redacted}" }

// Step owns at most one durable effect. External I/O never holds database locks.
func (c *EffectConsumer) Step(ctx context.Context) (bool, error) {
	batch, err := c.store.claimEffects(ctx, c.instance, c.owner, 1, 30*time.Second)
	if err != nil || len(batch) == 0 {
		return false, err
	}
	e := batch[0]
	err = c.store.prepareEffect(ctx, e)
	if err == nil {
		call, cancel := context.WithTimeout(ctx, 5*time.Second)
		balanceErr := c.cache.InvalidateUserBalance(call, e.UserID)
		keyErr := c.cache.InvalidateAPIKeyRateLimit(call, e.KeyID)
		var subscriptionErr error
		if e.SubscriptionID > 0 {
			subscriptionErr = c.cache.InvalidateSubscriptionCache(call, e.UserID, e.BillingGroupID)
		}
		cancel()
		if balanceErr != nil || keyErr != nil || subscriptionErr != nil {
			err = errEffectCache
		}
	}
	if err != nil {
		// On shutdown leave the lease intact; recovery after expiry retains the
		// local transaction marker. Never manufacture a successful delivery.
		if ctx.Err() == nil {
			if retryErr := c.store.retryEffect(ctx, e, effectErrorCode(err)); retryErr != nil {
				return true, retryErr
			}
		}
		return true, err
	}
	return true, c.store.finishEffectPass(ctx, e)
}

var errEffectCache = errors.New("render_control_effect_cache")

func effectErrorCode(err error) string {
	switch {
	case errors.Is(err, errEffectCache):
		return "effect_cache"
	case errors.Is(err, ErrConflict), errors.Is(err, ErrFence):
		return "effect_conflict"
	case errors.Is(err, ErrUnsupported):
		return "effect_mapping"
	case errors.Is(err, ErrCipher):
		return "effect_cipher"
	default:
		return "effect_storage"
	}
}

func (s *Store) prepareEffect(ctx context.Context, e Effect) error {
	ctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	tx, err := begin(ctx, s.db)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	var done bool
	var intentID, kind string
	var userID, keyID int64
	var recordedAt time.Time
	err = tx.QueryRowContext(ctx, `SELECT local_completed_at IS NOT NULL,intent_id,kind,user_id,api_key_id,created_at FROM render_control_effects WHERE id=$1 AND lease_owner=$2 AND fence=$3 AND lease_until>clock_timestamp() AND acknowledged_at IS NULL FOR UPDATE`, e.ID, e.Owner, e.Fence).Scan(&done, &intentID, &kind, &userID, &keyID, &recordedAt)
	if errors.Is(err, sql.ErrNoRows) {
		return ErrFence
	}
	if err != nil {
		return ErrStorage
	}
	if intentID != e.IntentID || kind != e.Kind || userID != e.UserID || keyID != e.KeyID {
		return ErrConflict
	}
	if done {
		return commit(tx)
	}
	i, err := scanIntent(tx.QueryRowContext(ctx, `SELECT `+columns+` FROM render_control_intents WHERE id=$1`, intentID))
	if err != nil {
		return err
	}
	if i.UserID != userID || i.KeyID != keyID || i.SubscriptionID != e.SubscriptionID || i.BillingGroupID != e.BillingGroupID || kind == "terminal" && (!i.Terminal() || i.LedgerRef != e.LedgerRef || i.ActualAmount != e.ActualAmount) {
		return ErrConflict
	}
	if kind == "terminal" && i.State == "settled" {
		if err = s.writeUsage(ctx, tx, i, recordedAt); err != nil {
			return err
		}
	}
	// The stock outbox already implements cross-instance local/Redis invalidation
	// and its delayed safety pass. Only hashes leave the API-key table.
	if _, err = tx.ExecContext(ctx, `INSERT INTO auth_cache_invalidation_outbox(cache_key) SELECT encode(sha256(convert_to(key,'UTF8')),'hex') FROM api_keys WHERE user_id=$1 AND deleted_at IS NULL AND key<>''`, userID); err != nil {
		return ErrStorage
	}
	result, err := tx.ExecContext(ctx, `UPDATE render_control_effects SET local_completed_at=clock_timestamp() WHERE id=$1 AND lease_owner=$2 AND fence=$3 AND lease_until>clock_timestamp()`, e.ID, e.Owner, e.Fence)
	if err != nil {
		return ErrStorage
	}
	if n, err := result.RowsAffected(); err != nil || n != 1 {
		return ErrFence
	}
	return commit(tx)
}

func (s *Store) writeUsage(ctx context.Context, tx *sql.Tx, i Intent, recordedAt time.Time) error {
	if i.UsageAccountID < 1 || i.BillingGroupID < 1 || len(i.Model) < 1 || len(i.Model) > 100 {
		return ErrUnsupported
	}
	mode, endpoint := "image", "/v1/images/generations"
	var size, inputSize, sizeSource any
	var videoSeconds any
	videoCount, audioCount := 0, 0
	if i.TaskType == "text_to_audio" {
		mode, endpoint = "audio", "/v1/jobs"
		if i.Outcome == "succeeded" {
			audioCount = 1
		}
	} else if i.TaskType == "text_to_video" || i.TaskType == "references_to_video" {
		mode, endpoint = "video", "/v1/videos"
		if i.Outcome == "succeeded" {
			videoCount = 1
			videoSeconds = (i.VideoMilliseconds + 999) / 1000
		}
	} else if i.ImageCount > 0 {
		var cipher []byte
		var key string
		if tx.QueryRowContext(ctx, `SELECT request_cipher,cipher_key_id FROM render_control_intents WHERE id=$1`, i.ID).Scan(&cipher, &key) != nil {
			return ErrStorage
		}
		raw, err := s.vault.open(i, key, cipher)
		if err != nil {
			return err
		}
		var request struct {
			Parameters map[string]any `json:"parameters"`
		}
		if json.Unmarshal(raw, &request) != nil {
			return ErrCipher
		}
		text, _ := request.Parameters["size"].(string)
		parts := strings.Split(text, "x")
		if len(parts) != 2 || !publicSizePattern.MatchString(text) {
			return ErrUnsupported
		}
		width, _ := strconv.Atoi(parts[0])
		height, _ := strconv.Atoi(parts[1])
		maximum := max(width, height)
		if maximum <= 1024 {
			size = "1K"
		} else if maximum <= 2048 {
			size = "2K"
		} else {
			size = "4K"
		}
		inputSize, sizeSource = text, "input"
	}
	if i.TaskType == "image_to_image" || i.TaskType == "references_to_image" {
		endpoint = "/v1/images/edits"
	}
	base := Money(0)
	if i.Outcome == "succeeded" {
		units := i.ImageCount
		if mode == "video" {
			units = (i.VideoMilliseconds + 999) / 1000
		}
		if mode == "audio" {
			units = (i.AudioMilliseconds + 999) / 1000
		}
		var err error
		if base, err = i.UnitPrice.multiply(units); err != nil {
			return ErrConflict
		}
	}
	var subscription any
	if i.SubscriptionID > 0 {
		subscription = i.SubscriptionID
	}
	args := []any{i.UserID, i.KeyID, i.UsageAccountID, i.LedgerRef, i.Model, i.BillingGroupID, i.ActualAmount.String(), i.ImageCount, videoCount, videoSeconds, mode, size, inputSize, sizeSource, endpoint, recordedAt, base.String(), i.RateMultiplier.String(), i.BillingType, subscription, audioCount, i.AudioMilliseconds}
	if _, err := tx.ExecContext(ctx, `INSERT INTO usage_logs(user_id,api_key_id,account_id,request_id,model,requested_model,group_id,total_cost,actual_cost,rate_multiplier,account_rate_multiplier,billing_type,request_type,stream,image_count,video_count,video_duration_seconds,billing_mode,image_size,image_input_size,image_size_source,inbound_endpoint,upstream_endpoint,created_at,subscription_id,audio_count,audio_duration_milliseconds)
		VALUES ($1,$2,$3,$4,$5,$5,$6,$17::numeric,$7::numeric,$18::numeric,1,$19,1,FALSE,$8,$9,$10,$11,$12,$13,$14,$15,'render-control:/gateway/v1/admissions',$16,$20,$21,$22) ON CONFLICT (request_id,api_key_id) DO NOTHING`, args...); err != nil {
		return ErrStorage
	}
	var same bool
	err := tx.QueryRowContext(ctx, `SELECT COALESCE(user_id=$1 AND account_id=$3 AND model=$5 AND requested_model=$5 AND group_id=$6 AND total_cost=$17::numeric AND actual_cost=$7::numeric AND image_count=$8 AND video_count=$9 AND video_duration_seconds IS NOT DISTINCT FROM $10::integer AND billing_mode=$11 AND image_size IS NOT DISTINCT FROM $12::varchar AND image_input_size IS NOT DISTINCT FROM $13::varchar AND image_size_source IS NOT DISTINCT FROM $14::varchar AND inbound_endpoint=$15 AND upstream_endpoint='render-control:/gateway/v1/admissions' AND created_at=$16 AND billing_type=$19 AND rate_multiplier=$18::numeric AND account_rate_multiplier=1 AND request_type=1 AND NOT stream AND subscription_id IS NOT DISTINCT FROM $20::bigint AND audio_count=$21 AND audio_duration_milliseconds=$22,FALSE) FROM usage_logs WHERE request_id=$4 AND api_key_id=$2`, args...).Scan(&same)
	if err != nil {
		return ErrStorage
	}
	if !same {
		return ErrConflict
	}
	return nil
}

func (s *Store) retryEffect(ctx context.Context, e Effect, code string) error {
	ctx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	result, err := s.db.ExecContext(ctx, `UPDATE render_control_effects SET attempts=LEAST(attempts+1,1000000),last_error_code=$4,available_at=clock_timestamp()+(LEAST(60,1 << LEAST(attempts,6))*INTERVAL '1 second'),lease_owner=NULL,lease_until=NULL WHERE id=$1 AND lease_owner=$2 AND fence=$3 AND lease_until>clock_timestamp() AND acknowledged_at IS NULL`, e.ID, e.Owner, e.Fence, code)
	if err != nil {
		return ErrStorage
	}
	if n, err := result.RowsAffected(); err != nil || n != 1 {
		return ErrFence
	}
	return nil
}

func (s *Store) finishEffectPass(ctx context.Context, e Effect) error {
	ctx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	result, err := s.db.ExecContext(ctx, `UPDATE render_control_effects SET cache_pass=cache_pass+1,last_error_code=NULL,available_at=clock_timestamp()+INTERVAL '30 seconds',acknowledged_at=CASE WHEN cache_pass=1 THEN clock_timestamp() ELSE NULL END,lease_owner=NULL,lease_until=NULL WHERE id=$1 AND lease_owner=$2 AND fence=$3 AND lease_until>clock_timestamp() AND local_completed_at IS NOT NULL AND cache_pass=$4 AND cache_pass<2 AND acknowledged_at IS NULL`, e.ID, e.Owner, e.Fence, e.CachePass)
	if err != nil {
		return ErrUncertain
	}
	if n, err := result.RowsAffected(); err != nil || n != 1 {
		return ErrFence
	}
	return nil
}
