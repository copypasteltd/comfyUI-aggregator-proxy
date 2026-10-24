package rendercontrol

import (
	"errors"
	"fmt"
	"strings"
	"sync"
	"testing"
	"time"
)

func recoveryFixture(t *testing.T) (*Store, Subject) {
	t.Helper()
	s, subject := store(t), fixture(t, "10")
	id, _ := newID("test")
	var err error
	subject, err = NewSubject("recovery-"+id[5:17], subject.userID, subject.keyID, subject.ip)
	if err != nil {
		t.Fatal(err)
	}
	return s, subject
}

func recoveryLease(t *testing.T, s *Store, subject Subject, id, owner string) RecoveryLease {
	t.Helper()
	leases, err := s.ClaimRecovery(ctx(), testTarget(t), subject.instance, owner, 1, 90*time.Second)
	if err != nil || len(leases) != 1 || leases[0].id != id {
		t.Fatal("failed to claim expected isolated recovery work", err)
	}
	return leases[0]
}

func dueRecovery(t *testing.T, id string) {
	t.Helper()
	runSQL(t, `UPDATE render_control_recovery SET next_attempt_at=clock_timestamp()-INTERVAL '1 second' WHERE intent_id=$1`, id)
}

func TestRecoveryLeaseClaimsAreConcurrentAndScoped(t *testing.T) {
	s, subject := recoveryFixture(t)
	for index := range 16 {
		reserve(t, s, subject, fmt.Sprintf("claim-%d", index), 1, "0.1")
	}
	other, _ := NewTarget("gateway_" + strings.Repeat("e", 32))
	for _, config := range []struct {
		target   Target
		instance string
	}{{other, subject.instance}, {testTarget(t), "other-instance"}} {
		leases, err := s.ClaimRecovery(ctx(), config.target, config.instance, "other-owner", 100, time.Minute)
		if err != nil || len(leases) != 0 {
			t.Fatal("recovery claim crossed target or instance")
		}
	}
	var wg sync.WaitGroup
	results := make(chan []RecoveryLease, 4)
	failures := make(chan error, 4)
	for index := range 4 {
		wg.Go(func() {
			leases, err := s.ClaimRecovery(ctx(), testTarget(t), subject.instance, fmt.Sprintf("owner-%d", index), 4, time.Minute)
			results <- leases
			failures <- err
		})
	}
	wg.Wait()
	close(results)
	close(failures)
	for err := range failures {
		if err != nil {
			t.Fatal("parallel recovery claims failed", err)
		}
	}
	seen := map[string]bool{}
	for leases := range results {
		for _, lease := range leases {
			if !lease.valid() || seen[lease.id] {
				t.Fatal("two recovery consumers shared one lease")
			}
			seen[lease.id] = true
		}
	}
	if len(seen) != 16 {
		t.Fatal("SKIP LOCKED claims lost due work")
	}
}

func TestRecoveryStaleFenceCannotMutateOrComplete(t *testing.T) {
	s, subject := recoveryFixture(t)
	i := reserve(t, s, subject, "stale-recovery", 1, "1")
	old := recoveryLease(t, s, subject, i.ID, "same-owner")
	runSQL(t, `UPDATE render_control_recovery SET lease_until=clock_timestamp()-INTERVAL '1 second' WHERE intent_id=$1`, i.ID)
	current := recoveryLease(t, s, subject, i.ID, "same-owner")
	if current.fence != old.fence+1 {
		t.Fatal("same owner reuse did not advance fencing")
	}
	admission, _ := newID("adm")
	job, _ := newID("job")
	proof := closedProof(t, i, closureID(i))
	operations := []func() error{
		func() error { _, err := s.GuardRecovery(ctx(), old); return err },
		func() error { _, err := s.MarkRecoveryDispatch(ctx(), old); return err },
		func() error { _, err := s.BindRecoveryAdmission(ctx(), old, admission, job); return err },
		func() error { _, err := s.ReleaseRecoveryClosed(ctx(), old, proof); return err },
		func() error { _, err := s.ReleaseRecoveryUnsent(ctx(), old); return err },
		func() error { return s.FinishRecovery(ctx(), old, "", false, 0) },
	}
	for _, operation := range operations {
		if err := operation(); !errors.Is(err, ErrFence) {
			t.Fatal("stale recovery fence changed state", err)
		}
	}
	loaded, err := s.GuardRecovery(ctx(), current)
	if err != nil || loaded.DispatchCount != 0 || loaded.State != "reserved" {
		t.Fatal("current lease lost its intent")
	}
	if _, err := s.ReleaseRecoveryUnsent(ctx(), current); err != nil {
		t.Fatal(err)
	}
	if err := s.FinishRecovery(ctx(), current, "", false, 0); err != nil {
		t.Fatal(err)
	}
	if _, err := s.GuardRecovery(ctx(), current); !errors.Is(err, ErrFence) {
		t.Fatal("finished lease retained mutation authority")
	}
	if leases, err := s.ClaimRecovery(ctx(), testTarget(t), subject.instance, "later-owner", 1, time.Minute); err != nil || len(leases) != 0 {
		t.Fatal("terminal intent was claimed again")
	}
	balances(t, subject, "10.00000000", "0.00000000", "0.00000000")
}

