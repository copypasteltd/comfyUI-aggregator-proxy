package controlclient

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func catalogSample() PublishedCapability {
	return PublishedCapability{PublishedModel: PublishedModel{ID: "studio-image-v1", Object: "model", Created: 1792713600, OwnedBy: "render-control"}, TaskType: "text_to_image", Asynchronous: true, Release: ReleaseIdentity{Version: 1, Digest: strings.Repeat("a", 64)}, SupportedParameters: []string{"seed"}, Presets: []PublicPreset{{Quality: "standard", Size: "1024x1024"}}, Output: PublicOutput{MediaTypes: []string{"image/png"}, ExpectedCount: 1, MaxBytes: 32 << 20}}
}

func TestCatalogUsesFixedAuthenticatedPathsAndSafeProjection(t *testing.T) {
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "GET" || r.URL.RawQuery != "" || r.Header.Get("Authorization") != "Bearer "+testToken || r.Header.Get("X-External-Tenant-Ref") != "instance-one:user:24" || r.Header.Get("X-External-Key-Ref") != "instance-one:key:72" || r.Header.Get("X-Client-IP") != "192.0.2.10" {
			t.Error("catalog lost fixed service or authenticated subject")
		}
		var entry any = catalogSample()
		if r.URL.Path == "/gateway/v1/models" {
			entry = catalogSample().PublishedModel
		} else if r.URL.Path != "/gateway/v1/capabilities" {
			t.Error("unexpected discovery route")
		}
		raw, _ := json.Marshal(entry)
		var item map[string]any
		_ = json.Unmarshal(raw, &item)
		item["workflow_api"] = map[string]any{"synthetic-private-node": "must-not-forward"}
		_ = json.NewEncoder(w).Encode(map[string]any{"object": "list", "data": []any{item}, "private_gateway": "must-not-forward"})
	}))
	defer server.Close()
	client, subject := testClient(t, server, 0), testSubject(t)
	models, err := client.Models(context.Background(), subject)
	if err != nil || len(models.Data) != 1 || models.Data[0].ID != catalogSample().ID {
		t.Fatal("authenticated model catalog failed", err)
	}
	capabilities, err := client.Capabilities(context.Background(), subject)
	raw, _ := json.Marshal(capabilities)
	if err != nil || len(capabilities.Data) != 1 || capabilities.Data[0].Release.Version != 1 || bytes.Contains(raw, []byte("must-not-forward")) || bytes.Contains(raw, []byte("workflow_api")) {
		t.Fatal("capability response was not safely projected", err)
	}
}

func TestCatalogRejectsMalformedIdentityAndCapabilities(t *testing.T) {
	for name, change := range map[string]func(*PublishedCapability){
		"alias":                func(m *PublishedCapability) { m.ID = "../private" },
		"object":               func(m *PublishedCapability) { m.Object = "other" },
		"owner":                func(m *PublishedCapability) { m.OwnedBy = "foreign-provider" },
		"created":              func(m *PublishedCapability) { m.Created = 0 },
		"async":                func(m *PublishedCapability) { m.Asynchronous = false },
		"task":                 func(m *PublishedCapability) { m.TaskType = "unknown" },
		"version":              func(m *PublishedCapability) { m.Release.Version = 0 },
		"digest":               func(m *PublishedCapability) { m.Release.Digest = "private" },
		"parameters_missing":   func(m *PublishedCapability) { m.SupportedParameters = nil },
		"parameters_duplicate": func(m *PublishedCapability) { m.SupportedParameters = []string{"seed", "seed"} },
		"parameters_unknown":   func(m *PublishedCapability) { m.SupportedParameters = []string{"workflow_api"} },
		"presets_missing":      func(m *PublishedCapability) { m.Presets = nil },
		"presets_duplicate":    func(m *PublishedCapability) { m.Presets = append(m.Presets, m.Presets[0]) },
		"quality":              func(m *PublishedCapability) { m.Presets[0].Quality = "private" },
		"size":                 func(m *PublishedCapability) { m.Presets[0].Size = "auto" },
		"seconds":              func(m *PublishedCapability) { n := -1.0; m.Presets[0].Seconds = &n },
		"output_mismatch":      func(m *PublishedCapability) { m.Output.MediaTypes = []string{"video/mp4"} },
		"output_duplicate":     func(m *PublishedCapability) { m.Output.MediaTypes = []string{"image/png", "image/png"} },
		"output_count":         func(m *PublishedCapability) { m.Output.ExpectedCount = 2 },
		"output_bytes":         func(m *PublishedCapability) { m.Output.MaxBytes = MaxMediaBytes + 1 },
	} {
		t.Run(name, func(t *testing.T) {
			item := catalogSample()
			change(&item)
			server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				_ = json.NewEncoder(w).Encode(CapabilityCatalog{Object: "list", Data: []PublishedCapability{item}})
			}))
			defer server.Close()
			if _, err := testClient(t, server, 0).Capabilities(context.Background(), testSubject(t)); err == nil {
				t.Fatal("invalid capability entered public discovery")
			}
		})
	}
}

func TestCatalogRejectsAmbiguousAndOversizedLists(t *testing.T) {
	model, _ := json.Marshal(catalogSample().PublishedModel)
	for name, raw := range map[string][]byte{
		"null":               []byte(`{"object":"list","data":null}`),
		"wrong_object":       []byte(`{"object":"models","data":[]}`),
		"duplicate_ids":      []byte(`{"object":"list","data":[` + string(model) + `,` + string(model) + `]}`),
		"duplicate_keys":     []byte(`{"object":"list","object":"other","data":[]}`),
		"duplicate_item_key": []byte(`{"object":"list","data":[{"id":"wrong","id":"studio-image-v1","object":"model","created":1,"owned_by":"render-control"}]}`),
		"trailing":           []byte(`{"object":"list","data":[]} {}`),
		"deep":               []byte(`{"object":"list","data":[],"private":` + strings.Repeat("[", 18) + "0" + strings.Repeat("]", 18) + `}`),
		"large":              []byte(`{"object":"list","data":[],"private":"` + strings.Repeat("x", 2<<20) + `"}`),
		"invalid_utf8":       append([]byte(`{"object":"list","data":[],"private":"`), 0xff, '"', '}'),
	} {
		t.Run(name, func(t *testing.T) {
			server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { _, _ = w.Write(raw) }))
			defer server.Close()
			if _, err := testClient(t, server, 0).Models(context.Background(), testSubject(t)); err == nil {
				t.Fatal("ambiguous catalog accepted")
			}
		})
	}
}

func TestCatalogCancellationAndRemoteDenialStayBounded(t *testing.T) {
	for _, status := range []int{401, 403, 503} {
		server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(status)
			_, _ = w.Write([]byte(`{"error":{"code":"not_available","message":"synthetic-private"}}`))
		}))
		client := testClient(t, server, 0)
		_, err := client.Models(context.Background(), testSubject(t))
		var call *CallError
		if !errors.As(err, &call) || call.HTTPStatus != status || call.Uncertain || strings.Contains(err.Error(), "synthetic-private") {
			t.Fatal("catalog denial lost bounded safe error semantics")
		}
		server.Close()
	}
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { t.Error("cancelled request reached gateway") }))
	defer server.Close()
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := testClient(t, server, 0).Capabilities(ctx, testSubject(t)); err == nil {
		t.Fatal("cancelled catalog call succeeded")
	}
}
