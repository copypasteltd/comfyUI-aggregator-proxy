package rendercontrol

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/Wei-Shaw/sub2api/internal/rendercontrol/controlclient"
)

func TestNativeTestGuardRejectsOtherDatabase(t *testing.T) {
	executable, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	processCtx, cancel := context.WithTimeout(ctx(), 20*time.Second)
	defer cancel()
	command := exec.CommandContext(processCtx, executable, "-test.run=^TestProcessBoundaryHelper$")
	// The harness supplies lib/pq keyword conninfo. A last dbname parameter
	// overrides only the child's target; the string is never passed in argv.
	command.Env = append(os.Environ(), "RC_SUB2API_TEST_DSN="+os.Getenv("RC_SUB2API_TEST_DSN")+" dbname=postgres", "RC_TEST_CHILD_STAGE=guard")
	output, err := command.CombinedOutput()
	var exit *exec.ExitError
	if !errors.As(err, &exit) || exit.ExitCode() != 2 || !strings.Contains(string(output), "refusing tests outside the reserved project-native disposable database namespace") {
		t.Fatal("native child did not refuse the non-test database before migrations")
	}
}

func TestVideoDurationAllocationBounds(t *testing.T) {
	for _, value := range []string{"1e999999999", "1e-999999999", "0.0001", "86400.001", "0", "-1", "1e2"} {
		t.Run(value, func(t *testing.T) {
			_, err := NewRequest("videos.create", controlclient.JobRequest{Model: "m", TaskType: "text_to_video", Input: map[string]any{"prompt": "synthetic"}, Parameters: map[string]any{"seconds": json.Number(value)}})
			if !errors.Is(err, ErrInvalid) {
				t.Fatal("unbounded or unsupported decimal duration accepted")
			}
		})
	}
}

func TestExpiredAndDisabledAdmission(t *testing.T) {
	for _, kind := range []string{"key_expired", "key_disabled", "user_disabled", "group_disabled", "missing_group"} {
		t.Run(kind, func(t *testing.T) {
			s, subject := store(t), fixture(t, "10")
			expected := ErrDenied
			switch kind {
			case "key_expired":
				runSQL(t, `UPDATE api_keys SET expires_at=NOW()-INTERVAL '1 second' WHERE id=$1`, subject.keyID)
			case "key_disabled":
				runSQL(t, `UPDATE api_keys SET status='disabled' WHERE id=$1`, subject.keyID)
			case "user_disabled":
				runSQL(t, `UPDATE users SET status='disabled' WHERE id=$1`, subject.userID)
			case "group_disabled":
				runSQL(t, `UPDATE groups SET status='disabled' WHERE id=(SELECT group_id FROM api_keys WHERE id=$1)`, subject.keyID)
			case "missing_group":
				runSQL(t, `UPDATE api_keys SET group_id=NULL WHERE id=$1`, subject.keyID)
				expected = ErrUnsupported
			}
			if _, err := s.Reserve(ctx(), subject, testTarget(t), "must-deny", imageRequest(t, "synthetic", 1), Quote{"p1", money(t, "1"), 1}); !errors.Is(err, expected) {
				t.Fatal("inactive admission was not rejected", err)
			}
			balances(t, subject, "10.00000000", "0.00000000", "0.00000000")
		})
	}
}

func TestDifferentKeysShareOneUserBalanceLock(t *testing.T) {
	s, a := store(t), fixture(t, "1")
	var keyID int64
	tag, _ := newID("test")
	if err := testDB.QueryRow(`INSERT INTO api_keys(user_id,group_id,key,name) SELECT user_id,group_id,$2,'synthetic' FROM api_keys WHERE id=$1 RETURNING id`, a.keyID, tag).Scan(&keyID); err != nil {
		t.Fatal(err)
	}
	b, _ := NewSubject(a.instance, a.userID, keyID, a.ip)
	var wg sync.WaitGroup
	results := make(chan error, 2)
	r := imageRequest(t, "synthetic", 1)
	q := Quote{"p1", money(t, "0.75"), 1}
	for _, subject := range []Subject{a, b} {
		wg.Go(func() { _, err := s.Reserve(ctx(), subject, testTarget(t), "different-key", r, q); results <- err })
	}
	wg.Wait()
	close(results)
	success := 0
	for err := range results {
		if err == nil {
			success++
		} else if !errors.Is(err, ErrBalance) {
			t.Fatal(err)
		}
	}
	if success != 1 {
		t.Fatal("different Keys overdrew one user balance")
	}
	balances(t, a, "0.25000000", "0.75000000", "0.00000000")
}

