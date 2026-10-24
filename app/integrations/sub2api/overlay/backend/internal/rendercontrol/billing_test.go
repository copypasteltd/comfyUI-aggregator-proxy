package rendercontrol

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"entgo.io/ent/dialect"
	entsql "entgo.io/ent/dialect/sql"
	dbent "github.com/Wei-Shaw/sub2api/ent"
	"github.com/Wei-Shaw/sub2api/internal/config"
	"github.com/Wei-Shaw/sub2api/internal/pkg/timezone"
	"github.com/Wei-Shaw/sub2api/internal/rendercontrol/controlclient"
	"github.com/Wei-Shaw/sub2api/internal/repository"
	"github.com/Wei-Shaw/sub2api/internal/server/middleware"
	"github.com/Wei-Shaw/sub2api/internal/service"
	"github.com/alicebob/miniredis/v2"
	"github.com/gin-gonic/gin"
	"github.com/redis/go-redis/v9"
)

func billingGroup(t *testing.T, subject Subject) int64 {
	t.Helper()
	var id int64
	if err := testDB.QueryRow(`SELECT group_id FROM api_keys WHERE id=$1`, subject.keyID).Scan(&id); err != nil {
		t.Fatal(err)
	}
	return id
}

func subscriptionFixture(t *testing.T, subject Subject) int64 {
	t.Helper()
	group := billingGroup(t, subject)
	runSQL(t, `UPDATE groups SET subscription_type='subscription' WHERE id=$1`, group)
	var id int64
	if err := testDB.QueryRow(`INSERT INTO user_subscriptions(user_id,group_id,starts_at,expires_at) VALUES ($1,$2,NOW()-INTERVAL '1 day',NOW()+INTERVAL '90 days') RETURNING id`, subject.userID, group).Scan(&id); err != nil {
		t.Fatal("subscription fixture", err)
	}
	return id
}

func subscriptionUsage(t *testing.T, id int64, expected string) {
	t.Helper()
	var daily, weekly, monthly string
	if err := testDB.QueryRow(`SELECT daily_usage_usd::text,weekly_usage_usd::text,monthly_usage_usd::text FROM user_subscriptions WHERE id=$1`, id).Scan(&daily, &weekly, &monthly); err != nil {
		t.Fatal(err)
	}
	if daily != expected || weekly != expected || monthly != expected {
		t.Fatalf("subscription counters %s/%s/%s, expected %s", daily, weekly, monthly, expected)
	}
}

func TestPricingRoundsCompleteAmountOnce(t *testing.T) {
	for _, item := range []struct {
		unit, rate string
		units      int64
		want       string
	}{
		{"0.00000001", "0.5", 2, "0.00000001"}, // rounding a unit first would double this
		{"0.00000001", "0.5", 1, "0.00000001"},
		{"0.00000001", "0.4999", 1, "0.00000000"},
		{"0.00000001", "0", 100, "0.00000000"},
		{"0.00000001", "999999.9999", 100, "1.00000000"},
		{"1000000", "0.0001", 1, "100.00000000"},
		{"1.23456789", "1.25", 3, "4.62962959"},
	} {
		t.Run(item.unit+"_"+item.rate+"_"+strconv.FormatInt(item.units, 10), func(t *testing.T) {
			rate, err := parseRate(item.rate)
			if err != nil {
				t.Fatal(err)
			}
			amount, err := pricedAmount(money(t, item.unit), item.units, rate)
			if err != nil || amount.String() != item.want {
				t.Fatal("incorrect exact decimal amount", amount, err)
			}
		})
	}
	for _, raw := range []string{"-1", "NaN", "Infinity", "1e-4", "01", "1000000", "0.00001"} {
		t.Run("invalid_"+raw, func(t *testing.T) {
			if _, err := parseRate(raw); !errors.Is(err, ErrInvalid) {
				t.Fatal("invalid multiplier accepted")
			}
		})
	}
	if _, err := pricedAmount(maxMoney, 1, Rate(2*rateScale)); !errors.Is(err, ErrInvalid) {
		t.Fatal("rated maximum overflow accepted")
	}
	if _, err := pricedAmount(maxMoney, 1, 9_999_999_999); !errors.Is(err, ErrInvalid) {
		t.Fatal("large decimal product overflowed before validation")
	}
	if _, err := pricedAmount(maxMoney, 2, 0); !errors.Is(err, ErrInvalid) {
		t.Fatal("free rate bypassed base amount bound")
	}
}

