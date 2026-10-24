package rendercontrol

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/Wei-Shaw/sub2api/internal/rendercontrol/controlclient"
	"github.com/Wei-Shaw/sub2api/internal/repository"
	"github.com/Wei-Shaw/sub2api/internal/service"
	"github.com/alicebob/miniredis/v2"
	"github.com/redis/go-redis/v9"
)

type effectCacheFixture struct {
	balance          atomic.Int64
	key              atomic.Int64
	subscription     atomic.Int64
	subscriptionFail atomic.Bool
	fail             atomic.Bool
	block            chan struct{}
}

func (c *effectCacheFixture) InvalidateUserBalance(ctx context.Context, _ int64) error {
	c.balance.Add(1)
	if c.block != nil {
		select {
		case c.block <- struct{}{}:
		default:
		}
		<-ctx.Done()
		return ctx.Err()
	}
	if c.fail.Load() {
		return errors.New("synthetic secret transport body must not persist")
	}
	return nil
}

func (c *effectCacheFixture) InvalidateAPIKeyRateLimit(ctx context.Context, _ int64) error {
	c.key.Add(1)
	if ctx.Err() != nil {
		return ctx.Err()
	}
	if c.fail.Load() {
		return errors.New("synthetic private cache failure")
	}
	return nil
}

func (c *effectCacheFixture) InvalidateSubscriptionCache(ctx context.Context, _, _ int64) error {
	c.subscription.Add(1)
	if ctx.Err() != nil {
		return ctx.Err()
	}
	if c.fail.Load() {
		return errors.New("synthetic subscription cache failure")
	}
	if c.subscriptionFail.Load() {
		return errors.New("synthetic subscription-only failure")
	}
	return nil
}

func effectAccount(t *testing.T) int64 {
	t.Helper()
	var id int64
	if err := testDB.QueryRow(`INSERT INTO accounts(name,platform,type,schedulable) VALUES ('synthetic-render-account','openai','apikey',FALSE) RETURNING id`).Scan(&id); err != nil {
		t.Fatal("account fixture", err)
	}
	return id
}

func effectConsumer(t *testing.T, s *Store, cache BillingInvalidator, instance string) *EffectConsumer {
	t.Helper()
	owner, _ := newID("consumer")
	c, err := NewEffectConsumer(s, cache, instance, owner)
	if err != nil {
		t.Fatal(err)
	}
	return c
}

func effectSettled(t *testing.T, f *publicFixture, outcome string) Intent {
	t.Helper()
	i := f.submit(t, "synthetic-effect-task")
	if _, err := f.s.MarkDispatch(ctx(), i.ID); err != nil {
		t.Fatal(err)
	}
	n := int64(1)
	if outcome != "succeeded" {
		n = 0
	}
	settled, err := f.s.Finalize(ctx(), testTarget(t), f.subject.instance, event(t, i, outcome, n))
	if err != nil {
		t.Fatal(err)
	}
	// Only the terminal is relevant in this isolated test. Production processes
	// consume both records and never directly edit their acknowledgement.
	runSQL(t, `UPDATE render_control_effects SET acknowledged_at=NOW() WHERE intent_id=$1 AND kind='reserved'`, i.ID)
	return settled
}

func dueEffects(t *testing.T, id string) {
	t.Helper()
	runSQL(t, `UPDATE render_control_effects SET available_at=clock_timestamp()-INTERVAL '1 second' WHERE intent_id=$1 AND acknowledged_at IS NULL`, id)
}

func effectCount(t *testing.T, i Intent, predicate string) int {
	t.Helper()
	return countRows(t, `SELECT count(*) FROM render_control_effects WHERE intent_id=$1 AND kind='terminal' AND `+predicate, i.ID)
}

