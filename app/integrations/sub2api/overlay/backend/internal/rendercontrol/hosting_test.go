package rendercontrol

import (
	"bytes"
	"database/sql"
	"encoding/base64"
	"encoding/json"
	"encoding/pem"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"

	"github.com/Wei-Shaw/sub2api/internal/config"
)

func hostingDirectory(t *testing.T) string {
	t.Helper()
	base := os.Getenv("GOTMPDIR")
	if !filepath.IsAbs(base) {
		t.Fatal("host test artifacts require the project-local Go temporary directory")
	}
	dir, err := os.MkdirTemp(base, "rc-host-")
	if err != nil {
		t.Fatal("host test directory creation failed")
	}
	t.Cleanup(func() {
		relative, err := filepath.Rel(base, dir)
		if err != nil || relative == "." || strings.HasPrefix(relative, "..") || filepath.IsAbs(relative) {
			t.Error("host test cleanup escaped its owned directory")
			return
		}
		if os.RemoveAll(dir) != nil {
			t.Error("host test directory cleanup failed")
		}
	})
	return dir
}

func hostingConfiguration(t *testing.T, f *publicFixture) (string, hostingFile) {
	t.Helper()
	dir := hostingDirectory(t)
	write := func(name string, data []byte) {
		if os.WriteFile(filepath.Join(dir, name), data, 0600) != nil {
			t.Fatal("synthetic host configuration write failed")
		}
	}
	write("encryption.key", []byte(base64.StdEncoding.EncodeToString(bytes.Repeat([]byte{7}, 32))))
	write("gateway.key", []byte(testTarget(t).gatewayID+"."+strings.Repeat("s", 43)))
	write("gateway-ca.pem", pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: f.g.server.Certificate().Raw}))
	file := hostingFile{Version: 1, Instance: f.subject.instance, ActiveKey: "test-key-v1", EncryptionKeys: map[string]string{"test-key-v1": "encryption.key"}, Gateways: []hostingGateway{{ID: testTarget(t).gatewayID, Origin: f.g.server.URL, CredentialFile: "gateway.key", CAFile: "gateway-ca.pem"}}, Models: []hostingModel{{UsageAccountID: f.config.Models[0].UsageAccountID, Model: "studio-image-v1", TaskType: "text_to_image", GatewayID: testTarget(t).gatewayID, PriceVersion: "prices-v1", UnitPrice: "1.00000000", GroupIDs: []int64{f.groupID}, Defaults: map[string]any{"size": "1024x1024", "quality": "standard"}}}, Runtime: hostingRuntime{Workers: 1, PollMilliseconds: 20, SettlementMilliseconds: 20, CallMilliseconds: 2000}}
	return filepath.Join(dir, "provider.json"), file
}

func writeHostingConfiguration(t *testing.T, path string, file hostingFile) {
	t.Helper()
	data, err := json.Marshal(file)
	if err != nil || os.WriteFile(path, data, 0600) != nil {
		t.Fatal("synthetic hosting configuration could not be saved")
	}
}

func TestHostingDisabledPreservesUpstreamWithoutDependencies(t *testing.T) {
	t.Setenv(HostingConfigEnvironment, "")
	cleanup, err := InstallFromEnvironment(nil, nil, nil, config.RunModeSimple)
	if err != nil || cleanup == nil {
		t.Fatal("disabled Provider required dependencies or changed stock startup")
	}
	cleanup()
	cleanup()
}

