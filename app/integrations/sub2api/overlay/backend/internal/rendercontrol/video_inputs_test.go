package rendercontrol

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
)

func referenceVideoSetup(t *testing.T) *publicFixture {
	f := compatibilitySetup(t)
	f.config.Models = append(f.config.Models, PublicModelRule{Model: "studio-ref-video-v1", TaskType: "references_to_video", Target: testTarget(t), PriceVersion: "video-v1", UnitPrice: money(t, "1"), GroupIDs: []int64{f.groupID}, Defaults: map[string]any{"seconds": json.Number("1")}})
	f.start(t)
	return f
}

func TestVideoInputReferenceJSONMultipartAndNativeReplay(t *testing.T) {
	f := referenceVideoSetup(t)
	body := `{"model":"studio-ref-video-v1","prompt":"first frame","seconds":"1","input_reference":{"image_url":"` + imageDataURL(fixtureImage) + `"},"workflow_options":{"seed":17,"fps":24,"negative_prompt":"blur"}}`
	headers := map[string]string{"Idempotency-Key": "single-frame-replay"}
	status, raw, _ := f.call(t, "POST", "/v1/videos", body, headers)
	if status != 202 {
		t.Fatal("JSON reference rejected", status, string(raw))
	}
	id := resultID(t, raw)
	_, request, err := f.s.Recover(ctx(), id)
	if err != nil || request.TaskType != "references_to_video" || request.Input["negative_prompt"] != "blur" || request.Parameters["seed"] != json.Number("17") || request.Parameters["fps"] != json.Number("24") {
		t.Fatal("Workflow options lost", err)
	}
	ref := request.Input["references"].([]any)[0].(map[string]any)
	if ref["role"] != "first_frame" || ref["asset_id"] == "asset_inline_placeholder" {
		t.Fatal("first frame not lowered to an owned ready asset")
	}
	multipart, kind := editMultipart(t, []editPart{{"model", []byte("studio-ref-video-v1"), false}, {"input_reference", fixtureImage, true}, {"prompt", []byte("first frame"), false}, {"seconds", []byte("1"), false}, {"workflow_options", []byte(`{"seed":17,"fps":24,"negative_prompt":"blur"}`), false}})
	status, raw, _ = f.call(t, "POST", "/v1/videos", multipart, map[string]string{"Content-Type": kind, "Idempotency-Key": headers["Idempotency-Key"]})
	if status != 202 || resultID(t, raw) != id {
		t.Fatal("multipart duplicated original request", status, string(raw))
	}
	native := `{"model":"studio-ref-video-v1","task_type":"references_to_video","input":{"prompt":"first frame","negative_prompt":"blur","references":[{"asset_id":"` + ref["asset_id"].(string) + `","role":"first_frame"}]},"parameters":{"seconds":1,"seed":17,"fps":24}}`
	status, raw, _ = f.call(t, "POST", "/v1/jobs", native, headers)
	if status != 202 || resultID(t, raw) != id {
		t.Fatal("native input did not replay", status, string(raw))
	}
	if countRows(t, `SELECT count(*) FROM render_control_asset_uploads WHERE api_key_id=$1`, f.subject.keyID) != 1 || countRows(t, `SELECT count(*) FROM render_control_intents WHERE api_key_id=$1`, f.subject.keyID) != 1 {
		t.Fatal("replay duplicated upload or reservation")
	}
	assertUploadClean(t, f)
	balances(t, f.subject, "9.00000000", "1.00000000", "0.00000000")
}

func TestVideoWorkflowReferencesPreserveWeightsAndCurrentOwnership(t *testing.T) {
	f := referenceVideoSetup(t)
	first := uploadFixtureAsset(t, f, "video-options-first")
	second := uploadFixtureAsset(t, f, "video-options-second")
	refs := `[{"asset_id":"` + first.ID + `","role":"character","weight":0.5},{"asset_id":"` + second.ID + `","role":"scene","weight":2}]`
	body := `{"model":"studio-ref-video-v1","prompt":"two references","workflow_options":{"references":` + refs + `,"seed":12}}`
	status, raw, _ := f.call(t, "POST", "/v1/videos", body, map[string]string{"Idempotency-Key": "video-options"})
	if status != 202 {
		t.Fatal("Workflow references rejected", status, string(raw))
	}
	id := resultID(t, raw)
	_, request, err := f.s.Recover(ctx(), id)
	if err != nil || request.Input["references"].([]any)[1].(map[string]any)["weight"] != json.Number("2") {
		t.Fatal("reference weight lost")
	}
	status, _, _ = f.call(t, "POST", "/v1/videos", strings.Replace(body, `"weight":2`, `"weight":1`, 1), map[string]string{"Idempotency-Key": "video-options"})
	if status != 409 {
		t.Fatal("changed weights replayed original", status)
	}
	other := referenceVideoSetup(t)
	status, _, _ = other.call(t, "POST", "/v1/videos", body, nil)
	if status != 404 || countRows(t, `SELECT count(*) FROM render_control_intents WHERE api_key_id=$1`, other.subject.keyID) != 0 {
		t.Fatal("another Key reused reference assets", status)
	}
	assertUploadClean(t, f)
	assertUploadClean(t, other)
}

