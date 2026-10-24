package rendercontrol

import (
	"bytes"
	"context"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/Wei-Shaw/sub2api/internal/rendercontrol/controlclient"
)

type recoveryGateway struct {
	mu                   sync.Mutex
	t                    *testing.T
	i                    Intent
	server               *httptest.Server
	client               *controlclient.Client
	state                string
	closed               bool
	calls                map[string]int
	dropAfter            map[string]int
	dropBeforeAdmission  bool
	denyAdmission        bool
	denyConfirm          bool
	confirmOnClose       bool
	expireLeaseOn        string
	requests             []string
	outcome              string
	ackFailures          int
	acked                bool
	ledgerRefs           []string
	foreignEvents        []controlclient.Settlement
	jobModelOverride     string
	jobDenied            bool
	cancelDenied         bool
	businessIP           string
	policyScopes         []string
	jobArtifacts         []controlclient.Artifact
	contentData          []byte
	contentType          string
	contentDenied        bool
	contentFailure       int
	contentTruncated     bool
	assets               map[string]controlclient.UploadedAsset
	assetData            map[string][]byte
	assetDeleted         map[string]bool
	assetDenied          bool
	dropAssetAfter       bool
	corruptAssetResponse bool
	catalogStatus        int
	catalogRaw           []byte
	catalogModels        []controlclient.PublishedModel
	catalogCapabilities  []controlclient.PublishedCapability
}

const admissionPath = "/gateway/v1/admissions"
const lookupPath = "/gateway/v1/admission-requests/lookup"
const closePath = "/gateway/v1/admission-requests/close"
const settlementPath = "/gateway/v1/settlements"

func coordinatorGateway(t *testing.T, i Intent) *recoveryGateway {
	t.Helper()
	g := &recoveryGateway{t: t, i: i, state: "unknown", calls: map[string]int{}, dropAfter: map[string]int{}}
	g.server = httptest.NewTLSServer(http.HandlerFunc(g.serve))
	t.Cleanup(g.server.Close)
	credential, _ := controlclient.ParseCredential(i.TargetGatewayID + "." + strings.Repeat("s", 43))
	roots := x509.NewCertPool()
	roots.AddCert(g.server.Certificate())
	var err error
	g.client, err = controlclient.New(controlclient.Config{GatewayID: i.TargetGatewayID, Origin: g.server.URL, Credential: credential, RootCAs: roots})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(g.client.Close)
	return g
}

func (g *recoveryGateway) configure(change func(*recoveryGateway)) {
	g.mu.Lock()
	defer g.mu.Unlock()
	change(g)
}

func (g *recoveryGateway) count(path string) int {
	g.mu.Lock()
	defer g.mu.Unlock()
	return g.calls[path]
}

func (g *recoveryGateway) admission() controlclient.Admission {
	status := "admission_pending"
	switch g.state {
	case "confirmed":
		status = "queued"
	case "aborted":
		status = "cancelled"
	}
	return controlclient.Admission{ID: "adm_" + g.i.ID[3:], JobID: "job_" + g.i.ID[3:], ProviderRequestID: g.i.ProviderRequestID, ExternalTaskRef: g.i.ID, Status: g.state, JobStatus: status, ExpiresAt: time.Now().Add(time.Minute).Unix()}
}

func (g *recoveryGateway) terminalEvent() controlclient.Settlement {
	count := int64(0)
	if g.outcome == "succeeded" {
		count = 1
	}
	return controlclient.Settlement{ID: "evt_" + g.i.ID[3:], JobID: "job_" + g.i.ID[3:], ExternalTaskRef: g.i.ID, AuthorizationRef: g.i.AuthorizationRef, PriceVersion: g.i.PriceVersion, Outcome: g.outcome, ImageCount: count}
}

func dropProtocolResponse(t *testing.T, w http.ResponseWriter) {
	t.Helper()
	connection, _, err := w.(http.Hijacker).Hijack()
	if err != nil {
		t.Error("could not inject protocol response loss")
		return
	}
	_ = connection.Close()
}

func gatewayTestError(w http.ResponseWriter, status int) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_, _ = w.Write([]byte(`{"error":{"code":"policy_denied","message":"synthetic error"}}`))
}

