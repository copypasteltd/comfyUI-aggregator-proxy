package rendercontrol

import (
	"bytes"
	"context"
	"crypto/x509"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/Wei-Shaw/sub2api/internal/rendercontrol/controlclient"
	"github.com/gin-gonic/gin"
)

func syntheticCapability(id, task string) controlclient.PublishedCapability {
	media := "image/png"
	if strings.HasSuffix(task, "video") {
		media = "video/mp4"
	}
	return controlclient.PublishedCapability{PublishedModel: controlclient.PublishedModel{ID: id, Object: "model", Created: 1792713600, OwnedBy: "render-control"}, TaskType: task, Asynchronous: true, Release: controlclient.ReleaseIdentity{Version: 2, Digest: strings.Repeat("a", 64)}, SupportedParameters: []string{"seed"}, Presets: []controlclient.PublicPreset{{Quality: "standard", Size: "1024x1024"}}, Output: controlclient.PublicOutput{MediaTypes: []string{media}, ExpectedCount: 1, MaxBytes: 32 << 20}}
}

func TestDiscoveryIntersectsConfiguredModelsWithoutFinancialEffects(t *testing.T) {
	f := publicSetup(t)
	f.g.configure(func(g *recoveryGateway) {
		g.catalogModels = []controlclient.PublishedModel{syntheticCapability("not-configured", "text_to_image").PublishedModel, syntheticCapability("studio-image-v1", "text_to_image").PublishedModel}
	})
	for _, path := range []string{"/v1/models", "/v1/models/studio-image-v1", "/v1/capabilities"} {
		status, raw, headers := f.call(t, "GET", path, "", map[string]string{"X-External-Tenant-Ref": "forged:user:1", "X-External-Key-Ref": "forged:key:1", "X-Client-IP": "203.0.113.8", "X-Forwarded-For": "203.0.113.9"})
		if status != 200 || bytes.Contains(raw, []byte("not-configured")) || !bytes.Contains(raw, []byte("studio-image-v1")) || headers.Get("Cache-Control") != "private, no-store" {
			t.Fatal("discovery did not honor authenticated route intersection", status)
		}
	}
	balances(t, f.subject, "10.00000000", "0.00000000", "0.00000000")
	if countRows(t, `SELECT count(*) FROM render_control_intents WHERE instance=$1`, f.subject.instance) != 0 || f.g.count(admissionPath) != 0 {
		t.Fatal("discovery created execution or financial effects")
	}
}

func TestDiscoveryRechecksCurrentGroupAndRemoteScopes(t *testing.T) {
	f := publicSetup(t)
	status, _, _ := f.call(t, "GET", "/v1/models/studio-image-v1", "", nil)
	if status != 200 {
		t.Fatal("published model missing")
	}
	f.g.configure(func(g *recoveryGateway) { g.policyScopes = []string{"capabilities:read"} })
	status, _, _ = f.call(t, "GET", "/v1/models/studio-image-v1", "", nil)
	if status != 404 {
		t.Fatal("revoked discovery scope retained model retrieval", status)
	}
	status, raw, _ := f.call(t, "GET", "/v1/capabilities", "", nil)
	if status != 200 || !bytes.Contains(raw, []byte("studio-image-v1")) {
		t.Fatal("model scope was incorrectly reused for capabilities")
	}
	f.g.configure(func(g *recoveryGateway) { g.policyScopes = []string{"models:read"} })
	status, raw, _ = f.call(t, "GET", "/v1/capabilities", "", nil)
	if status != 200 || bytes.Contains(raw, []byte("studio-image-v1")) {
		t.Fatal("capability scope was not independently enforced")
	}
	before := f.g.count("/gateway/v1/models")
	runSQL(t, `UPDATE groups SET model_allowlist='{"enabled":true,"models":["other-model"]}'::jsonb WHERE id=$1`, f.groupID)
	status, _, _ = f.call(t, "GET", "/v1/models/studio-image-v1", "", nil)
	if status != 404 || f.g.count("/gateway/v1/models") != before {
		t.Fatal("current upstream allowlist did not stop discovery")
	}
	runSQL(t, `UPDATE groups SET model_allowlist='{"enabled":false,"models":[]}'::jsonb,allow_image_generation=FALSE WHERE id=$1`, f.groupID)
	status, raw, _ = f.call(t, "GET", "/v1/models", "", nil)
	if status != 200 || bytes.Contains(raw, []byte("studio-image-v1")) || f.g.count("/gateway/v1/models") != before {
		t.Fatal("image feature policy retained a model advertisement")
	}
	runSQL(t, `UPDATE api_keys SET status='disabled' WHERE id=$1`, f.subject.keyID)
	status, _, _ = f.call(t, "GET", "/v1/models", "", nil)
	if status != 401 || f.g.count("/gateway/v1/models") != before {
		t.Fatal("disabled upstream Key retained discovery access")
	}
}

