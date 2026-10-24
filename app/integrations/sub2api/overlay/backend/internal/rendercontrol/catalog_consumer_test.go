package rendercontrol

import (
	"context"
	"crypto/x509"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/Wei-Shaw/sub2api/internal/config"
	"github.com/Wei-Shaw/sub2api/internal/rendercontrol/controlclient"
)

type catalogGatewayFixture struct {
	mu          sync.Mutex
	state       controlclient.GatewayCatalogState
	dropReceipt bool
	receipts    []controlclient.GatewayCatalogReceipt
	f           *publicFixture
	client      *controlclient.Client
}

func providerCatalogRelease(t *testing.T, f *publicFixture, revision int64, price string, empty bool) controlclient.GatewayCatalogRelease {
	t.Helper()
	defaults := map[string]any{"size": "1024x1024", "quality": "standard"}
	models := []controlclient.GatewayCatalogRule{}
	if !empty {
		models = append(models, controlclient.GatewayCatalogRule{Model: "studio-image-v1", TaskType: "text_to_image", UsageAccountID: strconv.FormatInt(f.config.Models[0].UsageAccountID, 10), GroupIDs: []string{strconv.FormatInt(f.groupID, 10)}, PriceVersion: fmt.Sprintf("prices-v%d", revision+1), UnitPrice: price, Defaults: defaults, ModelBindings: []controlclient.GatewayCatalogBinding{{ModelID: "mdl_" + strings.Repeat("b", 32), Revision: revision, TenantID: "ten_" + strings.Repeat("c", 32), ProjectID: "prj_" + strings.Repeat("d", 32), Alias: "studio-image-v1", TaskType: "text_to_image", ReleaseID: "wfr_" + strings.Repeat("e", 32), ReleaseDigest: strings.Repeat("f", 64), ChannelIDs: []string{"ch_" + strings.Repeat("1", 32)}, Defaults: defaults}}})
	}
	configuration := controlclient.GatewayCatalogConfiguration{FormatVersion: "gateway-catalog-v1", GatewayID: testTarget(t).gatewayID, InstanceID: f.subject.instance, Revision: revision, Models: models}
	raw, err := json.Marshal(configuration)
	if err != nil {
		t.Fatal(err)
	}
	return controlclient.GatewayCatalogRelease{GatewayID: configuration.GatewayID, InstanceID: configuration.InstanceID, Revision: revision, Digest: digest(raw), Action: "publish", CreatedAt: 1700000000 + revision, ConfigurationJSON: string(raw)}
}

func catalogProviderSetup(t *testing.T) (*publicFixture, *catalogGatewayFixture, *catalogConsumer) {
	t.Helper()
	f := publicSetup(t)
	g := &catalogGatewayFixture{f: f, state: controlclient.GatewayCatalogState{GatewayID: testTarget(t).gatewayID, Status: "legacy"}}
	server := httptest.NewTLSServer(http.HandlerFunc(g.serve))
	t.Cleanup(server.Close)
	roots := x509.NewCertPool()
	roots.AddCert(server.Certificate())
	credential, _ := controlclient.ParseCredential(testTarget(t).gatewayID + "." + strings.Repeat("s", 43))
	client, err := controlclient.New(controlclient.Config{GatewayID: testTarget(t).gatewayID, Origin: server.URL, Credential: credential, RootCAs: roots, MaxResponseBytes: 4 << 20})
	if err != nil {
		t.Fatal(err)
	}
	g.client = client
	t.Cleanup(client.Close)
	f.config.Clients = map[string]*controlclient.Client{testTarget(t).gatewayID: client}
	f.start(t)
	if err := f.handler.initializeCatalogs(ctx()); err != nil {
		t.Fatal("catalog initialization failed", err)
	}
	return f, g, &catalogConsumer{handler: f.handler}
}

func (g *catalogGatewayFixture) desired(release controlclient.GatewayCatalogRelease) {
	g.mu.Lock()
	defer g.mu.Unlock()
	instance := release.InstanceID
	g.state.InstanceID = &instance
	g.state.Revision = release.Revision
	g.state.Desired = &release
	g.state.LastReceipt = nil
	g.state.Status = "pending"
}

