package rendercontrol

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"net/netip"
	"os"
	"os/exec"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/Wei-Shaw/sub2api/internal/rendercontrol/controlclient"
	"github.com/Wei-Shaw/sub2api/internal/repository"
	_ "github.com/lib/pq"
)

var testDB *sql.DB

func TestMain(m *testing.M) {
	dsn := os.Getenv("RC_SUB2API_TEST_DSN")
	if dsn == "" {
		fmt.Fprintln(os.Stderr, "isolated native test database is required")
		os.Exit(2)
	}
	var err error
	testDB, err = sql.Open("postgres", dsn)
	if err != nil {
		fmt.Fprintln(os.Stderr, "test database initialization failed")
		os.Exit(2)
	}
	testDB.SetMaxOpenConns(12)
	testDB.SetMaxIdleConns(4)
	ctx, cancel := context.WithTimeout(context.Background(), 120*time.Second)
	defer cancel()
	if err = testDB.PingContext(ctx); err != nil {
		fmt.Fprintln(os.Stderr, "native test database is unavailable")
		os.Exit(2)
	}
	var database, server string
	var port int
	if err = testDB.QueryRowContext(ctx, `SELECT current_database(),host(inet_server_addr()),inet_server_port()`).Scan(&database, &server, &port); err != nil || !regexp.MustCompile(`^render_sub2api_test_[a-f0-9]{12}$`).MatchString(database) || server != "127.0.0.1" || port != 55438 {
		fmt.Fprintln(os.Stderr, "refusing tests outside the reserved project-native disposable database namespace")
		os.Exit(2)
	}
	if os.Getenv("RC_TEST_CHILD_STAGE") == "" {
		var existing int
		if err = testDB.QueryRowContext(ctx, `SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'`).Scan(&existing); err != nil || existing != 0 {
			fmt.Fprintln(os.Stderr, "refusing migrations into a nonempty test database")
			os.Exit(2)
		}
		if err = repository.ApplyMigrations(ctx, testDB); err != nil {
			fmt.Fprintln(os.Stderr, "upstream migrations:", err)
			os.Exit(2)
		}
		// A second run must verify checksums and make no schema changes.
		if err = repository.ApplyMigrations(ctx, testDB); err != nil {
			fmt.Fprintln(os.Stderr, "upstream migration replay failed")
			os.Exit(2)
		}
		var migrations, tables int
		if testDB.QueryRow(`SELECT count(*) FROM schema_migrations`).Scan(&migrations) != nil || testDB.QueryRow(`SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'`).Scan(&tables) != nil {
			os.Exit(2)
		}
		fmt.Printf("native upstream schema: migrations=%d tables=%d replay_verified=true\n", migrations, tables)
	}
	code := m.Run()
	_ = testDB.Close()
	os.Exit(code)
}

func ctx() context.Context { return context.Background() }

func money(t *testing.T, value string) Money {
	t.Helper()
	result, err := ParseMoney(value)
	if err != nil {
		t.Fatal("test money invalid")
	}
	return result
}

func store(t *testing.T) *Store {
	t.Helper()
	s, err := NewStore(testDB, "test-key-v1", map[string][]byte{"test-key-v1": bytes.Repeat([]byte{7}, 32)})
	if err != nil {
		t.Fatal(err)
	}
	return s
}

func runSQL(t *testing.T, query string, args ...any) {
	t.Helper()
	if _, err := testDB.Exec(query, args...); err != nil {
		t.Fatal("test fixture SQL:", err)
	}
}

func fixture(t *testing.T, balance string) Subject {
	t.Helper()
	tag, err := newID("test")
	if err != nil {
		t.Fatal(err)
	}
	var userID, groupID, keyID int64
	if err = testDB.QueryRow(`INSERT INTO users(email,password_hash,balance) VALUES ($1,'synthetic-hash',$2::numeric) RETURNING id`, tag+"@invalid.test", balance).Scan(&userID); err != nil {
		t.Fatal("user fixture:", err)
	}
	if err = testDB.QueryRow(`INSERT INTO groups(name) VALUES ($1) RETURNING id`, tag).Scan(&groupID); err != nil {
		t.Fatal("group fixture:", err)
	}
	if err = testDB.QueryRow(`INSERT INTO api_keys(user_id,group_id,key,name) VALUES ($1,$2,$3,'synthetic') RETURNING id`, userID, groupID, tag).Scan(&keyID); err != nil {
		t.Fatal("Key fixture:", err)
	}
	subject, err := NewSubject("native-test", userID, keyID, netip.MustParseAddr("127.0.0.1"))
	if err != nil {
		t.Fatal(err)
	}
	return subject
}

func imageRequest(t *testing.T, prompt string, n int) Request {
	t.Helper()
	request, err := NewRequest("images.generate", controlclient.JobRequest{Model: "studio-image-v1", TaskType: "text_to_image", Input: map[string]any{"prompt": prompt}, Parameters: map[string]any{"n": n}})
	if err != nil {
		t.Fatal(err)
	}
	return request
}