func TestDiscoveryProjectsAllFiveCapabilitiesAndRejectsTaskMismatch(t *testing.T) {
	for _, task := range []string{"text_to_image", "image_to_image", "references_to_image", "text_to_video", "references_to_video"} {
		t.Run(task, func(t *testing.T) {
			f := publicSetup(t)
			f.config.Models[0].TaskType = task
			if strings.HasSuffix(task, "video") {
				f.config.Models[0].Defaults["seconds"] = json.Number("5")
			}
			f.start(t)
			f.g.configure(func(g *recoveryGateway) {
				g.catalogCapabilities = []controlclient.PublishedCapability{syntheticCapability("studio-image-v1", task)}
			})
			status, raw, _ := f.call(t, "GET", "/v1/capabilities", "", nil)
			var result controlclient.CapabilityCatalog
			if status != 200 || json.Unmarshal(raw, &result) != nil || len(result.Data) != 1 || result.Data[0].TaskType != task || result.Data[0].Release.Version != 2 {
				t.Fatal("capability projection lost published specification", status)
			}
			f.g.configure(func(g *recoveryGateway) {
				g.catalogCapabilities = []controlclient.PublishedCapability{syntheticCapability("studio-image-v1", "text_to_image")}
			})
			if task != "text_to_image" {
				status, raw, _ = f.call(t, "GET", "/v1/capabilities", "", nil)
				if status != 200 || bytes.Contains(raw, []byte("studio-image-v1")) {
					t.Fatal("remote task drift was advertised under a wrong local route")
				}
			}
		})
	}
}

func TestDiscoveryUsesOnlyConfiguredOriginalTargetAndReportsPartialFailure(t *testing.T) {
	f := publicSetup(t)
	secondID := "gateway_" + strings.Repeat("e", 32)
	secondTarget, _ := NewTarget(secondID)
	seed := Intent{Instance: f.subject.instance, UserID: f.subject.userID, KeyID: f.subject.keyID, TrustedIP: f.subject.ip, TargetGatewayID: secondID}
	second := coordinatorGateway(t, seed)
	f.config.Clients[secondID] = second.client
	rule := f.config.Models[0]
	rule.Model, rule.Target = "second-image-v1", secondTarget
	f.config.Models = append(f.config.Models, rule)
	f.start(t)
	f.g.configure(func(g *recoveryGateway) {
		g.catalogModels = []controlclient.PublishedModel{syntheticCapability("studio-image-v1", "text_to_image").PublishedModel, syntheticCapability("second-image-v1", "text_to_image").PublishedModel}
	})
	second.configure(func(g *recoveryGateway) {
		g.catalogModels = []controlclient.PublishedModel{syntheticCapability("studio-image-v1", "text_to_image").PublishedModel}
	})
	status, raw, headers := f.call(t, "GET", "/v1/models", "", nil)
	var result controlclient.ModelCatalog
	if status != 200 || json.Unmarshal(raw, &result) != nil || len(result.Data) != 1 || result.Data[0].ID != "studio-image-v1" || headers.Get("X-Render-Control-Catalog") != "complete" || second.count("/gateway/v1/models") != 1 || f.g.count("/gateway/v1/models") != 1 {
		t.Fatal("catalog guessed another target or repeated a target request")
	}
	f.g.configure(func(g *recoveryGateway) { g.catalogStatus = 503 })
	second.configure(func(g *recoveryGateway) {
		g.catalogModels = []controlclient.PublishedModel{syntheticCapability("second-image-v1", "text_to_image").PublishedModel}
	})
	status, raw, headers = f.call(t, "GET", "/v1/models", "", nil)
	if status != 200 || json.Unmarshal(raw, &result) != nil || len(result.Data) != 1 || result.Data[0].ID != "second-image-v1" || headers.Get("X-Render-Control-Catalog") != "partial" {
		t.Fatal("failed gateway supplied stale model or hid partial discovery")
	}
	status, _, _ = f.call(t, "GET", "/v1/models/studio-image-v1", "", nil)
	if status != 503 || second.count("/gateway/v1/models") != 2 {
		t.Fatal("single model failure traversed alternate targets", status)
	}
	status, raw, _ = f.call(t, "GET", "/v1/capabilities", "", nil)
	if status != 503 || bytes.Contains(raw, []byte("studio-image-v1")) {
		t.Fatal("partial capabilities were presented as complete")
	}
}