func (g *catalogGatewayFixture) serve(w http.ResponseWriter, r *http.Request) {
	if !strings.HasPrefix(r.URL.Path, "/gateway/v1/catalog") {
		g.f.g.serve(w, r)
		return
	}
	g.mu.Lock()
	defer g.mu.Unlock()
	if r.Header.Get("Authorization") != "Bearer "+g.state.GatewayID+"."+strings.Repeat("s", 43) || r.Header.Get("X-External-Tenant-Ref") != "" || r.Header.Get("X-External-Key-Ref") != "" || r.Header.Get("X-Client-IP") != "" {
		g.f.g.t.Error("catalog acquired customer authority or lost service credential")
	}
	if r.Method == http.MethodGet {
		if r.URL.Query().Get("instance_id") != g.f.subject.instance {
			g.f.g.t.Error("catalog instance changed")
		}
		_ = json.NewEncoder(w).Encode(g.state)
		return
	}
	var receipt controlclient.GatewayCatalogReceipt
	if json.NewDecoder(r.Body).Decode(&receipt) != nil || g.state.Desired == nil || receipt.Digest != g.state.Desired.Digest || receipt.Revision != g.state.Desired.Revision || receipt.InstanceID != g.f.subject.instance {
		gatewayTestError(w, 409)
		return
	}
	g.receipts = append(g.receipts, receipt)
	view := controlclient.GatewayCatalogReceiptView{InstanceID: receipt.InstanceID, Revision: receipt.Revision, Digest: receipt.Digest, Status: receipt.Status, ErrorCode: receipt.ErrorCode, ObservedAt: 1700000100}
	g.state.LastReceipt = &view
	activated := receipt.Status == "applied"
	if activated {
		g.state.Confirmed = g.state.Desired
		g.state.Managed = true
		g.state.Status = "confirmed"
	} else {
		g.state.Status = "rejected"
	}
	if g.dropReceipt {
		g.dropReceipt = false
		connection, _, err := w.(http.Hijacker).Hijack()
		if err == nil {
			_ = connection.Close()
		}
		return
	}
	_ = json.NewEncoder(w).Encode(controlclient.GatewayCatalogReceiptResult{Receipt: view, Activated: activated, State: g.state})
}

func stepCatalog(t *testing.T, c *catalogConsumer) {
	t.Helper()
	deadline, cancel := context.WithTimeout(ctx(), 5*time.Second)
	defer cancel()
	if err := c.step(deadline, testTarget(t).gatewayID); err != nil {
		t.Fatal("catalog step failed", err)
	}
}