func reserve(t *testing.T, s *Store, subject Subject, token string, n int, unit string) Intent {
	t.Helper()
	i, err := s.Reserve(ctx(), subject, testTarget(t), token, imageRequest(t, "synthetic test prompt", n), Quote{"prices-v1", money(t, unit), int64(n)})
	if err != nil {
		t.Fatal("reserve:", err)
	}
	return i
}

func event(t *testing.T, i Intent, outcome string, count int64) controlclient.Settlement {
	t.Helper()
	eventID, err := newID("evt")
	if err != nil {
		t.Fatal(err)
	}
	jobID := i.RemoteJobID
	if jobID == "" {
		jobID, err = newID("job")
		if err != nil {
			t.Fatal(err)
		}
	}
	return controlclient.Settlement{ID: eventID, JobID: jobID, ExternalTaskRef: i.ID, AuthorizationRef: i.AuthorizationRef, PriceVersion: i.PriceVersion, Outcome: outcome, ImageCount: count}
}

func balances(t *testing.T, subject Subject, balance, frozen, quota string) {
	t.Helper()
	var actualBalance, actualFrozen, actualQuota string
	if err := testDB.QueryRow(`SELECT u.balance::text,u.frozen_balance::text,k.quota_used::text FROM users u JOIN api_keys k ON k.user_id=u.id WHERE u.id=$1 AND k.id=$2`, subject.userID, subject.keyID).Scan(&actualBalance, &actualFrozen, &actualQuota); err != nil {
		t.Fatal(err)
	}
	if actualBalance != balance || actualFrozen != frozen || actualQuota != quota {
		t.Fatalf("balance/frozen/quota = %s/%s/%s; want %s/%s/%s", actualBalance, actualFrozen, actualQuota, balance, frozen, quota)
	}
}

func countRows(t *testing.T, query string, args ...any) int {
	t.Helper()
	var result int
	if err := testDB.QueryRow(query, args...).Scan(&result); err != nil {
		t.Fatal(err)
	}
	return result
}

func TestMoneyBoundaries(t *testing.T) {
	for _, value := range []string{"0", "0.00000001", "1.23456789", "1000000.00000000"} {
		t.Run("accept_"+value, func(t *testing.T) {
			amount, err := ParseMoney(value)
			if err != nil {
				t.Fatal(err)
			}
			round, err := ParseMoney(amount.String())
			if err != nil || round != amount {
				t.Fatal("money did not round trip exactly")
			}
		})
	}
	for _, value := range []string{"-1", "+1", "01", "1e-8", "NaN", ".1", "1.", "1.000000001", "1000000.00000001", "9999999999999999999", " 1"} {
		t.Run("reject_"+value, func(t *testing.T) {
			if _, err := ParseMoney(value); !errors.Is(err, ErrInvalid) {
				t.Fatal("invalid money accepted")
			}
		})
	}
	if _, err := Money(1).multiply(0); !errors.Is(err, ErrInvalid) {
		t.Fatal("zero price units accepted")
	}
	if _, err := maxMoney.multiply(86400); !errors.Is(err, ErrInvalid) {
		t.Fatal("overflow accepted")
	}
}

func TestRequestCanonicalizationAndBounds(t *testing.T) {
	first := controlclient.JobRequest{Model: "m", TaskType: "references_to_image", Input: map[string]any{"prompt": "synthetic", "references": []any{map[string]any{"asset_id": "synthetic"}}}, Parameters: map[string]any{"n": 1, "seed": 3}}
	a, err := NewRequest("images.edit", first)
	if err != nil {
		t.Fatal(err)
	}
	first.Parameters = map[string]any{"seed": 3, "n": 1}
	b, err := NewRequest("images.edit", first)
	if err != nil || a.hash != b.hash {
		t.Fatal("map order changed canonical request")
	}
	for _, task := range []string{"text_to_image", "image_to_image", "references_to_image", "text_to_video", "references_to_video"} {
		t.Run(task, func(t *testing.T) {
			operation := "images.edit"
			parameters := map[string]any{"n": 1}
			if strings.HasSuffix(task, "video") {
				operation, parameters = "videos.create", map[string]any{"seconds": json.Number("5.001")}
			}
			r, err := NewRequest(operation, controlclient.JobRequest{Model: "m", TaskType: task, Input: map[string]any{"prompt": "synthetic"}, Parameters: parameters})
			if err != nil || strings.HasSuffix(task, "video") && r.units != 6 {
				t.Fatal("task/decimal duration not accepted")
			}
		})
	}
	for _, raw := range []string{`{"a":1,"a":2}`, strings.Repeat("[", 35) + "0" + strings.Repeat("]", 35)} {
		first.Input = map[string]any{"raw": json.RawMessage(raw)}
		if _, err := NewRequest("images.edit", first); !errors.Is(err, ErrInvalid) {
			t.Fatal("ambiguous or deep raw JSON accepted")
		}
	}
	first.Input = map[string]any{"prompt": strings.Repeat("x", 2<<20)}
	if _, err := NewRequest("images.edit", first); !errors.Is(err, ErrInvalid) {
		t.Fatal("oversized request accepted")
	}
	if _, err := NewSubject("bad instance", 1, 1, netip.MustParseAddr("127.0.0.1")); !errors.Is(err, ErrInvalid) {
		t.Fatal("invalid identity accepted")
	}
	if strings.Contains(fmt.Sprintf("%v %#v", a, a), "synthetic") {
		t.Fatal("request formatting leaked body")
	}
}