func TestPricingUsesNativeMediaAndUserPriority(t *testing.T) {
	for _, item := range []struct{ name, task, override, settings, want, source string }{
		{"group", "text_to_image", "", "", "1.60000000", "group"},
		{"user", "text_to_image", "0.5", "", "1.00000000", "user"},
		{"null_user", "text_to_image", "NULL", "", "1.60000000", "group"},
		{"independent_image", "references_to_image", "0.5", "image_rate_independent=TRUE,image_rate_multiplier=0.25", "0.50000000", "image"},
		{"independent_video", "references_to_video", "0.5", "video_rate_independent=TRUE,video_rate_multiplier=0.125", "0.25000000", "video"},
		{"image_not_video", "text_to_video", "0.5", "image_rate_independent=TRUE,image_rate_multiplier=9", "1.00000000", "user"},
		{"video_not_image", "image_to_image", "0.5", "video_rate_independent=TRUE,video_rate_multiplier=9", "1.00000000", "user"},
	} {
		t.Run(item.name, func(t *testing.T) {
			s, subject := store(t), fixture(t, "20")
			group := billingGroup(t, subject)
			runSQL(t, `UPDATE groups SET rate_multiplier=0.8,peak_rate_enabled=TRUE,peak_start='00:00',peak_end='23:59',peak_rate_multiplier=9 WHERE id=$1`, group)
			if item.override != "" {
				var value any = item.override
				if item.override == "NULL" {
					value = nil
				}
				runSQL(t, `INSERT INTO user_group_rate_multipliers(user_id,group_id,rate_multiplier) VALUES ($1,$2,$3::numeric)`, subject.userID, group, value)
			}
			if item.settings != "" {
				runSQL(t, `UPDATE groups SET `+item.settings+` WHERE id=$1`, group)
			}
			request, err := NewRequest(publicOperation(item.task), controlclient.JobRequest{Model: "synthetic-media", TaskType: item.task, Input: map[string]any{"prompt": "synthetic"}, Parameters: map[string]any{"n": 2, "seconds": 2}})
			if err != nil {
				t.Fatal(err)
			}
			i, err := s.Reserve(ctx(), subject, testTarget(t), "native-rate", request, Quote{"p1", money(t, "1"), 2})
			if err != nil || i.HoldAmount.String() != item.want || i.RateSource != item.source {
				t.Fatal("native media priority/peak exclusion failed", i.HoldAmount, i.RateSource, err)
			}
		})
	}
}

func TestPricingRemainsFrozenAcrossRegroupAndReplay(t *testing.T) {
	s, subject := store(t), fixture(t, "10")
	group := billingGroup(t, subject)
	runSQL(t, `UPDATE groups SET rate_multiplier=0.25 WHERE id=$1`, group)
	i := reserve(t, s, subject, "freeze-rate", 2, "1")
	other := fixture(t, "10")
	runSQL(t, `UPDATE api_keys SET group_id=$2 WHERE id=$1`, subject.keyID, billingGroup(t, other))
	runSQL(t, `UPDATE groups SET rate_multiplier=9 WHERE id=$1`, group)
	replayed, err := s.Reserve(ctx(), subject, testTarget(t), "freeze-rate", imageRequest(t, "synthetic test prompt", 2), Quote{"changed", money(t, "7"), 2})
	if err != nil || replayed.ID != i.ID || replayed.RateMultiplier != 2500 || replayed.BillingGroupID != group || replayed.HoldAmount.String() != "0.50000000" {
		t.Fatal("replay repriced original intent", err)
	}
	for _, change := range []string{"rate_multiplier=2", "rate_source='image'", "billing_type=1", "subscription_id=1", "unit_price=2", "max_units=3", "price_version='mutated'"} {
		if _, err := testDB.Exec(`UPDATE render_control_intents SET `+change+` WHERE id=$1`, i.ID); err == nil {
			t.Fatal("database allowed accounting mutation", change)
		}
	}
	if _, err := s.MarkDispatch(ctx(), i.ID); err != nil {
		t.Fatal(err)
	}
	settled, err := s.Finalize(ctx(), testTarget(t), subject.instance, event(t, i, "succeeded", 1))
	if err != nil || settled.ActualAmount.String() != "0.25000000" {
		t.Fatal("settlement repriced original intent", err)
	}
	balances(t, subject, "9.75000000", "0.00000000", "0.25000000")
}

func TestPricingFreePublicTasksRetainMeteringAndAuthentication(t *testing.T) {
	f := publicSetup(t)
	runSQL(t, `UPDATE users SET balance=0 WHERE id=$1`, f.subject.userID)
	runSQL(t, `UPDATE groups SET rate_multiplier=0 WHERE id=$1`, f.groupID)
	i := f.submit(t, "free-native-auth")
	if i.HoldAmount != 0 || i.RateMultiplier != 0 {
		t.Fatal("free task manufactured a charge")
	}
	if _, err := f.s.MarkDispatch(ctx(), i.ID); err != nil {
		t.Fatal(err)
	}
	i, err := f.s.Finalize(ctx(), testTarget(t), i.Instance, event(t, i, "succeeded", 1))
	if err != nil || i.ActualAmount != 0 || i.ImageCount != 1 {
		t.Fatal("free success lost real metering", err)
	}
	consumer := effectConsumer(t, f.s, &effectCacheFixture{}, i.Instance)
	for range 2 {
		if worked, err := consumer.Step(ctx()); !worked || err != nil {
			t.Fatal("free usage consumption", err)
		}
	}
	if countRows(t, `SELECT count(*) FROM usage_logs WHERE request_id=$1 AND total_cost=1 AND actual_cost=0 AND image_count=1 AND rate_multiplier=0`, i.LedgerRef) != 1 {
		t.Fatal("free native usage was omitted or mispriced")
	}
	balances(t, f.subject, "0.00000000", "0.00000000", "0.00000000")
	// The real stock middleware receives a client-supplied imitation header,
	// but no trusted Gin marker. Its normal balance check must still reject.
	router := gin.New()
	router.Use(gin.HandlerFunc(f.auth))
	router.POST("/v1/images/generations", func(c *gin.Context) { c.Status(204) })
	server := httptest.NewTLSServer(router)
	defer server.Close()
	request, _ := http.NewRequest(http.MethodPost, server.URL+"/v1/images/generations", strings.NewReader(`{}`))
	request.Header.Set("Authorization", "Bearer "+f.credential)
	request.Header.Set("X-Render-Control-Owns-Billing", "true")
	client := server.Client()
	client.Timeout = 5 * time.Second
	response, err := client.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.StatusCode != 403 {
		t.Fatal("client header bypassed stock billing", response.StatusCode)
	}
}

