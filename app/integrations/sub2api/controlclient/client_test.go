package controlclient

import (
	"compress/gzip"
	"context"
	"crypto/x509"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/netip"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

var testToken = "gateway_" + strings.Repeat("a", 32) + "." + strings.Repeat("b", 43)
var admissionID = "adm_" + strings.Repeat("c", 32)
var jobID = "job_" + strings.Repeat("d", 32)
var eventID = "evt_" + strings.Repeat("e", 32)

func TestStableGatewayIdentitySurvivesCredentialRotation(t *testing.T) {
	serviceID := "gateway_" + strings.Repeat("f", 32)
	tokens := []string{testToken, "gateway_" + strings.Repeat("c", 32) + "." + strings.Repeat("d", 43)}
	var calls atomic.Int64
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer "+tokens[0] && r.Header.Get("Authorization") != "Bearer "+tokens[1] {
			t.Error("rotation used an unexpected authentication credential")
		}
		var request RequestIdentity
		if json.NewDecoder(r.Body).Decode(&request) != nil {
			t.Error("identity body missing")
		}
		closure := "gcl_" + strings.Repeat("1", 32)
		calls.Add(1)
		_ = json.NewEncoder(w).Encode(RequestResolution{RequestIdentity: request, GatewayID: serviceID, Status: "closed", ClosureID: &closure})
	}))
	defer server.Close()
	roots := x509.NewCertPool()
	roots.AddCert(server.Certificate())
	for _, token := range tokens {
		credential, _ := ParseCredential(token)
		connection, err := New(Config{GatewayID: serviceID, Origin: server.URL, Credential: credential, RootCAs: roots})
		if err != nil {
			t.Fatal(err)
		}
		if connection.GatewayID() != serviceID || connection.GatewayID() == strings.SplitN(token, ".", 2)[0] {
			t.Fatal("credential identity replaced the stable gateway service")
		}
		identity, err := NewRequestIdentity(serviceID, testSubject(t), AdmissionRequest{ProviderRequestID: "rotation-request-0001", ExternalTaskRef: "original-task", PriceVersion: "v1", AuthorizationRef: "original-authorization"})
		if err != nil {
			t.Fatal(err)
		}
		resolution, err := connection.LookupRequest(context.Background(), identity)
		connection.Close()
		proof, ok := resolution.ClosureProof()
		if err != nil || !ok || !proof.Matches(identity) {
			t.Fatal("credential rotation changed the existing request proof identity", err)
		}
	}
	if calls.Load() != 2 {
		t.Fatal("rotation skipped a concrete authenticated request")
	}
}

func TestClientRequiresExplicitStableGatewayIdentity(t *testing.T) {
	credential, _ := ParseCredential(testToken)
	for _, id := range []string{"", "gateway_bad", "worker_" + strings.Repeat("a", 32), "gateway_" + strings.Repeat("A", 32), "gateway_" + strings.Repeat("a", 32) + "\n"} {
		if connection, err := New(Config{GatewayID: id, Origin: "https://127.0.0.1:1", Credential: credential}); err == nil {
			connection.Close()
			t.Fatal("invalid stable service identity accepted")
		}
	}
}

func testSubject(t *testing.T) Subject {
	t.Helper()
	subject, err := NewSubject("instance-one", 24, 72, netip.MustParseAddr("::ffff:192.0.2.10"))
	if err != nil {
		t.Fatal(err)
	}
	return subject
}

func testClient(t *testing.T, server *httptest.Server, limit int64) *Client {
	t.Helper()
	credential, err := ParseCredential(testToken)
	if err != nil {
		t.Fatal(err)
	}
	pool := x509.NewCertPool()
	pool.AddCert(server.Certificate())
	client, err := New(Config{GatewayID: "gateway_" + strings.Repeat("a", 32), Origin: server.URL, Credential: credential, RootCAs: pool, MaxResponseBytes: limit})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(client.Close)
	return client
}

func admissionRequest() AdmissionRequest {
	return AdmissionRequest{ProviderRequestID: "persisted-provider-request-0001", ExternalTaskRef: "public-task-001", PriceVersion: "prices-v1", AuthorizationRef: "persisted-ledger-hold-001", Request: JobRequest{Model: "studio-image-v1", TaskType: "text_to_image", Input: map[string]any{"prompt": "synthetic protocol sample"}, Parameters: map[string]any{"n": 1}}}
}

