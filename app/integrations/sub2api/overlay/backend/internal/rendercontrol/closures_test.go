package rendercontrol

import (
	"context"
	"crypto/x509"
	"database/sql"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/Wei-Shaw/sub2api/internal/rendercontrol/controlclient"
	"github.com/lib/pq"
)

func testTarget(t *testing.T) Target {
	t.Helper()
	target, err := NewTarget("gateway_" + strings.Repeat("f", 32))
	if err != nil {
		t.Fatal(err)
	}
	return target
}

func closedProof(t *testing.T, i Intent, closureID string) controlclient.ClosureProof {
	t.Helper()
	identity, err := i.RequestIdentity()
	if err != nil {
		t.Fatal(err)
	}
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/gateway/v1/admission-requests/close" || r.Header.Get("X-External-Tenant-Ref") != "" {
			t.Error("proof request used the wrong protocol authority")
		}
		_ = json.NewEncoder(w).Encode(controlclient.RequestResolution{RequestIdentity: identity, GatewayID: i.TargetGatewayID, Status: "closed", ClosureID: &closureID})
	}))
	defer server.Close()
	credential, err := controlclient.ParseCredential(i.TargetGatewayID + "." + strings.Repeat("s", 43))
	if err != nil {
		t.Fatal(err)
	}
	roots := x509.NewCertPool()
	roots.AddCert(server.Certificate())
	client, err := controlclient.New(controlclient.Config{GatewayID: i.TargetGatewayID, Origin: server.URL, Credential: credential, RootCAs: roots})
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()
	resolution, err := client.CloseRequest(ctx(), identity)
	if err != nil {
		t.Fatal(err)
	}
	proof, ok := resolution.ClosureProof()
	if !ok {
		t.Fatal("verified test service did not issue a closure capability")
	}
	return proof
}

func closureID(i Intent) string { return "gcl_" + strings.TrimPrefix(i.ID, "rc_") }

func requireCheckViolation(t *testing.T, err error) {
	t.Helper()
	var failure *pq.Error
	if !errors.As(err, &failure) || failure.Code != "23514" {
		t.Fatal("expected database check violation")
	}
}

func TestTargetIsRequiredPersistedAndFrozenOnReplay(t *testing.T) {
	s, subject := store(t), fixture(t, "10")
	if _, err := NewTarget("not-a-gateway"); !errors.Is(err, ErrInvalid) {
		t.Fatal("invalid target accepted")
	}
	request := imageRequest(t, "synthetic test prompt", 1)
	quote := Quote{"prices-v1", money(t, "1"), 1}
	if _, err := s.Reserve(ctx(), subject, Target{}, "missing-target", request, quote); !errors.Is(err, ErrInvalid) {
		t.Fatal("unconfigured target reserved funds")
	}
	i := reserve(t, s, subject, "frozen-target", 1, "1")
	other, _ := NewTarget("gateway_" + strings.Repeat("e", 32))
	replay, err := s.Reserve(ctx(), subject, other, "frozen-target", request, quote)
	if err != nil || replay.ID != i.ID || replay.TargetGatewayID != i.TargetGatewayID {
		t.Fatal("routing configuration rebound an existing intent", err)
	}
	if _, err := s.MarkDispatch(ctx(), i.ID); err != nil {
		t.Fatal(err)
	}
	e := event(t, i, "succeeded", 1)
	if _, err := s.Finalize(ctx(), other, subject.instance, e); !errors.Is(err, ErrConflict) {
		t.Fatal("wrong gateway finalized another gateway's intent")
	}
	balances(t, subject, "9.00000000", "1.00000000", "0.00000000")
	if _, err := s.Finalize(ctx(), testTarget(t), subject.instance, e); err != nil {
		t.Fatal(err)
	}
}