func TestConcurrentIdempotencyAndFrozenQuote(t *testing.T) {
	s, subject := store(t), fixture(t, "10")
	var wg sync.WaitGroup
	ids, failures := make(chan string, 16), make(chan error, 16)
	r := imageRequest(t, "synthetic test prompt", 2)
	q := Quote{"prices-v1", money(t, "0.25"), 2}
	for range 16 {
		wg.Go(func() {
			i, err := s.Reserve(ctx(), subject, testTarget(t), "same-token", r, q)
			ids <- i.ID
			failures <- err
		})
	}
	wg.Wait()
	close(ids)
	close(failures)
	for err := range failures {
		if err != nil {
			t.Fatal(err)
		}
	}
	id := ""
	for current := range ids {
		if id != "" && id != current {
			t.Fatal("duplicate public IDs")
		}
		id = current
	}
	i, err := s.Reserve(ctx(), subject, testTarget(t), "same-token", r, Quote{"prices-v2", money(t, "0.9"), 2})
	if err != nil || i.ID != id || i.PriceVersion != "prices-v1" || i.HoldAmount != money(t, "0.5") {
		t.Fatal("replay changed ID or frozen price", err)
	}
	if _, err = s.Reserve(ctx(), subject, testTarget(t), "same-token", imageRequest(t, "different", 2), q); !errors.Is(err, ErrConflict) {
		t.Fatal("changed idempotent request accepted")
	}
	balances(t, subject, "9.50000000", "0.50000000", "0.00000000")
	if countRows(t, `SELECT count(*) FROM render_control_intents WHERE api_key_id=$1`, subject.keyID) != 1 || countRows(t, `SELECT count(*) FROM render_control_effects WHERE api_key_id=$1`, subject.keyID) != 1 {
		t.Fatal("duplicate intent or reserve effect")
	}
}

func TestOwnershipAndBillingMode(t *testing.T) {
	s := store(t)
	a, b := fixture(t, "3"), fixture(t, "3")
	forged, _ := NewSubject(a.instance, a.userID, b.keyID, a.ip)
	if _, err := s.Reserve(ctx(), forged, testTarget(t), "forged", imageRequest(t, "synthetic", 1), Quote{"p1", money(t, "1"), 1}); !errors.Is(err, ErrDenied) {
		t.Fatal("Key ownership not checked", err)
	}
	i := reserve(t, s, a, "own", 1, "1")
	if _, err := s.Load(ctx(), b, i.ID); !errors.Is(err, ErrNotFound) {
		t.Fatal("cross-Key task disclosure")
	}
	otherInstance, _ := NewSubject("another", a.userID, a.keyID, a.ip)
	if _, err := s.Load(ctx(), otherInstance, i.ID); !errors.Is(err, ErrNotFound) {
		t.Fatal("cross-instance task disclosure")
	}
	runSQL(t, `UPDATE groups SET subscription_type='subscription' WHERE id=(SELECT group_id FROM api_keys WHERE id=$1)`, b.keyID)
	if _, err := s.Reserve(ctx(), b, testTarget(t), "subscription", imageRequest(t, "synthetic", 1), Quote{"p1", money(t, "1"), 1}); !errors.Is(err, ErrSubscription) {
		t.Fatal("subscription silently fell back to balance", err)
	}
	balances(t, b, "3.00000000", "0.00000000", "0.00000000")
}

func TestConcurrentBalanceAndKeyLimits(t *testing.T) {
	for _, kind := range []string{"balance", "quota", "5h", "1d", "7d"} {
		t.Run(kind, func(t *testing.T) {
			s, subject := store(t), fixture(t, "10")
			switch kind {
			case "balance":
				runSQL(t, `UPDATE users SET balance=1 WHERE id=$1`, subject.userID)
			case "quota":
				runSQL(t, `UPDATE api_keys SET quota=1 WHERE id=$1`, subject.keyID)
			case "5h":
				runSQL(t, `UPDATE api_keys SET rate_limit_5h=1 WHERE id=$1`, subject.keyID)
			case "1d":
				runSQL(t, `UPDATE api_keys SET rate_limit_1d=1 WHERE id=$1`, subject.keyID)
			case "7d":
				runSQL(t, `UPDATE api_keys SET rate_limit_7d=1 WHERE id=$1`, subject.keyID)
			}
			r := imageRequest(t, "synthetic", 1)
			q := Quote{"p1", money(t, "0.6"), 1}
			var wg sync.WaitGroup
			results := make(chan error, 10)
			for index := range 10 {
				wg.Go(func() {
					_, err := s.Reserve(ctx(), subject, testTarget(t), fmt.Sprintf("request-%d", index), r, q)
					results <- err
				})
			}
			wg.Wait()
			close(results)
			accepted := 0
			for err := range results {
				if err == nil {
					accepted++
				} else if !errors.Is(err, ErrBalance) && !errors.Is(err, ErrQuota) {
					t.Fatal(err)
				}
			}
			if accepted != 1 {
				t.Fatalf("accepted %d concurrent requests, want one", accepted)
			}
		})
	}
}