func admissionResponse(status string) Admission {
	request := admissionRequest()
	jobState := map[string]string{"pending": "admission_pending", "confirmed": "queued", "aborted": "cancelled", "rejected": "failed", "expired": "expired"}[status]
	return Admission{ID: admissionID, JobID: jobID, ProviderRequestID: request.ProviderRequestID, ExternalTaskRef: request.ExternalTaskRef, Status: status, JobStatus: jobState, ExpiresAt: 2000000000}
}

func TestMetadataLifecycleAndIndependentServiceReconciliation(t *testing.T) {
	var seen sync.Map
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer "+testToken {
			t.Error("service authentication missing")
		}
		serviceOnly := strings.HasPrefix(r.URL.Path, "/gateway/v1/settlements")
		if serviceOnly {
			if r.Header.Get("X-External-Tenant-Ref") != "" || r.Header.Get("X-External-Key-Ref") != "" || r.Header.Get("X-Client-IP") != "" {
				t.Error("background reconciliation carried customer authority")
			}
		} else if r.Header.Get("X-External-Tenant-Ref") != "instance-one:user:24" || r.Header.Get("X-External-Key-Ref") != "instance-one:key:72" || r.Header.Get("X-Client-IP") != "192.0.2.10" {
			t.Error("authenticated subject references differ")
		}
		seen.Store(r.Method+" "+r.URL.Path, true)
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/gateway/v1/admissions":
			var data AdmissionRequest
			if err := json.NewDecoder(r.Body).Decode(&data); err != nil || data.ProviderRequestID != admissionRequest().ProviderRequestID || data.AuthorizationRef != admissionRequest().AuthorizationRef {
				t.Error("persisted admission identity changed")
			}
			w.WriteHeader(http.StatusAccepted)
			_ = json.NewEncoder(w).Encode(admissionResponse("pending"))
		case "/gateway/v1/admissions/" + admissionID + "/confirm":
			_ = json.NewEncoder(w).Encode(admissionResponse("confirmed"))
		case "/gateway/v1/admissions/" + admissionID + "/abort":
			_ = json.NewEncoder(w).Encode(admissionResponse("aborted"))
		case "/gateway/v1/jobs/" + jobID, "/gateway/v1/jobs/" + jobID + "/cancel":
			_ = json.NewEncoder(w).Encode(Job{ID: jobID, Status: "cancel_requested", UpdatedRevision: 3})
		case "/gateway/v1/settlements":
			_ = json.NewEncoder(w).Encode(map[string]any{"monetary_ledger": "sub2api", "data": []Settlement{{ID: eventID, JobID: jobID, ExternalTaskRef: "public-task-001", AuthorizationRef: "persisted-ledger-hold-001", PriceVersion: "prices-v1", Outcome: "succeeded", ImageCount: 1}}})
		case "/gateway/v1/settlements/" + eventID + "/ack":
			var data map[string]string
			_ = json.NewDecoder(r.Body).Decode(&data)
			if data["ledger_ref"] != "persisted-settlement-001" {
				t.Error("settlement reference changed")
			}
			_ = json.NewEncoder(w).Encode(map[string]any{"accepted": true, "id": eventID})
		default:
			t.Error("unexpected metadata route")
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer server.Close()
	client, subject, ctx := testClient(t, server, 0), testSubject(t), context.Background()
	if subject.TenantRef() != "instance-one:user:24" || subject.KeyRef() != "instance-one:key:72" {
		t.Fatal("unstable mapping references")
	}
	if _, err := client.Admit(ctx, subject, admissionRequest()); err != nil {
		t.Fatal(err)
	}
	if _, err := client.Confirm(ctx, subject, admissionID); err != nil {
		t.Fatal(err)
	}
	if _, err := client.Abort(ctx, subject, admissionID); err != nil {
		t.Fatal(err)
	}
	if _, err := client.Job(ctx, subject, jobID); err != nil {
		t.Fatal(err)
	}
	if _, err := client.Cancel(ctx, subject, jobID); err != nil {
		t.Fatal(err)
	}
	if events, err := client.Settlements(ctx); err != nil || len(events) != 1 {
		t.Fatal("settlement projection failed", err)
	}
	if err := client.Acknowledge(ctx, eventID, "persisted-settlement-001"); err != nil {
		t.Fatal(err)
	}
	count := 0
	seen.Range(func(any, any) bool { count++; return true })
	if count != 7 {
		t.Fatalf("metadata route coverage differs: %d", count)
	}
}