func TestSubscriptionRejectsInvalidEntitlementsWithoutBalanceFallback(t *testing.T) {
	for _, condition := range []string{"missing", "future", "expired", "suspended", "deleted"} {
		t.Run(condition, func(t *testing.T) {
			s, subject := store(t), fixture(t, "10")
			id := subscriptionFixture(t, subject)
			switch condition {
			case "missing":
				runSQL(t, `DELETE FROM user_subscriptions WHERE id=$1`, id)
			case "future":
				runSQL(t, `UPDATE user_subscriptions SET starts_at=NOW()+INTERVAL '1 day' WHERE id=$1`, id)
			case "expired":
				runSQL(t, `UPDATE user_subscriptions SET expires_at=NOW() WHERE id=$1`, id)
			case "suspended":
				runSQL(t, `UPDATE user_subscriptions SET status='suspended' WHERE id=$1`, id)
			case "deleted":
				runSQL(t, `UPDATE user_subscriptions SET deleted_at=NOW() WHERE id=$1`, id)
			}
			if _, err := s.Reserve(ctx(), subject, testTarget(t), "invalid-entitlement", imageRequest(t, "synthetic", 1), Quote{"p1", money(t, "1"), 1}); !errors.Is(err, ErrSubscription) {
				t.Fatal("invalid entitlement accepted or fell back to balance", err)
			}
			if countRows(t, `SELECT count(*) FROM render_control_intents WHERE api_key_id=$1`, subject.keyID) != 0 {
				t.Fatal("invalid entitlement persisted a reservation")
			}
			balances(t, subject, "10.00000000", "0.00000000", "0.00000000")
		})
	}
}

func TestSubscriptionCrossKeyAndInstanceReservationsAreAtomic(t *testing.T) {
	for _, window := range []string{"daily", "weekly", "monthly"} {
		t.Run(window, func(t *testing.T) {
			s, subject := store(t), fixture(t, "10")
			id := subscriptionFixture(t, subject)
			group := billingGroup(t, subject)
			runSQL(t, `UPDATE groups SET `+window+`_limit_usd=1 WHERE id=$1`, group)
			var key int64
			name, _ := newID("synthetic-key")
			if err := testDB.QueryRow(`INSERT INTO api_keys(user_id,group_id,key,name) VALUES ($1,$2,$3,'synthetic') RETURNING id`, subject.userID, group, name).Scan(&key); err != nil {
				t.Fatal(err)
			}
			other, _ := NewSubject("billing-other", subject.userID, key, subject.ip)
			var wg sync.WaitGroup
			results := make(chan error, 20)
			accepted := make(chan Intent, 20)
			for index := range 20 {
				wg.Go(func() {
					owner := subject
					if index%2 == 1 {
						owner = other
					}
					i, err := s.Reserve(ctx(), owner, testTarget(t), fmt.Sprintf("sub-concurrent-%d", index), imageRequest(t, "synthetic", 1), Quote{"p1", money(t, "1"), 1})
					if err == nil {
						accepted <- i
					}
					results <- err
				})
			}
			wg.Wait()
			close(results)
			close(accepted)
			success := 0
			for err := range results {
				if err == nil {
					success++
				} else if !errors.Is(err, ErrSubscriptionQuota) {
					t.Fatal("unexpected concurrent billing result", err)
				}
			}
			if success != 1 {
				t.Fatal("shared subscription oversubscribed", success)
			}
			subscriptionUsage(t, id, "0.0000000000")
			for i := range accepted {
				if _, err := s.ReleaseUnsent(ctx(), i.ID); err != nil {
					t.Fatal(err)
				}
			}
			reserve(t, s, subject, "released-capacity", 1, "1")
			balances(t, subject, "10.00000000", "0.00000000", "0.00000000")
		})
	}
}