func (g *recoveryGateway) serve(w http.ResponseWriter, r *http.Request) {
	g.mu.Lock()
	defer g.mu.Unlock()
	path := r.URL.Path
	g.calls[path]++
	if r.Header.Get("Authorization") != "Bearer "+g.i.TargetGatewayID+"."+strings.Repeat("s", 43) {
		g.t.Error("coordinator changed its authenticated service")
	}
	if path == "/gateway/v1/catalog" && r.Method == http.MethodGet {
		if r.Header.Get("X-External-Tenant-Ref") != "" || r.Header.Get("X-External-Key-Ref") != "" || r.Header.Get("X-Client-IP") != "" || r.URL.Query().Get("instance_id") != g.i.Instance {
			g.t.Error("catalog did not use isolated service identity")
		}
		_ = json.NewEncoder(w).Encode(controlclient.GatewayCatalogState{GatewayID: g.i.TargetGatewayID, Status: "legacy"})
		return
	}
	serviceOnly := strings.HasPrefix(path, "/gateway/v1/admission-requests/") || strings.HasPrefix(path, settlementPath)
	identity, _ := g.i.RequestIdentity()
	if serviceOnly {
		if r.Header.Get("X-External-Tenant-Ref") != "" || r.Header.Get("X-External-Key-Ref") != "" || r.Header.Get("X-Client-IP") != "" {
			g.t.Error("reconciliation acquired customer authority")
		}
	} else {
		expectedIP := g.i.TrustedIP.String()
		if g.businessIP != "" && strings.HasPrefix(path, "/gateway/v1/jobs/") {
			expectedIP = g.businessIP
		}
		current, _ := controlclient.NewSubject(g.i.Instance, g.i.UserID, g.i.KeyID, g.i.TrustedIP)
		if r.Header.Get("X-External-Tenant-Ref") != current.TenantRef() || r.Header.Get("X-External-Key-Ref") != current.KeyRef() || r.Header.Get("X-Client-IP") != expectedIP {
			g.t.Error("HTTP did not use the authenticated customer subject")
		}
	}
	// An independent settlement loop may briefly own this user's lock. Wait
	// within a strict bound: a lock held by the HTTP caller itself cannot be
	// released until this handler returns and will therefore fail this probe.
	tx, err := testDB.Begin()
	if err != nil {
		g.t.Error("HTTP lock probe failed")
	} else {
		if _, err = tx.Exec(`SET LOCAL lock_timeout='500ms'`); err != nil {
			g.t.Error("HTTP lock probe budget failed")
		}
		if _, err = tx.Exec(`SELECT id FROM users WHERE id=$1 FOR NO KEY UPDATE`, g.i.UserID); err != nil {
			g.t.Error("ledger transaction remained open across HTTP")
		}
		_ = tx.Rollback()
	}
	if g.expireLeaseOn == path {
		g.expireLeaseOn = ""
		if _, err := testDB.Exec(`UPDATE render_control_recovery SET lease_until=clock_timestamp()-INTERVAL '1 second' WHERE intent_id=$1`, g.i.ID); err != nil {
			g.t.Error("lease expiration injection failed")
		}
	}
	var result any
	switch {
	case path == "/gateway/v1/models" || path == "/gateway/v1/capabilities":
		scope := "models:read"
		if path == "/gateway/v1/capabilities" {
			scope = "capabilities:read"
		}
		if g.catalogStatus != 0 {
			gatewayTestError(w, g.catalogStatus)
			return
		}
		if g.policyScopes != nil && !(controlclient.DeliveryPolicy{Scopes: g.policyScopes}).Allows(scope) {
			gatewayTestError(w, 403)
			return
		}
		if g.catalogRaw != nil {
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write(g.catalogRaw)
			return
		}
		if path == "/gateway/v1/models" {
			models := g.catalogModels
			if models == nil {
				models = []controlclient.PublishedModel{syntheticCapability("studio-image-v1", "text_to_image").PublishedModel}
			}
			result = controlclient.ModelCatalog{Object: "list", Data: models}
		} else {
			models := g.catalogCapabilities
			if models == nil {
				models = []controlclient.PublishedCapability{syntheticCapability("studio-image-v1", "text_to_image")}
			}
			result = controlclient.CapabilityCatalog{Object: "list", Data: models}
		}
	case path == "/gateway/v1/access-policy":
		scopes := g.policyScopes
		if scopes == nil {
			scopes = []string{"images:create", "videos:create", "jobs:create", "jobs:read", "artifacts:read", "content:read", "content:download", "assets:upload", "assets:read", "content:sensitive", "content:delete"}
		}
		result = controlclient.DeliveryPolicy{Scopes: scopes}
	case path == "/gateway/v1/assets" || strings.HasPrefix(path, "/gateway/v1/assets/"):
		g.serveAssets(w, r)
		return
	case strings.HasPrefix(path, "/gateway/v1/content/"):
		g.serveContent(w, r)
		return
	case path == admissionPath:
		var body controlclient.AdmissionRequest
		if json.NewDecoder(r.Body).Decode(&body) != nil {
			g.t.Error("admission body invalid")
		}
		var dispatches int
		if testDB.QueryRow(`SELECT dispatch_count FROM render_control_intents WHERE id=$1`, g.i.ID).Scan(&dispatches) != nil || dispatches < 1 {
			g.t.Error("HTTP admission preceded durable dispatch")
		}
		canonical, err := NewRequest(g.i.Operation, body.Request)
		if err != nil || canonical.hash != g.i.RequestHash || body.ProviderRequestID != g.i.ProviderRequestID || body.ExternalTaskRef != g.i.ID || body.AuthorizationRef != g.i.AuthorizationRef || body.PriceVersion != g.i.PriceVersion {
			g.t.Error("recovery admission changed frozen identity or request")
		}
		if (body.Catalog == nil) != (g.i.Catalog == nil) || body.Catalog != nil && *body.Catalog != *g.i.Catalog {
			g.t.Error("admission changed the original catalog stamp")
		}
		g.requests = append(g.requests, canonical.hash)
		if g.dropBeforeAdmission {
			g.dropBeforeAdmission = false
			dropProtocolResponse(g.t, w)
			return
		}
		if g.denyAdmission {
			gatewayTestError(w, http.StatusForbidden)
			return
		}
		if g.closed {
			gatewayTestError(w, http.StatusConflict)
			return
		}
		if g.state == "unknown" {
			g.state = "pending"
		}
		result = g.admission()
	case path == lookupPath || path == closePath:
		var body controlclient.RequestIdentity
		if json.NewDecoder(r.Body).Decode(&body) != nil {
			g.t.Error("recovery identity invalid")
		}
		actual, _ := json.Marshal(body)
		expected, _ := json.Marshal(identity)
		if !bytes.Equal(actual, expected) {
			g.t.Error("recovery identity changed")
		}
		if path == closePath {
			if g.state == "unknown" {
				g.closed = true
			} else if g.state == "pending" {
				g.state = "aborted"
				g.outcome = "cancelled"
				if g.confirmOnClose {
					g.state, g.outcome = "confirmed", ""
				}
			}
		}
		resolution := controlclient.RequestResolution{RequestIdentity: identity, GatewayID: g.i.TargetGatewayID, Status: "unknown"}
		if g.closed {
			id := closureID(g.i)
			resolution.Status, resolution.ClosureID = "closed", &id
		} else if g.state != "unknown" {
			admission := g.admission()
			resolution.Status, resolution.Admission = "admitted", &admission
		}
		result = resolution
	case path == admissionPath+"/adm_"+g.i.ID[3:]+"/confirm":
		if g.denyConfirm {
			gatewayTestError(w, http.StatusForbidden)
			return
		}
		g.state = "confirmed"
		result = g.admission()
	case path == settlementPath:
		events := append([]controlclient.Settlement{}, g.foreignEvents...)
		if g.outcome != "" && !g.acked {
			events = append(events, g.terminalEvent())
		}
		result = map[string]any{"data": events, "monetary_ledger": "sub2api"}
	case path == "/gateway/v1/jobs/job_"+g.i.ID[3:] || path == "/gateway/v1/jobs/job_"+g.i.ID[3:]+"/cancel":
		cancel := strings.HasSuffix(path, "/cancel")
		if g.jobDenied || cancel && g.cancelDenied {
			gatewayTestError(w, http.StatusForbidden)
			return
		}
		if cancel {
			if r.Method != http.MethodPost || g.state != "confirmed" {
				g.t.Error("cancel did not target an already confirmed job")
			}
			g.outcome = "cancelled"
		}
		status := g.admission().JobStatus
		if g.outcome != "" {
			status = g.outcome
		}
		model := g.i.Model
		if g.jobModelOverride != "" {
			model = g.jobModelOverride
		}
		result = controlclient.Job{ID: "job_" + g.i.ID[3:], Status: status, Model: model, TaskType: g.i.TaskType, UpdatedRevision: 1, Artifacts: append([]controlclient.Artifact{}, g.jobArtifacts...)}
	case path == settlementPath+"/evt_"+g.i.ID[3:]+"/ack":
		var body struct {
			LedgerRef string `json:"ledger_ref"`
		}
		if json.NewDecoder(r.Body).Decode(&body) != nil || !validID("rcl", body.LedgerRef) {
			g.t.Error("ack did not use a durable ledger reference")
		}
		g.ledgerRefs = append(g.ledgerRefs, body.LedgerRef)
		if g.ackFailures > 0 {
			g.ackFailures--
			gatewayTestError(w, http.StatusServiceUnavailable)
			return
		}
		g.acked = true
		result = map[string]any{"id": "evt_" + g.i.ID[3:], "accepted": true}
	default:
		g.t.Error("unexpected coordinator protocol action")
		gatewayTestError(w, http.StatusNotFound)
		return
	}
	if g.dropAfter[path] > 0 {
		g.dropAfter[path]--
		dropProtocolResponse(g.t, w)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(result)
}

func testCoordinator(t *testing.T, s *Store, subject Subject, gateway *recoveryGateway, owner string) *Coordinator {
	t.Helper()
	c, err := NewCoordinator(s, gateway.client, CoordinatorConfig{Target: testTarget(t), Instance: subject.instance, Owner: owner, CallTimeout: 2 * time.Second})
	if err != nil {
		t.Fatal(err)
	}
	return c
}

func stepCoordinator(t *testing.T, c *Coordinator, i Intent, failure bool) {
	t.Helper()
	dueRecovery(t, i.ID)
	claimed, err := c.Step(ctx())
	if !claimed || (err != nil) != failure {
		t.Fatal("unexpected coordinator step outcome", err)
	}
}

func TestCoordinatorRecoversAcceptedAdmissionWithoutReceipt(t *testing.T) {
	s, subject := recoveryFixture(t)
	i := reserve(t, s, subject, "admission-loss", 1, "1")
	g := coordinatorGateway(t, i)
	g.configure(func(g *recoveryGateway) { g.dropAfter[admissionPath] = 1 })
	c := testCoordinator(t, s, subject, g, "before-restart")
	stepCoordinator(t, c, i, true)
	loaded, _ := s.Load(ctx(), subject, i.ID)
	if loaded.State != "dispatching" || loaded.AdmissionID != "" || loaded.DispatchCount != 1 {
		t.Fatal("lost admission response changed original dispatch intent")
	}
	balances(t, subject, "9.00000000", "1.00000000", "0.00000000")
	c = testCoordinator(t, store(t), subject, g, "after-restart")
	stepCoordinator(t, c, i, false)
	stepCoordinator(t, c, i, false)
	if g.count(admissionPath) != 1 || g.count(admissionPath+"/adm_"+i.ID[3:]+"/confirm") != 1 {
		t.Fatal("recovery repeated an accepted admission or confirmation")
	}
	g.configure(func(g *recoveryGateway) { g.outcome = "succeeded" })
	batch, err := c.ReconcileSettlements(ctx())
	if err != nil || batch.Resolved != 1 || batch.Acknowledged != 1 {
		t.Fatal("recovered task could not settle and acknowledge", err)
	}
	balances(t, subject, "9.00000000", "0.00000000", "1.00000000")
}

func TestCoordinatorRetriesUnknownUsingOriginalFrozenRequest(t *testing.T) {
	s, subject := recoveryFixture(t)
	i := reserve(t, s, subject, "unknown-admission", 1, "1")
	g := coordinatorGateway(t, i)
	g.configure(func(g *recoveryGateway) { g.dropBeforeAdmission = true })
	c := testCoordinator(t, s, subject, g, "unknown-owner")
	stepCoordinator(t, c, i, true)
	stepCoordinator(t, c, i, false)
	loaded, _ := s.Load(ctx(), subject, i.ID)
	if loaded.DispatchCount != 2 || loaded.State != "admitted" || g.count(admissionPath) != 2 {
		t.Fatal("unknown recovery did not record the original-ID resend")
	}
	g.configure(func(g *recoveryGateway) {
		if len(g.requests) != 2 || g.requests[0] != g.requests[1] {
			t.Fatal("unknown recovery changed canonical request")
		}
	})
	balances(t, subject, "9.00000000", "1.00000000", "0.00000000")
}

func TestCoordinatorRecoversConfirmedAdmissionWithoutReceipt(t *testing.T) {
	s, subject := recoveryFixture(t)
	i := reserve(t, s, subject, "confirm-loss", 1, "1")
	g := coordinatorGateway(t, i)
	confirm := admissionPath + "/adm_" + i.ID[3:] + "/confirm"
	g.configure(func(g *recoveryGateway) { g.dropAfter[confirm] = 1 })
	c := testCoordinator(t, s, subject, g, "confirm-owner")
	stepCoordinator(t, c, i, false)
	stepCoordinator(t, c, i, true)
	stepCoordinator(t, c, i, false)
	if g.count(confirm) != 1 {
		t.Fatal("confirmation response loss caused duplicate confirm")
	}
	balances(t, subject, "9.00000000", "1.00000000", "0.00000000")
}

func TestCoordinatorClosesAfterPolicyDenialAndKeyRevocation(t *testing.T) {
	for _, phase := range []string{"admit", "confirm", "confirm_won"} {
		t.Run(phase, func(t *testing.T) {
			s, subject := recoveryFixture(t)
			i := reserve(t, s, subject, "policy-close", 1, "1")
			g := coordinatorGateway(t, i)
			g.configure(func(g *recoveryGateway) {
				g.denyAdmission = phase == "admit"
				g.denyConfirm = phase != "admit"
				g.confirmOnClose = phase == "confirm_won"
			})
			c := testCoordinator(t, s, subject, g, "close-owner")
			stepCoordinator(t, c, i, phase == "admit")
			if phase != "admit" {
				stepCoordinator(t, c, i, true)
			}
			runSQL(t, `UPDATE api_keys SET status='disabled',deleted_at=NOW() WHERE id=$1`, subject.keyID)
			stepCoordinator(t, c, i, false)
			if g.count(closePath) != 1 {
				t.Fatal("permanent business denial did not durably close original intent")
			}
			if phase == "admit" {
				loaded, _ := s.Load(ctx(), subject, i.ID)
				if loaded.State != "released" || loaded.Outcome != "remote_closed" {
					t.Fatal("closed unknown request did not consume its proof")
				}
				balances(t, subject, "10.00000000", "0.00000000", "0.00000000")
			} else {
				balances(t, subject, "9.00000000", "1.00000000", "0.00000000")
				if phase == "confirm_won" {
					g.configure(func(g *recoveryGateway) { g.outcome = "succeeded" })
				}
				batch, err := c.ReconcileSettlements(ctx())
				if err != nil || batch.Acknowledged != 1 {
					t.Fatal("old revoked task could not settle", err)
				}
				if phase == "confirm_won" {
					balances(t, subject, "9.00000000", "0.00000000", "1.00000000")
				} else {
					balances(t, subject, "10.00000000", "0.00000000", "0.00000000")
				}
			}
		})
	}
}

func TestCoordinatorRejectsExpiredLeaseResponse(t *testing.T) {
	s, subject := recoveryFixture(t)
	i := reserve(t, s, subject, "expired-response", 1, "1")
	g := coordinatorGateway(t, i)
	g.configure(func(g *recoveryGateway) { g.expireLeaseOn = admissionPath })
	c := testCoordinator(t, s, subject, g, "expired-owner")
	dueRecovery(t, i.ID)
	if claimed, err := c.Step(ctx()); !claimed || !errors.Is(err, ErrFence) {
		t.Fatal("expired HTTP response retained local mutation authority", err)
	}
	loaded, _ := s.Load(ctx(), subject, i.ID)
	if loaded.State != "dispatching" || loaded.AdmissionID != "" {
		t.Fatal("stale worker bound remote admission")
	}
	c = testCoordinator(t, store(t), subject, g, "current-owner")
	stepCoordinator(t, c, i, false)
	if g.count(admissionPath) != 1 {
		t.Fatal("lease reclamation resent known remote admission")
	}
}

func TestCoordinatorKeepsBoundUnknownAndCorruptRequestFrozen(t *testing.T) {
	for _, kind := range []string{"bound_unknown", "cipher"} {
		t.Run(kind, func(t *testing.T) {
			s, subject := recoveryFixture(t)
			i := reserve(t, s, subject, "recovery-hold", 1, "1")
			g := coordinatorGateway(t, i)
			c := testCoordinator(t, s, subject, g, "hold-owner")
			if kind == "bound_unknown" {
				stepCoordinator(t, c, i, false)
				g.configure(func(g *recoveryGateway) { g.state = "unknown" })
			} else {
				runSQL(t, `UPDATE render_control_intents SET request_cipher=set_byte(request_cipher,octet_length(request_cipher)-1,get_byte(request_cipher,octet_length(request_cipher)-1) # 1) WHERE id=$1`, i.ID)
			}
			stepCoordinator(t, c, i, true)
			if kind == "bound_unknown" && g.count(admissionPath) != 1 || kind == "cipher" && g.count(admissionPath) != 0 {
				t.Fatal("missing evidence caused a new admission")
			}
			if g.count(closePath) != 0 {
				t.Fatal("missing evidence silently cancelled intent")
			}
			balances(t, subject, "9.00000000", "1.00000000", "0.00000000")
		})
	}
}

func TestCoordinatorSettlementAckReplayDoesNotRebill(t *testing.T) {
	for _, kind := range []string{"not_received", "receipt_lost"} {
		t.Run(kind, func(t *testing.T) {
			s, subject := recoveryFixture(t)
			i := reserve(t, s, subject, "ack-recovery", 1, "1")
			g := coordinatorGateway(t, i)
			g.configure(func(g *recoveryGateway) {
				g.outcome = "succeeded"
				if kind == "not_received" {
					g.ackFailures = 1
				} else {
					g.dropAfter[settlementPath+"/evt_"+i.ID[3:]+"/ack"] = 1
				}
			})
			c := testCoordinator(t, s, subject, g, "ack-owner")
			stepCoordinator(t, c, i, false)
			stepCoordinator(t, c, i, false)
			if batch, err := c.ReconcileSettlements(ctx()); err == nil || batch.Resolved != 1 || batch.Acknowledged != 0 {
				t.Fatal("ack fault was not observed")
			}
			batch, err := c.ReconcileSettlements(ctx())
			if err != nil || kind == "not_received" && batch.Acknowledged != 1 || kind == "receipt_lost" && batch.Seen != 0 {
				t.Fatal("ack replay could not recover", err)
			}
			g.configure(func(g *recoveryGateway) {
				for _, reference := range g.ledgerRefs {
					if reference != g.ledgerRefs[0] {
						t.Fatal("ack replay changed ledger reference")
					}
				}
			})
			balances(t, subject, "9.00000000", "0.00000000", "1.00000000")
			if countRows(t, `SELECT count(*) FROM usage_billing_dedup WHERE api_key_id=$1`, subject.keyID) != 1 {
				t.Fatal("ack replay created a second charge")
			}
		})
	}
}

func TestCoordinatorDoesNotAcknowledgeUnknownSettlement(t *testing.T) {
	s, subject := recoveryFixture(t)
	i := reserve(t, s, subject, "foreign-event", 1, "1")
	g := coordinatorGateway(t, i)
	g.configure(func(g *recoveryGateway) {
		g.outcome = "succeeded"
		foreign := g.terminalEvent()
		foreign.ExternalTaskRef = "rc_" + strings.Repeat("e", 32)
		foreign.ID = "evt_" + strings.Repeat("e", 32)
		g.foreignEvents = []controlclient.Settlement{foreign}
	})
	c := testCoordinator(t, s, subject, g, "foreign-owner")
	stepCoordinator(t, c, i, false)
	stepCoordinator(t, c, i, false)
	batch, err := c.ReconcileSettlements(ctx())
	if !errors.Is(err, ErrNotFound) || batch.Seen != 2 || batch.Conflicts != 1 || batch.Acknowledged != 1 {
		t.Fatal("unknown event was acknowledged or blocked a valid settlement", err)
	}
	balances(t, subject, "9.00000000", "0.00000000", "1.00000000")
}

func TestCoordinatorRejectsWrongServiceAndPlaintext(t *testing.T) {
	s, subject := recoveryFixture(t)
	i := reserve(t, s, subject, "coordinator-config", 1, "1")
	g := coordinatorGateway(t, i)
	other, _ := NewTarget("gateway_" + strings.Repeat("e", 32))
	if _, err := NewCoordinator(s, g.client, CoordinatorConfig{Target: other, Instance: subject.instance, Owner: "wrong-service"}); !errors.Is(err, ErrInvalid) {
		t.Fatal("coordinator used credentials for another target")
	}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { t.Error("plaintext coordinator sent a request") }))
	defer server.Close()
	credential, _ := controlclient.ParseCredential(i.TargetGatewayID + "." + strings.Repeat("s", 43))
	client, err := controlclient.New(controlclient.Config{GatewayID: i.TargetGatewayID, Origin: server.URL, Credential: credential, AllowLoopbackHTTP: true})
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()
	if _, err := NewCoordinator(s, client, CoordinatorConfig{Target: testTarget(t), Instance: subject.instance, Owner: "plaintext"}); !errors.Is(err, ErrInvalid) {
		t.Fatal("coordinator accepted a plaintext financial recovery connection")
	}
}