func TestRedirectNeverForwardsCredential(t *testing.T) {
	var targetCalls atomic.Int32
	target := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { targetCalls.Add(1); w.WriteHeader(http.StatusOK) }))
	defer target.Close()
	origin := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, target.URL+"/capture", http.StatusTemporaryRedirect)
	}))
	defer origin.Close()
	client := testClient(t, origin, 0)
	_, err := client.Admit(context.Background(), testSubject(t), admissionRequest())
	var call *CallError
	if !errors.As(err, &call) || call.HTTPStatus != 307 || !call.Uncertain || targetCalls.Load() != 0 {
		t.Fatal("redirect crossed origin or lost admission uncertainty")
	}
}

func TestResponseLimitsIncludeDecodedGzipAndCloseBodies(t *testing.T) {
	for _, compressed := range []bool{false, true} {
		t.Run(fmt.Sprint(compressed), func(t *testing.T) {
			var calls atomic.Int32
			server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls.Add(1)
				if compressed {
					w.Header().Set("Content-Encoding", "gzip")
					writer := gzip.NewWriter(w)
					_, _ = writer.Write([]byte(strings.Repeat("x", 8192)))
					_ = writer.Close()
				} else {
					_, _ = w.Write([]byte(strings.Repeat("x", 8192)))
				}
			}))
			defer server.Close()
			client := testClient(t, server, 1024)
			_, err := client.Admit(context.Background(), testSubject(t), admissionRequest())
			var call *CallError
			if !errors.As(err, &call) || call.Code != "response_too_large" || !call.Uncertain || calls.Load() != 1 {
				t.Fatal("oversized response accepted or automatically retried")
			}
		})
	}
}

func TestErrorsAndFormattingDoNotContainCredentialsOrResponseBodies(t *testing.T) {
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusForbidden)
		_ = json.NewEncoder(w).Encode(map[string]any{"error": map[string]string{"code": testToken, "message": "synthetic-private-prompt " + testToken}})
	}))
	defer server.Close()
	client := testClient(t, server, 0)
	_, err := client.Job(context.Background(), testSubject(t), jobID)
	var call *CallError
	if !errors.As(err, &call) || call.RemoteCode != "http_error" {
		t.Fatal("invalid remote error was not sanitized")
	}
	formatted := fmt.Sprintf("%v %+v %#v %v %#v", client, client, client, err, err) + fmt.Sprintf("%+v %#v", Config{Credential: client.secret}, client.secret)
	if strings.Contains(formatted, testToken) || strings.Contains(formatted, "synthetic-private-prompt") {
		t.Fatal("diagnostic formatting leaked sensitive data")
	}
}

func TestCancellationDoesNotRetryAdmission(t *testing.T) {
	var calls atomic.Int32
	entered := make(chan struct{})
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		close(entered)
		select {
		case <-r.Context().Done():
		case <-time.After(time.Second):
		}
	}))
	defer server.Close()
	client := testClient(t, server, 0)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	result := make(chan error, 1)
	subject := testSubject(t)
	go func() { _, err := client.Admit(ctx, subject, admissionRequest()); result <- err }()
	select {
	case <-entered:
	case <-time.After(5 * time.Second):
		t.Fatal("admission did not reach local server")
	}
	cancel()
	select {
	case err := <-result:
		var call *CallError
		if !errors.As(err, &call) || !call.Uncertain || !errors.Is(err, context.Canceled) || calls.Load() != 1 {
			t.Fatal("cancelled admission was retried or lost its uncertainty")
		}
	case <-time.After(5 * time.Second):
		t.Fatal("context cancellation did not bound the request")
	}
}

