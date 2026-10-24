package repository

import (
	"context"
	"database/sql"
	"errors"
	"regexp"

	"github.com/Wei-Shaw/sub2api/internal/service"
)

var renderControlAmount = regexp.MustCompile(`^(0|[1-9][0-9]{0,6})\.[0-9]{8}$`)

// ClaimRenderControlBilling participates in the caller's intent transaction.
// It deliberately does not call Apply or the separately committed batch holds.
func ClaimRenderControlBilling(ctx context.Context, tx *sql.Tx, requestID string, keyID int64, fingerprint string) (bool, error) {
	if tx == nil || keyID < 1 || len(requestID) < 1 || len(requestID) > 255 || len(fingerprint) != 64 {
		return false, errors.New("invalid render control billing claim")
	}
	return (&usageBillingRepository{}).claimUsageBillingRequest(ctx, tx, requestID, keyID, fingerprint)
}

// IncrementRenderControlKeyUsage mirrors the pinned quota/window semantics,
// with exact NUMERIC parameters rather than a float64 round trip. The caller
// already owns the user, Key and intent locks. Existing authorized work retains
// its original counters after soft deletion, without granting new access or
// changing deleted_at. This Provider-specific terminal rule is documented in 40.
func IncrementRenderControlKeyUsage(ctx context.Context, tx *sql.Tx, keyID int64, amount string) error {
	if tx == nil || keyID < 1 || !renderControlAmount.MatchString(amount) {
		return errors.New("invalid render control billing amount")
	}
	result, err := tx.ExecContext(ctx, `UPDATE api_keys SET
		quota_used = quota_used + $1::numeric,
		status = CASE WHEN quota > 0 AND status = $3 AND quota_used < quota AND quota_used + $1::numeric >= quota THEN $4 ELSE status END,
		usage_5h = CASE WHEN window_5h_start IS NOT NULL AND window_5h_start + INTERVAL '5 hours' <= NOW() THEN $1::numeric ELSE usage_5h + $1::numeric END,
		usage_1d = CASE WHEN window_1d_start IS NOT NULL AND window_1d_start + INTERVAL '24 hours' <= NOW() THEN $1::numeric ELSE usage_1d + $1::numeric END,
		usage_7d = CASE WHEN window_7d_start IS NOT NULL AND window_7d_start + INTERVAL '7 days' <= NOW() THEN $1::numeric ELSE usage_7d + $1::numeric END,
		window_5h_start = CASE WHEN window_5h_start IS NULL OR window_5h_start + INTERVAL '5 hours' <= NOW() THEN NOW() ELSE window_5h_start END,
		window_1d_start = CASE WHEN window_1d_start IS NULL OR window_1d_start + INTERVAL '24 hours' <= NOW() THEN date_trunc('day', NOW()) ELSE window_1d_start END,
		window_7d_start = CASE WHEN window_7d_start IS NULL OR window_7d_start + INTERVAL '7 days' <= NOW() THEN date_trunc('day', NOW()) ELSE window_7d_start END,
		updated_at = NOW() WHERE id = $2`, amount, keyID, service.StatusAPIKeyActive, service.StatusAPIKeyQuotaExhausted)
	if err != nil {
		return err
	}
	if n, err := result.RowsAffected(); err != nil || n != 1 {
		return errors.New("render control original Key missing")
	}
	return nil
}