func TestVideoReferenceInvalidInputsNeverUploadOrReserve(t *testing.T) {
	f := referenceVideoSetup(t)
	prefix := `{"model":"studio-ref-video-v1","prompt":"first frame","input_reference":{"image_url":"` + imageDataURL(fixtureImage) + `"}`
	for name, body := range map[string]string{
		"unknown_option":   prefix + `,"workflow_options":{"node_id":"1"}}`,
		"null_option":      prefix + `,"workflow_options":null}`,
		"bad_seed":         prefix + `,"workflow_options":{"seed":-1}}`,
		"bad_fps":          prefix + `,"workflow_options":{"fps":121}}`,
		"bad_strength":     prefix + `,"workflow_options":{"strength":0.5}}`,
		"mixed_references": prefix + `,"workflow_options":{"references":[{"asset_id":"asset_first01","role":"scene"},{"asset_id":"asset_other01","role":"character"}]}}`,
		"duplicate_option": prefix + `,"workflow_options":{"seed":1,"seed":2}}`,
		"file_id":          `{"model":"studio-ref-video-v1","prompt":"x","input_reference":{"file_id":"file_unmapped"}}`,
		"external_url":     `{"model":"studio-ref-video-v1","prompt":"x","input_reference":{"image_url":"http://127.0.0.1/private.png"}}`,
		"string_reference": `{"model":"studio-ref-video-v1","prompt":"x","input_reference":"asset_first01"}`,
	} {
		t.Run(name, func(t *testing.T) {
			status, _, _ := f.call(t, "POST", "/v1/videos", body, nil)
			if status != 400 {
				t.Fatal("invalid input accepted", status)
			}
			assertUploadClean(t, f)
		})
	}
	for name, parts := range map[string][]editPart{
		"duplicate_file":   {{"model", []byte("studio-ref-video-v1"), false}, {"prompt", []byte("x"), false}, {"input_reference", fixtureImage, true}, {"input_reference", fixtureImage, true}},
		"wrong_file":       {{"model", []byte("studio-ref-video-v1"), false}, {"prompt", []byte("x"), false}, {"input_reference", []byte("invalid image"), true}},
		"duplicate_prompt": {{"model", []byte("studio-ref-video-v1"), false}, {"prompt", []byte("x"), false}, {"prompt", []byte("y"), false}, {"input_reference", fixtureImage, true}},
	} {
		t.Run(name, func(t *testing.T) {
			body, kind := editMultipart(t, parts)
			status, _, _ := f.call(t, "POST", "/v1/videos", body, map[string]string{"Content-Type": kind})
			if status != 400 {
				t.Fatal("invalid multipart accepted", status)
			}
			assertUploadClean(t, f)
		})
	}
	if countRows(t, `SELECT count(*) FROM render_control_asset_uploads WHERE api_key_id=$1`, f.subject.keyID) != 0 || countRows(t, `SELECT count(*) FROM render_control_intents WHERE api_key_id=$1`, f.subject.keyID) != 0 {
		t.Fatal("invalid inputs produced persistent side effects")
	}
}

