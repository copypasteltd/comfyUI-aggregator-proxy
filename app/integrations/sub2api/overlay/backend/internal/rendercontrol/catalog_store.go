package rendercontrol

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"sort"

	"github.com/Wei-Shaw/sub2api/internal/rendercontrol/controlclient"
	"github.com/lib/pq"
)

const maxOwnedModels = 16384

func (s *Store) replayCatalogRequest(ctx context.Context, subject Subject, operation, token, hash string) (Intent, error) {
	i, err := scanIntent(s.db.QueryRowContext(ctx, `SELECT `+columns+` FROM render_control_intents WHERE instance=$1 AND user_id=$2 AND api_key_id=$3 AND operation=$4 AND idempotency_hash=$5`, subject.instance, subject.userID, subject.keyID, operation, digest([]byte(token))))
	if err != nil {
		return Intent{}, err
	}
	if hash != "" && i.ClientRequestHash != hash {
		return Intent{}, ErrConflict
	}
	return i, nil
}

func catalogRoot(ctx context.Context, tx *sql.Tx, instance, gateway string) (sql.NullInt64, error) {
	var confirmed sql.NullInt64
	if _, err := tx.ExecContext(ctx, `SELECT pg_advisory_xact_lock(hashtextextended('rc-catalog-owner:'||$1,0))`, instance); err != nil {
		return confirmed, ErrStorage
	}
	if _, err := tx.ExecContext(ctx, `INSERT INTO render_control_catalogs(instance,target_gateway_id) VALUES ($1,$2) ON CONFLICT DO NOTHING`, instance, gateway); err != nil {
		return confirmed, ErrStorage
	}
	if err := tx.QueryRowContext(ctx, `SELECT confirmed_revision FROM render_control_catalogs WHERE instance=$1 AND target_gateway_id=$2 FOR UPDATE`, instance, gateway).Scan(&confirmed); err != nil {
		return confirmed, ErrStorage
	}
	return confirmed, nil
}

func rememberCatalogOwners(ctx context.Context, tx *sql.Tx, instance, gateway string, aliases []string) error {
	sort.Strings(aliases)
	for _, alias := range aliases {
		if _, err := tx.ExecContext(ctx, `INSERT INTO render_control_model_owners(instance,model,target_gateway_id) VALUES ($1,$2,$3) ON CONFLICT DO NOTHING`, instance, alias, gateway); err != nil {
			return ErrStorage
		}
		var owner string
		if err := tx.QueryRowContext(ctx, `SELECT target_gateway_id FROM render_control_model_owners WHERE instance=$1 AND model=$2`, instance, alias).Scan(&owner); err != nil {
			return ErrStorage
		}
		if owner != gateway {
			return ErrConflict
		}
	}
	var count int
	if tx.QueryRowContext(ctx, `SELECT count(*) FROM render_control_model_owners WHERE instance=$1`, instance).Scan(&count) != nil {
		return ErrStorage
	}
	if count > maxOwnedModels {
		return ErrInvalid
	}
	return nil
}

