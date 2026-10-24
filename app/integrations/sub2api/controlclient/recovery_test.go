package controlclient

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
)

var expectedGatewayID = "gateway_" + strings.Repeat("f", 32)

func requestIdentity(t *testing.T) RequestIdentity {
	t.Helper()
	identity, err := NewRequestIdentity(expectedGatewayID, testSubject(t), admissionRequest())
	if err != nil {
		t.Fatal(err)
	}
	return identity
}

func TestRequestRecoveryUsesOnlyServiceAuthority(t *testing.T) {
	identity := requestIdentity(t)
	closure := "gcl_" + strings.Repeat("a", 32)
	var calls atomic.Int32
	var closed atomic.Bool
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		if r.Method != http.MethodPost || r.URL.RawQuery != "" || r.Header.Get("Authorization") != "Bearer "+testToken || r.Header.Get("X-External-Tenant-Ref") != "" || r.Header.Get("X-External-Key-Ref") != "" || r.Header.Get("X-Client-IP") != "" {
			t.Error("recovery request acquired customer authority or put references in a URL")
		}
		var body map[string]any
		if json.NewDecoder(r.Body).Decode(&body) != nil || len(body) != 6 || body["authorization_ref"] != identity.AuthorizationRef || body["external_tenant_ref"] != "instance-one:user:24" || body["external_key_ref"] != "instance-one:key:72" {
			t.Error("recovery identity was not the persisted authenticated identity")
		}
		if r.URL.Path == "/gateway/v1/admission-requests/close" {
			closed.Store(true)
		} else if r.URL.Path != "/gateway/v1/admission-requests/lookup" {
			t.Error("unexpected recovery route")
		}
		response := RequestResolution{RequestIdentity: identity, GatewayID: expectedGatewayID, Status: "unknown"}
		if closed.Load() {
			response.Status, response.ClosureID = "closed", &closure
		}
		_ = json.NewEncoder(w).Encode(response)
	}))
	defer server.Close()
	client := testClient(t, server, 0)
	first, err := client.LookupRequest(context.Background(), identity)
	if err != nil || first.Status != "unknown" || first.ClosureID != nil {
		t.Fatal("absence was not distinguished from a durable closure", err)
	}
	result, err := client.CloseRequest(context.Background(), identity)
	if err != nil || result.Status != "closed" || result.ClosureID == nil || *result.ClosureID != closure {
		t.Fatal("durable closure receipt was lost", err)
	}
	replay, err := client.LookupRequest(context.Background(), identity)
	if err != nil || replay.Status != "closed" || *replay.ClosureID != closure || calls.Load() != 3 {
		t.Fatal("lookup changed closure identity or retried an operation", err)
	}
}