func TestClosureProofReleasesOnceAfterRevocation(t *testing.T) {
	s, subject := store(t), fixture(t, "10")
	i := reserve(t, s, subject, "close-once", 2, "1")
	for range 2 {
		if _, err := s.MarkDispatch(ctx(), i.ID); err != nil {
			t.Fatal(err)
		}
	}
	proof := closedProof(t, i, closureID(i))
	runSQL(t, `UPDATE api_keys SET status='disabled',deleted_at=NOW() WHERE id=$1`, subject.keyID)
	runSQL(t, `UPDATE users SET status='disabled' WHERE id=$1`, subject.userID)
	var wg sync.WaitGroup
	results := make(chan Intent, 12)
	failures := make(chan error, 12)
	for range 12 {
		wg.Go(func() {
			result, err := store(t).ReleaseClosed(ctx(), i.ID, proof)
			results <- result
			failures <- err
		})
	}
	wg.Wait()
	close(results)
	close(failures)
	for err := range failures {
		if err != nil {
			t.Fatal("closure replay failed", err)
		}
	}
	ref := ""
	for result := range results {
		if ref == "" {
			ref = result.LedgerRef
		}
		if ref == "" || result.LedgerRef != ref || result.State != "released" || result.Outcome != "remote_closed" || result.DispatchCount != 2 || result.TerminalEventID != "" || result.RemoteJobID != "" {
			t.Fatal("closure replay changed ledger identity or invented a remote event")
		}
	}
	balances(t, subject, "10.00000000", "0.00000000", "0.00000000")
	if countRows(t, `SELECT count(*) FROM render_control_closures WHERE intent_id=$1`, i.ID) != 1 || countRows(t, `SELECT count(*) FROM render_control_effects WHERE intent_id=$1 AND kind='terminal'`, i.ID) != 1 || countRows(t, `SELECT count(*) FROM usage_billing_dedup WHERE api_key_id=$1`, subject.keyID) != 1 {
		t.Fatal("closure did not commit exactly one proof, dedup and terminal effect")
	}
	different := closedProof(t, i, "gcl_"+strings.Repeat("e", 32))
	if _, err := s.ReleaseClosed(ctx(), i.ID, different); !errors.Is(err, ErrConflict) {
		t.Fatal("different proof replaced committed closure")
	}
	if _, err := s.MarkDispatch(ctx(), i.ID); !errors.Is(err, ErrConflict) {
		t.Fatal("closed request dispatched again")
	}
	if _, err := s.Finalize(ctx(), testTarget(t), subject.instance, event(t, i, "succeeded", 1)); !errors.Is(err, ErrConflict) {
		t.Fatal("late settlement charged a closed request")
	}
}

func TestClosureProofCannotCrossIntentOrIdentity(t *testing.T) {
	s, subject := store(t), fixture(t, "10")
	i := reserve(t, s, subject, "proof-scope", 1, "1")
	if _, err := s.MarkDispatch(ctx(), i.ID); err != nil {
		t.Fatal(err)
	}
	if _, err := s.ReleaseClosed(ctx(), i.ID, controlclient.ClosureProof{}); !errors.Is(err, ErrConflict) {
		t.Fatal("empty proof released a possibly sent request")
	}
	for _, field := range []string{"gateway", "instance", "user", "key", "request", "task", "price", "authorization"} {
		t.Run(field, func(t *testing.T) {
			changed := i
			switch field {
			case "gateway":
				changed.TargetGatewayID = "gateway_" + strings.Repeat("e", 32)
			case "instance":
				changed.Instance = "other-instance"
			case "user":
				changed.UserID++
			case "key":
				changed.KeyID++
			case "request":
				changed.ProviderRequestID, _ = newID("rcr")
			case "task":
				changed.ID, _ = newID("rc")
			case "price":
				changed.PriceVersion = "other-price"
			case "authorization":
				changed.AuthorizationRef, _ = newID("rca")
			}
			proof := closedProof(t, changed, closureID(changed))
			if _, err := s.ReleaseClosed(ctx(), i.ID, proof); !errors.Is(err, ErrConflict) {
				t.Fatal("proof with changed " + field + " released original funds")
			}
		})
	}
	balances(t, subject, "9.00000000", "1.00000000", "0.00000000")
	if countRows(t, `SELECT count(*) FROM render_control_closures WHERE intent_id=$1`, i.ID) != 0 {
		t.Fatal("rejected proof persisted")
	}
}

func TestBoundAdmissionCannotConsumeClosureProof(t *testing.T) {
	s, subject := store(t), fixture(t, "10")
	i := reserve(t, s, subject, "bound-close", 1, "1")
	proof := closedProof(t, i, closureID(i))
	if _, err := s.MarkDispatch(ctx(), i.ID); err != nil {
		t.Fatal(err)
	}
	admission, _ := newID("adm")
	job, _ := newID("job")
	bound, err := s.BindAdmission(ctx(), i.ID, admission, job)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := s.ReleaseClosed(ctx(), i.ID, proof); !errors.Is(err, ErrConflict) {
		t.Fatal("bound admission was refunded from a contradictory proof")
	}
	if _, err := s.Finalize(ctx(), testTarget(t), subject.instance, event(t, bound, "succeeded", 1)); err != nil {
		t.Fatal(err)
	}
	if _, err := s.ReleaseClosed(ctx(), i.ID, proof); !errors.Is(err, ErrConflict) {
		t.Fatal("settled admission was refunded")
	}
	balances(t, subject, "9.00000000", "0.00000000", "1.00000000")
}