func TestProviderCatalogActivationSnapshotsWithdrawalAndRestart(t *testing.T) {
	f, g, c := catalogProviderSetup(t)
	legacy := f.submit(t, "legacy-before-managed")
	if legacy.Catalog != nil {
		t.Fatal("legacy intent acquired a future stamp")
	}
	g.desired(providerCatalogRelease(t, f, 1, "2.00000000", false))
	stepCatalog(t, c)
	i := f.submit(t, "managed-original")
	if i.Catalog == nil || i.Catalog.Revision != 1 || i.PriceVersion != "prices-v2" || i.HoldAmount != money(t, "2") {
		t.Fatal("catalog and funds were not frozen together")
	}
	g.desired(providerCatalogRelease(t, f, 2, "3.00000000", false))
	stepCatalog(t, c)
	replayed := f.submit(t, "managed-original")
	if replayed.ID != i.ID || replayed.Catalog.Revision != 1 || replayed.HoldAmount != money(t, "2") {
		t.Fatal("new catalog rewrote an old request")
	}
	next := f.submit(t, "managed-new-version")
	if next.Catalog == nil || next.Catalog.Revision != 2 || next.HoldAmount != money(t, "3") {
		t.Fatal("new request did not use confirmed configuration")
	}
	g.desired(providerCatalogRelease(t, f, 3, "1", true))
	stepCatalog(t, c)
	status, _, _ := f.call(t, http.MethodPost, "/v1/jobs", publicImageBody, map[string]string{"Idempotency-Key": "withdrawn-new"})
	if status != 404 {
		t.Fatal("empty catalog admitted a new request", status)
	}
	if f.submit(t, "managed-original").ID != i.ID || f.submit(t, "legacy-before-managed").ID != legacy.ID {
		t.Fatal("withdrawal orphaned original idempotency")
	}
	for _, old := range []Intent{i, legacy} {
		status, _, _ := f.call(t, http.MethodGet, "/v1/jobs/"+old.ID, "", nil)
		if status != 200 {
			t.Fatal("withdrawal orphaned old task reads", status)
		}
	}
	status, _, _ = f.call(t, http.MethodGet, "/v1/jobs", "", nil)
	if status != 200 {
		t.Fatal("withdrawn task collection failed", status)
	}
	host := &hostedHandler{public: f.handler}
	if !host.ownsModel(ctx(), "studio-image-v1") {
		t.Fatal("withdrawn model can fall into stock routing")
	}
	// Restart with no bootstrap rules, recovering managed withdrawal and names.
	f.config.Models = nil
	f.start(t)
	if err := f.handler.initializeCatalogs(ctx()); err != nil {
		t.Fatal(err)
	}
	if len(f.handler.modelSnapshot(ctx()).models) != 0 || !(&hostedHandler{public: f.handler}).ownsModel(ctx(), "studio-image-v1") {
		t.Fatal("restart restored bootstrap rules or forgot ownership")
	}
	if f.submit(t, "managed-original").ID != i.ID {
		t.Fatal("restart lost original request")
	}
	if _, err := testDB.Exec(`UPDATE render_control_intents SET catalog_revision=2 WHERE id=$1`, i.ID); err == nil {
		t.Fatal("intent stamp was mutable")
	}
	if _, err := testDB.Exec(`UPDATE render_control_catalog_releases SET digest=repeat('0',64) WHERE instance=$1`, f.subject.instance); err == nil {
		t.Fatal("catalog history was mutable")
	}
	if _, err := testDB.Exec(`UPDATE render_control_catalogs SET confirmed_revision=1 WHERE instance=$1`, f.subject.instance); err == nil {
		t.Fatal("local confirmation moved backwards")
	}
	// NULL inside a PostgreSQL CHECK cannot silently grant an invalid archive
	// authority; these are database invariants, including non-HTTP writers.
	for _, statement := range []string{
		`UPDATE render_control_model_owners SET read_revision=4,task_type=NULL,group_ids=ARRAY[1::bigint] WHERE instance=$1`,
		`UPDATE render_control_model_owners SET read_revision=4,task_type='text_to_image',group_ids=ARRAY[NULL::bigint] WHERE instance=$1`,
	} {
		if _, err := testDB.Exec(statement, f.subject.instance); err == nil {
			t.Fatal("incomplete historical read authority was accepted")
		}
	}
}