func TestSubscriptionKeyLimitsIncludeRatedPendingAmounts(t *testing.T) {
	for _, field := range []string{"quota", "rate_limit_5h", "rate_limit_1d", "rate_limit_7d"} {
		t.Run(field, func(t *testing.T) {
			s, subject := store(t), fixture(t, "0")
			id := subscriptionFixture(t, subject)
			runSQL(t, `UPDATE groups SET rate_multiplier=2 WHERE id=$1`, billingGroup(t, subject))
			runSQL(t, `UPDATE api_keys SET `+field+`=1 WHERE id=$1`, subject.keyID)
			if _, err := s.Reserve(ctx(), subject, testTarget(t), "rated-key-limit", imageRequest(t, "synthetic", 1), Quote{"p1", money(t, "1"), 1}); !errors.Is(err, ErrQuota) {
				t.Fatal("subscription bypassed Key monetary limit", err)
			}
			if countRows(t, `SELECT count(*) FROM user_subscriptions WHERE id=$1 AND daily_window_start IS NULL`, id) != 1 {
				t.Fatal("rejected request activated windows")
			}
			balances(t, subject, "0.00000000", "0.00000000", "0.00000000")
		})
	}
}

func TestSubscriptionSettlesOnceAgainstOriginalEntitlement(t *testing.T) {
	for _, condition := range []string{"active", "expired", "suspended", "deleted", "regrouped"} {
		t.Run(condition, func(t *testing.T) {
			s, subject := store(t), fixture(t, "10")
			id := subscriptionFixture(t, subject)
			runSQL(t, `UPDATE groups SET rate_multiplier=0.25 WHERE id=$1`, billingGroup(t, subject))
			i := reserve(t, s, subject, "original-subscription", 2, "1")
			if i.SubscriptionID != id || i.BillingType != 1 || i.HoldAmount.String() != "0.50000000" {
				t.Fatal("subscription snapshot missing")
			}
			if _, err := s.MarkDispatch(ctx(), i.ID); err != nil {
				t.Fatal(err)
			}
			switch condition {
			case "expired":
				runSQL(t, `UPDATE user_subscriptions SET expires_at=NOW()-INTERVAL '1 second' WHERE id=$1`, id)
			case "suspended":
				runSQL(t, `UPDATE user_subscriptions SET status='suspended' WHERE id=$1`, id)
			case "deleted":
				runSQL(t, `UPDATE user_subscriptions SET deleted_at=NOW() WHERE id=$1`, id)
			case "regrouped":
				other := fixture(t, "10")
				runSQL(t, `UPDATE api_keys SET group_id=$2,deleted_at=NOW() WHERE id=$1`, subject.keyID, billingGroup(t, other))
			}
			e := event(t, i, "succeeded", 1)
			var wg sync.WaitGroup
			results := make(chan error, 8)
			for range 8 {
				wg.Go(func() { _, err := s.Finalize(ctx(), testTarget(t), i.Instance, e); results <- err })
			}
			wg.Wait()
			close(results)
			for err := range results {
				if err != nil {
					t.Fatal("concurrent terminal replay", err)
				}
			}
			subscriptionUsage(t, id, "0.2500000000")
			balances(t, subject, "10.00000000", "0.00000000", "0.25000000")
			if _, err := testDB.Exec(`DELETE FROM user_subscriptions WHERE id=$1`, id); err == nil {
				t.Fatal("original billing entitlement could be hard deleted")
			}
		})
	}
}

func TestSubscriptionTerminalRollbackAndZeroCostOutcomes(t *testing.T) {
	s, subject := store(t), fixture(t, "0")
	id := subscriptionFixture(t, subject)
	i := reserve(t, s, subject, "rollback-subscription", 1, "1")
	if _, err := s.MarkDispatch(ctx(), i.ID); err != nil {
		t.Fatal(err)
	}
	runSQL(t, fmt.Sprintf(`CREATE FUNCTION rc_test_sub_terminal_fault() RETURNS TRIGGER LANGUAGE plpgsql AS $$ BEGIN IF NEW.id='%s' AND NEW.state='settled' THEN RAISE EXCEPTION 'synthetic terminal write fault'; END IF; RETURN NEW; END $$; CREATE TRIGGER rc_test_sub_terminal_fault BEFORE UPDATE ON render_control_intents FOR EACH ROW EXECUTE FUNCTION rc_test_sub_terminal_fault()`, i.ID))
	remove := func() {
		runSQL(t, `DROP TRIGGER IF EXISTS rc_test_sub_terminal_fault ON render_control_intents; DROP FUNCTION IF EXISTS rc_test_sub_terminal_fault()`)
	}
	t.Cleanup(remove)
	e := event(t, i, "succeeded", 1)
	if _, err := s.Finalize(ctx(), testTarget(t), i.Instance, e); err == nil {
		t.Fatal("terminal write fault was ignored")
	}
	subscriptionUsage(t, id, "0.0000000000")
	balances(t, subject, "0.00000000", "0.00000000", "0.00000000")
	if countRows(t, `SELECT count(*) FROM render_control_effects WHERE intent_id=$1 AND kind='terminal'`, i.ID) != 0 {
		t.Fatal("failed terminal leaked an effect")
	}
	remove()
	if _, err := s.Finalize(ctx(), testTarget(t), i.Instance, e); err != nil {
		t.Fatal("dedup was committed outside the failed transaction", err)
	}
	subscriptionUsage(t, id, "1.0000000000")
	for _, outcome := range []string{"failed", "cancelled", "expired", "not_dispatched"} {
		t.Run(outcome, func(t *testing.T) {
			i := reserve(t, s, subject, "zero-"+outcome, 1, "1")
			if outcome == "not_dispatched" {
				_, err := s.ReleaseUnsent(ctx(), i.ID)
				if err != nil {
					t.Fatal(err)
				}
			} else {
				if _, err := s.MarkDispatch(ctx(), i.ID); err != nil {
					t.Fatal(err)
				}
				if _, err := s.Finalize(ctx(), testTarget(t), i.Instance, event(t, i, outcome, 0)); err != nil {
					t.Fatal(err)
				}
			}
			subscriptionUsage(t, id, "1.0000000000")
		})
	}
}