func TestClosureRollsBackProofFundsAndDedupOnEffectFailure(t *testing.T) {
	s, subject := store(t), fixture(t, "10")
	i := reserve(t, s, subject, "closure-fault", 1, "1")
	if _, err := s.MarkDispatch(ctx(), i.ID); err != nil {
		t.Fatal(err)
	}
	proof := closedProof(t, i, closureID(i))
	runSQL(t, `CREATE FUNCTION rc_test_closure_effect_failure() RETURNS TRIGGER LANGUAGE plpgsql AS $$ BEGIN IF NEW.kind='terminal' THEN RAISE EXCEPTION 'synthetic closure effect failure'; END IF; RETURN NEW; END $$`)
	runSQL(t, `CREATE TRIGGER rc_test_closure_effect_failure BEFORE INSERT ON render_control_effects FOR EACH ROW EXECUTE FUNCTION rc_test_closure_effect_failure()`)
	defer runSQL(t, `DROP FUNCTION IF EXISTS rc_test_closure_effect_failure() CASCADE`)
	if _, err := s.ReleaseClosed(ctx(), i.ID, proof); !errors.Is(err, ErrStorage) {
		t.Fatal("injected effect failure was hidden", err)
	}
	balances(t, subject, "9.00000000", "1.00000000", "0.00000000")
	if countRows(t, `SELECT count(*) FROM render_control_closures WHERE intent_id=$1`, i.ID) != 0 || countRows(t, `SELECT count(*) FROM usage_billing_dedup WHERE api_key_id=$1`, subject.keyID) != 0 {
		t.Fatal("closure proof or dedup escaped transaction rollback")
	}
	loaded, err := s.Load(ctx(), subject, i.ID)
	if err != nil || loaded.State != "dispatching" {
		t.Fatal("closure failure changed the intent")
	}
	runSQL(t, `DROP TRIGGER rc_test_closure_effect_failure ON render_control_effects`)
	if _, err := s.ReleaseClosed(ctx(), i.ID, proof); err != nil {
		t.Fatal("safe replay after rollback failed", err)
	}
}

func TestClosureRacesWithLocalTransitions(t *testing.T) {
	for _, action := range []string{"dispatch", "bind", "finalize"} {
		t.Run(action, func(t *testing.T) {
			for range 8 {
				s, subject := store(t), fixture(t, "10")
				i := reserve(t, s, subject, "closure-race", 1, "1")
				if action != "dispatch" {
					if _, err := s.MarkDispatch(ctx(), i.ID); err != nil {
						t.Fatal(err)
					}
				}
				proof := closedProof(t, i, closureID(i))
				admission, _ := newID("adm")
				e := event(t, i, "succeeded", 1)
				var closureErr, actionErr error
				var wg sync.WaitGroup
				wg.Go(func() { _, closureErr = s.ReleaseClosed(ctx(), i.ID, proof) })
				wg.Go(func() {
					switch action {
					case "dispatch":
						_, actionErr = s.MarkDispatch(ctx(), i.ID)
					case "bind":
						_, actionErr = s.BindAdmission(ctx(), i.ID, admission, e.JobID)
					case "finalize":
						_, actionErr = s.Finalize(ctx(), testTarget(t), subject.instance, e)
					}
				})
				wg.Wait()
				for _, err := range []error{closureErr, actionErr} {
					if err != nil && !errors.Is(err, ErrConflict) {
						t.Fatal("unexpected racing transition failure", err)
					}
				}
				loaded, err := s.Load(ctx(), subject, i.ID)
				if err != nil {
					t.Fatal(err)
				}
				switch loaded.State {
				case "released":
					if closureErr != nil || loaded.Outcome != "remote_closed" || action != "dispatch" && actionErr == nil {
						t.Fatal("closure and incompatible transition both won")
					}
					balances(t, subject, "10.00000000", "0.00000000", "0.00000000")
				case "settled":
					if action != "finalize" || actionErr != nil || closureErr == nil {
						t.Fatal("racing settlement lost exclusivity")
					}
					balances(t, subject, "9.00000000", "0.00000000", "1.00000000")
				case "admitted":
					if action != "bind" || actionErr != nil || closureErr == nil {
						t.Fatal("bound task lost its hold")
					}
					balances(t, subject, "9.00000000", "1.00000000", "0.00000000")
				default:
					t.Fatal("racing closure did not reach a valid outcome")
				}
			}
		})
	}
}