func TestReserveTransactionRollsBackOnOutboxFailure(t *testing.T) {
	s, subject := store(t), fixture(t, "10")
	// A real PG trigger aborts after funds and intent have been changed. A unique
	// predicate confines the injected fault to this test's freshly created Key.
	query := fmt.Sprintf(`CREATE FUNCTION rc_test_fail_reserve() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.api_key_id=%d AND NEW.kind='reserved' THEN RAISE EXCEPTION 'injected'; END IF; RETURN NEW; END $$;
	CREATE TRIGGER rc_test_fail_reserve BEFORE INSERT ON render_control_effects FOR EACH ROW EXECUTE FUNCTION rc_test_fail_reserve()`, subject.keyID)
	runSQL(t, query)
	t.Cleanup(func() {
		runSQL(t, `DROP TRIGGER rc_test_fail_reserve ON render_control_effects; DROP FUNCTION rc_test_fail_reserve()`)
	})
	if _, err := s.Reserve(ctx(), subject, testTarget(t), "fault", imageRequest(t, "synthetic", 1), Quote{"p1", money(t, "1"), 1}); !errors.Is(err, ErrStorage) {
		t.Fatal("injected outbox fault not returned", err)
	}
	balances(t, subject, "10.00000000", "0.00000000", "0.00000000")
	if countRows(t, `SELECT count(*) FROM render_control_intents WHERE api_key_id=$1`, subject.keyID) != 0 {
		t.Fatal("orphan intent after transaction failure")
	}
}

func TestDispatchVersusUnsentRelease(t *testing.T) {
	s, subject := store(t), fixture(t, "20")
	for index := range 12 {
		i := reserve(t, s, subject, fmt.Sprintf("race-%d", index), 1, "1")
		start := make(chan struct{})
		var a, b error
		var wg sync.WaitGroup
		wg.Go(func() { <-start; _, a = s.MarkDispatch(ctx(), i.ID) })
		wg.Go(func() { <-start; _, b = s.ReleaseUnsent(ctx(), i.ID) })
		close(start)
		wg.Wait()
		if a == nil && !errors.Is(b, ErrDispatched) || b == nil && !errors.Is(a, ErrConflict) || a != nil && b != nil {
			t.Fatalf("dispatch/release race: %v / %v", a, b)
		}
		loaded, err := s.Load(ctx(), subject, i.ID)
		if err != nil {
			t.Fatal(err)
		}
		if loaded.State == "dispatching" {
			if _, err = s.ReleaseUnsent(ctx(), i.ID); !errors.Is(err, ErrDispatched) {
				t.Fatal("unknown send was refunded")
			}
			if _, err = s.Finalize(ctx(), testTarget(t), subject.instance, event(t, loaded, "expired", 0)); err != nil {
				t.Fatal(err)
			}
		} else {
			second, err := s.ReleaseUnsent(ctx(), i.ID)
			if err != nil || loaded.LedgerRef != second.LedgerRef {
				t.Fatal("release was not idempotent")
			}
		}
	}
	balances(t, subject, "20.00000000", "0.00000000", "0.00000000")
}

func TestTerminalCorrelationAndFrozenBounds(t *testing.T) {
	s, subject := store(t), fixture(t, "10")
	i := reserve(t, s, subject, "correlation", 2, "1")
	e := event(t, i, "succeeded", 1)
	if _, err := s.Finalize(ctx(), testTarget(t), subject.instance, e); !errors.Is(err, ErrConflict) {
		t.Fatal("undispatched terminal accepted")
	}
	var err error
	if i, err = s.MarkDispatch(ctx(), i.ID); err != nil {
		t.Fatal(err)
	}
	admissionID, _ := newID("adm")
	if i, err = s.BindAdmission(ctx(), i.ID, admissionID, e.JobID); err != nil {
		t.Fatal(err)
	}
	for _, mutate := range []func(*controlclient.Settlement){
		func(v *controlclient.Settlement) { v.AuthorizationRef, _ = newID("rca") },
		func(v *controlclient.Settlement) { v.PriceVersion = "other-prices" },
		func(v *controlclient.Settlement) { v.JobID, _ = newID("job") },
		func(v *controlclient.Settlement) { v.ImageCount = 3 },
		func(v *controlclient.Settlement) { v.VideoMilliseconds = 1 },
		func(v *controlclient.Settlement) { v.Outcome = "failed" },
	} {
		wrong := e
		mutate(&wrong)
		if _, err := s.Finalize(ctx(), testTarget(t), subject.instance, wrong); !errors.Is(err, ErrConflict) {
			t.Fatal("wrong terminal correlation/metric accepted", err)
		}
	}
	if _, err := s.Finalize(ctx(), testTarget(t), "wrong-instance", e); !errors.Is(err, ErrConflict) {
		t.Fatal("wrong gateway instance accepted")
	}
	balances(t, subject, "8.00000000", "2.00000000", "0.00000000")
	settled, err := s.Finalize(ctx(), testTarget(t), subject.instance, e)
	if err != nil || settled.ActualAmount != money(t, "1") {
		t.Fatal(err)
	}
	balances(t, subject, "9.00000000", "0.00000000", "1.00000000")
	for range 3 {
		replay, err := s.Finalize(ctx(), testTarget(t), subject.instance, e)
		if err != nil || replay.LedgerRef != settled.LedgerRef {
			t.Fatal("settlement replay changed ledger reference")
		}
	}
	otherEvent := e
	otherEvent.ID, _ = newID("evt")
	if _, err := s.Finalize(ctx(), testTarget(t), subject.instance, otherEvent); !errors.Is(err, ErrConflict) {
		t.Fatal("second terminal accepted")
	}
	e.ImageCount = 2
	if _, err := s.Finalize(ctx(), testTarget(t), subject.instance, e); !errors.Is(err, ErrConflict) {
		t.Fatal("same event with changed fingerprint accepted")
	}
	if _, err := s.ReleaseUnsent(ctx(), i.ID); !errors.Is(err, ErrDispatched) {
		t.Fatal("settled capture was separately released")
	}
	if countRows(t, `SELECT count(*) FROM render_control_effects WHERE intent_id=$1 AND kind='terminal'`, i.ID) != 1 || countRows(t, `SELECT count(*) FROM usage_billing_dedup WHERE api_key_id=$1`, subject.keyID) != 1 {
		t.Fatal("duplicate settlement effects/dedup")
	}
}