func TestEffectsWriteNativeUsageAndBothCachePasses(t *testing.T) {
	f := publicSetup(t)
	i := effectSettled(t, f, "succeeded")
	server := miniredis.RunT(t)
	redisClient := redis.NewClient(&redis.Options{Addr: server.Addr(), MaxRetries: -1, DialTimeout: time.Second, ReadTimeout: time.Second, WriteTimeout: time.Second, ContextTimeoutEnabled: true})
	t.Cleanup(func() { _ = redisClient.Close() })
	cache := repository.NewBillingCache(redisClient)
	if err := cache.SetUserBalance(ctx(), i.UserID, 10); err != nil {
		t.Fatal(err)
	}
	if err := cache.SetAPIKeyRateLimit(ctx(), i.KeyID, &service.APIKeyRateLimitCacheData{}); err != nil {
		t.Fatal(err)
	}
	c := effectConsumer(t, f.s, cache, i.Instance)
	if worked, err := c.Step(ctx()); !worked || err != nil {
		t.Fatal("production cache first pass", err)
	}
	if _, err := cache.GetUserBalance(ctx(), i.UserID); !errors.Is(err, redis.Nil) {
		t.Fatal("actual upstream Redis cache was not deleted")
	}
	if _, err := cache.GetAPIKeyRateLimit(ctx(), i.KeyID); !errors.Is(err, redis.Nil) {
		t.Fatal("actual upstream rate limit cache was not deleted", err)
	}
	if effectCount(t, i, "local_completed_at IS NOT NULL AND cache_pass=1 AND acknowledged_at IS NULL AND available_at>NOW()+INTERVAL '29 seconds'") != 1 {
		t.Fatal("first pass acknowledged early or lost safety delay")
	}
	if worked, err := c.Step(ctx()); worked || err != nil {
		t.Fatal("consumer ignored the persisted safety delay", err)
	}
	if countRows(t, `SELECT count(*) FROM usage_logs WHERE request_id=$1 AND api_key_id=$2 AND user_id=$3 AND account_id=$4 AND group_id=$5 AND actual_cost=1 AND total_cost=1 AND image_count=1 AND billing_mode='image' AND image_size='1K' AND image_input_size='1024x1024' AND image_size_source='input'`, i.LedgerRef, i.KeyID, i.UserID, i.UsageAccountID, i.BillingGroupID) != 1 {
		t.Fatal("native usage projection is wrong")
	}
	queued := countRows(t, `SELECT count(*) FROM auth_cache_invalidation_outbox WHERE cache_key=encode(sha256(convert_to($1,'UTF8')),'hex')`, f.credential)
	if queued < 1 {
		t.Fatal("stock auth invalidation outbox was not populated")
	}
	// Simulate a bounded old read repopulating Redis after the first deletion.
	if err := cache.SetUserBalance(ctx(), i.UserID, 10); err != nil {
		t.Fatal(err)
	}
	if err := cache.SetAPIKeyRateLimit(ctx(), i.KeyID, &service.APIKeyRateLimitCacheData{}); err != nil {
		t.Fatal(err)
	}
	dueEffects(t, i.ID)
	restarted := effectConsumer(t, store(t), cache, i.Instance)
	if worked, err := restarted.Step(ctx()); !worked || err != nil {
		t.Fatal("restart safety pass", err)
	}
	if _, err := cache.GetUserBalance(ctx(), i.UserID); !errors.Is(err, redis.Nil) {
		t.Fatal("second pass retained a stale balance")
	}
	if _, err := cache.GetAPIKeyRateLimit(ctx(), i.KeyID); !errors.Is(err, redis.Nil) {
		t.Fatal("second pass retained a stale rate limit cache", err)
	}
	if effectCount(t, i, "cache_pass=2 AND acknowledged_at IS NOT NULL") != 1 {
		t.Fatal("second cache pass was not durable")
	}
	if countRows(t, `SELECT count(*) FROM usage_logs WHERE request_id=$1`, i.LedgerRef) != 1 || countRows(t, `SELECT count(*) FROM auth_cache_invalidation_outbox WHERE cache_key=encode(sha256(convert_to($1,'UTF8')),'hex')`, f.credential) != queued {
		t.Fatal("restart repeated local effects")
	}
	balances(t, f.subject, "9.00000000", "0.00000000", "1.00000000")
}

func TestEffectsFreezeImageDefaultForOmittedModelSize(t *testing.T) {
	f := publicSetup(t)
	f.config.Models[0].Defaults = nil
	f.start(t)
	i := effectSettled(t, f, "succeeded")
	_, request, err := f.s.Recover(ctx(), i.ID)
	if err != nil || request.Parameters["size"] != "1024x1024" {
		t.Fatal("default size was not frozen before dispatch", err)
	}
	if _, err := effectConsumer(t, f.s, &effectCacheFixture{}, i.Instance).Step(ctx()); err != nil {
		t.Fatal("default-sized request could not record usage", err)
	}
	if countRows(t, `SELECT count(*) FROM usage_logs WHERE request_id=$1 AND image_size='1K' AND image_input_size='1024x1024'`, i.LedgerRef) != 1 {
		t.Fatal("default image usage metadata missing")
	}
}