func insertClosureSQL(t *testing.T, tx *sql.Tx, i Intent, keyRef string) {
	t.Helper()
	identity, err := i.RequestIdentity()
	if err != nil {
		t.Fatal(err)
	}
	if _, err := tx.Exec(`INSERT INTO render_control_closures(intent_id,target_gateway_id,closure_id,provider_request_id,external_task_ref,price_version,authorization_ref,external_tenant_ref,external_key_ref,proof_hash) VALUES ($1,$2,$3,$4,$1,$5,$6,$7,$8,$9)`, i.ID, i.TargetGatewayID, closureID(i), i.ProviderRequestID, i.PriceVersion, i.AuthorizationRef, identity.ExternalTenantRef, keyRef, strings.Repeat("a", 64)); err != nil {
		t.Fatal("synthetic proof fixture failed")
	}
}

func TestDatabaseRequiresMatchingProofAndTerminalInOneTransaction(t *testing.T) {
	for _, kind := range []string{"proof_only", "terminal_only", "wrong_owner"} {
		t.Run(kind, func(t *testing.T) {
			s, subject := store(t), fixture(t, "10")
			i := reserve(t, s, subject, "db-proof", 1, "1")
			identity, _ := i.RequestIdentity()
			tx, err := testDB.Begin()
			if err != nil {
				t.Fatal(err)
			}
			defer tx.Rollback()
			if kind != "terminal_only" {
				key := identity.ExternalKeyRef
				if kind == "wrong_owner" {
					key += "-wrong"
				}
				insertClosureSQL(t, tx, i, key)
			}
			if kind != "proof_only" {
				if _, err = tx.Exec(`UPDATE render_control_intents SET state='released',outcome='remote_closed',actual_amount=0,terminal_hash=$2,ledger_ref=$3 WHERE id=$1`, i.ID, strings.Repeat("a", 64), "rcl_"+strings.TrimPrefix(i.ID, "rc_")); err != nil {
					t.Fatal("synthetic terminal fixture failed")
				}
			}
			requireCheckViolation(t, tx.Commit())
			balances(t, subject, "9.00000000", "1.00000000", "0.00000000")
			if countRows(t, `SELECT count(*) FROM render_control_closures WHERE intent_id=$1`, i.ID) != 0 {
				t.Fatal("inconsistent proof survived commit")
			}
		})
	}
}

func TestDatabasePreservesClosureAndIntentIdentity(t *testing.T) {
	s, subject := store(t), fixture(t, "10")
	i := reserve(t, s, subject, "immutable-proof", 1, "1")
	proof := closedProof(t, i, closureID(i))
	if _, err := s.ReleaseClosed(ctx(), i.ID, proof); err != nil {
		t.Fatal(err)
	}
	for _, query := range []string{
		`INSERT INTO render_control_intents(id,instance,user_id,api_key_id,client_ip,operation,idempotency_hash,request_hash,request_cipher,cipher_key_id,task_type,provider_request_id,authorization_ref,price_version,unit_price,max_units,hold_amount) SELECT id,instance,user_id,api_key_id,client_ip,operation,idempotency_hash,request_hash,request_cipher,cipher_key_id,task_type,provider_request_id,authorization_ref,price_version,unit_price,max_units,hold_amount FROM render_control_intents WHERE id=$1`,
		`DELETE FROM render_control_closures WHERE intent_id=$1`,
		`UPDATE render_control_closures SET closure_id='gcl_eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee' WHERE intent_id=$1`,
		`UPDATE render_control_intents SET target_gateway_id='gateway_eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee' WHERE id=$1`,
		`UPDATE render_control_intents SET authorization_ref=authorization_ref||'-changed' WHERE id=$1`,
		`UPDATE render_control_intents SET price_version='changed' WHERE id=$1`,
		`UPDATE render_control_intents SET request_hash=repeat('e',64) WHERE id=$1`,
	} {
		_, err := testDB.Exec(query, i.ID)
		requireCheckViolation(t, err)
	}
	balances(t, subject, "10.00000000", "0.00000000", "0.00000000")
}