func hostDiscovery(t *testing.T, f *publicFixture, stock http.Handler) {
	t.Helper()
	router, err := f.handler.NewNativeRouter(f.auth, nil)
	if err != nil {
		t.Fatal(err)
	}
	owned := map[string]bool{}
	for _, rule := range f.config.Models {
		owned[rule.Model] = true
	}
	f.server = httptest.NewTLSServer(&hostedHandler{provider: router, stock: stock, models: owned})
	f.server.Client().Timeout = 8 * time.Second
	t.Cleanup(f.server.Close)
}

func TestHostedDiscoveryMergesStockFieldsAndPreservesCodexAndUnownedModels(t *testing.T) {
	f := publicSetup(t)
	var stockCalls atomic.Int32
	stock := gin.New()
	stock.Use(gin.HandlerFunc(f.auth))
	codex := []byte("{\n \"models\": [{\"slug\":\"synthetic-codex\",\"private_shape\":true}]\n}")
	stock.GET("/v1/models", func(c *gin.Context) {
		stockCalls.Add(1)
		if c.Query("client_version") != "" {
			c.Header("ETag", `"codex-etag"`)
			if c.GetHeader("If-None-Match") == `"codex-etag"` {
				c.Status(304)
				return
			}
			c.Data(200, "application/json", codex)
			return
		}
		if c.GetHeader("If-None-Match") != "" || c.GetHeader("If-Modified-Since") != "" || c.GetHeader("Accept-Encoding") != "" || c.GetHeader("Range") != "" {
			t.Error("merged catalog retained a stock conditional or compression header")
		}
		c.Header("ETag", `"stock-etag"`)
		c.Header("Content-Length", "999999")
		c.Data(200, "application/json", []byte(`{"object":"list","vendor_extension":{"kept":true},"data":[{"id":"ordinary-model","object":"model","created":123,"owned_by":"stock","shutdown_date":null,"permission":["retained"]},{"id":"studio-image-v1","object":"model","created":456,"owned_by":"stock-collision"},{"id":"ordinary-model","object":"model","created":999,"owned_by":"duplicate"}]}`))
	})
	stock.GET("/v1/models/:model", func(c *gin.Context) {
		c.Header("ETag", `"original-single"`)
		c.Data(200, "application/json", []byte(`{"stock_single":true}`))
	})
	hostDiscovery(t, f, stock)
	status, raw, headers := f.call(t, "GET", "/v1/models", "", map[string]string{"If-None-Match": `"stock-etag"`, "If-Modified-Since": "Thu, 22 Oct 2026 00:00:00 GMT", "Accept-Encoding": "identity", "Range": "bytes=0-1"})
	var root map[string]json.RawMessage
	var data []map[string]any
	_ = json.Unmarshal(raw, &root)
	_ = json.Unmarshal(root["data"], &data)
	if status != 200 || len(data) != 2 || data[0]["id"] != "ordinary-model" || data[1]["owned_by"] != "render-control" || !bytes.Contains(raw, []byte("shutdown_date")) || !bytes.Contains(raw, []byte("permission")) || !bytes.Contains(root["vendor_extension"], []byte("true")) || headers.Get("ETag") != "" || headers.Get("Content-Length") == "999999" || headers.Get("Cache-Control") != "private, no-store" {
		t.Fatal("merged discovery dropped stock fields or reused stock validators", status)
	}
	f.g.configure(func(g *recoveryGateway) { g.catalogStatus = 503 })
	status, raw, headers = f.call(t, "GET", "/v1/models", "", nil)
	if status != 200 || !bytes.Contains(raw, []byte("ordinary-model")) || bytes.Contains(raw, []byte("studio-image-v1")) || headers.Get("X-Render-Control-Catalog") != "partial" {
		t.Fatal("stock collision resurrected an unverified Provider model")
	}
	before := f.g.count("/gateway/v1/models")
	status, raw, headers = f.call(t, "GET", "/v1/models?client_version=0.144.0", "", nil)
	if status != 200 || !bytes.Equal(raw, codex) || headers.Get("ETag") != `"codex-etag"` || f.g.count("/gateway/v1/models") != before {
		t.Fatal("Codex manifest was changed by Provider discovery")
	}
	status, raw, _ = f.call(t, "GET", "/v1/models?client_version=0.144.0", "", map[string]string{"If-None-Match": `"codex-etag"`})
	if status != 304 || len(raw) != 0 || f.g.count("/gateway/v1/models") != before {
		t.Fatal("Codex conditional behavior was intercepted")
	}
	status, raw, headers = f.call(t, "GET", "/v1/models/ordinary-model", "", nil)
	if status != 200 || !bytes.Equal(raw, []byte(`{"stock_single":true}`)) || headers.Get("ETag") != `"original-single"` {
		t.Fatal("unowned individual model did not retain its stock handler")
	}
	beforeStock := stockCalls.Load()
	status, _, _ = f.call(t, "GET", "/v1/models", "", map[string]string{"Authorization": "Bearer synthetic-invalid"})
	if status != 401 || stockCalls.Load() != beforeStock {
		t.Fatal("ordinary catalog bypassed Provider authentication")
	}
}