func TestSubscriptionWindowPlanPreservesPinnedSemantics(t *testing.T) {
	zone := timezone.Location()
	anchor := time.Date(2026, 1, 31, 12, 0, 0, 0, zone)
	point := func(value time.Time) *time.Time { return &value }
	for _, name := range []string{"activation", "daily", "one_time", "weekly", "monthly_30_days", "final_partial", "legacy_midnight"} {
		t.Run(name, func(t *testing.T) {
			sub := service.UserSubscription{StartsAt: anchor, ExpiresAt: anchor.Add(90 * 24 * time.Hour), DailyWindowStart: point(anchor), WeeklyWindowStart: point(anchor), MonthlyWindowStart: point(anchor)}
			now := anchor.Add(30 * 24 * time.Hour)
			switch name {
			case "activation":
				sub.DailyWindowStart, sub.WeeklyWindowStart, sub.MonthlyWindowStart = nil, nil, nil
			case "daily":
				now = anchor.Add(13 * time.Hour)
			case "one_time":
				sub.ExpiresAt, now = anchor.Add(24*time.Hour), anchor.Add(13*time.Hour)
			case "weekly":
				now = anchor.Add(7 * 24 * time.Hour)
			case "final_partial":
				sub.ExpiresAt, now = anchor.Add(7*24*time.Hour), anchor.Add(7*24*time.Hour)
			case "legacy_midnight":
				sub.WeeklyWindowStart = point(timezone.StartOfDay(anchor))
				now = anchor.Add(7*24*time.Hour - time.Hour)
			}
			plan := service.PlanRenderControlWindows(sub, now)
			switch name {
			case "activation":
				if !plan.Activate || plan.Daily == nil || !plan.Daily.Equal(timezone.StartOfDay(now)) || !plan.Weekly.Equal(now) || !plan.Monthly.Equal(now) {
					t.Fatal("initial window anchors changed")
				}
			case "daily":
				if plan.Daily == nil || !plan.Daily.Equal(timezone.StartOfDay(now)) {
					t.Fatal("daily window did not use configured midnight")
				}
			case "one_time":
				if plan.Daily != nil {
					t.Fatal("one-time daily allowance was reissued")
				}
			case "weekly":
				if plan.Weekly == nil || !plan.Weekly.Equal(now) {
					t.Fatal("weekly exact boundary changed")
				}
			case "monthly_30_days":
				if plan.Monthly == nil || !plan.Monthly.Equal(now) || now.Month() != time.March {
					t.Fatal("30-day month was replaced with calendar arithmetic")
				}
			case "final_partial", "legacy_midnight":
				if plan.Weekly != nil {
					t.Fatal("final or legacy partial period reissued allowance")
				}
			}
		})
	}
}

func TestSubscriptionPostgresWindowsKeepOutstandingReservations(t *testing.T) {
	for _, item := range []struct{ window, age string }{{"daily", "2 days"}, {"weekly", "8 days"}, {"monthly", "31 days"}} {
		t.Run(item.window, func(t *testing.T) {
			s, subject := store(t), fixture(t, "0")
			id := subscriptionFixture(t, subject)
			runSQL(t, `UPDATE groups SET `+item.window+`_limit_usd=1 WHERE id=$1`, billingGroup(t, subject))
			i := reserve(t, s, subject, "old-window-reservation", 1, "0.75")
			runSQL(t, `UPDATE user_subscriptions SET starts_at=NOW()-INTERVAL '60 days',`+item.window+`_window_start=NOW()-$2::interval,`+item.window+`_usage_usd=1 WHERE id=$1`, id, item.age)
			if _, err := s.Reserve(ctx(), subject, testTarget(t), "new-window-overflow", imageRequest(t, "synthetic", 1), Quote{"p1", money(t, "0.5"), 1}); !errors.Is(err, ErrSubscriptionQuota) {
				t.Fatal("window reset lost an outstanding reservation", err)
			}
			if countRows(t, `SELECT count(*) FROM user_subscriptions WHERE id=$1 AND `+item.window+`_usage_usd=1`, id) != 1 {
				t.Fatal("denied admission committed its window reset")
			}
			if _, err := s.ReleaseUnsent(ctx(), i.ID); err != nil {
				t.Fatal(err)
			}
			reserve(t, s, subject, "new-window-capacity", 1, "0.5")
			if countRows(t, `SELECT count(*) FROM user_subscriptions WHERE id=$1 AND `+item.window+`_usage_usd=0`, id) != 1 {
				t.Fatal("authoritative expired window was not reset")
			}
		})
	}
}