func TestHostingInstallsRealAuthAndRuntimeOnExistingServer(t *testing.T) {
	f := publicSetup(t)
	i := f.submit(t, "hosted-persisted-request")
	path, file := hostingConfiguration(t, f)
	writeHostingConfiguration(t, path, file)
	t.Setenv(HostingConfigEnvironment, path)
	stock := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("X-Stock-Handler", "retained")
		w.WriteHeader(http.StatusNoContent)
	})
	server := httptest.NewUnstartedServer(stock)
	if cleanup, err := InstallFromEnvironment(testDB, f.auth, server.Config, config.RunModeStandard); cleanup != nil || !errors.Is(err, ErrInvalid) {
		t.Fatal("enabled hosting accepted missing cache dependency")
	}
	cleanup, err := InstallFromEnvironment(testDB, f.auth, server.Config, config.RunModeStandard, &effectCacheFixture{})
	if err != nil {
		t.Fatal("host installation failed", err)
	}
	t.Cleanup(cleanup)
	if _, err := InstallFromEnvironment(testDB, f.auth, server.Config, config.RunModeStandard, &effectCacheFixture{}); !errors.Is(err, ErrConflict) {
		t.Fatal("same server accepted duplicate Provider lifecycles")
	}
	server.StartTLS()
	t.Cleanup(server.Close)
	f.server = server
	for _, route := range []string{"/stock-stream", "/v1/jobs-extra"} {
		status, _, headers := f.call(t, http.MethodGet, route, "", nil)
		if status != 204 || headers.Get("X-Stock-Handler") != "retained" {
			t.Fatal("Provider intercepted an unrelated upstream route")
		}
	}
	status, raw, _ := f.call(t, http.MethodPost, "/v1/jobs", publicImageBody, map[string]string{"Authorization": "Bearer synthetic-invalid"})
	if status != 401 || bytes.Contains(raw, []byte("synthetic-invalid")) {
		t.Fatal("installed Provider bypassed real upstream authentication")
	}
	awaitRuntime(t, "installed Provider did not automatically advance persisted work", func() bool { return f.g.count(admissionPath+"/adm_"+i.ID[3:]+"/confirm") == 1 })
	status, raw, headers := f.call(t, http.MethodGet, "/v1/jobs/"+i.ID, "", nil)
	var result publicJob
	if status != 200 || json.Unmarshal(raw, &result) != nil || result.ID != i.ID || result.Status != "queued" || headers.Get("Cache-Control") != "no-store" {
		t.Fatal("installed Provider returned an invalid scoped task projection", status)
	}
	f.g.configure(func(g *recoveryGateway) { g.outcome, g.ackFailures = "succeeded", 1 })
	awaitRuntime(t, "installed Provider did not settle and replay acknowledgement", func() bool { return f.g.count(settlementPath+"/evt_"+i.ID[3:]+"/ack") >= 2 })
	balances(t, f.subject, "9.00000000", "0.00000000", "1.00000000")
	awaitRuntime(t, "installed Provider did not automatically write native usage", func() bool {
		return countRows(t, `SELECT count(*) FROM usage_logs WHERE api_key_id=$1 AND model='studio-image-v1' AND actual_cost=1`, f.subject.keyID) == 1
	})
	var group sync.WaitGroup
	for range 4 {
		group.Go(cleanup)
	}
	group.Wait()
	status, _, headers = f.call(t, http.MethodPost, "/v1/jobs", publicImageBody, nil)
	if status != 503 || headers.Get("Retry-After") != "2" {
		t.Fatal("stopped Provider accepted a new request")
	}
	status, _, _ = f.call(t, http.MethodGet, "/stock-stream", "", nil)
	if status != 204 {
		t.Fatal("Provider cleanup stopped the upstream handler")
	}
	if countRows(t, `SELECT count(*) FROM render_control_intents WHERE instance=$1`, f.subject.instance) != 1 {
		t.Fatal("host cleanup or metadata reading created additional tasks")
	}
}

func TestHostingRejectsInvalidConfigurationWithoutChangingServer(t *testing.T) {
	f := publicSetup(t)
	for name, change := range map[string]func(*hostingFile){
		"version":               func(c *hostingFile) { c.Version = 2 },
		"instance":              func(c *hostingFile) { c.Instance = "unsafe instance" },
		"missing_key":           func(c *hostingFile) { c.ActiveKey = "missing-key" },
		"missing_secret":        func(c *hostingFile) { c.Gateways[0].CredentialFile = "missing-synthetic-secret" },
		"wrong_gateway":         func(c *hostingFile) { c.Gateways[0].ID = "gateway_" + strings.Repeat("e", 32) },
		"plaintext":             func(c *hostingFile) { c.Gateways[0].Origin = "http://127.0.0.1:8443" },
		"duplicate_gateway":     func(c *hostingFile) { c.Gateways = append(c.Gateways, c.Gateways[0]) },
		"negative_price":        func(c *hostingFile) { c.Models[0].UnitPrice = "-1" },
		"missing_usage_account": func(c *hostingFile) { c.Models[0].UsageAccountID = 0 },
		"long_usage_model":      func(c *hostingFile) { c.Models[0].Model = strings.Repeat("m", 101) },
		"untrusted_default":     func(c *hostingFile) { c.Models[0].Defaults = map[string]any{"workflow_json": "untrusted"} },
		"proxy":                 func(c *hostingFile) { c.TrustedProxies = []string{"not-a-proxy"} },
		"unbounded_poll":        func(c *hostingFile) { c.Runtime.PollMilliseconds = -1 },
		"overflow_duration":     func(c *hostingFile) { c.Runtime.LeaseMilliseconds = int(^uint(0) >> 1) },
		"public_plaintext":      func(c *hostingFile) { c.PublicOrigin = "http://render.example.invalid" },
		"public_credentials":    func(c *hostingFile) { c.PublicOrigin = "https://synthetic:private@render.example.invalid" },
		"public_path":           func(c *hostingFile) { c.PublicOrigin = "https://render.example.invalid/private" },
		"public_query":          func(c *hostingFile) { c.PublicOrigin = "https://render.example.invalid?private=1" },
		"negative_sync_wait":    func(c *hostingFile) { c.SyncWaitMilliseconds = -1 },
		"unbounded_sync_wait":   func(c *hostingFile) { c.SyncWaitMilliseconds = 60001 },
	} {
		t.Run(name, func(t *testing.T) {
			path, file := hostingConfiguration(t, f)
			change(&file)
			writeHostingConfiguration(t, path, file)
			t.Setenv(HostingConfigEnvironment, path)
			stock := http.NewServeMux()
			server := &http.Server{Handler: stock}
			cleanup, err := InstallFromEnvironment(testDB, f.auth, server, config.RunModeStandard, &effectCacheFixture{})
			if cleanup != nil || err == nil || server.Handler != stock || strings.Contains(fmt.Sprint(err), "missing-synthetic-secret") {
				t.Fatal("invalid configuration altered the server or exposed input")
			}
		})
	}
	if f.g.count(settlementPath) != 0 || f.g.count(admissionPath) != 0 {
		t.Fatal("invalid configuration started background gateway calls")
	}
}