func TestUnsafeOriginsSubjectsAndIdentifiersAreRejectedBeforeHTTP(t *testing.T) {
	credential, _ := ParseCredential(testToken)
	for _, origin := range []string{"http://192.0.2.10", "https://user:password@example.com", "https://example.com/base", "https://example.com?secret=1", "https://example.com#fragment", "file:///tmp/test"} {
		if client, err := New(Config{GatewayID: "gateway_" + strings.Repeat("a", 32), Origin: origin, Credential: credential, AllowLoopbackHTTP: true}); err == nil {
			client.Close()
			t.Fatal("unsafe origin accepted")
		}
	}
	for _, instance := range []string{"", "client\r\nheader", "remote/user"} {
		if _, err := NewSubject(instance, 1, 2, netip.MustParseAddr("127.0.0.1")); err == nil {
			t.Fatal("unsafe instance reference accepted")
		}
	}
	if _, err := NewSubject("valid-instance", 0, 2, netip.MustParseAddr("127.0.0.1")); err == nil {
		t.Fatal("untrusted user record accepted")
	}
	if _, err := NewSubject("valid-instance", 1, 2, netip.Addr{}); err == nil {
		t.Fatal("missing client IP accepted")
	}
	if _, err := ParseCredential("sk-gen_" + testToken); err == nil {
		t.Fatal("business credential accepted as gateway")
	}
	var calls atomic.Int32
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { calls.Add(1) }))
	defer server.Close()
	client := testClient(t, server, 0)
	if _, err := client.Job(context.Background(), testSubject(t), "../../admin/api/v1/users"); err == nil {
		t.Fatal("unsafe identifier accepted")
	}
	if _, err := client.Admit(context.Background(), Subject{}, admissionRequest()); err == nil {
		t.Fatal("empty subject accepted")
	}
	if calls.Load() != 0 {
		t.Fatal("rejected request reached server")
	}
}

func TestMalformedSuccessfulAdmissionRemainsUncertain(t *testing.T) {
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]string{"id": "wrong", "job_id": jobID})
	}))
	defer server.Close()
	client := testClient(t, server, 0)
	_, err := client.Admit(context.Background(), testSubject(t), admissionRequest())
	var call *CallError
	if !errors.As(err, &call) || call.Code != "invalid_response" || !call.Uncertain {
		t.Fatal("malformed admission response lost original intent")
	}
}

func TestSuccessfulAdmissionCannotContradictPersistedState(t *testing.T) {
	for _, defect := range []string{"unknown_status", "pending_already_queued", "expired_not_terminal", "missing_expiry", "wrong_request"} {
		t.Run(defect, func(t *testing.T) {
			response := admissionResponse("pending")
			switch defect {
			case "unknown_status":
				response.Status = "unrecognized"
			case "pending_already_queued":
				response.JobStatus = "queued"
			case "expired_not_terminal":
				response.Status = "expired"
			case "missing_expiry":
				response.ExpiresAt = 0
			case "wrong_request":
				response.ProviderRequestID = "different-persisted-request-0002"
			}
			server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { _ = json.NewEncoder(w).Encode(response) }))
			defer server.Close()
			_, err := testClient(t, server, 0).Admit(context.Background(), testSubject(t), admissionRequest())
			var call *CallError
			if !errors.As(err, &call) || call.Code != "invalid_response" || !call.Uncertain {
				t.Fatal("contradictory admission state accepted")
			}
		})
	}
}

func TestUntrustedJobArtifactsAndDuplicateSettlementAreRejected(t *testing.T) {
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasPrefix(r.URL.Path, "/gateway/v1/jobs/") {
			_ = json.NewEncoder(w).Encode(Job{ID: jobID, Status: "succeeded", UpdatedRevision: 2, Artifacts: []Artifact{{ID: "../../admin"}}})
			return
		}
		event := Settlement{ID: eventID, JobID: jobID, ExternalTaskRef: "task-001", AuthorizationRef: "hold-001", PriceVersion: "v1", Outcome: "succeeded", ImageCount: 1}
		_ = json.NewEncoder(w).Encode(map[string]any{"monetary_ledger": "sub2api", "data": []Settlement{event, event}})
	}))
	defer server.Close()
	client := testClient(t, server, 0)
	if _, err := client.Job(context.Background(), testSubject(t), jobID); err == nil {
		t.Fatal("untrusted artifact path accepted")
	}
	if _, err := client.Settlements(context.Background()); err == nil {
		t.Fatal("duplicate settlement event accepted")
	}
}