func TestSubscriptionNativeUsageCacheAndPubsub(t *testing.T) {
	f := publicSetup(t)
	id := subscriptionFixture(t, f.subject)
	runSQL(t, `UPDATE users SET balance=0 WHERE id=$1`, f.subject.userID)
	runSQL(t, `UPDATE groups SET rate_multiplier=0.5 WHERE id=$1`, f.groupID)
	i := effectSettled(t, f, "succeeded")
	server := miniredis.RunT(t)
	redisClient := redis.NewClient(&redis.Options{Addr: server.Addr(), MaxRetries: -1, DialTimeout: time.Second, ReadTimeout: time.Second, WriteTimeout: time.Second, ContextTimeoutEnabled: true})
	t.Cleanup(func() { _ = redisClient.Close() })
	cache := repository.NewBillingCache(redisClient)
	client := dbent.NewClient(dbent.Driver(entsql.OpenDB(dialect.Postgres, testDB)))
	subRepo := repository.NewUserSubscriptionRepository(client)
	cfg := &config.Config{RunMode: config.RunModeStandard}
	billing := service.NewBillingCacheService(cache, nil, subRepo, nil, nil, nil, cfg, nil)
	t.Cleanup(billing.Stop)
	subs := service.NewSubscriptionService(repository.NewGroupRepository(client, testDB), subRepo, billing, client, cfg)
	t.Cleanup(subs.Stop)
	invalidator := NewNativeBillingInvalidator(cache, subs)
	if invalidator == nil || NewNativeBillingInvalidator(nil, subs) != nil || NewNativeBillingInvalidator(cache, nil) != nil {
		t.Fatal("native invalidator dependency checks failed")
	}
	// Observe the actual stock publication without relying on a guessed channel.
	publication := redisClient.PSubscribe(ctx(), "*")
	t.Cleanup(func() { _ = publication.Close() })
	if _, err := publication.Receive(ctx()); err != nil {
		t.Fatal(err)
	}
	consumer := effectConsumer(t, f.s, invalidator, i.Instance)
	for range 2 {
		if err := cache.SetSubscriptionCache(ctx(), i.UserID, i.BillingGroupID, &service.SubscriptionCacheData{}); err != nil {
			t.Fatal(err)
		}
		if worked, err := consumer.Step(ctx()); !worked || err != nil {
			t.Fatal("native subscription effect", err)
		}
		if _, err := cache.GetSubscriptionCache(ctx(), i.UserID, i.BillingGroupID); !errors.Is(err, redis.Nil) {
			t.Fatal("subscription cache was not invalidated", err)
		}
		call, cancel := context.WithTimeout(ctx(), time.Second)
		message, err := publication.ReceiveMessage(call)
		cancel()
		if err != nil || message.Payload != fmt.Sprintf("sub:%d:%d", i.UserID, i.BillingGroupID) {
			t.Fatal("native subscription invalidation was not published", err)
		}
		dueEffects(t, i.ID)
	}
	if countRows(t, `SELECT count(*) FROM usage_logs WHERE request_id=$1 AND subscription_id=$2 AND billing_type=1 AND total_cost=1 AND actual_cost=0.5 AND rate_multiplier=0.5`, i.LedgerRef, id) != 1 {
		t.Fatal("native usage lost frozen subscription pricing")
	}
	subscriptionUsage(t, id, "0.5000000000")
	balances(t, f.subject, "0.00000000", "0.00000000", "0.50000000")
}