func TestEffectsCacheFailureRetriesWithoutRebillingOrDuplicateUsage(t *testing.T) {
	f := publicSetup(t)
	i := effectSettled(t, f, "succeeded")
	cache := &effectCacheFixture{}
	cache.fail.Store(true)
	c := effectConsumer(t, f.s, cache, i.Instance)
	for attempt := 1; attempt <= 8; attempt++ {
		dueEffects(t, i.ID)
		if worked, err := c.Step(ctx()); !worked || !errors.Is(err, errEffectCache) {
			t.Fatal("cache failure was lost", err)
		}
		var attempts int
		var delay float64
		if err := testDB.QueryRow(`SELECT attempts,EXTRACT(EPOCH FROM available_at-clock_timestamp()) FROM render_control_effects WHERE intent_id=$1 AND kind='terminal' AND local_completed_at IS NOT NULL AND acknowledged_at IS NULL AND last_error_code='effect_cache' AND lease_owner IS NULL`, i.ID).Scan(&attempts, &delay); err != nil || attempts != attempt || delay < 0 || delay > 60 || attempt >= 7 && delay < 59 {
			t.Fatal("durable bounded retry invalid", err)
		}
	}
	if countRows(t, `SELECT count(*) FROM usage_logs WHERE request_id=$1`, i.LedgerRef) != 1 {
		t.Fatal("cache retries duplicated usage")
	}
	cache.fail.Store(false)
	for range 2 {
		dueEffects(t, i.ID)
		if _, err := c.Step(ctx()); err != nil {
			t.Fatal(err)
		}
	}
	balances(t, f.subject, "9.00000000", "0.00000000", "1.00000000")
}

func TestEffectsConcurrentConsumersAndScopeIsolation(t *testing.T) {
	f := publicSetup(t)
	i := effectSettled(t, f, "succeeded")
	cache := &effectCacheFixture{}
	wrong := effectConsumer(t, f.s, cache, "different-instance")
	if worked, err := wrong.Step(ctx()); worked || err != nil {
		t.Fatal("effect crossed instance", err)
	}
	var group sync.WaitGroup
	for range 12 {
		group.Go(func() {
			c := effectConsumer(t, f.s, cache, i.Instance)
			if _, err := c.Step(ctx()); err != nil {
				t.Error(err)
			}
		})
	}
	group.Wait()
	if cache.balance.Load() != 1 || cache.key.Load() != 1 || countRows(t, `SELECT count(*) FROM usage_logs WHERE request_id=$1`, i.LedgerRef) != 1 {
		t.Fatal("concurrent consumption repeated side effects")
	}
	balances(t, f.subject, "9.00000000", "0.00000000", "1.00000000")
}