func TestLegacyUnknownTargetCannotBeGuessedOrDispatched(t *testing.T) {
	for _, dispatched := range []bool{false, true} {
		t.Run(strconv.FormatBool(dispatched), func(t *testing.T) {
			s, subject := store(t), fixture(t, "10")
			i := reserve(t, s, subject, "legacy-target", 1, "1")
			proof := closedProof(t, i, closureID(i))
			if dispatched {
				if _, err := s.MarkDispatch(ctx(), i.ID); err != nil {
					t.Fatal(err)
				}
			}
			// Simulate a 247 row surviving the nullable 248 column addition.
			// This owned disposable test database is never a production repair.
			tx, err := testDB.Begin()
			if err != nil {
				t.Fatal(err)
			}
			defer tx.Rollback()
			for _, query := range []string{`SET CONSTRAINTS ALL IMMEDIATE`, `ALTER TABLE render_control_intents DISABLE TRIGGER rc_guard_intent_identity`, `UPDATE render_control_intents SET target_gateway_id=NULL WHERE id=$1`, `ALTER TABLE render_control_intents ENABLE TRIGGER rc_guard_intent_identity`} {
				var args []any
				if strings.Contains(query, "$1") {
					args = []any{i.ID}
				}
				if _, err = tx.Exec(query, args...); err != nil {
					t.Fatal("legacy fixture failed")
				}
			}
			if err = tx.Commit(); err != nil {
				t.Fatal("legacy fixture commit failed")
			}
			loaded, err := s.Load(ctx(), subject, i.ID)
			if err != nil || loaded.TargetGatewayID != "" {
				t.Fatal("legacy target was inferred")
			}
			if _, err := loaded.RequestIdentity(); !errors.Is(err, ErrInvalid) {
				t.Fatal("unknown target produced a recovery identity")
			}
			if _, err := s.MarkDispatch(ctx(), i.ID); !errors.Is(err, ErrConflict) {
				t.Fatal("unknown target dispatched")
			}
			_, err = testDB.Exec(`UPDATE render_control_intents SET state='dispatching',dispatch_count=dispatch_count+1 WHERE id=$1`, i.ID)
			requireCheckViolation(t, err)
			if _, err := s.ReleaseClosed(ctx(), i.ID, proof); !errors.Is(err, ErrConflict) {
				t.Fatal("current routing supplied a legacy proof target")
			}
			_, err = testDB.Exec(`UPDATE render_control_intents SET target_gateway_id=$2 WHERE id=$1`, i.ID, testTarget(t).gatewayID)
			requireCheckViolation(t, err)
			if dispatched {
				if _, err := s.ReleaseUnsent(ctx(), i.ID); !errors.Is(err, ErrDispatched) {
					t.Fatal("unknown legacy submission refunded")
				}
				balances(t, subject, "9.00000000", "1.00000000", "0.00000000")
			} else if _, err := s.ReleaseUnsent(ctx(), i.ID); err != nil {
				t.Fatal("provably unsent legacy reservation could not be released")
			}
		})
	}
}

func TestClosureSurvivesActualProcessExit(t *testing.T) {
	s, subject := store(t), fixture(t, "10")
	i := reserve(t, s, subject, "closure-process", 1, "1")
	if _, err := s.MarkDispatch(ctx(), i.ID); err != nil {
		t.Fatal(err)
	}
	executable, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	for _, stage := range []string{"closure_proved", "closed"} {
		processCtx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
		command := exec.CommandContext(processCtx, executable, "-test.run=^TestProcessBoundaryHelper$")
		command.Env = append(os.Environ(), "RC_TEST_CHILD_STAGE="+stage, "RC_TEST_INTENT="+i.ID, "RC_TEST_USER="+strconv.FormatInt(subject.userID, 10), "RC_TEST_KEY="+strconv.FormatInt(subject.keyID, 10))
		output, err := command.CombinedOutput()
		cancel()
		var exit *exec.ExitError
		if !errors.As(err, &exit) || exit.ExitCode() != 73 {
			t.Fatalf("child did not stop at closure boundary: %s", output)
		}
		if stage == "closure_proved" {
			balances(t, subject, "9.00000000", "1.00000000", "0.00000000")
			if countRows(t, `SELECT count(*) FROM render_control_closures WHERE intent_id=$1`, i.ID) != 0 {
				t.Fatal("uncommitted receipt released funds across process exit")
			}
		}
	}
	loaded, err := store(t).Load(ctx(), subject, i.ID)
	if err != nil || loaded.State != "released" || loaded.Outcome != "remote_closed" || loaded.TargetGatewayID != i.TargetGatewayID {
		t.Fatal("closed state or original target lost after process exit", err)
	}
	proof := closedProof(t, loaded, closureID(loaded))
	replay, err := store(t).ReleaseClosed(ctx(), loaded.ID, proof)
	if err != nil || replay.LedgerRef != loaded.LedgerRef {
		t.Fatal("closure replay after process exit changed ledger identity", err)
	}
	balances(t, subject, "10.00000000", "0.00000000", "0.00000000")
}