func TestSubscriptionNativeProcessCrashAfterSettlement(t *testing.T) {
	s, subject := store(t), fixture(t, "0")
	id := subscriptionFixture(t, subject)
	i := reserve(t, s, subject, "native-subscription-crash", 1, "1")
	if _, err := s.MarkDispatch(ctx(), i.ID); err != nil {
		t.Fatal(err)
	}
	executable, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	processCtx, cancel := context.WithTimeout(ctx(), 20*time.Second)
	defer cancel()
	command := exec.CommandContext(processCtx, executable, "-test.run=^TestProcessBoundaryHelper$")
	command.Env = append(os.Environ(), "RC_TEST_CHILD_STAGE=subscription_settled", "RC_TEST_INTENT="+i.ID, "RC_TEST_USER="+strconv.FormatInt(subject.userID, 10), "RC_TEST_KEY="+strconv.FormatInt(subject.keyID, 10), "RC_TEST_INSTANCE="+subject.instance)
	output, err := command.CombinedOutput()
	var exit *exec.ExitError
	if !errors.As(err, &exit) || exit.ExitCode() != 73 {
		t.Fatalf("native subscription process did not stop after commit: %s", output)
	}
	loaded, err := store(t).Load(ctx(), subject, i.ID)
	if err != nil || !loaded.Terminal() || loaded.SubscriptionID != id {
		t.Fatal("subscription terminal did not survive abrupt exit", err)
	}
	e := controlclient.Settlement{ID: loaded.TerminalEventID, JobID: loaded.RemoteJobID, ExternalTaskRef: loaded.ID, AuthorizationRef: loaded.AuthorizationRef, PriceVersion: loaded.PriceVersion, Outcome: "succeeded", ImageCount: 1}
	if _, err := store(t).Finalize(ctx(), testTarget(t), loaded.Instance, e); err != nil {
		t.Fatal("restart could not replay original settlement", err)
	}
	subscriptionUsage(t, id, "1.0000000000")
	balances(t, subject, "0.00000000", "0.00000000", "1.00000000")
}

func TestSubscriptionPublicQuotaErrorContract(t *testing.T) {
	f := publicSetup(t)
	id := subscriptionFixture(t, f.subject)
	runSQL(t, `UPDATE groups SET daily_limit_usd=0.5 WHERE id=$1`, f.groupID)
	status, raw, _ := f.call(t, http.MethodPost, "/v1/jobs", publicImageBody, nil)
	var response struct {
		Error struct{ Code string } `json:"error"`
	}
	if status != 429 || json.Unmarshal(raw, &response) != nil || response.Error.Code != "subscription_quota_exceeded" {
		t.Fatal("subscription quota error changed public contract", status, string(raw))
	}
	subscriptionUsage(t, id, "0.0000000000")
	balances(t, f.subject, "10.00000000", "0.00000000", "0.00000000")
}

func TestSubscriptionPublicNativeAuthAndExpiredReplay(t *testing.T) {
	f := publicSetup(t)
	id := subscriptionFixture(t, f.subject)
	runSQL(t, `UPDATE users SET balance=0 WHERE id=$1`, f.subject.userID)
	client := dbent.NewClient(dbent.Driver(entsql.OpenDB(dialect.Postgres, testDB)))
	cfg := &config.Config{RunMode: config.RunModeStandard}
	subs := service.NewSubscriptionService(repository.NewGroupRepository(client, testDB), repository.NewUserSubscriptionRepository(client), nil, client, cfg)
	t.Cleanup(subs.Stop)
	keys := service.NewAPIKeyService(repository.NewAPIKeyRepository(client, testDB), nil, nil, nil, nil, nil, cfg)
	f.auth = middleware.NewAPIKeyAuthMiddleware(keys, subs, cfg)
	f.start(t)
	i := f.submit(t, "native-subscription-auth")
	if i.BillingType != 1 || i.SubscriptionID != id {
		t.Fatal("real subscription middleware did not reach atomic admission")
	}
	runSQL(t, `UPDATE user_subscriptions SET expires_at=NOW()-INTERVAL '1 second' WHERE id=$1`, id)
	if replay := f.submit(t, "native-subscription-auth"); replay.ID != i.ID {
		t.Fatal("expired subscription rebound an accepted request")
	}
	status, raw, _ := f.call(t, http.MethodPost, "/v1/jobs", publicImageBody, map[string]string{"Idempotency-Key": "new-after-subscription-expiry"})
	if status != 403 || !strings.Contains(string(raw), `"subscription_required"`) {
		t.Fatal("expired subscription admitted new work", status, string(raw))
	}
	if countRows(t, `SELECT count(*) FROM render_control_intents WHERE api_key_id=$1`, f.subject.keyID) != 1 {
		t.Fatal("replay or denial created another hold")
	}
	balances(t, f.subject, "0.00000000", "0.00000000", "0.00000000")
}