func TestEffectsNativeUsageOutcomesAndVideoMetering(t *testing.T) {
	for _, outcome := range []string{"failed", "cancelled", "expired", "succeeded"} {
		t.Run(outcome, func(t *testing.T) {
			f := publicSetup(t)
			i := effectSettled(t, f, outcome)
			// Existing tasks settle after revocation or a soft deletion; logging
			// must preserve the frozen original identities as well.
			runSQL(t, `UPDATE api_keys SET status='disabled',deleted_at=NOW(),group_id=NULL WHERE id=$1`, i.KeyID)
			c := effectConsumer(t, f.s, &effectCacheFixture{}, i.Instance)
			if _, err := c.Step(ctx()); err != nil {
				t.Fatal(err)
			}
			if countRows(t, `SELECT count(*) FROM usage_logs WHERE request_id=$1 AND api_key_id=$2 AND group_id=$3 AND actual_cost=$4::numeric`, i.LedgerRef, i.KeyID, i.BillingGroupID, i.ActualAmount.String()) != 1 {
				t.Fatal("terminal usage missing after Key revocation")
			}
		})
	}
	t.Run("video", func(t *testing.T) {
		f := publicSetup(t)
		request, err := NewRequest("videos.create", controlclient.JobRequest{Model: "studio-video-v1", TaskType: "text_to_video", Input: map[string]any{"prompt": "synthetic"}, Parameters: map[string]any{"seconds": json.Number("2.001")}})
		if err != nil {
			t.Fatal(err)
		}
		request.usageAccountID = f.config.Models[0].UsageAccountID
		i, err := f.s.Reserve(ctx(), f.subject, testTarget(t), "video-effect", request, Quote{"video-v1", money(t, "0.00000001"), 3})
		if err != nil {
			t.Fatal(err)
		}
		if _, err = f.s.MarkDispatch(ctx(), i.ID); err != nil {
			t.Fatal(err)
		}
		e := event(t, i, "succeeded", 0)
		e.VideoMilliseconds = 2001
		i, err = f.s.Finalize(ctx(), testTarget(t), i.Instance, e)
		if err != nil {
			t.Fatal(err)
		}
		runSQL(t, `UPDATE render_control_effects SET acknowledged_at=NOW() WHERE intent_id=$1 AND kind='reserved'`, i.ID)
		if _, err = effectConsumer(t, f.s, &effectCacheFixture{}, i.Instance).Step(ctx()); err != nil {
			t.Fatal(err)
		}
		if countRows(t, `SELECT count(*) FROM usage_logs WHERE request_id=$1 AND video_count=1 AND video_duration_seconds=3 AND image_count=0 AND billing_mode='video' AND actual_cost=0.00000003`, i.LedgerRef) != 1 {
			t.Fatal("video seconds or exact NUMERIC amount wrong")
		}
	})
}

func TestEffectsMappingIsFrozenAndMissingHistoricalMappingRetained(t *testing.T) {
	f := publicSetup(t)
	i := f.submit(t, "frozen-effect-account")
	originalAccount := i.UsageAccountID
	f.config.Models[0].UsageAccountID = effectAccount(t)
	f.start(t)
	replay := f.submit(t, "frozen-effect-account")
	if replay.UsageAccountID != originalAccount {
		t.Fatal("configuration rebound original accounting identity")
	}
	for _, query := range []string{`UPDATE render_control_intents SET usage_account_id=NULL WHERE id=$1`, `UPDATE render_control_intents SET billing_group_id=NULL WHERE id=$1`, `DELETE FROM accounts WHERE id=(SELECT usage_account_id FROM render_control_intents WHERE id=$1)`} {
		if _, err := testDB.Exec(query, i.ID); err == nil {
			t.Fatal("database allowed accounting identity to be removed")
		}
	}
	// A lower-level legacy reservation has no historical accounting identity.
	s, subject := recoveryFixture(t)
	legacy := reserve(t, s, subject, "legacy-no-account", 1, "1")
	if _, err := s.MarkDispatch(ctx(), legacy.ID); err != nil {
		t.Fatal(err)
	}
	legacy, err := s.Finalize(ctx(), testTarget(t), subject.instance, event(t, legacy, "succeeded", 1))
	if err != nil {
		t.Fatal(err)
	}
	runSQL(t, `UPDATE render_control_effects SET acknowledged_at=NOW() WHERE intent_id=$1 AND kind='reserved'`, legacy.ID)
	if _, err := effectConsumer(t, s, &effectCacheFixture{}, subject.instance).Step(ctx()); !errors.Is(err, ErrUnsupported) {
		t.Fatal("missing mapping was silently delivered", err)
	}
	if effectCount(t, legacy, "last_error_code='effect_mapping' AND acknowledged_at IS NULL AND local_completed_at IS NULL") != 1 || countRows(t, `SELECT count(*) FROM usage_logs WHERE request_id=$1`, legacy.LedgerRef) != 0 {
		t.Fatal("legacy mapping lost evidence")
	}
	balances(t, subject, "9.00000000", "0.00000000", "1.00000000")
}

