package service

import (
	"context"
	"time"

	"github.com/Wei-Shaw/sub2api/internal/pkg/timezone"
)

// RenderControlWindowPlan exposes the pinned subscription's existing pure window
// rules. The caller must lock the authoritative row and apply this in its own
// billing transaction; no money or cached usage is passed through float64 here.
type RenderControlWindowPlan struct {
	Daily, Weekly, Monthly *time.Time
	Activate               bool
}

func PlanRenderControlWindows(sub UserSubscription, now time.Time) RenderControlWindowPlan {
	now = now.In(timezone.Location())
	sub.StartsAt = sub.StartsAt.In(timezone.Location())
	if !sub.IsWindowActivated() {
		day := timezone.StartOfDay(now)
		return RenderControlWindowPlan{Daily: &day, Weekly: &now, Monthly: &now, Activate: true}
	}
	var plan RenderControlWindowPlan
	if value, ok := sub.automaticDailyWindowStartAt(now); ok {
		plan.Daily = &value
	}
	if value, ok := sub.automaticWindowStartAt(sub.WeeklyWindowStart, 7*24*time.Hour, now); ok {
		plan.Weekly = &value
	}
	if value, ok := sub.automaticWindowStartAt(sub.MonthlyWindowStart, 30*24*time.Hour, now); ok {
		plan.Monthly = &value
	}
	return plan
}

// RenderControlInvalidateSubscription retains native L1 + Redis + pubsub
// behavior while honoring the effect consumer's context for external I/O.
// L1 deletion is asynchronous in the pinned cache; the durable second pass
// reduces late refill races, and is not a global linearizability guarantee.
func (s *SubscriptionService) RenderControlInvalidateSubscription(ctx context.Context, userID, groupID int64) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	s.InvalidateSubCache(userID, groupID)
	if s.billingCacheService == nil {
		return ErrBillingServiceUnavailable
	}
	if err := s.billingCacheService.InvalidateSubscription(ctx, userID, groupID); err != nil {
		return err
	}
	return s.billingCacheService.PublishSubscriptionCacheInvalidation(ctx, subCacheKey(userID, groupID))
}