func TestDistinctIntentsCannotReuseTerminalEvent(t *testing.T) {
	s, subject := store(t), fixture(t, "10")
	a, b := reserve(t, s, subject, "event-a", 1, "1"), reserve(t, s, subject, "event-b", 1, "1")
	for _, i := range []Intent{a, b} {
		if _, err := s.MarkDispatch(ctx(), i.ID); err != nil {
			t.Fatal(err)
		}
	}
	first, second := event(t, a, "succeeded", 1), event(t, b, "succeeded", 1)
	second.ID = first.ID
	if _, err := s.Finalize(ctx(), testTarget(t), subject.instance, first); err != nil {
		t.Fatal(err)
	}
	if _, err := s.Finalize(ctx(), testTarget(t), subject.instance, second); !errors.Is(err, ErrConflict) {
		t.Fatal("event ID reused for a second intent", err)
	}
	balances(t, subject, "8.00000000", "1.00000000", "1.00000000")
	if countRows(t, `SELECT count(*) FROM usage_billing_dedup WHERE api_key_id=$1`, subject.keyID) != 1 {
		t.Fatal("failed duplicate event escaped rollback")
	}
}

func TestFrozenBalanceConflictPreservesIntent(t *testing.T) {
	s, subject := store(t), fixture(t, "10")
	i := reserve(t, s, subject, "frozen-conflict", 1, "1")
	if _, err := s.MarkDispatch(ctx(), i.ID); err != nil {
		t.Fatal(err)
	}
	runSQL(t, `UPDATE users SET frozen_balance=0 WHERE id=$1`, subject.userID)
	if _, err := s.Finalize(ctx(), testTarget(t), subject.instance, event(t, i, "succeeded", 1)); !errors.Is(err, ErrConflict) {
		t.Fatal("missing user hold was silently settled")
	}
	loaded, err := s.Load(ctx(), subject, i.ID)
	if err != nil || loaded.State != "dispatching" || loaded.LedgerRef != "" {
		t.Fatal("failed balance reconciliation changed terminal state")
	}
	balances(t, subject, "9.00000000", "0.00000000", "0.00000000")
}

func TestLedgerForeignKeysPreventHardDeletion(t *testing.T) {
	s, subject := store(t), fixture(t, "10")
	reserve(t, s, subject, "retain-ledger", 1, "1")
	if _, err := testDB.Exec(`DELETE FROM api_keys WHERE id=$1`, subject.keyID); err == nil {
		t.Fatal("hard deleted Key with durable ledger")
	}
	if _, err := testDB.Exec(`DELETE FROM users WHERE id=$1`, subject.userID); err == nil {
		t.Fatal("hard deleted user with durable ledger")
	}
	balances(t, subject, "9.00000000", "1.00000000", "0.00000000")
}

func TestPendingRecoveryIsBounded(t *testing.T) {
	s, subject := store(t), fixture(t, "10")
	reserve(t, s, subject, "pending", 1, "1")
	if _, err := s.Pending(ctx(), 101); !errors.Is(err, ErrInvalid) {
		t.Fatal("unbounded recovery list accepted")
	}
	rows, err := s.Pending(ctx(), 2)
	if err != nil || len(rows) != 2 {
		t.Fatal("bounded durable recovery list failed", err)
	}
	for _, i := range rows {
		if i.Terminal() || !validID("rc", i.ID) {
			t.Fatal("invalid recovery projection")
		}
	}
}

func TestConcurrentEffectConsumersDoNotShareLeases(t *testing.T) {
	s, subject := store(t), fixture(t, "10")
	for index := range 6 {
		reserve(t, s, subject, fmt.Sprintf("consumer-%d", index), 1, "0.1")
	}
	var wg sync.WaitGroup
	batches := make(chan []Effect, 2)
	failures := make(chan error, 2)
	for index := range 2 {
		wg.Go(func() {
			rows, err := s.ClaimEffects(ctx(), fmt.Sprintf("parallel-consumer-%d", index), 100, time.Minute)
			batches <- rows
			failures <- err
		})
	}
	wg.Wait()
	close(batches)
	close(failures)
	for err := range failures {
		if err != nil {
			t.Fatal(err)
		}
	}
	seen := map[int64]bool{}
	found := 0
	for batch := range batches {
		for _, e := range batch {
			if seen[e.ID] {
				t.Fatal("same outbox lease assigned to concurrent consumers")
			}
			seen[e.ID] = true
			if e.KeyID == subject.keyID {
				found++
			}
		}
	}
	if found != 6 {
		t.Fatal("newly durable effects were not claimable")
	}
}