func TestRecoveryLeaseUsesActualDatabaseClock(t *testing.T) {
	s, subject := recoveryFixture(t)
	i := reserve(t, s, subject, "clock-recovery", 1, "1")
	lease := recoveryLease(t, s, subject, i.ID, "clock-owner")
	runSQL(t, `UPDATE render_control_recovery SET lease_until=clock_timestamp()+INTERVAL '200 milliseconds' WHERE intent_id=$1`, i.ID)
	tx, err := begin(ctx(), testDB)
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback()
	var initiallyValid bool
	if err = tx.QueryRow(`SELECT CURRENT_TIMESTAMP<lease_until FROM render_control_recovery WHERE intent_id=$1`, i.ID).Scan(&initiallyValid); err != nil || !initiallyValid {
		t.Fatal("clock fixture did not start within its lease")
	}
	if _, err = tx.Exec(`SELECT pg_sleep(0.25)`); err != nil {
		t.Fatal("clock fixture failed")
	}
	if _, err = lockMutation(ctx(), tx, i.ID, &lease); !errors.Is(err, ErrFence) {
		t.Fatal("transaction start time extended an expired lease")
	}
}

func TestRecoveryBackoffAndClosePlanSurviveReclaim(t *testing.T) {
	s, subject := recoveryFixture(t)
	i := reserve(t, s, subject, "backoff-recovery", 1, "1")
	lease := recoveryLease(t, s, subject, i.ID, "backoff-owner")
	if err := s.FinishRecovery(ctx(), lease, "response contains private content", true, time.Second); !errors.Is(err, ErrInvalid) {
		t.Fatal("raw error text entered persistent recovery metadata")
	}
	if err := s.FinishRecovery(ctx(), lease, "gateway_call_failed", true, RecoveryBackoff(lease.failures)); err != nil {
		t.Fatal(err)
	}
	leases, err := s.ClaimRecovery(ctx(), testTarget(t), subject.instance, "too-early", 1, time.Minute)
	if err != nil || len(leases) != 0 {
		t.Fatal("failed work skipped its durable backoff")
	}
	dueRecovery(t, i.ID)
	current := recoveryLease(t, store(t), subject, i.ID, "after-restart")
	if current.failures != 1 || !current.closing || RecoveryBackoff(current.failures) != 4*time.Second || RecoveryBackoff(31) != 5*time.Minute || RecoveryBackoff(7) != 256*time.Second {
		t.Fatal("backoff or close intent was reset by a new store")
	}
	if err := s.FinishRecovery(ctx(), current, "", true, 0); err != nil {
		t.Fatal(err)
	}
	runSQL(t, `UPDATE render_control_recovery SET failures=31 WHERE intent_id=$1`, i.ID)
	current = recoveryLease(t, s, subject, i.ID, "saturated-owner")
	if err := s.FinishRecovery(ctx(), current, "gateway_call_failed", true, RecoveryBackoff(current.failures)); err != nil {
		t.Fatal(err)
	}
	if countRows(t, `SELECT failures FROM render_control_recovery WHERE intent_id=$1`, i.ID) != 31 {
		t.Fatal("persistent retry count overflowed")
	}
}

func TestRecoveryWorkAndReservationCommitTogether(t *testing.T) {
	s, subject := recoveryFixture(t)
	runSQL(t, `CREATE FUNCTION rc_test_recovery_insert_failure() RETURNS TRIGGER LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic recovery insert failure'; END $$`)
	runSQL(t, `CREATE TRIGGER rc_test_recovery_insert_failure BEFORE INSERT ON render_control_recovery FOR EACH ROW EXECUTE FUNCTION rc_test_recovery_insert_failure()`)
	defer runSQL(t, `DROP FUNCTION IF EXISTS rc_test_recovery_insert_failure() CASCADE`)
	_, err := s.Reserve(ctx(), subject, testTarget(t), "atomic-recovery", imageRequest(t, "synthetic", 1), Quote{"p1", money(t, "1"), 1})
	if !errors.Is(err, ErrStorage) {
		t.Fatal("recovery insert failure was hidden", err)
	}
	balances(t, subject, "10.00000000", "0.00000000", "0.00000000")
	if countRows(t, `SELECT count(*) FROM render_control_intents WHERE api_key_id=$1`, subject.keyID) != 0 || countRows(t, `SELECT count(*) FROM render_control_effects WHERE api_key_id=$1`, subject.keyID) != 0 {
		t.Fatal("reservation or outbox escaped recovery work rollback")
	}
	runSQL(t, `DROP TRIGGER rc_test_recovery_insert_failure ON render_control_recovery`)
	i := reserve(t, s, subject, "atomic-recovery", 1, "1")
	if countRows(t, `SELECT count(*) FROM render_control_recovery WHERE intent_id=$1`, i.ID) != 1 {
		t.Fatal("successful reservation omitted durable recovery work")
	}
}