func TestProviderCatalogOwnershipAndTaskCannotBeReassigned(t *testing.T) {
	f, _, _ := catalogProviderSetup(t)
	release := providerCatalogRelease(t, f, 1, "2", false)
	rules, err := f.handler.rulesForCatalog(release)
	if err != nil || f.handler.persistCatalog(ctx(), release, rules, true) != "" {
		t.Fatal("candidate could not be persisted")
	}
	// Ownership is reserved before the applied receipt, even after a crash.
	if err = f.handler.refreshCatalogs(ctx()); err != nil {
		t.Fatal(err)
	}
	other := "gateway_" + strings.Repeat("2", 32)
	roots := x509.NewCertPool()
	roots.AddCert(f.g.server.Certificate())
	credential, _ := controlclient.ParseCredential(other + "." + strings.Repeat("s", 43))
	otherClient, err := controlclient.New(controlclient.Config{GatewayID: other, Origin: f.g.server.URL, Credential: credential, RootCAs: roots})
	if err != nil {
		t.Fatal("second target fixture invalid")
	}
	t.Cleanup(otherClient.Close)
	f.handler.clients[other] = otherClient
	var config controlclient.GatewayCatalogConfiguration
	if json.Unmarshal([]byte(release.ConfigurationJSON), &config) != nil {
		t.Fatal("fixture configuration invalid")
	}
	config.GatewayID = other
	raw, _ := json.Marshal(config)
	foreign := release
	foreign.GatewayID, foreign.ConfigurationJSON, foreign.Digest = other, string(raw), digest(raw)
	foreignRules, err := f.handler.rulesForCatalog(foreign)
	if err != nil {
		t.Fatal("foreign candidate fixture was invalid")
	}
	if f.handler.persistCatalog(ctx(), foreign, foreignRules, true) != "unsupported_configuration" {
		t.Fatal("a different target acquired the reserved alias")
	}
	if countRows(t, `SELECT count(*) FROM render_control_catalog_releases WHERE instance=$1 AND target_gateway_id=$2`, f.subject.instance, other) != 0 {
		t.Fatal("ownership rejection left a partial publication")
	}
	if err = f.handler.confirmCatalog(ctx(), release); err != nil {
		t.Fatal(err)
	}
	changed := providerCatalogRelease(t, f, 2, "3", false)
	if json.Unmarshal([]byte(changed.ConfigurationJSON), &config) != nil {
		t.Fatal("fixture configuration invalid")
	}
	config.Models[0].TaskType = "image_to_image"
	config.Models[0].ModelBindings[0].TaskType = "image_to_image"
	raw, _ = json.Marshal(config)
	changed.ConfigurationJSON, changed.Digest = string(raw), digest(raw)
	changedRules, err := f.handler.rulesForCatalog(changed)
	if err != nil || f.handler.persistCatalog(ctx(), changed, changedRules, true) != "unsupported_configuration" {
		t.Fatal("changing task type orphaned historical reads")
	}
	// A delayed confirmation from another process cannot move the pointer back.
	newer := providerCatalogRelease(t, f, 3, "3", false)
	newRules, err := f.handler.rulesForCatalog(newer)
	if err != nil || f.handler.persistCatalog(ctx(), newer, newRules, true) != "" || f.handler.confirmCatalog(ctx(), newer) != nil || f.handler.confirmCatalog(ctx(), release) != nil || f.handler.refreshCatalogs(ctx()) != nil {
		t.Fatal("monotonic confirmation failed")
	}
	if f.handler.modelSnapshot(ctx()).models["studio-image-v1"].model.Catalog.Revision != 3 {
		t.Fatal("late confirmation restored an old quote")
	}
}

func TestProviderCatalogCoordinatorKeepsStampAfterWithdrawalAndResponseLoss(t *testing.T) {
	f, g, c := catalogProviderSetup(t)
	g.desired(providerCatalogRelease(t, f, 1, "2", false))
	stepCatalog(t, c)
	i := f.submit(t, "original-managed-dispatch")
	f.g.configure(func(g *recoveryGateway) { g.dropAfter[admissionPath] = 1 })
	coordinator, err := NewCoordinator(f.s, g.client, CoordinatorConfig{Target: testTarget(t), Instance: f.subject.instance, Owner: "catalog-recovery", CallTimeout: 2 * time.Second})
	if err != nil {
		t.Fatal(err)
	}
	stepCoordinator(t, coordinator, i, true)
	g.desired(providerCatalogRelease(t, f, 2, "1", true))
	stepCatalog(t, c)
	stepCoordinator(t, coordinator, i, false)
	stepCoordinator(t, coordinator, i, false)
	recovered, err := f.s.Load(ctx(), f.subject, i.ID)
	if err != nil || recovered.State != "admitted" || recovered.Catalog == nil || *recovered.Catalog != *i.Catalog || f.g.count(admissionPath) != 1 || f.g.count(admissionPath+"/adm_"+i.ID[3:]+"/confirm") != 1 {
		t.Fatal("withdrawal or lost response rewrote the original admission")
	}
}

func TestProviderCatalogLostReceiptRecoversFromRemoteConfirmation(t *testing.T) {
	f, g, c := catalogProviderSetup(t)
	release := providerCatalogRelease(t, f, 1, "2", false)
	g.desired(release)
	g.dropReceipt = true
	if err := c.step(ctx(), testTarget(t).gatewayID); err == nil {
		t.Fatal("lost receipt response was treated as confirmed")
	}
	if len(f.handler.modelSnapshot(ctx()).models) != 1 || f.handler.modelSnapshot(ctx()).models["studio-image-v1"].model.Catalog != nil {
		t.Fatal("candidate was used without activated response")
	}
	if countRows(t, `SELECT count(*) FROM render_control_catalog_releases WHERE instance=$1`, f.subject.instance) != 1 {
		t.Fatal("lost receipt had no durable candidate")
	}
	f.start(t)
	if err := f.handler.initializeCatalogs(ctx()); err != nil {
		t.Fatal(err)
	}
	c.handler = f.handler
	stepCatalog(t, c)
	i := f.submit(t, "recovered-remote-confirmation")
	if i.Catalog == nil || i.Catalog.Digest != release.Digest {
		t.Fatal("remote confirmation did not restore local snapshot")
	}
	if len(g.receipts) != 1 {
		t.Fatal("recovery created a new publication instead of recovering confirmation")
	}
}