func TestConcurrentTerminalsChooseOne(t *testing.T) {
	s, subject := store(t), fixture(t, "10")
	i := reserve(t, s, subject, "terminal-race", 1, "1")
	if _, err := s.MarkDispatch(ctx(), i.ID); err != nil {
		t.Fatal(err)
	}
	success, failure := event(t, i, "succeeded", 1), event(t, i, "failed", 0)
	failure.JobID = success.JobID
	var wg sync.WaitGroup
	results := make(chan error, 16)
	for index := range 16 {
		wg.Go(func() {
			e := success
			if index%2 != 0 {
				e = failure
			}
			_, err := s.Finalize(ctx(), testTarget(t), subject.instance, e)
			results <- err
		})
	}
	wg.Wait()
	close(results)
	accepted := 0
	for err := range results {
		if err == nil {
			accepted++
		} else if !errors.Is(err, ErrConflict) {
			t.Fatal(err)
		}
	}
	if accepted != 8 {
		t.Fatalf("%d terminal replays accepted, want eight of the winning event", accepted)
	}
	loaded, err := s.Load(ctx(), subject, i.ID)
	if err != nil {
		t.Fatal(err)
	}
	if loaded.Outcome == "succeeded" {
		balances(t, subject, "9.00000000", "0.00000000", "1.00000000")
	} else {
		balances(t, subject, "10.00000000", "0.00000000", "0.00000000")
	}
}

func TestSettlementRollsBackAllEffects(t *testing.T) {
	s, subject := store(t), fixture(t, "10")
	i := reserve(t, s, subject, "terminal-fault", 1, "1")
	if _, err := s.MarkDispatch(ctx(), i.ID); err != nil {
		t.Fatal(err)
	}
	e := event(t, i, "succeeded", 1)
	runSQL(t, fmt.Sprintf(`CREATE FUNCTION rc_test_fail_terminal() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.api_key_id=%d AND NEW.kind='terminal' THEN RAISE EXCEPTION 'injected'; END IF; RETURN NEW; END $$;
	CREATE TRIGGER rc_test_fail_terminal BEFORE INSERT ON render_control_effects FOR EACH ROW EXECUTE FUNCTION rc_test_fail_terminal()`, subject.keyID))
	if _, err := s.Finalize(ctx(), testTarget(t), subject.instance, e); !errors.Is(err, ErrStorage) {
		t.Fatal("terminal injection did not fail", err)
	}
	runSQL(t, `DROP TRIGGER rc_test_fail_terminal ON render_control_effects; DROP FUNCTION rc_test_fail_terminal()`)
	balances(t, subject, "9.00000000", "1.00000000", "0.00000000")
	if countRows(t, `SELECT count(*) FROM usage_billing_dedup WHERE api_key_id=$1`, subject.keyID) != 0 {
		t.Fatal("billing dedup escaped rollback")
	}
	if _, err := s.Finalize(ctx(), testTarget(t), subject.instance, e); err != nil {
		t.Fatal("replay after rollback failed", err)
	}
	balances(t, subject, "9.00000000", "0.00000000", "1.00000000")
}

func TestRevokedKeysStillSettle(t *testing.T) {
	for _, kind := range []string{"disabled", "soft_deleted"} {
		t.Run(kind, func(t *testing.T) {
			s, subject := store(t), fixture(t, "10")
			i := reserve(t, s, subject, "revoked", 1, "1")
			if _, err := s.MarkDispatch(ctx(), i.ID); err != nil {
				t.Fatal(err)
			}
			if kind == "disabled" {
				runSQL(t, `UPDATE api_keys SET status='disabled' WHERE id=$1;`, subject.keyID)
			} else {
				runSQL(t, `UPDATE api_keys SET deleted_at=NOW() WHERE id=$1`, subject.keyID)
				runSQL(t, `UPDATE users SET deleted_at=NOW() WHERE id=$1`, subject.userID)
			}
			if _, err := s.Finalize(ctx(), testTarget(t), subject.instance, event(t, i, "succeeded", 1)); err != nil {
				t.Fatal("revocation blocked settlement", err)
			}
			balances(t, subject, "9.00000000", "0.00000000", "1.00000000")
			if _, err := s.Reserve(ctx(), subject, testTarget(t), "new-after-revoke", imageRequest(t, "synthetic", 1), Quote{"p1", money(t, "1"), 1}); !errors.Is(err, ErrDenied) {
				t.Fatal("revoked Key admitted new task", err)
			}
		})
	}
}

