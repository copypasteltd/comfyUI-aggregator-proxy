package rendercontrol

import (
	"context"
	"time"
)

type Effect struct {
	ID             int64
	IntentID       string
	Kind           string
	UserID         int64
	KeyID          int64
	ActualAmount   Money
	LedgerRef      string
	Owner          string
	Fence          int64
	CachePass      int
	BillingGroupID int64
	SubscriptionID int64
}

// ClaimEffects is bounded, SKIP LOCKED and reclaimable. A consumer must finish
// all of its idempotent side effects before acknowledging the current fence.
func (s *Store) ClaimEffects(ctx context.Context, owner string, limit int, lease time.Duration) ([]Effect, error) {
	return s.claimEffects(ctx, "", owner, limit, lease)
}

func (s *Store) claimEffects(ctx context.Context, instance, owner string, limit int, lease time.Duration) ([]Effect, error) {
	if !labelPattern.MatchString(owner) || limit < 1 || limit > 100 || lease < time.Second || lease > time.Minute {
		return nil, ErrInvalid
	}
	ctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	tx, err := begin(ctx, s.db)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	rows, err := tx.QueryContext(ctx, `WITH candidates AS (
		SELECT e.id,COALESCE(i.billing_group_id,0) billing_group_id,COALESCE(i.subscription_id,0) subscription_id FROM render_control_effects e JOIN render_control_intents i ON i.id=e.intent_id
		WHERE e.acknowledged_at IS NULL AND e.available_at<=NOW() AND (e.lease_until IS NULL OR e.lease_until<=NOW()) AND ($4='' OR i.instance=$4)
		ORDER BY e.available_at,e.id LIMIT $1 FOR UPDATE OF e SKIP LOCKED)
		UPDATE render_control_effects e SET lease_owner=$2,lease_until=NOW()+($3::bigint*INTERVAL '1 millisecond'),fence=e.fence+1
		FROM candidates c WHERE e.id=c.id RETURNING e.id,e.intent_id,e.kind,e.user_id,e.api_key_id,e.actual_amount::text,COALESCE(e.ledger_ref,''),e.fence,e.cache_pass,c.billing_group_id,c.subscription_id`, limit, owner, lease.Milliseconds(), instance)
	if err != nil {
		return nil, ErrStorage
	}
	result := make([]Effect, 0, limit)
	for rows.Next() {
		var e Effect
		var amount string
		if err = rows.Scan(&e.ID, &e.IntentID, &e.Kind, &e.UserID, &e.KeyID, &amount, &e.LedgerRef, &e.Fence, &e.CachePass, &e.BillingGroupID, &e.SubscriptionID); err != nil {
			_ = rows.Close()
			return nil, ErrStorage
		}
		if e.ActualAmount, err = ParseMoney(amount); err != nil {
			_ = rows.Close()
			return nil, ErrStorage
		}
		e.Owner = owner
		result = append(result, e)
	}
	readErr := rows.Err()
	_ = rows.Close()
	if readErr != nil {
		return nil, ErrStorage
	}
	return result, commit(tx)
}

func (s *Store) AcknowledgeEffect(ctx context.Context, id int64, owner string, fence int64) error {
	if id < 1 || !labelPattern.MatchString(owner) || fence < 1 {
		return ErrInvalid
	}
	ctx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	// Repeated acknowledgements of this same lease succeed. An expired lease
	// cannot be first-acknowledged, and a reclaimed fence invalidates the old one.
	result, err := s.db.ExecContext(ctx, `UPDATE render_control_effects SET acknowledged_at=COALESCE(acknowledged_at,NOW()) WHERE id=$1 AND lease_owner=$2 AND fence=$3 AND (acknowledged_at IS NOT NULL OR lease_until>NOW())`, id, owner, fence)
	if err != nil {
		return ErrUncertain
	}
	if affected, err := result.RowsAffected(); err != nil || affected != 1 {
		return ErrFence
	}
	return nil
}
