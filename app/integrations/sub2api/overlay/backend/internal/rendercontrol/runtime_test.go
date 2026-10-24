package rendercontrol

import (
	"context"
	"crypto/x509"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/Wei-Shaw/sub2api/internal/rendercontrol/controlclient"
)

func awaitRuntime(t *testing.T, message string, check func() bool) {
	t.Helper()
	deadline := time.NewTimer(8 * time.Second)
	defer deadline.Stop()
	ticker := time.NewTicker(10 * time.Millisecond)
	defer ticker.Stop()
	for {
		if check() {
			return
		}
		select {
		case <-deadline.C:
			t.Fatal(message)
		case <-ticker.C:
		}
	}
}

func runtimeForTest(t *testing.T, s *Store, subject Subject, clients map[string]*controlclient.Client) *Runtime {
	t.Helper()
	id, _ := newID("host")
	runtime, err := NewRuntime(s, clients, RuntimeConfig{Instance: subject.instance, Owner: id, Workers: 2, PollInterval: 20 * time.Millisecond, SettlementInterval: 20 * time.Millisecond, CallTimeout: 2 * time.Second})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		if err := runtime.Stop(ctx); err != nil {
			t.Error("runtime cleanup did not finish")
		}
	})
	return runtime
}

func protocolClient(t *testing.T, server *httptest.Server, id string) *controlclient.Client {
	t.Helper()
	credential, _ := controlclient.ParseCredential(id + "." + strings.Repeat("s", 43))
	roots := x509.NewCertPool()
	roots.AddCert(server.Certificate())
	client, err := controlclient.New(controlclient.Config{GatewayID: id, Origin: server.URL, Credential: credential, RootCAs: roots})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(client.Close)
	return client
}

func TestRuntimeAutomaticallyRecoversAndSettlesWithAckReplay(t *testing.T) {
	s, subject := recoveryFixture(t)
	i := reserve(t, s, subject, "runtime-automatic", 1, "1")
	g := coordinatorGateway(t, i)
	runtime := runtimeForTest(t, s, subject, map[string]*controlclient.Client{i.TargetGatewayID: g.client})
	if err := runtime.Start(context.Background()); err != nil {
		t.Fatal(err)
	}
	confirmPath := admissionPath + "/adm_" + i.ID[3:] + "/confirm"
	awaitRuntime(t, "background runtime did not recover and confirm the durable intent", func() bool { return g.count(confirmPath) == 1 })
	g.configure(func(g *recoveryGateway) { g.outcome, g.ackFailures = "succeeded", 1 })
	ackPath := settlementPath + "/evt_" + i.ID[3:] + "/ack"
	awaitRuntime(t, "background runtime did not replay settlement acknowledgement", func() bool {
		snapshot := runtime.Snapshot()
		return g.count(ackPath) >= 2 && snapshot.SettlementsConfirmed >= 1
	})
	loaded, err := s.Load(ctx(), subject, i.ID)
	if err != nil || loaded.State != "settled" || loaded.Outcome != "succeeded" || loaded.ID != i.ID || g.count(admissionPath) != 1 {
		t.Fatal("hosted recovery changed identity, terminal or dispatch count", err)
	}
	balances(t, subject, "9.00000000", "0.00000000", "1.00000000")
	if runtime.Snapshot().SettlementFailures < 1 {
		t.Fatal("acknowledgement failure was not observed")
	}
	var wait sync.WaitGroup
	for range 8 {
		wait.Go(func() {
			ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
			defer cancel()
			if runtime.Stop(ctx) != nil {
				t.Error("concurrent runtime stop failed")
			}
		})
	}
	wait.Wait()
	before := runtime.Snapshot()
	time.Sleep(60 * time.Millisecond)
	if before.State != "stopped" || runtime.Snapshot() != before || !errors.Is(runtime.Start(ctx()), ErrConflict) {
		t.Fatal("runtime retained background loops or restarted after stop")
	}
}