func TestCipherAuthenticationAndRotation(t *testing.T) {
	s, subject := store(t), fixture(t, "10")
	i := reserve(t, s, subject, "encrypted", 1, "1")
	var ciphertext []byte
	if err := testDB.QueryRow(`SELECT request_cipher FROM render_control_intents WHERE id=$1`, i.ID).Scan(&ciphertext); err != nil || bytes.Contains(ciphertext, []byte("synthetic")) {
		t.Fatal("plaintext stored in intent")
	}
	wrong, _ := NewStore(testDB, "test-key-v1", map[string][]byte{"test-key-v1": bytes.Repeat([]byte{8}, 32)})
	if _, _, err := wrong.Recover(ctx(), i.ID); !errors.Is(err, ErrCipher) {
		t.Fatal("wrong key decrypted request")
	}
	rotated, _ := NewStore(testDB, "test-key-v2", map[string][]byte{"test-key-v1": bytes.Repeat([]byte{7}, 32), "test-key-v2": bytes.Repeat([]byte{9}, 32)})
	loaded, request, err := rotated.Recover(ctx(), i.ID)
	if err != nil || loaded.ID != i.ID || request.Model != "studio-image-v1" {
		t.Fatal("old key was not recoverable after rotation", err)
	}
	runSQL(t, `UPDATE render_control_intents SET request_cipher=set_byte(request_cipher,octet_length(request_cipher)-1,get_byte(request_cipher,octet_length(request_cipher)-1) # 1) WHERE id=$1`, i.ID)
	if _, _, err := s.Recover(ctx(), i.ID); !errors.Is(err, ErrCipher) {
		t.Fatal("ciphertext modification not detected")
	}
	balances(t, subject, "9.00000000", "1.00000000", "0.00000000")
}

func TestVideoRoundingAndMinimumMoney(t *testing.T) {
	s, subject := store(t), fixture(t, "1")
	r, err := NewRequest("videos.create", controlclient.JobRequest{Model: "video-v1", TaskType: "references_to_video", Input: map[string]any{"prompt": "synthetic"}, Parameters: map[string]any{"seconds": json.Number("5.001")}})
	if err != nil {
		t.Fatal(err)
	}
	i, err := s.Reserve(ctx(), subject, testTarget(t), "video", r, Quote{"p1", money(t, "0.00000001"), 6})
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.MarkDispatch(ctx(), i.ID); err != nil {
		t.Fatal(err)
	}
	e := event(t, i, "succeeded", 0)
	e.VideoMilliseconds = 4001
	if i, err = s.Finalize(ctx(), testTarget(t), subject.instance, e); err != nil || i.ActualAmount != 5 {
		t.Fatal("video ceiling or exact minimum money incorrect", err)
	}
	balances(t, subject, "0.99999995", "0.00000000", "0.00000005")
}

func TestKeyWindowsAndPendingHoldsAcrossReset(t *testing.T) {
	s, subject := store(t), fixture(t, "10")
	runSQL(t, `UPDATE api_keys SET quota=2,rate_limit_1d=2,usage_1d=2,window_1d_start=NOW()-INTERVAL '2 days' WHERE id=$1`, subject.keyID)
	i := reserve(t, s, subject, "old-window", 1, "1")
	// An unfinished hold survives a further window reset and still occupies quota.
	runSQL(t, `UPDATE api_keys SET window_1d_start=NOW()-INTERVAL '3 days' WHERE id=$1`, subject.keyID)
	if _, err := s.Reserve(ctx(), subject, testTarget(t), "over-limit", imageRequest(t, "synthetic", 2), Quote{"p1", money(t, "1"), 2}); !errors.Is(err, ErrQuota) {
		t.Fatal("window reset lost outstanding hold", err)
	}
	if _, err := s.MarkDispatch(ctx(), i.ID); err != nil {
		t.Fatal(err)
	}
	if _, err := s.Finalize(ctx(), testTarget(t), subject.instance, event(t, i, "succeeded", 1)); err != nil {
		t.Fatal(err)
	}
	var usage, status string
	if err := testDB.QueryRow(`SELECT usage_1d::text,status FROM api_keys WHERE id=$1`, subject.keyID).Scan(&usage, &status); err != nil || usage != "1.00000000" || status != "active" {
		t.Fatal("expired upstream window was not reset correctly")
	}
	j := reserve(t, s, subject, "last-quota", 1, "1")
	if _, err := s.MarkDispatch(ctx(), j.ID); err != nil {
		t.Fatal(err)
	}
	if _, err := s.Finalize(ctx(), testTarget(t), subject.instance, event(t, j, "succeeded", 1)); err != nil {
		t.Fatal(err)
	}
	if err := testDB.QueryRow(`SELECT status FROM api_keys WHERE id=$1`, subject.keyID).Scan(&status); err != nil || status != "quota_exhausted" {
		t.Fatal("quota exhaustion status did not mirror upstream")
	}
}