func TestEffectsUsageConflictAndLocalFailureRollback(t *testing.T) {
	for _, kind := range []string{"conflict", "transaction_failure"} {
		t.Run(kind, func(t *testing.T) {
			f := publicSetup(t)
			i := effectSettled(t, f, "succeeded")
			cache := &effectCacheFixture{}
			if kind == "conflict" {
				runSQL(t, `INSERT INTO usage_logs(user_id,api_key_id,account_id,request_id,model,actual_cost) VALUES ($1,$2,$3,$4,'wrong-model',9)`, i.UserID, i.KeyID, i.UsageAccountID, i.LedgerRef)
			} else {
				name := fmt.Sprintf("rc_effect_fault_%d", i.KeyID)
				runSQL(t, `CREATE FUNCTION `+name+`() RETURNS TRIGGER LANGUAGE plpgsql AS $$ BEGIN IF NEW.local_completed_at IS NOT NULL AND NEW.intent_id='`+i.ID+`' THEN RAISE EXCEPTION 'synthetic fault'; END IF; RETURN NEW; END $$; CREATE TRIGGER `+name+` BEFORE UPDATE ON render_control_effects FOR EACH ROW EXECUTE FUNCTION `+name+`()`)
				t.Cleanup(func() { runSQL(t, `DROP TRIGGER `+name+` ON render_control_effects; DROP FUNCTION `+name+`()`) })
			}
			_, err := effectConsumer(t, f.s, cache, i.Instance).Step(ctx())
			if kind == "conflict" && !errors.Is(err, ErrConflict) || kind == "transaction_failure" && !errors.Is(err, ErrStorage) {
				t.Fatal("local failure not retained", err)
			}
			if cache.balance.Load() != 0 || effectCount(t, i, "local_completed_at IS NULL AND acknowledged_at IS NULL") != 1 {
				t.Fatal("failed local transaction advanced cache or marker")
			}
			if kind == "transaction_failure" && countRows(t, `SELECT count(*) FROM usage_logs WHERE request_id=$1`, i.LedgerRef) != 0 {
				t.Fatal("usage survived failed marker transaction")
			}
			balances(t, f.subject, "9.00000000", "0.00000000", "1.00000000")
		})
	}
}

func TestEffectsStaleFenceCannotPrepareRetryOrFinish(t *testing.T) {
	f := publicSetup(t)
	i := effectSettled(t, f, "succeeded")
	first, err := f.s.claimEffects(ctx(), i.Instance, "old-consumer", 1, time.Second)
	if err != nil || len(first) != 1 {
		t.Fatal(err)
	}
	runSQL(t, `UPDATE render_control_effects SET lease_until=clock_timestamp()-INTERVAL '1 second' WHERE id=$1`, first[0].ID)
	second, err := f.s.claimEffects(ctx(), i.Instance, "new-consumer", 1, time.Minute)
	if err != nil || len(second) != 1 || second[0].Fence != first[0].Fence+1 {
		t.Fatal("reclaim", err)
	}
	for _, operation := range []func() error{func() error { return f.s.prepareEffect(ctx(), first[0]) }, func() error { return f.s.retryEffect(ctx(), first[0], "effect_cache") }, func() error { return f.s.finishEffectPass(ctx(), first[0]) }} {
		if err := operation(); !errors.Is(err, ErrFence) {
			t.Fatal("old fence wrote effect", err)
		}
	}
	if err := f.s.prepareEffect(ctx(), second[0]); err != nil {
		t.Fatal(err)
	}
	if err := f.s.finishEffectPass(ctx(), second[0]); err != nil {
		t.Fatal(err)
	}
	if countRows(t, `SELECT count(*) FROM usage_logs WHERE request_id=$1`, i.LedgerRef) != 1 {
		t.Fatal("fenced replay usage missing")
	}
}

func TestEffectsNativeProcessCrashAfterLocalCommit(t *testing.T) {
	f := publicSetup(t)
	i := effectSettled(t, f, "succeeded")
	executable, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	processCtx, cancel := context.WithTimeout(ctx(), 20*time.Second)
	defer cancel()
	command := exec.CommandContext(processCtx, executable, "-test.run=^TestProcessBoundaryHelper$")
	command.Env = append(os.Environ(), "RC_TEST_CHILD_STAGE=effect_prepared", "RC_TEST_INTENT="+i.ID, "RC_TEST_USER="+strconv.FormatInt(i.UserID, 10), "RC_TEST_KEY="+strconv.FormatInt(i.KeyID, 10), "RC_TEST_INSTANCE="+i.Instance)
	output, err := command.CombinedOutput()
	var exit *exec.ExitError
	if !errors.As(err, &exit) || exit.ExitCode() != 73 {
		t.Fatalf("native effect process did not stop after commit: %s", output)
	}
	if effectCount(t, i, "local_completed_at IS NOT NULL AND acknowledged_at IS NULL AND cache_pass=0") != 1 {
		t.Fatal("local effect transaction did not survive abrupt exit")
	}
	runSQL(t, `UPDATE render_control_effects SET lease_until=clock_timestamp()-INTERVAL '1 second' WHERE intent_id=$1 AND kind='terminal'`, i.ID)
	if _, err := effectConsumer(t, store(t), &effectCacheFixture{}, i.Instance).Step(ctx()); err != nil {
		t.Fatal("restart", err)
	}
	if countRows(t, `SELECT count(*) FROM usage_logs WHERE request_id=$1`, i.LedgerRef) != 1 {
		t.Fatal("native restart duplicated usage")
	}
	balances(t, f.subject, "9.00000000", "0.00000000", "1.00000000")
}

