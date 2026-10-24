package rendercontrol

import (
	"bytes"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/Wei-Shaw/sub2api/internal/rendercontrol/controlclient"
)

const compatibilityImageBody = `{"model":"studio-image-v1","prompt":"synthetic-private-prompt"}`

var fixtureImage, _ = base64.StdEncoding.DecodeString("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYPgPAAEDAQAIicLsAAAAAElFTkSuQmCC")

func compatibilitySetup(t *testing.T) *publicFixture {
	f := publicSetup(t)
	f.config.PublicOrigin = "https://render.example.invalid"
	f.config.SyncWait = 50 * time.Millisecond
	f.start(t)
	return f
}

func (g *recoveryGateway) serveContent(w http.ResponseWriter, r *http.Request) {
	if g.contentDenied {
		gatewayTestError(w, 403)
		return
	}
	if g.contentFailure != 0 {
		gatewayTestError(w, g.contentFailure)
		return
	}
	id := "content_" + g.i.ID[3:]
	if r.URL.Path != "/gateway/v1/content/"+id && r.URL.Path != "/gateway/v1/content/"+id+"/download" {
		gatewayTestError(w, 404)
		return
	}
	data := g.contentData
	if data == nil {
		data = fixtureImage
	}
	mediaType := g.contentType
	if mediaType == "" {
		mediaType = "image/png"
	}
	sum := sha256.Sum256(data)
	if r.Method == http.MethodDelete {
		if r.Header.Get("Idempotency-Key") == "" {
			g.t.Error("content deletion omitted caller token")
		}
		w.WriteHeader(202)
		_ = json.NewEncoder(w).Encode(controlclient.ContentDeletion{ID: id, Status: "delete_requested"})
		return
	}
	if !strings.HasSuffix(r.URL.Path, "/download") {
		_ = json.NewEncoder(w).Encode(controlclient.ContentMetadata{ID: id, Bytes: int64(len(data)), MediaType: mediaType, SHA256: hex.EncodeToString(sum[:]), ExpiresAt: time.Now().Add(time.Hour).Unix()})
		return
	}
	w.Header().Set("Content-Type", mediaType)
	w.Header().Set("ETag", `"`+hex.EncodeToString(sum[:])+`"`)
	w.Header().Set("Content-Length", strconv.Itoa(len(data)))
	if g.contentTruncated {
		w.Header().Set("Content-Length", strconv.Itoa(len(data)+1))
	}
	w.Header().Set("Set-Cookie", "synthetic-service-cookie=must-not-leak")
	w.Header().Set("Location", "https://private.invalid/secret-path")
	w.Header().Set("Content-Disposition", `inline; filename="unsafe-internal-path"`)
	if r.Header.Get("Range") != "" {
		if r.Header.Get("Range") != "bytes=1-3" {
			gatewayTestError(w, 416)
			return
		}
		w.Header().Set("Content-Length", "3")
		w.Header().Set("Content-Range", "bytes 1-3/"+strconv.Itoa(len(data)))
		w.WriteHeader(206)
		data = data[1:4]
	}
	if r.Method != http.MethodHead {
		_, _ = w.Write(data)
	}
}

func completeCompatibility(t *testing.T, f *publicFixture, i Intent, video bool) {
	t.Helper()
	f.g.configure(func(g *recoveryGateway) { g.i = i; g.state = "unknown"; g.outcome = ""; g.jobArtifacts = nil })
	c := testCoordinator(t, f.s, f.subject, f.g, "compatibility-completion")
	stepCoordinator(t, c, i, false)
	stepCoordinator(t, c, i, false)
	f.g.configure(func(g *recoveryGateway) {
		g.outcome = "succeeded"
		g.jobArtifacts = []controlclient.Artifact{{ID: "content_" + i.ID[3:]}}
		if video {
			g.contentType = "video/mp4"
		}
	})
}