func TestRuntimeConcurrentStartAndParentCancellation(t *testing.T) {
	s, subject := recoveryFixture(t)
	seed := Intent{Instance: subject.instance, UserID: subject.userID, KeyID: subject.keyID, TrustedIP: subject.ip, TargetGatewayID: testTarget(t).gatewayID}
	g := coordinatorGateway(t, seed)
	runtime := runtimeForTest(t, s, subject, map[string]*controlclient.Client{seed.TargetGatewayID: g.client})
	parent, cancel := context.WithCancel(context.Background())
	defer cancel()
	var started atomic.Int32
	var group sync.WaitGroup
	for range 16 {
		group.Go(func() {
			if err := runtime.Start(parent); err == nil {
				started.Add(1)
			} else if !errors.Is(err, ErrConflict) {
				t.Error("concurrent start returned an unexpected result")
			}
		})
	}
	group.Wait()
	if started.Load() != 1 {
		t.Fatal("runtime started more than one lifecycle")
	}
	cancel()
	awaitRuntime(t, "parent cancellation did not stop runtime loops", func() bool { return runtime.Snapshot().State == "stopped" })
	if runtime.Stop(ctx()) != nil {
		t.Fatal("stopped runtime could not be stopped again")
	}
	other := runtimeForTest(t, s, subject, map[string]*controlclient.Client{seed.TargetGatewayID: g.client})
	if other.Stop(ctx()) != nil || !errors.Is(other.Start(ctx()), ErrConflict) {
		t.Fatal("stop before start did not close the lifecycle")
	}
}

func TestRuntimeStopCancelsInflightAndKeepsUnknownFundsFrozen(t *testing.T) {
	s, subject := recoveryFixture(t)
	i := reserve(t, s, subject, "runtime-inflight", 1, "1")
	entered, interrupted := make(chan struct{}), make(chan struct{})
	var once sync.Once
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == settlementPath {
			w.Header().Set("Content-Type", "application/json")
			_, _ = io.WriteString(w, `{"data":[],"monetary_ledger":"sub2api"}`)
			return
		}
		if r.URL.Path != admissionPath {
			t.Error("runtime changed the original operation")
		}
		_, _ = io.Copy(io.Discard, io.LimitReader(r.Body, 3<<20))
		once.Do(func() { close(entered) })
		<-r.Context().Done()
		close(interrupted)
	}))
	t.Cleanup(server.Close)
	client := protocolClient(t, server, i.TargetGatewayID)
	runtime := runtimeForTest(t, s, subject, map[string]*controlclient.Client{i.TargetGatewayID: client})
	if runtime.Start(ctx()) != nil {
		t.Fatal("runtime did not start")
	}
	select {
	case <-entered:
	case <-time.After(5 * time.Second):
		t.Fatal("runtime never dispatched its persistent intent")
	}
	tx, err := testDB.Begin()
	if err != nil {
		t.Fatal("in-flight lock probe could not start")
	}
	_, err = tx.Exec(`SELECT id FROM users WHERE id=$1 FOR NO KEY UPDATE NOWAIT`, subject.userID)
	_ = tx.Rollback()
	if err != nil {
		t.Fatal("runtime held the user lock while its HTTP call was blocked")
	}
	stop, cancel := context.WithTimeout(ctx(), 2*time.Second)
	defer cancel()
	if runtime.Stop(stop) != nil {
		t.Fatal("stop did not cancel the active HTTP request")
	}
	select {
	case <-interrupted:
	case <-time.After(time.Second):
		t.Fatal("active HTTP request remained open after stop")
	}
	loaded, err := s.Load(ctx(), subject, i.ID)
	if err != nil || loaded.State != "dispatching" || loaded.DispatchCount != 1 || loaded.ProviderRequestID != i.ProviderRequestID || loaded.Terminal() {
		t.Fatal("interrupted request lost its durable identity or was treated as unsent", err)
	}
	balances(t, subject, "9.00000000", "1.00000000", "0.00000000")
	leases, err := s.ClaimRecovery(ctx(), testTarget(t), subject.instance, "new-process", 1, time.Minute)
	if err != nil || len(leases) != 0 {
		t.Fatal("stop improperly cleared the still-valid recovery lease")
	}
	// Controlled test-clock expiry models the wait required of a successor.
	runSQL(t, `UPDATE render_control_recovery SET lease_until=clock_timestamp()-INTERVAL '1 second',next_attempt_at=clock_timestamp()-INTERVAL '1 second' WHERE intent_id=$1`, i.ID)
	g := coordinatorGateway(t, loaded)
	g.configure(func(g *recoveryGateway) { g.state = "pending" })
	successor := runtimeForTest(t, s, subject, map[string]*controlclient.Client{i.TargetGatewayID: g.client})
	if successor.Start(ctx()) != nil {
		t.Fatal("successor runtime did not start")
	}
	awaitRuntime(t, "successor did not recover the original accepted request", func() bool { return g.count(admissionPath+"/adm_"+i.ID[3:]+"/confirm") == 1 })
	if g.count(admissionPath) != 0 {
		t.Fatal("successor resubmitted an accepted request")
	}
}