func (h *PublicHandler) initializeCatalogs(ctx context.Context) error {
	ids := make([]string, 0, len(h.clients))
	for id := range h.clients {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	for _, gateway := range ids {
		tx, err := begin(ctx, h.store.db)
		if err != nil {
			return err
		}
		confirmed, err := catalogRoot(ctx, tx, h.instance, gateway)
		if err == nil && !confirmed.Valid {
			aliases := []string{}
			rules := []PublicModelRule{}
			for alias, rule := range h.models {
				if rule.model.Target.gatewayID == gateway {
					aliases = append(aliases, alias)
					rules = append(rules, rule.model)
				}
			}
			err = rememberCatalogOwners(ctx, tx, h.instance, gateway, aliases)
			if err == nil {
				err = saveCatalogReadAuthority(ctx, tx, h.instance, gateway, 0, rules, true)
			}
		}
		if err == nil {
			err = commit(tx)
		}
		_ = tx.Rollback()
		if err != nil {
			return err
		}
	}
	return h.refreshCatalogs(ctx)
}

// Each validation failure has a fixed receipt code. SQL details never cross the
// service boundary; all account/group locks end before the receipt HTTP call.
func validateCatalogAccounts(ctx context.Context, tx *sql.Tx, rules []PublicModelRule) string {
	accounts, groups := map[int64]bool{}, map[int64]bool{}
	for _, rule := range rules {
		accounts[rule.UsageAccountID] = true
		for _, id := range rule.GroupIDs {
			groups[id] = true
		}
	}
	sets := []struct {
		values         map[int64]bool
		query, failure string
	}{
		{accounts, `SELECT id,platform='openai' AND NOT schedulable AND deleted_at IS NULL FROM accounts WHERE id=ANY($1) ORDER BY id FOR SHARE`, "account_unavailable"},
		{groups, `SELECT id,status='active' AND deleted_at IS NULL AND subscription_type IN ('standard','subscription') FROM groups WHERE id=ANY($1) ORDER BY id FOR SHARE`, "group_unavailable"},
	}
	for _, set := range sets {
		ids := make([]int64, 0, len(set.values))
		for id := range set.values {
			ids = append(ids, id)
		}
		sort.Slice(ids, func(i, j int) bool { return ids[i] < ids[j] })
		rows, err := tx.QueryContext(ctx, set.query, pq.Array(ids))
		if err != nil {
			return "storage_unavailable"
		}
		found, valid := 0, true
		for rows.Next() {
			var id int64
			var available bool
			if rows.Scan(&id, &available) != nil {
				valid = false
				break
			}
			found++
			valid = valid && available
		}
		readErr := rows.Err()
		_ = rows.Close()
		if readErr != nil {
			return "storage_unavailable"
		}
		if !valid || found != len(ids) {
			return set.failure
		}
	}
	return ""
}

func (h *PublicHandler) persistCatalog(ctx context.Context, release controlclient.GatewayCatalogRelease, rules []PublicModelRule, validateAccounts bool) string {
	tx, err := begin(ctx, h.store.db)
	if err != nil {
		return "storage_unavailable"
	}
	defer tx.Rollback()
	if _, err = catalogRoot(ctx, tx, h.instance, release.GatewayID); err != nil {
		return "storage_unavailable"
	}
	if validateAccounts {
		if code := validateCatalogAccounts(ctx, tx, rules); code != "" {
			return code
		}
	}
	raw, err := json.Marshal(release)
	if err != nil {
		return "unsupported_configuration"
	}
	if _, err = tx.ExecContext(ctx, `INSERT INTO render_control_catalog_releases(instance,target_gateway_id,revision,digest,configuration_json,release_json) VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT DO NOTHING`, h.instance, release.GatewayID, release.Revision, release.Digest, release.ConfigurationJSON, string(raw)); err != nil {
		return "storage_unavailable"
	}
	var existing string
	if tx.QueryRowContext(ctx, `SELECT release_json FROM render_control_catalog_releases WHERE instance=$1 AND target_gateway_id=$2 AND revision=$3`, h.instance, release.GatewayID, release.Revision).Scan(&existing) != nil {
		return "storage_unavailable"
	}
	if existing != string(raw) {
		return "unsupported_configuration"
	}
	aliases := make([]string, 0, len(rules))
	for _, rule := range rules {
		aliases = append(aliases, rule.Model)
	}
	if err = rememberCatalogOwners(ctx, tx, h.instance, release.GatewayID, aliases); err != nil {
		if errors.Is(err, ErrStorage) {
			return "storage_unavailable"
		}
		return "unsupported_configuration"
	}
	if err = saveCatalogReadAuthority(ctx, tx, h.instance, release.GatewayID, release.Revision, rules, false); err != nil {
		return "unsupported_configuration"
	}
	if commit(tx) != nil {
		return "storage_unavailable"
	}
	return ""
}

func (h *PublicHandler) confirmCatalog(ctx context.Context, release controlclient.GatewayCatalogRelease) error {
	tx, err := begin(ctx, h.store.db)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	confirmed, err := catalogRoot(ctx, tx, h.instance, release.GatewayID)
	if err != nil {
		return err
	}
	if !confirmed.Valid || confirmed.Int64 < release.Revision {
		rules, err := h.rulesForCatalog(release)
		if err != nil {
			return err
		}
		if err = saveCatalogReadAuthority(ctx, tx, h.instance, release.GatewayID, release.Revision, rules, true); err != nil {
			return err
		}
		var digest string
		if tx.QueryRowContext(ctx, `SELECT digest FROM render_control_catalog_releases WHERE instance=$1 AND target_gateway_id=$2 AND revision=$3`, h.instance, release.GatewayID, release.Revision).Scan(&digest) != nil || digest != release.Digest {
			return ErrConflict
		}
		if _, err = tx.ExecContext(ctx, `UPDATE render_control_catalogs SET confirmed_revision=$3 WHERE instance=$1 AND target_gateway_id=$2`, h.instance, release.GatewayID, release.Revision); err != nil {
			return ErrStorage
		}
	}
	return commit(tx)
}

// Keep only the latest confirmed read authority for each owned name. Withdrawal
// preserves it; the remote content policy and current upstream group still gate
// every read. Bound the complete historical group set before acknowledging.
func saveCatalogReadAuthority(ctx context.Context, tx *sql.Tx, instance, gateway string, revision int64, rules []PublicModelRule, apply bool) error {
	aliases := []string{}
	groupCount := 0
	for _, rule := range rules {
		aliases = append(aliases, rule.Model)
		groupCount += len(rule.GroupIDs)
		var task sql.NullString
		if tx.QueryRowContext(ctx, `SELECT task_type FROM render_control_model_owners WHERE instance=$1 AND model=$2 AND target_gateway_id=$3`, instance, rule.Model, gateway).Scan(&task) != nil {
			return ErrStorage
		}
		if task.Valid && task.String != rule.TaskType {
			return ErrConflict
		}
	}
	var previous int
	if tx.QueryRowContext(ctx, `SELECT COALESCE(sum(cardinality(group_ids)),0) FROM render_control_model_owners WHERE instance=$1 AND model<>ALL($2)`, instance, pq.Array(aliases)).Scan(&previous) != nil {
		return ErrStorage
	}
	if previous+groupCount > 131072 {
		return ErrInvalid
	}
	if !apply {
		return nil
	}
	for _, rule := range rules {
		if _, err := tx.ExecContext(ctx, `UPDATE render_control_model_owners SET read_revision=$4,task_type=$5,group_ids=$6 WHERE instance=$1 AND model=$2 AND target_gateway_id=$3 AND read_revision<=$4`, instance, rule.Model, gateway, revision, rule.TaskType, pq.Array(rule.GroupIDs)); err != nil {
			return ErrStorage
		}
	}
	return nil
}

func lockReservationCatalog(ctx context.Context, tx *sql.Tx, subject Subject, target Target, request Request, quote Quote) error {
	var confirmed sql.NullInt64
	err := tx.QueryRowContext(ctx, `SELECT confirmed_revision FROM render_control_catalogs WHERE instance=$1 AND target_gateway_id=$2 FOR SHARE`, subject.instance, target.gatewayID).Scan(&confirmed)
	if err != nil && !errors.Is(err, sql.ErrNoRows) {
		return ErrStorage
	}
	if !confirmed.Valid {
		if request.catalog != nil {
			return ErrConflict
		}
		return nil
	}
	stamp := request.catalog
	if stamp == nil || !stamp.Valid() || stamp.InstanceID != subject.instance || stamp.Revision != confirmed.Int64 {
		return ErrConflict
	}
	var raw string
	if tx.QueryRowContext(ctx, `SELECT release_json FROM render_control_catalog_releases WHERE instance=$1 AND target_gateway_id=$2 AND revision=$3 AND digest=$4`, subject.instance, target.gatewayID, stamp.Revision, stamp.Digest).Scan(&raw) != nil {
		return ErrConflict
	}
	var release controlclient.GatewayCatalogRelease
	if json.Unmarshal([]byte(raw), &release) != nil {
		return ErrStorage
	}
	configuration, err := controlclient.ParseGatewayCatalog(release, target.gatewayID, subject.instance)
	if err != nil {
		return ErrStorage
	}
	var groupID sql.NullInt64
	if tx.QueryRowContext(ctx, `SELECT group_id FROM api_keys WHERE id=$1`, subject.keyID).Scan(&groupID) != nil {
		return ErrStorage
	}
	for _, rule := range configuration.Models {
		account, _ := controlclient.PositiveCatalogID(rule.UsageAccountID)
		price, parseErr := ParseMoney(rule.UnitPrice)
		if rule.Model != request.model || rule.TaskType != request.taskType || rule.PriceVersion != quote.Version || parseErr != nil || price != quote.UnitPrice || account != request.usageAccountID {
			continue
		}
		for _, value := range rule.GroupIDs {
			group, _ := controlclient.PositiveCatalogID(value)
			if groupID.Valid && group == groupID.Int64 {
				return nil
			}
		}
	}
	return ErrConflict
}