func resultID(t *testing.T, raw []byte) string {
	t.Helper()
	var result map[string]any
	if json.Unmarshal(raw, &result) != nil {
		t.Fatal("invalid compatibility JSON")
	}
	id, _ := result["id"].(string)
	if !validID("rc", id) {
		t.Fatal("compatibility response lost stable public ID")
	}
	return id
}

func TestCompatibilityStrictNormalizationBeforeReservation(t *testing.T) {
	for name, body := range map[string]string{
		"duplicate":        `{"model":"studio-image-v1","model":"other","prompt":"x"}`,
		"case":             `{"Model":"studio-image-v1","prompt":"x"}`,
		"stream":           `{"model":"studio-image-v1","prompt":"x","stream":true}`,
		"private_identity": `{"model":"studio-image-v1","prompt":"x","tenant_id":"forged"}`,
		"multiple":         `{"model":"studio-image-v1","prompt":"x","n":2}`,
		"float_rounding":   `{"model":"studio-image-v1","prompt":"x","n":1.00000000000000001}`,
		"invalid_format":   `{"model":"studio-image-v1","prompt":"x","response_format":"file"}`,
		"null_format":      `{"model":"studio-image-v1","prompt":"x","response_format":null}`,
		"empty_prompt":     `{"model":"studio-image-v1","prompt":""}`,
		"parameters":       `{"model":"studio-image-v1","prompt":"x","parameters":{"n":1}}`,
		"video_field":      `{"model":"studio-image-v1","prompt":"x","seconds":1}`,
		"oversize":         strings.Repeat(" ", (2<<20)+1),
		"trailing":         compatibilityImageBody + " {}",
	} {
		t.Run(name, func(t *testing.T) {
			if _, _, err := parseGeneration([]byte(body), false); !errors.Is(err, ErrInvalid) {
				t.Fatal("invalid compatibility body accepted")
			}
		})
	}
	for name, seconds := range map[string]string{"negative": "-1", "over": "120.001", "float_precision": "0.00100000000000001", "exponent": "1e0", "null": "null"} {
		t.Run(name, func(t *testing.T) {
			body := `{"model":"studio-video-v1","prompt":"x","seconds":` + seconds + `}`
			if _, _, err := parseGeneration([]byte(body), true); err == nil {
				t.Fatal("invalid video duration accepted")
			}
		})
	}
	request, format, err := parseGeneration([]byte(compatibilityImageBody), false)
	native, _ := ParsePublicRequest([]byte(publicImageBody))
	a, _ := json.Marshal(request)
	b, _ := json.Marshal(native)
	if err != nil || format != "b64_json" || !bytes.Equal(a, b) {
		t.Fatal("native and compatibility canonical inputs diverged")
	}
}

func TestCompatibilityAsyncSharesNativeDurableIdempotency(t *testing.T) {
	f := compatibilitySetup(t)
	status, raw, headers := f.call(t, "POST", "/v1/images/generations/async", compatibilityImageBody, map[string]string{"Idempotency-Key": "cross-entry"})
	if status != 202 || headers.Get("Retry-After") != "2" {
		t.Fatal("async compatibility submit failed", status)
	}
	id := resultID(t, raw)
	if headers.Get("Location") != "/v1/images/tasks/"+id || !bytes.Contains(raw, []byte(`"status":"processing"`)) || !bytes.Contains(raw, []byte(`"task_id":"`+id+`"`)) {
		t.Fatal("async task projection mismatch")
	}
	status, raw, _ = f.call(t, "POST", "/v1/jobs", publicImageBody, map[string]string{"Idempotency-Key": "cross-entry"})
	if status != 202 || resultID(t, raw) != id || countRows(t, `SELECT count(*) FROM render_control_intents WHERE api_key_id=$1`, f.subject.keyID) != 1 || f.g.count(admissionPath) != 0 {
		t.Fatal("cross-entry replay created or dispatched a second intent")
	}
	status, _, _ = f.call(t, "GET", "/v1/videos/"+id, "", nil)
	if status != 404 {
		t.Fatal("image task accepted as video")
	}
	status, raw, _ = f.call(t, "GET", "/v1/images/tasks/"+id, "", nil)
	if status != 200 || resultID(t, raw) != id || bytes.Contains(raw, []byte("synthetic-private-prompt")) {
		t.Fatal("image polling projection failed")
	}
	balances(t, f.subject, "9.00000000", "1.00000000", "0.00000000")
}