func TestRuntimeGatewaysProgressIndependentlyUnderFailure(t *testing.T) {
	s, subject := recoveryFixture(t)
	i := reserve(t, s, subject, "failed-target", 1, "1")
	other, _ := NewTarget("gateway_" + strings.Repeat("e", 32))
	second, err := s.Reserve(ctx(), subject, other, "healthy-target", imageRequest(t, "healthy target", 1), Quote{"prices-v1", money(t, "1"), 1})
	if err != nil {
		t.Fatal(err)
	}
	var failedCalls atomic.Int32
	failed := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		failedCalls.Add(1)
		gatewayTestError(w, http.StatusServiceUnavailable)
	}))
	t.Cleanup(failed.Close)
	badClient := protocolClient(t, failed, i.TargetGatewayID)
	healthy := coordinatorGateway(t, second)
	runtime := runtimeForTest(t, s, subject, map[string]*controlclient.Client{i.TargetGatewayID: badClient, other.gatewayID: healthy.client})
	if runtime.Start(ctx()) != nil {
		t.Fatal("multi-target runtime did not start")
	}
	awaitRuntime(t, "one gateway failure blocked another target", func() bool { return healthy.count(admissionPath+"/adm_"+second.ID[3:]+"/confirm") == 1 })
	snapshot := runtime.Snapshot()
	if snapshot.RecoveryFailures < 1 || snapshot.SettlementFailures < 1 || failedCalls.Load() > 200 {
		t.Fatal("gateway errors were not bounded and isolated")
	}
	loaded, err := s.Load(ctx(), subject, i.ID)
	if err != nil || loaded.Terminal() || loaded.State != "dispatching" {
		t.Fatal("unreachable gateway caused release or a fabricated terminal")
	}
	balances(t, subject, "8.00000000", "2.00000000", "0.00000000")
}

func TestRuntimeRejectsUnsafeBudgetsAndClients(t *testing.T) {
	s, subject := recoveryFixture(t)
	seed := Intent{Instance: subject.instance, UserID: subject.userID, KeyID: subject.keyID, TrustedIP: subject.ip, TargetGatewayID: testTarget(t).gatewayID}
	g := coordinatorGateway(t, seed)
	for name, change := range map[string]func(*RuntimeConfig){
		"workers":          func(c *RuntimeConfig) { c.Workers = 17 },
		"negative_workers": func(c *RuntimeConfig) { c.Workers = -1 },
		"poll":             func(c *RuntimeConfig) { c.PollInterval = time.Nanosecond },
		"settlement":       func(c *RuntimeConfig) { c.SettlementInterval = time.Hour },
		"lease":            func(c *RuntimeConfig) { c.Lease = 20 * time.Second },
		"owner":            func(c *RuntimeConfig) { c.Owner = strings.Repeat("a", 49) },
	} {
		t.Run(name, func(t *testing.T) {
			config := RuntimeConfig{Instance: subject.instance, Owner: "safe-owner"}
			change(&config)
			if _, err := NewRuntime(s, map[string]*controlclient.Client{seed.TargetGatewayID: g.client}, config); !errors.Is(err, ErrInvalid) {
				t.Fatal("unsafe runtime budget accepted")
			}
		})
	}
	for _, clients := range []map[string]*controlclient.Client{nil, {seed.TargetGatewayID: nil}, {"gateway_" + strings.Repeat("e", 32): g.client}} {
		if _, err := NewRuntime(s, clients, RuntimeConfig{Instance: subject.instance, Owner: "safe-owner"}); !errors.Is(err, ErrInvalid) {
			t.Fatal("invalid runtime client map accepted")
		}
	}
}
