package controlclient

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func gatewayReleaseFixture(t *testing.T, task string) GatewayCatalogRelease {
	t.Helper()
	defaults := map[string]any{"quality": "standard"}
	if strings.HasSuffix(task, "video") || task == "text_to_audio" {
		defaults["seconds"] = json.Number("1")
	} else {
		defaults["size"] = "1024x1024"
	}
	config := GatewayCatalogConfiguration{FormatVersion: "gateway-catalog-v1", GatewayID: "gateway_" + strings.Repeat("a", 32), InstanceID: "instance-one", Revision: 1, Models: []GatewayCatalogRule{{Model: "studio-v1", TaskType: task, UsageAccountID: "9223372036854775807", GroupIDs: []string{"1"}, PriceVersion: "prices-v1", UnitPrice: "0.00000001", Defaults: defaults, ModelBindings: []GatewayCatalogBinding{{ModelID: "mdl_" + strings.Repeat("b", 32), Revision: 1, TenantID: "ten_" + strings.Repeat("c", 32), ProjectID: "prj_" + strings.Repeat("d", 32), Alias: "studio-v1", TaskType: task, ReleaseID: "wfr_" + strings.Repeat("e", 32), ReleaseDigest: strings.Repeat("f", 64), ChannelIDs: []string{"ch_" + strings.Repeat("1", 32)}, Defaults: defaults}}}}}
	raw, err := json.Marshal(config)
	if err != nil {
		t.Fatal(err)
	}
	hash := sha256.Sum256(raw)
	return GatewayCatalogRelease{GatewayID: config.GatewayID, InstanceID: config.InstanceID, Revision: 1, Digest: hex.EncodeToString(hash[:]), Action: "publish", CreatedAt: 1700000000, ConfigurationJSON: string(raw)}
}

func TestGatewayCatalogStrictConfigurationAndAllTaskTypes(t *testing.T) {
	for _, task := range []string{"text_to_image", "image_to_image", "references_to_image", "text_to_video", "references_to_video", "text_to_audio"} {
		t.Run(task, func(t *testing.T) {
			release := gatewayReleaseFixture(t, task)
			parsed, err := ParseGatewayCatalog(release, release.GatewayID, release.InstanceID)
			if err != nil || parsed.Models[0].TaskType != task || parsed.Models[0].UsageAccountID != "9223372036854775807" {
				t.Fatal("frozen task/int64 string did not survive", err)
			}
		})
	}
	good := gatewayReleaseFixture(t, "text_to_image")
	cases := map[string]func(*GatewayCatalogRelease){
		"wrong_digest":      func(r *GatewayCatalogRelease) { r.Digest = strings.Repeat("0", 64) },
		"wrong_gateway":     func(r *GatewayCatalogRelease) { r.GatewayID = "gateway_" + strings.Repeat("1", 32) },
		"wrong_instance":    func(r *GatewayCatalogRelease) { r.InstanceID = "instance-other" },
		"oversized":         func(r *GatewayCatalogRelease) { r.ConfigurationJSON = strings.Repeat(" ", MaxGatewayCatalogBytes+1) },
		"revision_overflow": func(r *GatewayCatalogRelease) { r.Revision = 2147483648 },
		"invalid_source":    func(r *GatewayCatalogRelease) { r.Action = "rollback"; n := int64(1); r.SourceRevision = &n },
	}
	for name, mutate := range cases {
		t.Run(name, func(t *testing.T) {
			r := good
			mutate(&r)
			if _, err := ParseGatewayCatalog(r, good.GatewayID, good.InstanceID); err == nil {
				t.Fatal("invalid envelope accepted")
			}
		})
	}
	mutations := map[string]func(string) string{
		"duplicate":        func(s string) string { return strings.Replace(s, `"revision":1`, `"revision":1,"revision":1`, 1) },
		"case_alias":       func(s string) string { return strings.Replace(s, `"revision":1`, `"Revision":1`, 1) },
		"missing":          func(s string) string { return strings.Replace(s, `"revision":1,`, "", 1) },
		"unknown":          func(s string) string { return strings.Replace(s, `"revision":1`, `"revision":1,"extra":1`, 1) },
		"account_overflow": func(s string) string { return strings.Replace(s, "9223372036854775807", "9223372036854775808", 1) },
		"numeric_account": func(s string) string {
			return strings.Replace(s, `"usage_account_id":"9223372036854775807"`, `"usage_account_id":1`, 1)
		},
		"zero_price":     func(s string) string { return strings.Replace(s, `"0.00000001"`, `"0.00000000"`, 1) },
		"exponent_price": func(s string) string { return strings.Replace(s, `"0.00000001"`, `"1e-8"`, 1) },
		"nil_models": func(s string) string {
			return `{"format_version":"gateway-catalog-v1","gateway_id":"` + good.GatewayID + `","instance_id":"instance-one","revision":1,"models":null}`
		},
		"mismatched_binding": func(s string) string { return strings.Replace(s, `"alias":"studio-v1"`, `"alias":"another"`, 1) },
		"nil_defaults": func(s string) string {
			return strings.Replace(s, `"defaults":{"quality":"standard","size":"1024x1024"}`, `"defaults":null`, 1)
		},
	}
	for name, mutate := range mutations {
		t.Run(name, func(t *testing.T) {
			r := good
			r.ConfigurationJSON = mutate(r.ConfigurationJSON)
			hash := sha256.Sum256([]byte(r.ConfigurationJSON))
			r.Digest = hex.EncodeToString(hash[:])
			if _, err := ParseGatewayCatalog(r, good.GatewayID, good.InstanceID); err == nil {
				t.Fatal("invalid frozen configuration accepted")
			}
		})
	}
	// Digest the received bytes, including harmless whitespace, rather than a
	// locally marshalled representation of the decoded configuration.
	good.ConfigurationJSON += "\n"
	hash := sha256.Sum256([]byte(good.ConfigurationJSON))
	good.Digest = hex.EncodeToString(hash[:])
	if _, err := ParseGatewayCatalog(good, good.GatewayID, good.InstanceID); err != nil {
		t.Fatal("raw-byte digest was replaced by local canonicalization", err)
	}
}