func TestCompatibilitySyncTimeoutKeepsIntentAndReplayDeliversVerifiedImage(t *testing.T) {
	f := compatibilitySetup(t)
	status, raw, headers := f.call(t, "POST", "/v1/images/generations", compatibilityImageBody, map[string]string{"Idempotency-Key": "sync-recovery"})
	// Decode explicitly because JSON uses snake_case extension fields.
	var envelope map[string]map[string]any
	if json.Unmarshal(raw, &envelope) != nil || status != 504 {
		t.Fatal("sync timeout not returned", status, string(raw))
	}
	id, _ := envelope["error"]["job_id"].(string)
	if !validID("rc", id) || envelope["error"]["code"] != "sync_wait_timeout" || headers.Get("Location") != "/v1/jobs/"+id {
		t.Fatal("sync timeout omitted recovery locator")
	}
	i, err := f.s.Load(ctx(), f.subject, id)
	if err != nil || i.DispatchCount != 0 || f.g.count(admissionPath) != 0 {
		t.Fatal("sync timeout dispatched or lost intent")
	}
	completeCompatibility(t, f, i, false)
	status, raw, headers = f.call(t, "POST", "/v1/images/generations", compatibilityImageBody, map[string]string{"Idempotency-Key": "sync-recovery"})
	var result struct {
		Created int64
		Data    []map[string]string
	}
	if status != 200 || json.Unmarshal(raw, &result) != nil || len(result.Data) != 1 || result.Created < 1 || headers.Get("Retry-After") != "" {
		t.Fatal("recovered sync response invalid", status, string(raw))
	}
	decoded, err := base64.StdEncoding.DecodeString(result.Data[0]["b64_json"])
	if err != nil || !bytes.Equal(decoded, fixtureImage) || countRows(t, `SELECT count(*) FROM render_control_intents WHERE api_key_id=$1`, f.subject.keyID) != 1 {
		t.Fatal("sync replay did not deliver original verified image")
	}
	urlBody := strings.TrimSuffix(compatibilityImageBody, "}") + `,"response_format":"url"}`
	status, raw, _ = f.call(t, "POST", "/v1/images/generations", urlBody, map[string]string{"Idempotency-Key": "sync-recovery", "X-Forwarded-Host": "forged.invalid", "X-Forwarded-Proto": "http"})
	if status != 200 || json.Unmarshal(raw, &result) != nil || result.Data[0]["url"] != f.config.PublicOrigin+"/v1/content/content_"+id[3:]+"/download" {
		t.Fatal("URL delivery trusted customer host or changed task", status)
	}
	balances(t, f.subject, "9.00000000", "1.00000000", "0.00000000")
}

func TestCompatibilitySyncChecksDeliveryPolicyAndCapacityBeforeReserve(t *testing.T) {
	f := compatibilitySetup(t)
	for _, missing := range []string{"images:create", "jobs:create", "jobs:read", "artifacts:read", "content:read", "content:download"} {
		t.Run(missing, func(t *testing.T) {
			scopes := []string{}
			for _, scope := range []string{"images:create", "jobs:create", "jobs:read", "artifacts:read", "content:read", "content:download"} {
				if scope != missing {
					scopes = append(scopes, scope)
				}
			}
			f.g.configure(func(g *recoveryGateway) { g.policyScopes = scopes })
			status, _, _ := f.call(t, "POST", "/v1/images/generations", compatibilityImageBody, nil)
			if status != 403 {
				t.Fatal("missing delivery scope accepted", status)
			}
		})
	}
	f.g.configure(func(g *recoveryGateway) { g.policyScopes = nil })
	for range cap(f.handler.syncSlots) {
		f.handler.syncSlots <- struct{}{}
	}
	status, _, headers := f.call(t, "POST", "/v1/images/generations", compatibilityImageBody, nil)
	for range cap(f.handler.syncSlots) {
		<-f.handler.syncSlots
	}
	if status != 429 || headers.Get("Retry-After") == "" || countRows(t, `SELECT count(*) FROM render_control_intents WHERE api_key_id=$1`, f.subject.keyID) != 0 {
		t.Fatal("sync rejection left an invisible reservation")
	}
	balances(t, f.subject, "10.00000000", "0.00000000", "0.00000000")
}