func coordinatorProcessStep(t *testing.T, s *Store, subject Subject) {
	t.Helper()
	origin := os.Getenv("RC_TEST_GATEWAY_ORIGIN")
	parsed, err := url.Parse(origin)
	if err != nil || parsed.Scheme != "https" || parsed.Hostname() != "127.0.0.1" {
		t.Fatal("child gateway origin is not the owned loopback TLS fixture")
	}
	encoded := os.Getenv("RC_TEST_GATEWAY_CERT")
	if len(encoded) > 16384 {
		t.Fatal("child test certificate is oversized")
	}
	der, err := base64.StdEncoding.DecodeString(encoded)
	if err != nil {
		t.Fatal("child test certificate invalid")
	}
	certificate, err := x509.ParseCertificate(der)
	if err != nil {
		t.Fatal("child test certificate invalid")
	}
	roots := x509.NewCertPool()
	roots.AddCert(certificate)
	credential, _ := controlclient.ParseCredential(testTarget(t).gatewayID + "." + strings.Repeat("s", 43))
	client, err := controlclient.New(controlclient.Config{GatewayID: testTarget(t).gatewayID, Origin: origin, Credential: credential, RootCAs: roots})
	if err != nil {
		t.Fatal(err)
	}
	c, err := NewCoordinator(s, client, CoordinatorConfig{Target: testTarget(t), Instance: subject.instance, Owner: "crash-child", CallTimeout: 2 * time.Second})
	if err != nil {
		t.Fatal(err)
	}
	claimed, err := c.Step(ctx())
	if !claimed || err == nil {
		t.Fatal("child did not persist its lost-admission recovery step")
	}
}