func TestHostingRejectsMalformedFilesAndUnsupportedMode(t *testing.T) {
	f := publicSetup(t)
	path, file := hostingConfiguration(t, f)
	stock := http.NewServeMux()
	server := &http.Server{Handler: stock}
	t.Setenv(HostingConfigEnvironment, path)
	for name, raw := range map[string][]byte{
		"unknown":   []byte(`{"version":1,"unexpected_secret":"synthetic-private"}`),
		"duplicate": []byte(`{"version":1,"version":1}`),
		"oversize":  bytes.Repeat([]byte(" "), (256<<10)+1),
		"trailing":  []byte(`{} {}`),
	} {
		t.Run(name, func(t *testing.T) {
			if os.WriteFile(path, raw, 0600) != nil {
				t.Fatal("malformed fixture write failed")
			}
			if cleanup, err := InstallFromEnvironment(testDB, f.auth, server, config.RunModeStandard, &effectCacheFixture{}); cleanup != nil || err == nil || server.Handler != stock {
				t.Fatal("malformed Provider file accepted")
			}
		})
	}
	writeHostingConfiguration(t, path, file)
	for name, mode := range map[string]string{"simple": config.RunModeSimple, "unknown": "unknown"} {
		t.Run(name, func(t *testing.T) {
			if cleanup, err := InstallFromEnvironment(testDB, f.auth, server, mode, &effectCacheFixture{}); cleanup != nil || !errors.Is(err, ErrInvalid) {
				t.Fatal("Provider accepted a mode without standard billing")
			}
		})
	}
	t.Setenv(HostingConfigEnvironment, "relative-provider.json")
	if cleanup, err := InstallFromEnvironment(testDB, f.auth, server, config.RunModeStandard, &effectCacheFixture{}); cleanup != nil || !errors.Is(err, ErrInvalid) {
		t.Fatal("relative top-level configuration path accepted")
	}
	t.Setenv(HostingConfigEnvironment, filepath.Dir(path))
	if cleanup, err := InstallFromEnvironment(testDB, f.auth, server, config.RunModeStandard, &effectCacheFixture{}); cleanup != nil || !errors.Is(err, ErrInvalid) {
		t.Fatal("non-regular configuration accepted")
	}
	t.Setenv(HostingConfigEnvironment, path)
	if os.WriteFile(filepath.Join(filepath.Dir(path), "encryption.key"), []byte("invalid-synthetic-key"), 0600) != nil {
		t.Fatal("key fixture write failed")
	}
	if cleanup, err := InstallFromEnvironment(testDB, f.auth, server, config.RunModeStandard, &effectCacheFixture{}); cleanup != nil || !errors.Is(err, ErrInvalid) {
		t.Fatal("invalid encryption key accepted")
	}
}

func TestHostingMissingSchemaFailsWithoutCreatingTables(t *testing.T) {
	f := publicSetup(t)
	path, file := hostingConfiguration(t, f)
	writeHostingConfiguration(t, path, file)
	t.Setenv(HostingConfigEnvironment, path)
	db, err := sql.Open("postgres", os.Getenv("RC_SUB2API_TEST_DSN"))
	if err != nil {
		t.Fatal("schema-probe connection failed")
	}
	t.Cleanup(func() { _ = db.Close() })
	db.SetMaxOpenConns(1)
	db.SetMaxIdleConns(1)
	if _, err = db.Exec(`SET search_path TO pg_catalog`); err != nil {
		t.Fatal("isolated schema-probe fixture failed")
	}
	stock := http.NewServeMux()
	server := &http.Server{Handler: stock}
	if cleanup, err := InstallFromEnvironment(db, f.auth, server, config.RunModeStandard, &effectCacheFixture{}); cleanup != nil || !errors.Is(err, ErrStorage) || server.Handler != stock {
		t.Fatal("missing migration schema did not fail safely")
	}
	if f.g.count(settlementPath) != 0 || f.g.count(admissionPath) != 0 {
		t.Fatal("missing schema started gateway work")
	}
	var tables int
	if db.QueryRow(`SELECT count(*) FROM information_schema.tables WHERE table_schema='pg_catalog' AND table_name LIKE 'render_control_%'`).Scan(&tables) != nil || tables != 0 {
		t.Fatal("installation mutated the selected schema")
	}
}