func TestVideoReferenceChecksPermissionTargetAndCapacityBeforeUpload(t *testing.T) {
	f := referenceVideoSetup(t)
	body := `{"model":"studio-ref-video-v1","prompt":"x","input_reference":{"image_url":"` + imageDataURL(fixtureImage) + `"}}`
	for _, scopes := range [][]string{{"videos:create", "assets:upload"}, {"jobs:create", "assets:upload"}, {"jobs:create", "videos:create"}} {
		f.g.configure(func(g *recoveryGateway) { g.policyScopes = scopes })
		status, _, _ := f.call(t, "POST", "/v1/videos", body, nil)
		if status != 403 {
			t.Fatal("missing video permission accepted", status)
		}
	}
	f.g.configure(func(g *recoveryGateway) { g.policyScopes = nil })
	for range cap(f.handler.uploadSlots) {
		f.handler.uploadSlots <- struct{}{}
	}
	status, _, _ := f.call(t, "POST", "/v1/videos", body, nil)
	for range cap(f.handler.uploadSlots) {
		<-f.handler.uploadSlots
	}
	if status != 429 || countRows(t, `SELECT count(*) FROM render_control_asset_uploads WHERE api_key_id=$1`, f.subject.keyID) != 0 {
		t.Fatal("capacity rejection uploaded data", status)
	}
	asset := uploadFixtureAsset(t, f, "video-original-target")
	seed := Intent{Instance: f.subject.instance, UserID: f.subject.userID, KeyID: f.subject.keyID, TrustedIP: f.subject.ip, TargetGatewayID: "gateway_" + strings.Repeat("b", 32)}
	other := coordinatorGateway(t, seed)
	target, _ := NewTarget(seed.TargetGatewayID)
	f.config.Clients[seed.TargetGatewayID] = other.client
	f.config.Models[1].Target = target
	f.start(t)
	status, _, _ = f.call(t, "POST", "/v1/videos", `{"model":"studio-ref-video-v1","prompt":"x","input_reference":{"asset_id":"`+asset.ID+`"}}`, nil)
	if status != 404 || countRows(t, `SELECT count(*) FROM render_control_intents WHERE api_key_id=$1`, f.subject.keyID) != 0 {
		t.Fatal("reference moved between gateways", status)
	}
	assertUploadClean(t, f)
}

func TestHostedVideoMultipartFallbackAndConfiguredRoutingCleanSpool(t *testing.T) {
	directory := hostingDirectory(t)
	for _, configured := range []bool{false, true} {
		body, kind := editMultipart(t, []editPart{{"input_reference", fixtureImage, true}, {"prompt", []byte("x"), false}, {"model", []byte("video-model"), false}})
		h := &hostedHandler{uploadDirectory: directory, uploadSlots: make(chan struct{}, 2), models: map[string]bool{"video-model": configured}, stock: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if _, deadline := r.Context().Deadline(); deadline || r.Header.Get("Content-Type") != kind {
				t.Error("stock routing changed context or multipart type")
			}
			actual, err := io.ReadAll(r.Body)
			if err != nil || string(actual) != body {
				t.Error("stock routing changed body")
			}
			w.WriteHeader(204)
		}), provider: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(401) })}
		request := httptest.NewRequest("POST", "/v1/videos", strings.NewReader(body))
		request.Header.Set("Content-Type", kind)
		recorder := httptest.NewRecorder()
		h.ServeHTTP(recorder, request)
		want := 204
		if configured {
			want = 401
		}
		files, err := os.ReadDir(directory)
		if recorder.Code != want || err != nil || len(files) != 0 || len(h.uploadSlots) != 0 {
			t.Fatal("video routing or cleanup failed", recorder.Code, err)
		}
	}
}

func TestWorkflowScalarOptionsNormalizeAcrossImageSurfaces(t *testing.T) {
	f := editSetup(t, false)
	body := jsonEdit(t, []string{imageDataURL(fixtureImage)}, map[string]any{"seed": 17, "negative_prompt": "blur", "strength": 0.5})
	status, raw, _ := f.call(t, "POST", "/v1/images/edits/async", body, nil)
	if status != 202 {
		t.Fatal("edit options rejected", status, string(raw))
	}
	_, request, err := f.s.Recover(ctx(), resultID(t, raw))
	if err != nil || request.Input["negative_prompt"] != "blur" || request.Parameters["seed"] != json.Number("17") || request.Parameters["strength"] != json.Number("0.5") {
		t.Fatal("edit option projection lost parameters")
	}
	status, _, _ = f.call(t, "POST", "/v1/images/generations/async", `{"model":"studio-image-v1","prompt":"x","workflow_options":{"seed":13,"negative_prompt":"blur"}}`, nil)
	if status != 202 {
		t.Fatal("generation options rejected", status)
	}
	assertUploadClean(t, f)
}