func TestRequestRecoveryRejectsContradictoryOrUnboundResponses(t *testing.T) {
	identity := requestIdentity(t)
	closure := "gcl_" + strings.Repeat("a", 32)
	pending, aborted, confirmed := admissionResponse("pending"), admissionResponse("aborted"), admissionResponse("confirmed")
	tests := []struct {
		name     string
		closing  bool
		valid    bool
		response RequestResolution
	}{
		{"unknown_lookup", false, true, RequestResolution{RequestIdentity: identity, Status: "unknown"}},
		{"unknown_close", true, false, RequestResolution{RequestIdentity: identity, Status: "unknown"}},
		{"closed_lookup", false, true, RequestResolution{RequestIdentity: identity, Status: "closed", ClosureID: &closure}},
		{"closed_missing_receipt", true, false, RequestResolution{RequestIdentity: identity, Status: "closed"}},
		{"unknown_with_receipt", false, false, RequestResolution{RequestIdentity: identity, Status: "unknown", ClosureID: &closure}},
		{"closed_with_admission", true, false, RequestResolution{RequestIdentity: identity, Status: "closed", ClosureID: &closure, Admission: &aborted}},
		{"pending_lookup", false, true, RequestResolution{RequestIdentity: identity, Status: "admitted", Admission: &pending}},
		{"pending_after_close", true, false, RequestResolution{RequestIdentity: identity, Status: "admitted", Admission: &pending}},
		{"aborted_after_close", true, true, RequestResolution{RequestIdentity: identity, Status: "admitted", Admission: &aborted}},
		{"confirmed_after_close", true, true, RequestResolution{RequestIdentity: identity, Status: "admitted", Admission: &confirmed}},
		{"admitted_with_receipt", true, false, RequestResolution{RequestIdentity: identity, Status: "admitted", Admission: &aborted, ClosureID: &closure}},
		{"admitted_without_record", true, false, RequestResolution{RequestIdentity: identity, Status: "admitted"}},
		{"unknown_state", true, false, RequestResolution{RequestIdentity: identity, Status: "refunded", ClosureID: &closure}},
	}
	for _, field := range []string{"request", "task", "price", "authorization", "tenant", "key"} {
		changed := identity
		switch field {
		case "request":
			changed.ProviderRequestID += "-changed"
		case "task":
			changed.ExternalTaskRef += "-changed"
		case "price":
			changed.PriceVersion += "-changed"
		case "authorization":
			changed.AuthorizationRef += "-changed"
		case "tenant":
			changed.ExternalTenantRef += "-changed"
		case "key":
			changed.ExternalKeyRef += "-changed"
		}
		tests = append(tests, struct {
			name     string
			closing  bool
			valid    bool
			response RequestResolution
		}{"mismatched_" + field, true, false, RequestResolution{RequestIdentity: changed, Status: "closed", ClosureID: &closure}})
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			test.response.GatewayID = expectedGatewayID
			server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				_ = json.NewEncoder(w).Encode(test.response)
			}))
			defer server.Close()
			client := testClient(t, server, 0)
			_, err := client.resolveRequest(context.Background(), identity, test.closing)
			if test.valid {
				if err != nil {
					t.Fatal("valid recovery response rejected", err)
				}
			} else {
				var failure *CallError
				if !errors.As(err, &failure) || failure.Code != "invalid_response" || failure.Uncertain != test.closing {
					t.Fatal("contradiction accepted or wrong uncertainty", err)
				}
			}
		})
	}
}

func TestRequestRecoveryLossRetainsWriteUncertaintyWithoutRetry(t *testing.T) {
	for _, closing := range []bool{false, true} {
		t.Run(map[bool]string{false: "lookup", true: "close"}[closing], func(t *testing.T) {
			var calls atomic.Int32
			server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls.Add(1)
				connection, _, err := w.(http.Hijacker).Hijack()
				if err != nil {
					t.Error("could not inject response loss")
					return
				}
				_ = connection.Close()
			}))
			defer server.Close()
			client := testClient(t, server, 0)
			_, err := client.resolveRequest(context.Background(), requestIdentity(t), closing)
			var failure *CallError
			if !errors.As(err, &failure) || failure.Code != "transport_error" || failure.Uncertain != closing || calls.Load() != 1 {
				t.Fatal("response loss was retried or treated as proof of absence", err)
			}
		})
	}
}

func TestRequestRecoveryRejectsUnconstructedIdentityBeforeHTTP(t *testing.T) {
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		t.Error("invalid recovery identity reached HTTP")
	}))
	defer server.Close()
	client := testClient(t, server, 0)
	if _, err := NewRequestIdentity(expectedGatewayID, Subject{}, admissionRequest()); err == nil {
		t.Fatal("empty authenticated subject accepted")
	}
	if _, err := client.CloseRequest(context.Background(), RequestIdentity{}); err == nil {
		t.Fatal("empty persisted identity accepted")
	}
	if _, err := NewRequestIdentity("", testSubject(t), admissionRequest()); err == nil {
		t.Fatal("missing persisted gateway target accepted")
	}
}

func TestRequestRecoveryRejectsDifferentGatewayUnderValidHTTPS(t *testing.T) {
	identity := requestIdentity(t)
	closure := "gcl_" + strings.Repeat("a", 32)
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(RequestResolution{RequestIdentity: identity, GatewayID: "gateway_" + strings.Repeat("b", 32), Status: "closed", ClosureID: &closure})
	}))
	defer server.Close()
	client := testClient(t, server, 0)
	_, err := client.CloseRequest(context.Background(), identity)
	var failure *CallError
	if !errors.As(err, &failure) || failure.Code != "invalid_response" || !failure.Uncertain {
		t.Fatal("a different gateway's closure was accepted for the persisted intent", err)
	}
}