func TestHostedDiscoveryRejectsBadStockCatalogAndKeepsRejections(t *testing.T) {
	for name, response := range map[string]struct {
		status int
		body   string
	}{
		"not_list":       {200, `{"models":[]}`},
		"duplicate_keys": {200, `{"object":"list","data":[],"data":[]}`},
		"null_data":      {200, `{"object":"list","data":null}`},
		"bad_item":       {200, `{"object":"list","data":[{"created":1}]}`},
		"over_limit":     {200, strings.Repeat("x", (4<<20)+1)},
		"unexpected_304": {304, ""},
		"denied":         {403, `{"error":{"code":"stock_denied"}}`},
		"unavailable":    {503, `{"error":{"code":"stock_busy"}}`},
	} {
		t.Run(name, func(t *testing.T) {
			f := publicSetup(t)
			hostDiscovery(t, f, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(response.status)
				_, _ = w.Write([]byte(response.body))
			}))
			status, raw, _ := f.call(t, "GET", "/v1/models", "", nil)
			if response.status == 403 {
				if status != 403 || !bytes.Equal(raw, []byte(response.body)) {
					t.Fatal("stock rejection became a successful Provider list")
				}
			} else if status != 503 {
				t.Fatal("invalid stock list was silently overwritten", status)
			}
			if f.g.count("/gateway/v1/models") != 0 {
				t.Fatal("stock rejection was ignored before discovery")
			}
		})
	}
}

