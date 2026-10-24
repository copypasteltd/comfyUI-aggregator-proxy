package rendercontrol

import (
	"context"
	"database/sql"
	"errors"
	"time"

	"github.com/Wei-Shaw/sub2api/internal/service"
)

// The caller already holds user -> Key (and, for admission, current group).
// Terminal work finds the immutable original subscription before the intent
// lock. Current status may reject new work but never suppress existing billing.
func lockSubscription(ctx context.Context, tx *sql.Tx, id, userID, groupID int64, admitting bool) (service.UserSubscription, time.Time, error) {
	var sub service.UserSubscription
	var daily, weekly, monthly, deleted sql.NullTime
	var now time.Time
	err := tx.QueryRowContext(ctx, `SELECT id,user_id,group_id,starts_at,expires_at,status,
		daily_window_start,weekly_window_start,monthly_window_start,deleted_at,clock_timestamp()
		FROM user_subscriptions WHERE (id=$1 AND $1>0) OR ($1=0 AND user_id=$2 AND group_id=$3 AND deleted_at IS NULL)
		FOR NO KEY UPDATE`, id, userID, groupID).Scan(&sub.ID, &sub.UserID, &sub.GroupID, &sub.StartsAt, &sub.ExpiresAt, &sub.Status, &daily, &weekly, &monthly, &deleted, &now)
	if errors.Is(err, sql.ErrNoRows) {
		return sub, now, ErrSubscription
	}
	if err != nil {
		return sub, now, ErrStorage
	}
	if sub.UserID != userID || sub.GroupID != groupID {
		return sub, now, ErrConflict
	}
	if daily.Valid {
		sub.DailyWindowStart = &daily.Time
	}
	if weekly.Valid {
		sub.WeeklyWindowStart = &weekly.Time
	}
	if monthly.Valid {
		sub.MonthlyWindowStart = &monthly.Time
	}
	if deleted.Valid {
		sub.DeletedAt = &deleted.Time
	}
	if admitting && !subscriptionActiveAt(sub, now) {
		return sub, now, ErrSubscription
	}
	return sub, now, nil
}

func subscriptionActiveAt(sub service.UserSubscription, now time.Time) bool {
	return sub.Status == "active" && sub.DeletedAt == nil && !now.Before(sub.StartsAt) && now.Before(sub.ExpiresAt)
}

func maintainSubscription(ctx context.Context, tx *sql.Tx, sub service.UserSubscription, now time.Time) error {
	// Expiry, suspension and soft deletion do not mint new allowance. A late
	// terminal can still add actual consumption to this original row.
	if !subscriptionActiveAt(sub, now) {
		return nil
	}
	plan := service.PlanRenderControlWindows(sub, now)
	if plan.Daily == nil && plan.Weekly == nil && plan.Monthly == nil {
		return nil
	}
	_, err := tx.ExecContext(ctx, `UPDATE user_subscriptions SET
		daily_window_start=COALESCE($2::timestamptz,daily_window_start),
		weekly_window_start=COALESCE($3::timestamptz,weekly_window_start),
		monthly_window_start=COALESCE($4::timestamptz,monthly_window_start),
		daily_usage_usd=CASE WHEN $2::timestamptz IS NOT NULL AND NOT $5 THEN 0 ELSE daily_usage_usd END,
		weekly_usage_usd=CASE WHEN $3::timestamptz IS NOT NULL AND NOT $5 THEN 0 ELSE weekly_usage_usd END,
		monthly_usage_usd=CASE WHEN $4::timestamptz IS NOT NULL AND NOT $5 THEN 0 ELSE monthly_usage_usd END,updated_at=NOW()
		WHERE id=$1`, sub.ID, plan.Daily, plan.Weekly, plan.Monthly, plan.Activate)
	if err != nil {
		return ErrStorage
	}
	return nil
}

func reserveSubscription(ctx context.Context, tx *sql.Tx, userID, groupID int64, hold Money) (int64, error) {
	sub, now, err := lockSubscription(ctx, tx, 0, userID, groupID, true)
	if err != nil {
		return 0, err
	}
	if err = maintainSubscription(ctx, tx, sub, now); err != nil {
		return 0, err
	}
	var allowed bool
	err = tx.QueryRowContext(ctx, `WITH pending AS (
		SELECT COALESCE(SUM(hold_amount),0) amount FROM render_control_intents
		WHERE subscription_id=$1 AND state IN ('reserved','dispatching','admitted'))
		SELECT (COALESCE(g.daily_limit_usd,0)<=0 OR s.daily_usage_usd+pending.amount+$2::numeric<=g.daily_limit_usd)
		AND (COALESCE(g.weekly_limit_usd,0)<=0 OR s.weekly_usage_usd+pending.amount+$2::numeric<=g.weekly_limit_usd)
		AND (COALESCE(g.monthly_limit_usd,0)<=0 OR s.monthly_usage_usd+pending.amount+$2::numeric<=g.monthly_limit_usd)
		FROM user_subscriptions s JOIN groups g ON g.id=s.group_id CROSS JOIN pending WHERE s.id=$1`, sub.ID, hold.String()).Scan(&allowed)
	if err != nil {
		return 0, ErrStorage
	}
	if !allowed {
		return 0, ErrSubscriptionQuota
	}
	return sub.ID, nil
}

func settleSubscription(ctx context.Context, tx *sql.Tx, i Intent) error {
	sub, now, err := lockSubscription(ctx, tx, i.SubscriptionID, i.UserID, i.BillingGroupID, false)
	if err != nil {
		return err
	}
	if i.ActualAmount > 0 {
		if err = maintainSubscription(ctx, tx, sub, now); err != nil {
			return err
		}
		if _, err = tx.ExecContext(ctx, `UPDATE user_subscriptions SET daily_usage_usd=daily_usage_usd+$2::numeric,
			weekly_usage_usd=weekly_usage_usd+$2::numeric,monthly_usage_usd=monthly_usage_usd+$2::numeric,updated_at=NOW() WHERE id=$1`, i.SubscriptionID, i.ActualAmount.String()); err != nil {
			return ErrStorage
		}
	}
	return nil
}