func gatewayStateFixture(release GatewayCatalogRelease) GatewayCatalogState {
	instance := release.InstanceID
	return GatewayCatalogState{GatewayID: release.GatewayID, InstanceID: &instance, Revision: release.Revision, Status: "pending", Desired: &release}
}

func TestGatewayCatalogServiceIdentityAndReceiptBinding(t *testing.T) {
	release := gatewayReleaseFixture(t, "text_to_image")
	state := gatewayStateFixture(release)
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		for _, header := range []string{"X-External-Tenant-Ref", "X-External-Key-Ref", "X-Client-IP"} {
			if r.Header.Get(header) != "" {
				t.Error("configuration call leaked customer delegation")
			}
		}
		if r.Header.Get("Authorization") != "Bearer "+testToken {
			t.Error("service credential missing")
		}
		if r.Method == http.MethodGet {
			if r.URL.Query().Get("instance_id") != "instance-one" {
				t.Error("wrong instance query")
			}
			_ = json.NewEncoder(w).Encode(state)
			return
		}
		var receipt GatewayCatalogReceipt
		if json.NewDecoder(r.Body).Decode(&receipt) != nil || receipt.Digest != release.Digest {
			t.Error("receipt identity changed")
		}
		state.Managed, state.Status, state.Confirmed = true, "confirmed", &release
		view := GatewayCatalogReceiptView{InstanceID: receipt.InstanceID, Revision: receipt.Revision, Digest: receipt.Digest, Status: "applied", ObservedAt: 1700000001}
		state.LastReceipt = &view
		_ = json.NewEncoder(w).Encode(GatewayCatalogReceiptResult{Receipt: view, Activated: true, State: state})
	}))
	defer server.Close()
	client := testClient(t, server, 2<<20)
	if _, err := client.GatewayCatalog(context.Background(), "instance-one"); err != nil {
		t.Fatal("valid desired state rejected", err)
	}
	result, err := client.ReceiveGatewayCatalog(context.Background(), GatewayCatalogReceipt{InstanceID: release.InstanceID, Revision: 1, Digest: release.Digest, Status: "applied"})
	if err != nil || !result.Activated || result.State.Confirmed.Stamp() != release.Stamp() {
		t.Fatal("receipt was not bound to confirmed bytes", err)
	}
}

func TestGatewayCatalogRejectsContradictoryStates(t *testing.T) {
	release := gatewayReleaseFixture(t, "text_to_image")
	cases := map[string]func(*GatewayCatalogState){
		"wrong_gateway":                func(s *GatewayCatalogState) { s.GatewayID = "gateway_" + strings.Repeat("2", 32) },
		"wrong_instance":               func(s *GatewayCatalogState) { x := "wrong-instance"; s.InstanceID = &x },
		"managed_without_confirmation": func(s *GatewayCatalogState) { s.Managed = true },
		"wrong_revision":               func(s *GatewayCatalogState) { s.Revision = 2 },
		"confirmed_without_pointer":    func(s *GatewayCatalogState) { s.Status = "confirmed" },
		"stale_without_flag":           func(s *GatewayCatalogState) { s.Status = "stale" },
		"legacy_with_desired":          func(s *GatewayCatalogState) { s.Status = "legacy" },
	}
	for name, mutate := range cases {
		t.Run(name, func(t *testing.T) {
			state := gatewayStateFixture(release)
			mutate(&state)
			server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { _ = json.NewEncoder(w).Encode(state) }))
			defer server.Close()
			client := testClient(t, server, 2<<20)
			if _, err := client.GatewayCatalog(context.Background(), "instance-one"); err == nil {
				t.Fatal("contradictory state accepted")
			}
		})
	}
	for _, body := range []string{`{"gateway_id":"` + release.GatewayID + `","revision":0,"instance_id":null,"managed":false,"status":"legacy","desired_stale":false,"confirmed_stale":false,"desired":null,"confirmed":null,"last_receipt":null}`, `{"gateway_id":"` + release.GatewayID + `","revision":0,"instance_id":null,"status":"legacy","desired_stale":false,"confirmed_stale":false,"desired":null,"confirmed":null,"last_receipt":null}`} {
		server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { _, _ = w.Write([]byte(body)) }))
		client := testClient(t, server, 2<<20)
		_, err := client.GatewayCatalog(context.Background(), "instance-one")
		server.Close()
		if (strings.Contains(body, `"managed":false`)) != (err == nil) {
			t.Fatal("required false-valued field presence was not checked", err)
		}
	}
}