func TestLateBindingCannotChangeTerminal(t *testing.T) {
	s, subject := store(t), fixture(t, "10")
	i := reserve(t, s, subject, "late-bind", 1, "1")
	if _, err := s.MarkDispatch(ctx(), i.ID); err != nil {
		t.Fatal(err)
	}
	e := event(t, i, "succeeded", 1)
	settled, err := s.Finalize(ctx(), testTarget(t), subject.instance, e)
	if err != nil {
		t.Fatal(err)
	}
	admissionID, _ := newID("adm")
	late, err := s.BindAdmission(ctx(), i.ID, admissionID, e.JobID)
	if err != nil || late.State != "settled" || late.LedgerRef != settled.LedgerRef {
		t.Fatal("late binding overwrote terminal", err)
	}
	wrongJob, _ := newID("job")
	if _, err := s.BindAdmission(ctx(), i.ID, admissionID, wrongJob); !errors.Is(err, ErrConflict) {
		t.Fatal("remote binding changed")
	}
}

func TestArchivedDedupCannotBecomeSecondCharge(t *testing.T) {
	s, subject := store(t), fixture(t, "10")
	i := reserve(t, s, subject, "archived", 1, "1")
	if _, err := s.MarkDispatch(ctx(), i.ID); err != nil {
		t.Fatal(err)
	}
	e := event(t, i, "succeeded", 1)
	encoded, _ := json.Marshal(e)
	runSQL(t, `INSERT INTO usage_billing_dedup_archive(request_id,api_key_id,request_fingerprint,created_at) VALUES ($1,$2,$3,NOW())`, "render-control:"+i.ID+":terminal", subject.keyID, digest(encoded))
	if _, err := s.Finalize(ctx(), testTarget(t), subject.instance, e); !errors.Is(err, ErrConflict) {
		t.Fatal("inconsistent archived claim accepted")
	}
	balances(t, subject, "9.00000000", "1.00000000", "0.00000000")
	if countRows(t, `SELECT count(*) FROM usage_billing_dedup WHERE api_key_id=$1`, subject.keyID) != 0 {
		t.Fatal("archived conflict left a hot claim")
	}
}

func TestEffectLeaseFenceAndNoRebilling(t *testing.T) {
	s, subject := store(t), fixture(t, "10")
	i := reserve(t, s, subject, "effects", 1, "1")
	if _, err := s.MarkDispatch(ctx(), i.ID); err != nil {
		t.Fatal(err)
	}
	if _, err := s.Finalize(ctx(), testTarget(t), subject.instance, event(t, i, "succeeded", 1)); err != nil {
		t.Fatal(err)
	}
	// Other tests' pending effects are intentionally not acknowledged here.
	// A fresh consumer claims bounded batches until it reaches this test's rows.
	var target Effect
	for range 20 {
		batch, err := s.ClaimEffects(ctx(), "consumer-a", 100, time.Minute)
		if err != nil {
			t.Fatal(err)
		}
		for _, e := range batch {
			if e.IntentID == i.ID && e.Kind == "terminal" {
				target = e
			}
		}
		if target.ID > 0 || len(batch) == 0 {
			break
		}
	}
	if target.ID == 0 {
		t.Fatal("terminal effect was not durable")
	}
	if err := s.AcknowledgeEffect(ctx(), target.ID, "consumer-b", target.Fence); !errors.Is(err, ErrFence) {
		t.Fatal("wrong consumer acknowledged effect")
	}
	runSQL(t, `UPDATE render_control_effects SET lease_until=NOW()-INTERVAL '1 second' WHERE id=$1`, target.ID)
	if err := s.AcknowledgeEffect(ctx(), target.ID, target.Owner, target.Fence); !errors.Is(err, ErrFence) {
		t.Fatal("expired lease acknowledged effect")
	}
	batch, err := s.ClaimEffects(ctx(), "consumer-b", 100, time.Minute)
	if err != nil {
		t.Fatal(err)
	}
	var replacement Effect
	for _, e := range batch {
		if e.ID == target.ID {
			replacement = e
		}
	}
	if replacement.Fence != target.Fence+1 {
		t.Fatal("reclaim did not advance fence")
	}
	if err := s.AcknowledgeEffect(ctx(), target.ID, target.Owner, target.Fence); !errors.Is(err, ErrFence) {
		t.Fatal("old fence acknowledged reclaimed effect")
	}
	for range 2 {
		if err := s.AcknowledgeEffect(ctx(), replacement.ID, replacement.Owner, replacement.Fence); err != nil {
			t.Fatal("current acknowledgement replay failed", err)
		}
	}
	balances(t, subject, "9.00000000", "0.00000000", "1.00000000")
}

