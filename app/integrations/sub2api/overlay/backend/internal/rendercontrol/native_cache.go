package rendercontrol

import (
	"context"

	"github.com/Wei-Shaw/sub2api/internal/service"
)

type nativeBillingInvalidator struct {
	cache service.BillingCache
	subs  *service.SubscriptionService
}

// NewNativeBillingInvalidator is used by the stock composition root. Subscription
// invalidation must pass through the real service's local cache and pubsub too.
func NewNativeBillingInvalidator(cache service.BillingCache, subs *service.SubscriptionService) BillingInvalidator {
	if cache == nil || subs == nil {
		return nil
	}
	return &nativeBillingInvalidator{cache: cache, subs: subs}
}

func (c *nativeBillingInvalidator) InvalidateUserBalance(ctx context.Context, userID int64) error {
	return c.cache.InvalidateUserBalance(ctx, userID)
}

func (c *nativeBillingInvalidator) InvalidateAPIKeyRateLimit(ctx context.Context, keyID int64) error {
	return c.cache.InvalidateAPIKeyRateLimit(ctx, keyID)
}

func (c *nativeBillingInvalidator) InvalidateSubscriptionCache(ctx context.Context, userID, groupID int64) error {
	return c.subs.RenderControlInvalidateSubscription(ctx, userID, groupID)
}
