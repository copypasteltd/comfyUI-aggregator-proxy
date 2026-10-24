package controlclient_test

import (
	"context"
	"crypto/x509"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/netip"
	"strings"
	"testing"

	client "render-control/sub2api-controlclient"
)

func TestClosureProofCannotBeConstructedFromPublicJSON(t *testing.T) {
	const gateway = "gateway_ffffffffffffffffffffffffffffffff"
	subject, _ := client.NewSubject("proof-test", 1, 2, netip.MustParseAddr("127.0.0.1"))
	request := client.AdmissionRequest{ProviderRequestID: "request-identity-0001", ExternalTaskRef: "task-one", PriceVersion: "p1", AuthorizationRef: "authorization-one"}
	identity, _ := client.NewRequestIdentity(gateway, subject, request)
	closure := "gcl_" + strings.Repeat("a", 32)
	public := client.RequestResolution{RequestIdentity: identity, GatewayID: gateway, Status: "closed", ClosureID: &closure}
	if proof, ok := public.ClosureProof(); ok || proof.Matches(identity) {
		t.Fatal("public response fields forged a trusted capability")
	}
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(public)
	}))
	defer server.Close()
	credential, _ := client.ParseCredential(gateway + "." + strings.Repeat("s", 43))
	roots := x509.NewCertPool()
	roots.AddCert(server.Certificate())
	// URL schemes are case-insensitive. Use the accepted uppercase form so
	// transport security cannot be inferred from a case-sensitive raw prefix.
	origin := "HTTPS" + strings.TrimPrefix(server.URL, "https")
	connection, err := client.New(client.Config{GatewayID: gateway, Origin: origin, Credential: credential, RootCAs: roots})
	if err != nil {
		t.Fatal(err)
	}
	defer connection.Close()
	for _, action := range []string{"lookup", "close"} {
		t.Run(action, func(t *testing.T) {
			var resolution client.RequestResolution
			if action == "lookup" {
				resolution, err = connection.LookupRequest(context.Background(), identity)
			} else {
				resolution, err = connection.CloseRequest(context.Background(), identity)
			}
			if err != nil {
				t.Fatal(err)
			}
			proof, ok := resolution.ClosureProof()
			if !ok || !proof.Matches(identity) || proof.ID() != closure {
				t.Fatal("validated closure did not issue its bound capability")
			}
			for _, field := range []string{"request", "task", "price", "authorization", "tenant", "key", "gateway"} {
				changed := identity
				switch field {
				case "request":
					changed.ProviderRequestID += "x"
				case "task":
					changed.ExternalTaskRef += "x"
				case "price":
					changed.PriceVersion += "x"
				case "authorization":
					changed.AuthorizationRef += "x"
				case "tenant":
					changed.ExternalTenantRef += "x"
				case "key":
					changed.ExternalKeyRef += "x"
				case "gateway":
					changed, _ = client.NewRequestIdentity("gateway_eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee", subject, request)
				}
				if proof.Matches(changed) {
					t.Fatal("proof accepted changed " + field)
				}
			}
			encoded, _ := json.Marshal(resolution)
			var restored client.RequestResolution
			if json.Unmarshal(encoded, &restored) != nil {
				t.Fatal("public metadata could not round trip")
			}
			if copy, ok := restored.ClosureProof(); ok || copy.Matches(identity) {
				t.Fatal("persisted public JSON restored trusted transport authority")
			}
			encoded, _ = json.Marshal(proof)
			var copy client.ClosureProof
			if string(encoded) != "{}" || json.Unmarshal(encoded, &copy) != nil || copy.Matches(identity) {
				t.Fatal("proof capability escaped through JSON")
			}
			if strings.Contains(fmt.Sprintf("%v %#v", proof, proof), request.AuthorizationRef) {
				t.Fatal("formatted proof exposed its references")
			}
			// Public wire fields can be edited; they cannot mutate the copied,
			// validated capability or authorize a different intent.
			resolution.ProviderRequestID = "edited-reference"
			*resolution.ClosureID = "gcl_" + strings.Repeat("b", 32)
			if !proof.Matches(identity) || proof.ID() != closure {
				t.Fatal("wire mutation changed the sealed proof")
			}
		})
	}
}

func TestUnknownAndAdmittedResponsesDoNotIssueClosureProof(t *testing.T) {
	const gateway = "gateway_ffffffffffffffffffffffffffffffff"
	subject, _ := client.NewSubject("proof-test", 1, 2, netip.MustParseAddr("127.0.0.1"))
	identity, _ := client.NewRequestIdentity(gateway, subject, client.AdmissionRequest{ProviderRequestID: "request-identity-0001", ExternalTaskRef: "task-one", PriceVersion: "p1", AuthorizationRef: "authorization-one"})
	for _, status := range []string{"unknown", "admitted"} {
		t.Run(status, func(t *testing.T) {
			response := client.RequestResolution{RequestIdentity: identity, GatewayID: gateway, Status: status}
			if status == "admitted" {
				response.Admission = &client.Admission{ID: "adm_" + strings.Repeat("1", 32), JobID: "job_" + strings.Repeat("1", 32), ProviderRequestID: identity.ProviderRequestID, ExternalTaskRef: identity.ExternalTaskRef, Status: "confirmed", JobStatus: "queued", ExpiresAt: 1}
			}
			server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { _ = json.NewEncoder(w).Encode(response) }))
			defer server.Close()
			credential, _ := client.ParseCredential(gateway + "." + strings.Repeat("s", 43))
			roots := x509.NewCertPool()
			roots.AddCert(server.Certificate())
			connection, _ := client.New(client.Config{GatewayID: gateway, Origin: server.URL, Credential: credential, RootCAs: roots})
			defer connection.Close()
			result, err := connection.LookupRequest(context.Background(), identity)
			if err != nil {
				t.Fatal(err)
			}
			if proof, ok := result.ClosureProof(); ok || proof.Matches(identity) {
				t.Fatal("non-closed response authorized a refund")
			}
		})
	}
}

func TestExplicitLoopbackHTTPDoesNotIssueFinancialClosureProof(t *testing.T) {
	const gateway = "gateway_ffffffffffffffffffffffffffffffff"
	subject, _ := client.NewSubject("proof-test", 1, 2, netip.MustParseAddr("127.0.0.1"))
	identity, _ := client.NewRequestIdentity(gateway, subject, client.AdmissionRequest{ProviderRequestID: "request-identity-0001", ExternalTaskRef: "task-one", PriceVersion: "p1", AuthorizationRef: "authorization-one"})
	closure := "gcl_" + strings.Repeat("a", 32)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(client.RequestResolution{RequestIdentity: identity, GatewayID: gateway, Status: "closed", ClosureID: &closure})
	}))
	defer server.Close()
	credential, _ := client.ParseCredential(gateway + "." + strings.Repeat("s", 43))
	connection, err := client.New(client.Config{GatewayID: gateway, Origin: server.URL, Credential: credential, AllowLoopbackHTTP: true})
	if err != nil {
		t.Fatal(err)
	}
	defer connection.Close()
	result, err := connection.CloseRequest(context.Background(), identity)
	if err != nil || result.Status != "closed" {
		t.Fatal("explicit loopback metadata transport failed", err)
	}
	if proof, ok := result.ClosureProof(); ok || proof.Matches(identity) {
		t.Fatal("plaintext metadata authorized financial release")
	}
}