func TestCompatibilityAsyncRechecksDownloadAuthorization(t *testing.T) {
	f := compatibilitySetup(t)
	i := f.submit(t, "async-delivery-policy")
	completeCompatibility(t, f, i, false)
	status, raw, _ := f.call(t, "GET", "/v1/images/tasks/"+i.ID, "", nil)
	if status != 200 || !bytes.Contains(raw, []byte(`"image_url":"https://render.example.invalid/v1/content/`)) {
		t.Fatal("authorized async URL missing", status, string(raw))
	}
	f.g.configure(func(g *recoveryGateway) { g.contentDenied = true })
	status, raw, _ = f.call(t, "GET", "/v1/images/tasks/"+i.ID, "", nil)
	if status != 200 || !bytes.Contains(raw, []byte(`"status":"completed"`)) || bytes.Contains(raw, []byte("image_url")) || bytes.Contains(raw, []byte(`"result"`)) {
		t.Fatal("revoked download scope leaked delivery link", status)
	}
	status, _, _ = f.call(t, "GET", "/v1/content/content_"+i.ID[3:]+"/download", "", nil)
	if status != 403 {
		t.Fatal("content route ignored current download denial", status)
	}
}

func TestCompatibilityVideoJSONAndOriginalTaskContent(t *testing.T) {
	f := compatibilitySetup(t)
	f.config.Models = append(f.config.Models, PublicModelRule{Model: "studio-video-v1", TaskType: "text_to_video", Target: testTarget(t), PriceVersion: "prices-v1", UnitPrice: money(t, "1"), GroupIDs: []int64{f.groupID}, Defaults: map[string]any{"seconds": json.Number("2")}})
	f.start(t)
	body := `{"model":"studio-video-v1","prompt":"synthetic video"}`
	status, raw, headers := f.call(t, "POST", "/v1/videos", body, map[string]string{"Idempotency-Key": "video-compat"})
	if status != 202 || !bytes.Contains(raw, []byte(`"object":"video"`)) || !bytes.Contains(raw, []byte(`"status":"queued"`)) {
		t.Fatal("video create did not use async video projection", status)
	}
	id := resultID(t, raw)
	if headers.Get("Location") != "/v1/videos/"+id {
		t.Fatal("video locator incorrect")
	}
	i, _, err := f.s.Recover(ctx(), id)
	if err != nil || i.TaskType != "text_to_video" || i.MaxUnits != 2 {
		t.Fatal("video defaults were not frozen")
	}
	status, _, _ = f.call(t, "GET", "/v1/images/tasks/"+id, "", nil)
	if status != 404 {
		t.Fatal("video accepted on image route")
	}
	status, _, _ = f.call(t, "GET", "/v1/videos/"+id+"/content", "", nil)
	if status != 409 {
		t.Fatal("unready video exposed content")
	}
	completeCompatibility(t, f, i, true)
	status, raw, _ = f.call(t, "GET", "/v1/videos/"+id, "", nil)
	if status != 200 || !bytes.Contains(raw, []byte(`"status":"completed"`)) || bytes.Contains(raw, []byte(i.ProviderRequestID)) {
		t.Fatal("video public projection incorrect")
	}
	status, raw, headers = f.call(t, "GET", "/v1/videos/"+id+"/content", "", map[string]string{"Range": "bytes=1-3"})
	if status != 206 || !bytes.Equal(raw, fixtureImage[1:4]) || headers.Get("Content-Type") != "video/mp4" {
		t.Fatal("video content did not use authorized original artifact", status)
	}
	status, raw, headers = f.call(t, "HEAD", "/v1/videos/"+id+"/content", "", nil)
	if status != 200 || len(raw) != 0 || headers.Get("Content-Length") != strconv.Itoa(len(fixtureImage)) {
		t.Fatal("video HEAD invalid", status)
	}
}