func TestActualProcessRestartBoundaries(t *testing.T) {
	s, subject := store(t), fixture(t, "10")
	executable, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	crash := func(stage, id string) {
		t.Helper()
		processCtx, cancel := context.WithTimeout(ctx(), 20*time.Second)
		defer cancel()
		command := exec.CommandContext(processCtx, executable, "-test.run=^TestProcessBoundaryHelper$")
		command.Env = append(os.Environ(), "RC_TEST_CHILD_STAGE="+stage, "RC_TEST_INTENT="+id, "RC_TEST_USER="+strconv.FormatInt(subject.userID, 10), "RC_TEST_KEY="+strconv.FormatInt(subject.keyID, 10))
		output, err := command.CombinedOutput()
		var exit *exec.ExitError
		if !errors.As(err, &exit) || exit.ExitCode() != 73 {
			t.Fatalf("child did not stop at committed %s boundary: %s", stage, output)
		}
	}
	crash("reserved", "")
	var id string
	if err := testDB.QueryRow(`SELECT id FROM render_control_intents WHERE api_key_id=$1`, subject.keyID).Scan(&id); err != nil {
		t.Fatal("intent did not survive child exit")
	}
	loaded, request, err := store(t).Recover(ctx(), id)
	if err != nil || loaded.State != "reserved" || request.Model != "studio-image-v1" {
		t.Fatal("reserve recovery failed", err)
	}
	balances(t, subject, "9.00000000", "1.00000000", "0.00000000")
	crash("dispatching", id)
	if _, err := s.ReleaseUnsent(ctx(), id); !errors.Is(err, ErrDispatched) {
		t.Fatal("restart lost unknown submission guard")
	}
	crash("settled", id)
	loaded, err = store(t).Load(ctx(), subject, id)
	if err != nil || loaded.State != "settled" {
		t.Fatal("settlement did not survive child exit", err)
	}
	e := controlclient.Settlement{ID: "evt_" + strings.Repeat("7", 32), JobID: "job_" + strings.Repeat("7", 32), ExternalTaskRef: id, AuthorizationRef: loaded.AuthorizationRef, PriceVersion: loaded.PriceVersion, Outcome: "succeeded", ImageCount: 1}
	replay, err := store(t).Finalize(ctx(), testTarget(t), subject.instance, e)
	if err != nil || replay.LedgerRef != loaded.LedgerRef {
		t.Fatal("terminal replay after process exit changed charge", err)
	}
	balances(t, subject, "9.00000000", "0.00000000", "1.00000000")
	if countRows(t, `SELECT count(*) FROM render_control_effects WHERE intent_id=$1 AND kind='terminal' AND acknowledged_at IS NULL`, id) != 1 {
		t.Fatal("settlement lost pending side effects before ack")
	}
}

func TestProcessBoundaryHelper(t *testing.T) {
	stage := os.Getenv("RC_TEST_CHILD_STAGE")
	if stage == "" {
		t.Skip("runs only as a native crash-boundary subprocess")
	}
	userID, _ := strconv.ParseInt(os.Getenv("RC_TEST_USER"), 10, 64)
	keyID, _ := strconv.ParseInt(os.Getenv("RC_TEST_KEY"), 10, 64)
	instance := os.Getenv("RC_TEST_INSTANCE")
	if instance == "" {
		instance = "native-test"
	}
	subject, err := NewSubject(instance, userID, keyID, netip.MustParseAddr("127.0.0.1"))
	if err != nil {
		t.Fatal(err)
	}
	s, id := store(t), os.Getenv("RC_TEST_INTENT")
	switch stage {
	case "effect_prepared":
		batch, err := s.claimEffects(ctx(), subject.instance, "native-effect-child", 1, time.Minute)
		if err != nil || len(batch) != 1 || batch[0].IntentID != id {
			t.Fatal("native effect claim failed", err)
		}
		if err := s.prepareEffect(ctx(), batch[0]); err != nil {
			t.Fatal("native local effect commit failed", err)
		}
	case "coordinator_step":
		coordinatorProcessStep(t, s, subject)
	case "reserved":
		reserve(t, s, subject, "process-restart", 1, "1")
	case "dispatching":
		if _, err := s.MarkDispatch(ctx(), id); err != nil {
			t.Fatal(err)
		}
	case "settled", "subscription_settled":
		i, err := s.Load(ctx(), subject, id)
		if err != nil {
			t.Fatal(err)
		}
		e := controlclient.Settlement{ID: "evt_" + strings.Repeat("7", 32), JobID: "job_" + strings.Repeat("7", 32), ExternalTaskRef: id, AuthorizationRef: i.AuthorizationRef, PriceVersion: i.PriceVersion, Outcome: "succeeded", ImageCount: 1}
		if stage == "subscription_settled" {
			e.ID, e.JobID = "evt_"+i.ID[3:], "job_"+i.ID[3:]
		}
		if _, err := s.Finalize(ctx(), testTarget(t), subject.instance, e); err != nil {
			t.Fatal(err)
		}
	case "closure_proved", "closed":
		i, err := s.Load(ctx(), subject, id)
		if err != nil {
			t.Fatal(err)
		}
		proof := closedProof(t, i, closureID(i))
		if stage == "closed" {
			if _, err := s.ReleaseClosed(ctx(), id, proof); err != nil {
				t.Fatal(err)
			}
		}
	default:
		t.Fatal("unknown child stage")
	}
	// No defers or graceful component shutdown: persistent commit boundaries
	// must survive the abrupt end of a real native Windows process.
	os.Exit(73)
}