func TestDiscoveryCapacityHasNoRemoteOrFinancialSideEffects(t *testing.T) {
	f := publicSetup(t)
	for range 8 {
		f.handler.catalogSlots <- struct{}{}
	}
	for _, path := range []string{"/v1/models", "/v1/models/studio-image-v1", "/v1/capabilities"} {
		status, raw, headers := f.call(t, "GET", path, "", nil)
		if status != 429 || !bytes.Contains(raw, []byte("catalog_capacity")) || headers.Get("Retry-After") != "2" {
			t.Fatal("catalog capacity was unbounded", status)
		}
	}
	for range 8 {
		<-f.handler.catalogSlots
	}
	if f.g.count("/gateway/v1/models") != 0 || f.g.count("/gateway/v1/capabilities") != 0 {
		t.Fatal("rejected discovery called gateway")
	}
	balances(t, f.subject, "10.00000000", "0.00000000", "0.00000000")
}

func TestDiscoveryParallelTargetsAndCancellationReleaseAllWorkers(t *testing.T) {
	for _, cancelled := range []bool{false, true} {
		t.Run(map[bool]string{false: "bounded_success", true: "cancelled"}[cancelled], func(t *testing.T) {
			f := publicSetup(t)
			f.config.Models = nil
			f.config.Clients = map[string]*controlclient.Client{}
			var active, peak, completed atomic.Int32
			started := make(chan struct{}, 8)
			release := make(chan struct{})
			var releaseOnce sync.Once
			releaseAll := func() { releaseOnce.Do(func() { close(release) }) }
			defer releaseAll()
			for index := range 6 {
				id := "gateway_" + strings.Repeat(string(rune('a'+index)), 32)
				model := "model-" + string(rune('a'+index))
				server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					n := active.Add(1)
					for p := peak.Load(); n > p && !peak.CompareAndSwap(p, n); p = peak.Load() {
					}
					defer func() { active.Add(-1); completed.Add(1) }()
					started <- struct{}{}
					select {
					case <-release:
					case <-r.Context().Done():
						return
					}
					_ = json.NewEncoder(w).Encode(controlclient.ModelCatalog{Object: "list", Data: []controlclient.PublishedModel{syntheticCapability(model, "text_to_image").PublishedModel}})
				}))
				t.Cleanup(server.Close)
				credential, _ := controlclient.ParseCredential(id + "." + strings.Repeat("s", 43))
				roots := x509.NewCertPool()
				roots.AddCert(server.Certificate())
				client, err := controlclient.New(controlclient.Config{GatewayID: id, Origin: server.URL, Credential: credential, RootCAs: roots})
				if err != nil {
					t.Fatal(err)
				}
				t.Cleanup(client.Close)
				f.config.Clients[id] = client
				target, _ := NewTarget(id)
				f.config.Models = append(f.config.Models, PublicModelRule{Model: model, TaskType: "text_to_image", Target: target, PriceVersion: "prices-v1", UnitPrice: money(t, "1"), GroupIDs: []int64{f.groupID}})
			}
			f.start(t)
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			request, _ := http.NewRequestWithContext(ctx, "GET", f.server.URL+"/v1/models", nil)
			request.Header.Set("Authorization", "Bearer "+f.credential)
			done := make(chan int, 1)
			go func() {
				response, err := f.server.Client().Do(request)
				if err != nil {
					done <- 0
					return
				}
				response.Body.Close()
				done <- response.StatusCode
			}()
			for range 4 {
				select {
				case <-started:
				case <-time.After(3 * time.Second):
					t.Fatal("parallel catalog workers did not start")
				}
			}
			select {
			case <-started:
				t.Fatal("catalog exceeded four parallel target calls")
			case <-time.After(50 * time.Millisecond):
			}
			if cancelled {
				cancel()
			} else {
				releaseAll()
			}
			select {
			case status := <-done:
				if !cancelled && status != 200 {
					t.Fatal("bounded discovery failed", status)
				}
			case <-time.After(3 * time.Second):
				t.Fatal("catalog request did not finish")
			}
			deadline := time.Now().Add(3 * time.Second)
			for active.Load() != 0 || len(f.handler.catalogSlots) != 0 {
				if time.Now().After(deadline) {
					t.Fatal("catalog cancellation retained workers or slots")
				}
				time.Sleep(5 * time.Millisecond)
			}
			if peak.Load() != 4 || !cancelled && completed.Load() != 6 {
				t.Fatal("catalog target concurrency or completion count incorrect")
			}
		})
	}
}