func TestContentBindingIsScopedImmutableAndAtomic(t *testing.T) {
	f := compatibilitySetup(t)
	i := f.submit(t, "binding-original")
	completeCompatibility(t, f, i, false)
	status, _, _ := f.call(t, "GET", "/v1/jobs/"+i.ID, "", nil)
	if status != 200 {
		t.Fatal("authorized job artifacts were not linked", status)
	}
	id := "content_" + i.ID[3:]
	var wg sync.WaitGroup
	for range 4 {
		wg.Go(func() {
			if err := f.s.bindContent(ctx(), f.subject, i.ID, []controlclient.Artifact{{ID: id}}); err != nil {
				t.Error("concurrent binding replay failed", err)
			}
		})
	}
	wg.Wait()
	if countRows(t, `SELECT count(*) FROM render_control_content_bindings WHERE instance=$1`, f.subject.instance) != 1 {
		t.Fatal("binding replay duplicated routing")
	}
	other := publicSetup(t)
	status, _, _ = f.call(t, "GET", "/v1/content/"+id, "", map[string]string{"Authorization": "Bearer " + other.credential})
	if status != 404 {
		t.Fatal("cross-Key artifact metadata visible", status)
	}
	foreign := f.subject
	foreign.instance = "foreign-instance"
	if _, err := f.s.loadContent(ctx(), foreign, id); !errors.Is(err, ErrNotFound) {
		t.Fatal("cross-instance content visible")
	}
	if _, err := testDB.Exec(`UPDATE render_control_content_bindings SET intent_id=intent_id WHERE instance=$1`, f.subject.instance); err == nil {
		t.Fatal("binding update was not immutable")
	}
	if _, err := testDB.Exec(`INSERT INTO render_control_content_bindings(instance,content_id,intent_id) VALUES ('foreign-instance',$1,$2)`, id, i.ID); err == nil {
		t.Fatal("binding crossed persisted instance")
	}
	next := f.submit(t, "binding-collision")
	completeCompatibility(t, f, next, false)
	extra := "content_" + strings.Repeat("e", 32)
	if err := f.s.bindContent(ctx(), f.subject, next.ID, []controlclient.Artifact{{ID: extra}, {ID: id}}); !errors.Is(err, ErrConflict) {
		t.Fatal("content ID rebound to another intent", err)
	}
	if countRows(t, `SELECT count(*) FROM render_control_content_bindings WHERE instance=$1`, f.subject.instance) != 1 {
		t.Fatal("failed batch left partial content bindings")
	}
}