func TestSubscriptionSettlementUsesCurrentWindowAndOneTimeQuota(t *testing.T) {
	t.Run("settlement_after_midnight", func(t *testing.T) {
		s, subject := store(t), fixture(t, "0")
		id := subscriptionFixture(t, subject)
		runSQL(t, `UPDATE groups SET daily_limit_usd=1 WHERE id=$1`, billingGroup(t, subject))
		i := reserve(t, s, subject, "settle-current-window", 1, "0.6")
		if _, err := s.MarkDispatch(ctx(), i.ID); err != nil {
			t.Fatal(err)
		}
		runSQL(t, `UPDATE user_subscriptions SET starts_at=NOW()-INTERVAL '60 days',daily_window_start=NOW()-INTERVAL '2 days',daily_usage_usd=0.8 WHERE id=$1`, id)
		if _, err := s.Finalize(ctx(), testTarget(t), i.Instance, event(t, i, "succeeded", 1)); err != nil {
			t.Fatal(err)
		}
		subscriptionUsage(t, id, "0.6000000000")
		if _, err := s.Reserve(ctx(), subject, testTarget(t), "after-current-window-charge", imageRequest(t, "synthetic", 1), Quote{"p1", money(t, "0.5"), 1}); !errors.Is(err, ErrSubscriptionQuota) {
			t.Fatal("current window omitted late settlement", err)
		}
	})
	t.Run("one_time_daily_is_not_refilled", func(t *testing.T) {
		s, subject := store(t), fixture(t, "0")
		id := subscriptionFixture(t, subject)
		runSQL(t, `UPDATE groups SET daily_limit_usd=1 WHERE id=$1`, billingGroup(t, subject))
		runSQL(t, `UPDATE user_subscriptions SET starts_at=NOW()-INTERVAL '6 hours',expires_at=NOW()+INTERVAL '6 hours',daily_window_start=NOW()-INTERVAL '2 days',daily_usage_usd=1 WHERE id=$1`, id)
		if _, err := s.Reserve(ctx(), subject, testTarget(t), "spent-one-time-quota", imageRequest(t, "synthetic", 1), Quote{"p1", money(t, "0.1"), 1}); !errors.Is(err, ErrSubscriptionQuota) {
			t.Fatal("calendar reset reissued one-time allowance", err)
		}
	})
}

func TestSubscriptionVideoPriceUsesCeilingThenFrozenMultiplier(t *testing.T) {
	f := publicSetup(t)
	id := subscriptionFixture(t, f.subject)
	runSQL(t, `UPDATE groups SET video_rate_independent=TRUE,video_rate_multiplier=0.25,rate_multiplier=9 WHERE id=$1`, f.groupID)
	request, err := NewRequest("videos.create", controlclient.JobRequest{Model: "studio-video-v1", TaskType: "text_to_video", Input: map[string]any{"prompt": "synthetic"}, Parameters: map[string]any{"seconds": 5}})
	if err != nil {
		t.Fatal(err)
	}
	request.usageAccountID = f.config.Models[0].UsageAccountID
	i, err := f.s.Reserve(ctx(), f.subject, testTarget(t), "video-subscription", request, Quote{"video-v1", money(t, "1"), 5})
	if err != nil || i.HoldAmount.String() != "1.25000000" {
		t.Fatal("video hold ignored independent multiplier", err)
	}
	if _, err := f.s.MarkDispatch(ctx(), i.ID); err != nil {
		t.Fatal(err)
	}
	e := event(t, i, "succeeded", 0)
	e.VideoMilliseconds = 2001
	i, err = f.s.Finalize(ctx(), testTarget(t), i.Instance, e)
	if err != nil || i.ActualAmount.String() != "0.75000000" {
		t.Fatal("video settlement rounded a different unit", err)
	}
	runSQL(t, `UPDATE render_control_effects SET acknowledged_at=NOW() WHERE intent_id=$1 AND kind='reserved'`, i.ID)
	if worked, err := effectConsumer(t, f.s, &effectCacheFixture{}, i.Instance).Step(ctx()); !worked || err != nil {
		t.Fatal(err)
	}
	if countRows(t, `SELECT count(*) FROM usage_logs WHERE request_id=$1 AND total_cost=3 AND actual_cost=0.75 AND video_duration_seconds=3 AND video_count=1 AND rate_multiplier=0.25 AND subscription_id=$2 AND billing_type=1`, i.LedgerRef, id) != 1 {
		t.Fatal("native video usage lost rated metering")
	}
	subscriptionUsage(t, id, "0.7500000000")
	balances(t, f.subject, "10.00000000", "0.00000000", "0.75000000")
}

func TestSubscriptionCacheFailureRetainsLocalCommit(t *testing.T) {
	f := publicSetup(t)
	id := subscriptionFixture(t, f.subject)
	i := effectSettled(t, f, "succeeded")
	cache := &effectCacheFixture{}
	cache.subscriptionFail.Store(true)
	consumer := effectConsumer(t, f.s, cache, i.Instance)
	if worked, err := consumer.Step(ctx()); !worked || !errors.Is(err, errEffectCache) {
		t.Fatal("subscription-only failure was acknowledged", err)
	}
	if effectCount(t, i, "local_completed_at IS NOT NULL AND acknowledged_at IS NULL AND attempts=1 AND last_error_code='effect_cache'") != 1 {
		t.Fatal("subscription cache failure lost durable retry")
	}
	cache.subscriptionFail.Store(false)
	for range 2 {
		dueEffects(t, i.ID)
		if worked, err := consumer.Step(ctx()); !worked || err != nil {
			t.Fatal("subscription retry", err)
		}
	}
	if cache.subscription.Load() != 3 || effectCount(t, i, "acknowledged_at IS NOT NULL AND cache_pass=2") != 1 || countRows(t, `SELECT count(*) FROM usage_logs WHERE request_id=$1`, i.LedgerRef) != 1 {
		t.Fatal("subscription retry skipped a cache pass or duplicated usage")
	}
	subscriptionUsage(t, id, "1.0000000000")
	balances(t, f.subject, "10.00000000", "0.00000000", "1.00000000")
}