func TestCoordinatorRecoverySurvivesActualProcessExit(t *testing.T) {
	s, subject := recoveryFixture(t)
	i := reserve(t, s, subject, "coordinator-process", 1, "1")
	g := coordinatorGateway(t, i)
	g.configure(func(g *recoveryGateway) { g.dropAfter[admissionPath] = 1 })
	executable, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	processCtx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	command := exec.CommandContext(processCtx, executable, "-test.run=^TestProcessBoundaryHelper$")
	command.Env = append(os.Environ(), "RC_TEST_CHILD_STAGE=coordinator_step", "RC_TEST_INTENT="+i.ID, "RC_TEST_USER="+strconv.FormatInt(subject.userID, 10), "RC_TEST_KEY="+strconv.FormatInt(subject.keyID, 10), "RC_TEST_INSTANCE="+subject.instance, "RC_TEST_GATEWAY_ORIGIN="+g.server.URL, "RC_TEST_GATEWAY_CERT="+base64.StdEncoding.EncodeToString(g.server.Certificate().Raw))
	output, err := command.CombinedOutput()
	var exit *exec.ExitError
	if !errors.As(err, &exit) || exit.ExitCode() != 73 {
		t.Fatalf("coordinator child did not exit at durable recovery boundary: %s", output)
	}
	loaded, _ := s.Load(ctx(), subject, i.ID)
	if loaded.State != "dispatching" || loaded.DispatchCount != 1 || loaded.AdmissionID != "" {
		t.Fatal("original dispatch did not survive coordinator process exit")
	}
	if countRows(t, `SELECT failures FROM render_control_recovery WHERE intent_id=$1`, i.ID) != 1 {
		t.Fatal("retry metadata did not survive process exit")
	}
	c := testCoordinator(t, store(t), subject, g, "crash-successor")
	stepCoordinator(t, c, i, false)
	if g.count(admissionPath) != 1 {
		t.Fatal("successor did not find original accepted admission")
	}
	balances(t, subject, "9.00000000", "1.00000000", "0.00000000")
}