func TestContentProxyValidatesRangesAndDropsPrivateResponseHeaders(t *testing.T) {
	f := compatibilitySetup(t)
	i := f.submit(t, "media-proxy")
	completeCompatibility(t, f, i, false)
	status, _, _ := f.call(t, "GET", "/v1/jobs/"+i.ID, "", nil)
	if status != 200 {
		t.Fatal("fixture content association failed")
	}
	id := "content_" + i.ID[3:]
	for _, head := range []bool{false, true} {
		method := "GET"
		if head {
			method = "HEAD"
		}
		status, raw, headers := f.call(t, method, "/v1/content/"+id+"/download", "", map[string]string{"Range": "bytes=1-3", "Cookie": "customer-cookie=must-not-forward", "X-External-Key-Ref": "forged"})
		if status != 206 || headers.Get("Content-Range") != "bytes 1-3/"+strconv.Itoa(len(fixtureImage)) || headers.Get("Set-Cookie") != "" || headers.Get("Location") != "" || headers.Get("Content-Disposition") != `attachment; filename="`+id+`"` || headers.Get("Cache-Control") != "private, no-store" {
			t.Fatal("unsafe proxy metadata", status)
		}
		if head && len(raw) != 0 || !head && !bytes.Equal(raw, fixtureImage[1:4]) {
			t.Fatal("range/HEAD body mismatch")
		}
	}
	before := f.g.count("/gateway/v1/content/" + id + "/download")
	status, _, _ = f.call(t, "GET", "/v1/content/"+id+"/download", "", map[string]string{"Range": "bytes=0-1,3-4"})
	if status != 416 || f.g.count("/gateway/v1/content/"+id+"/download") != before {
		t.Fatal("invalid range reached remote storage")
	}
	for range cap(f.handler.mediaSlots) {
		f.handler.mediaSlots <- struct{}{}
	}
	status, _, _ = f.call(t, "GET", "/v1/content/"+id+"/download", "", nil)
	for range cap(f.handler.mediaSlots) {
		<-f.handler.mediaSlots
	}
	if status != 429 || f.g.count("/gateway/v1/content/"+id+"/download") != before {
		t.Fatal("download capacity was not bounded")
	}
	f.g.configure(func(g *recoveryGateway) { g.contentType = "video/mp4" })
	status, _, _ = f.call(t, "GET", "/v1/content/"+id+"/download", "", nil)
	if status != 409 {
		t.Fatal("artifact type contradicted task type", status)
	}
}

func TestContentReadsRetainAuthenticationWithExhaustedAndExpiredKeys(t *testing.T) {
	f := compatibilitySetup(t)
	i := f.submit(t, "read-after-exhaustion")
	completeCompatibility(t, f, i, false)
	_, _, _ = f.call(t, "GET", "/v1/jobs/"+i.ID, "", nil)
	id := "content_" + i.ID[3:]
	runSQL(t, `UPDATE users SET balance=0 WHERE id=$1`, f.subject.userID)
	runSQL(t, `UPDATE api_keys SET status='expired',expires_at=NOW()-INTERVAL '1 second' WHERE id=$1`, f.subject.keyID)
	for _, request := range []struct{ method, path string }{{"GET", "/v1/images/tasks/" + i.ID}, {"GET", "/v1/content/" + id}, {"GET", "/v1/content/" + id + "/download"}, {"HEAD", "/v1/content/" + id + "/download"}} {
		status, _, _ := f.call(t, request.method, request.path, "", nil)
		if status != 200 {
			t.Fatal("old content blocked by new-generation balance checks", status, request.path)
		}
	}
	status, raw, _ := f.call(t, "POST", "/v1/images/generations/async", compatibilityImageBody, nil)
	var envelope map[string]map[string]string
	if status < 400 || json.Unmarshal(raw, &envelope) != nil || envelope["error"]["type"] == "" || envelope["error"]["code"] == "" {
		t.Fatal("new generation bypassed auth or error format was not normalized", status)
	}
	runSQL(t, `UPDATE api_keys SET status='disabled' WHERE id=$1`, f.subject.keyID)
	status, _, _ = f.call(t, "GET", "/v1/content/"+id+"/download", "", nil)
	if status != 401 {
		t.Fatal("disabled Key retained media authority", status)
	}
}