func TestProviderCatalogRejectsUnavailableDependenciesAndKeepsPrevious(t *testing.T) {
	f, g, c := catalogProviderSetup(t)
	g.desired(providerCatalogRelease(t, f, 1, "2", false))
	stepCatalog(t, c)
	runSQL(t, `UPDATE accounts SET schedulable=TRUE WHERE id=$1`, f.config.Models[0].UsageAccountID)
	g.desired(providerCatalogRelease(t, f, 2, "3", false))
	stepCatalog(t, c)
	if g.receipts[len(g.receipts)-1].Status != "rejected" || g.receipts[len(g.receipts)-1].ErrorCode == nil || *g.receipts[len(g.receipts)-1].ErrorCode != "account_unavailable" {
		t.Fatal("invalid account was not rejected with fixed code")
	}
	if f.handler.modelSnapshot(ctx()).models["studio-image-v1"].model.Catalog.Revision != 1 {
		t.Fatal("rejection removed previous configuration")
	}
	// A broken old account must not prevent the operator withdrawing all rules.
	g.desired(providerCatalogRelease(t, f, 3, "1", true))
	stepCatalog(t, c)
	if len(f.handler.modelSnapshot(ctx()).models) != 0 {
		t.Fatal("unavailable old account prevented withdrawal")
	}
}

func TestProviderCatalogStaleRuleCannotReserveFunds(t *testing.T) {
	f, g, c := catalogProviderSetup(t)
	g.desired(providerCatalogRelease(t, f, 1, "2", false))
	stepCatalog(t, c)
	oldRule := f.handler.modelSnapshot(ctx()).models["studio-image-v1"].model
	g.desired(providerCatalogRelease(t, f, 2, "3", false))
	stepCatalog(t, c)
	request := imageRequest(t, "synthetic-private-prompt", 1)
	request.model = oldRule.Model
	request.catalog = oldRule.Catalog
	request.usageAccountID = oldRule.UsageAccountID
	before := providerCatalogBalances(t, f.subject.userID)
	_, err := f.s.Reserve(ctx(), f.subject, oldRule.Target, "stale-snapshot", request, Quote{oldRule.PriceVersion, oldRule.UnitPrice, request.units})
	if !errors.Is(err, ErrConflict) {
		t.Fatal("stale snapshot reserved funds", err)
	}
	if after := providerCatalogBalances(t, f.subject.userID); after != before {
		t.Fatal("rejected snapshot changed balance")
	}
	request.catalog = nil
	if _, err = f.s.Reserve(ctx(), f.subject, oldRule.Target, "missing-stamp", request, Quote{oldRule.PriceVersion, oldRule.UnitPrice, request.units}); !errors.Is(err, ErrConflict) {
		t.Fatal("managed gateway accepted an unstamped reservation")
	}
}

func providerCatalogBalances(t *testing.T, user int64) [2]string {
	t.Helper()
	var result [2]string
	if testDB.QueryRow(`SELECT balance::text,frozen_balance::text FROM users WHERE id=$1`, user).Scan(&result[0], &result[1]) != nil {
		t.Fatal("balance verification failed")
	}
	return result
}

func TestHostingEmptyBootstrapSupportsCatalogOnboardingAndCleanup(t *testing.T) {
	f := publicSetup(t)
	path, file := hostingConfiguration(t, f)
	file.Models = nil
	writeHostingConfiguration(t, path, file)
	t.Setenv(HostingConfigEnvironment, path)
	server := &http.Server{Handler: http.NewServeMux()}
	cleanup, err := InstallFromEnvironment(testDB, f.auth, server, config.RunModeStandard, &effectCacheFixture{})
	if err != nil || cleanup == nil {
		t.Fatal("empty bootstrap could not start catalog consumer", err)
	}
	cleanup()
	cleanup()
	if !server.Handler.(*hostedHandler).stopping.Load() {
		t.Fatal("catalog hosting cleanup did not stop routing")
	}
}