func TestEffectsReleasedReservationOnlyInvalidates(t *testing.T) {
	f := publicSetup(t)
	i := f.submit(t, "effect-released")
	if _, err := f.s.ReleaseUnsent(ctx(), i.ID); err != nil {
		t.Fatal(err)
	}
	cache := &effectCacheFixture{}
	c := effectConsumer(t, f.s, cache, i.Instance)
	for range 2 {
		if _, err := c.Step(ctx()); err != nil {
			t.Fatal(err)
		}
	}
	if cache.balance.Load() != 2 || countRows(t, `SELECT count(*) FROM usage_logs WHERE api_key_id=$1`, i.KeyID) != 0 {
		t.Fatal("released task was recorded as billable usage")
	}
	balances(t, f.subject, "10.00000000", "0.00000000", "0.00000000")
}

func TestEffectsRuntimeCancellationPreservesCurrentWork(t *testing.T) {
	f := publicSetup(t)
	i := effectSettled(t, f, "succeeded")
	cache := &effectCacheFixture{block: make(chan struct{}, 1)}
	runtime := runtimeForTest(t, f.s, f.subject, map[string]*controlclient.Client{i.TargetGatewayID: f.g.client})
	runtime.config.Effects = effectConsumer(t, f.s, cache, i.Instance)
	if err := runtime.Start(ctx()); err != nil {
		t.Fatal(err)
	}
	select {
	case <-cache.block:
	case <-time.After(3 * time.Second):
		t.Fatal("runtime did not start effect consumer")
	}
	stop, cancel := context.WithTimeout(ctx(), 3*time.Second)
	defer cancel()
	if err := runtime.Stop(stop); err != nil {
		t.Fatal(err)
	}
	if effectCount(t, i, "acknowledged_at IS NULL AND lease_until IS NOT NULL AND local_completed_at IS NOT NULL") != 1 {
		t.Fatal("shutdown lost durable in-flight effect")
	}
	snapshot := runtime.Snapshot()
	if snapshot.State != "stopped" || snapshot.EffectAttempts < 1 || snapshot.EffectWorked != 1 {
		t.Fatal("effect lifecycle counters wrong")
	}
	balances(t, f.subject, "9.00000000", "0.00000000", "1.00000000")
}

func TestEffectsAccountValidationAndConfiguration(t *testing.T) {
	for _, kind := range []string{"schedulable", "wrong_platform", "deleted", "missing"} {
		t.Run(kind, func(t *testing.T) {
			f := publicSetup(t)
			account := f.config.Models[0].UsageAccountID
			switch kind {
			case "schedulable":
				runSQL(t, `UPDATE accounts SET schedulable=TRUE WHERE id=$1`, account)
			case "wrong_platform":
				runSQL(t, `UPDATE accounts SET platform='claude' WHERE id=$1`, account)
			case "deleted":
				runSQL(t, `UPDATE accounts SET deleted_at=NOW() WHERE id=$1`, account)
			case "missing":
				f.config.Models[0].UsageAccountID = account + 1000000
				f.start(t)
			}
			status, raw, _ := f.call(t, "POST", "/v1/jobs", publicImageBody, map[string]string{"Idempotency-Key": "denied-account"})
			if status < 400 || strings.Contains(string(raw), "synthetic-render-account") || countRows(t, `SELECT count(*) FROM render_control_intents WHERE api_key_id=$1`, f.subject.keyID) != 0 {
				t.Fatal("invalid accounting identity accepted", status)
			}
			balances(t, f.subject, "10.00000000", "0.00000000", "0.00000000")
		})
	}
}