func TestHostedCompatibilitySelectsConfiguredModelsAndPreservesStockBody(t *testing.T) {
	f := compatibilitySetup(t)
	router, err := f.handler.NewNativeRouter(f.auth, nil)
	if err != nil {
		t.Fatal(err)
	}
	var mu sync.Mutex
	stockBodies := []string{}
	stock := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		data, _ := io.ReadAll(r.Body)
		mu.Lock()
		stockBodies = append(stockBodies, string(data))
		mu.Unlock()
		w.Header().Set("X-Stock-Handler", "retained")
		w.WriteHeader(204)
	})
	mux := &hostedHandler{provider: router, stock: stock, models: map[string]bool{"studio-image-v1": true}}
	server := httptest.NewTLSServer(mux)
	t.Cleanup(server.Close)
	f.server = server
	unknown := `{ "model":"stock-other-model", "prompt":"unchanged", "stock_extension":true }`
	status, _, headers := f.call(t, "POST", "/v1/images/generations", unknown, nil)
	if status != 204 || headers.Get("X-Stock-Handler") != "retained" {
		t.Fatal("unknown model lost stock routing", status)
	}
	mu.Lock()
	if len(stockBodies) != 1 || stockBodies[0] != unknown {
		t.Error("stock request body mutated")
	}
	mu.Unlock()
	status, raw, _ := f.call(t, "POST", "/v1/images/generations/async", compatibilityImageBody, map[string]string{"Idempotency-Key": "hosted-compat"})
	if status != 202 || !validID("rc", resultID(t, raw)) {
		t.Fatal("configured model did not select durable Provider")
	}
	for _, path := range []string{"/v1/images/tasks/stock-task", "/v1/videos/stock-video", "/v1/jobs-extra"} {
		status, _, _ = f.call(t, "GET", path, "", nil)
		if status != 204 {
			t.Fatal("stock resource route intercepted", path)
		}
	}
	status, _, _ = f.call(t, "POST", "/v1/images/generations/async", `{"model":"studio-image-v1","model":"stock-other-model","prompt":"x"}`, nil)
	if status != 400 {
		t.Fatal("ambiguous model body reached stock route", status)
	}
	mux.stopping.Store(true)
	status, _, _ = f.call(t, "POST", "/v1/images/generations/async", compatibilityImageBody, nil)
	if status != 503 {
		t.Fatal("stopped Provider accepted compatibility generation")
	}
	status, _, _ = f.call(t, "POST", "/v1/images/generations", unknown, nil)
	if status != 204 {
		t.Fatal("Provider stop also stopped stock model")
	}
}

func TestCompatibilityReferenceVideoPreservesOrderedAssetsAcrossEntrypoints(t *testing.T) {
	f := compatibilitySetup(t)
	f.config.Models = append(f.config.Models, PublicModelRule{Model: "studio-ref-video-v1", TaskType: "references_to_video", Target: testTarget(t), PriceVersion: "prices-v1", UnitPrice: money(t, "1"), GroupIDs: []int64{f.groupID}, Defaults: map[string]any{"seconds": json.Number("0.5")}})
	f.start(t)
	first := uploadFixtureAsset(t, f, "video-character")
	second := uploadFixtureAsset(t, f, "video-scene")
	third := uploadFixtureAsset(t, f, "video-alternative")
	refs := `[{"asset_id":"` + first.ID + `","role":"character","weight":1},{"asset_id":"` + second.ID + `","role":"scene","weight":0.5}]`
	body := `{"model":"studio-ref-video-v1","prompt":"ordered references","seconds":"0.5","references":` + refs + `}`
	status, raw, _ := f.call(t, "POST", "/v1/videos", body, map[string]string{"Idempotency-Key": "reference-cross-entry"})
	if status != 202 {
		t.Fatal("reference video compatibility request rejected", status, string(raw))
	}
	id := resultID(t, raw)
	native := `{"model":"studio-ref-video-v1","task_type":"references_to_video","input":{"prompt":"ordered references","references":` + refs + `},"parameters":{"seconds":0.5}}`
	status, raw, _ = f.call(t, "POST", "/v1/jobs", native, map[string]string{"Idempotency-Key": "reference-cross-entry"})
	if status != 202 || resultID(t, raw) != id {
		t.Fatal("video string duration or ordered references broke native replay", status)
	}
	i, request, err := f.s.Recover(ctx(), id)
	if err != nil || i.TaskType != "references_to_video" || i.MaxUnits != 1 {
		t.Fatal("reference video intent invalid", err)
	}
	ordered := request.Input["references"].([]any)
	if ordered[0].(map[string]any)["asset_id"] != first.ID || ordered[1].(map[string]any)["asset_id"] != second.ID {
		t.Fatal("reference order changed")
	}
	status, _, _ = f.call(t, "POST", "/v1/videos", strings.Replace(body, first.ID, third.ID, 1), map[string]string{"Idempotency-Key": "reference-cross-entry"})
	if status != 409 {
		t.Fatal("changed video input silently reused original task")
	}
}

func TestContentUsesPersistedGatewayAndCurrentDeleteAuthority(t *testing.T) {
	f := compatibilitySetup(t)
	i := f.submit(t, "persisted-content-target")
	completeCompatibility(t, f, i, false)
	status, _, _ := f.call(t, "GET", "/v1/jobs/"+i.ID, "", nil)
	if status != 200 {
		t.Fatal("content binding fixture failed")
	}
	next := i
	next.TargetGatewayID = "gateway_" + strings.Repeat("e", 32)
	other := coordinatorGateway(t, next)
	target, err := NewTarget(next.TargetGatewayID)
	if err != nil {
		t.Fatal(err)
	}
	f.config.Clients[next.TargetGatewayID] = other.client
	f.config.Models[0].Target = target
	f.start(t)
	id := "content_" + i.ID[3:]
	path := "/gateway/v1/content/" + id
	status, _, _ = f.call(t, "GET", "/v1/content/"+id, "", nil)
	if status != 200 || f.g.count(path) != 1 || other.count(path) != 0 {
		t.Fatal("routing change moved an existing artifact to another gateway", status)
	}
	for range 2 {
		status, raw, _ := f.call(t, "DELETE", "/v1/content/"+id, "", map[string]string{"Idempotency-Key": "delete-owned-content"})
		var result controlclient.ContentDeletion
		if status != 202 || json.Unmarshal(raw, &result) != nil || result.ID != id || result.Status != "delete_requested" {
			t.Fatal("owned content deletion failed", status)
		}
	}
	f.g.configure(func(g *recoveryGateway) { g.contentDenied = true })
	status, _, _ = f.call(t, "DELETE", "/v1/content/"+id, "", map[string]string{"Idempotency-Key": "delete-owned-content"})
	if status != 403 || other.count(path) != 0 {
		t.Fatal("delete ignored current permission or original target", status)
	}
	balances(t, f.subject, "9.00000000", "1.00000000", "0.00000000")
}

func TestContentTruncationAbortsStreamAndReleasesCapacity(t *testing.T) {
	f := compatibilitySetup(t)
	i := f.submit(t, "truncated-content")
	completeCompatibility(t, f, i, false)
	status, _, _ := f.call(t, "GET", "/v1/jobs/"+i.ID, "", nil)
	if status != 200 {
		t.Fatal("content binding fixture failed")
	}
	f.g.configure(func(g *recoveryGateway) { g.contentTruncated = true; g.contentData = bytes.Repeat(fixtureImage, 2048) })
	request, _ := http.NewRequest("GET", f.server.URL+"/v1/content/content_"+i.ID[3:]+"/download", nil)
	request.Header.Set("Authorization", "Bearer "+f.credential)
	response, err := f.server.Client().Do(request)
	if err != nil {
		t.Fatal("truncation test did not reach media headers")
	}
	defer response.Body.Close()
	data, readErr := io.ReadAll(response.Body)
	if readErr == nil || bytes.Contains(data, []byte(`"error"`)) || len(f.handler.mediaSlots) != 0 {
		t.Fatal("truncated media was declared complete, appended JSON, or leaked a slot")
	}
}
