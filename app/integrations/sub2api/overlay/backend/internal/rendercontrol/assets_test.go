package rendercontrol

import (
	"bytes"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"os"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/Wei-Shaw/sub2api/internal/rendercontrol/controlclient"
)

func (g *recoveryGateway) serveAssets(w http.ResponseWriter, r *http.Request) {
	if g.assetDenied {
		gatewayTestError(w, 403)
		return
	}
	if g.assets == nil {
		g.assets = map[string]controlclient.UploadedAsset{}
		g.assetData = map[string][]byte{}
		g.assetDeleted = map[string]bool{}
	}
	if r.Method == "POST" && r.URL.Path == "/gateway/v1/assets" {
		token := r.Header.Get("Idempotency-Key")
		reader, err := r.MultipartReader()
		if err != nil {
			g.t.Error("upload multipart missing")
			gatewayTestError(w, 400)
			return
		}
		part, err := reader.NextPart()
		if err != nil {
			g.t.Error("upload part missing")
			gatewayTestError(w, 400)
			return
		}
		data, err := io.ReadAll(io.LimitReader(part, controlclient.MaxAssetBytes+1))
		if err != nil || len(data) == 0 || int64(len(data)) > controlclient.MaxAssetBytes {
			gatewayTestError(w, 413)
			return
		}
		sum := sha256.Sum256(data)
		sha := hex.EncodeToString(sum[:])
		var persisted string
		if testDB.QueryRow(`SELECT file_sha256 FROM render_control_asset_uploads WHERE id=$1 AND instance=$2 AND user_id=$3 AND api_key_id=$4 AND target_gateway_id=$5`, token, g.i.Instance, g.i.UserID, g.i.KeyID, g.i.TargetGatewayID).Scan(&persisted) != nil || persisted != sha {
			g.t.Error("upload preceded persisted owner/target/hash")
		}
		asset, exists := g.assets[token]
		if exists && asset.SHA256 != sha {
			gatewayTestError(w, 409)
			return
		}
		if !exists {
			id := "asset_" + digest([]byte(token))[:32]
			asset = controlclient.UploadedAsset{ID: id, Object: "asset", Status: "ready", Bytes: int64(len(data)), SHA256: sha, MediaType: http.DetectContentType(data), Width: 1, Height: 1, ExpiresAt: time.Now().Add(time.Hour).Unix()}
			g.assets[token] = asset
			g.assetData[id] = append([]byte(nil), data...)
		}
		if g.dropAssetAfter {
			g.dropAssetAfter = false
			dropProtocolResponse(g.t, w)
			return
		}
		if g.corruptAssetResponse {
			asset.SHA256 = strings.Repeat("0", 64)
		}
		w.WriteHeader(201)
		_ = json.NewEncoder(w).Encode(asset)
		return
	}
	id := strings.TrimSuffix(strings.TrimPrefix(r.URL.Path, "/gateway/v1/assets/"), "/content")
	var asset controlclient.UploadedAsset
	for _, item := range g.assets {
		if item.ID == id {
			asset = item
			break
		}
	}
	if asset.ID == "" || g.assetDeleted[id] {
		gatewayTestError(w, 404)
		return
	}
	if r.Method == "DELETE" {
		g.assetDeleted[id] = true
		w.WriteHeader(202)
		_ = json.NewEncoder(w).Encode(controlclient.ContentDeletion{ID: id, Status: "delete_requested"})
		return
	}
	if !strings.HasSuffix(r.URL.Path, "/content") {
		_ = json.NewEncoder(w).Encode(controlclient.ContentMetadata{ID: id, Bytes: asset.Bytes, SHA256: asset.SHA256, MediaType: asset.MediaType, ExpiresAt: asset.ExpiresAt})
		return
	}
	w.Header().Set("Content-Type", asset.MediaType)
	w.Header().Set("Content-Length", strconv.FormatInt(asset.Bytes, 10))
	w.Header().Set("ETag", `"`+asset.SHA256+`"`)
	w.Header().Set("Set-Cookie", "private-secret=never")
	w.Header().Set("Location", "https://private.invalid/never")
	data := g.assetData[id]
	if r.Header.Get("Range") != "" {
		if r.Header.Get("Range") != "bytes=1-3" {
			gatewayTestError(w, 416)
			return
		}
		w.Header().Set("Content-Range", "bytes 1-3/"+strconv.Itoa(len(data)))
		w.Header().Set("Content-Length", "3")
		w.WriteHeader(206)
		data = data[1:4]
	}
	if r.Method != "HEAD" {
		_, _ = w.Write(data)
	}
}

func assertUploadClean(t *testing.T, f *publicFixture) {
	t.Helper()
	files, err := os.ReadDir(f.config.UploadDirectory)
	if err != nil || len(files) != 0 || len(f.handler.uploadSlots) != 0 {
		t.Fatal("upload left temporary files or capacity", err)
	}
}

func assetMultipart(t *testing.T, data []byte) (string, string) {
	t.Helper()
	var body bytes.Buffer
	writer := multipart.NewWriter(&body)
	part, err := writer.CreateFormFile("file", "private-source.png")
	if err != nil {
		t.Fatal(err)
	}
	_, _ = part.Write(data)
	_ = writer.Close()
	return body.String(), writer.FormDataContentType()
}
func uploadFixtureAsset(t *testing.T, f *publicFixture, token string) controlclient.UploadedAsset {
	t.Helper()
	body, kind := assetMultipart(t, fixtureImage)
	status, raw, _ := f.call(t, "POST", "/v1/assets?model=studio-image-v1", body, map[string]string{"Content-Type": kind, "Idempotency-Key": token})
	var result controlclient.UploadedAsset
	if status != 201 || json.Unmarshal(raw, &result) != nil {
		t.Fatal("asset upload failed", status, string(raw))
	}
	assertUploadClean(t, f)
	return result
}

func editSetup(t *testing.T, references bool) *publicFixture {
	t.Helper()
	f := compatibilitySetup(t)
	task := "image_to_image"
	if references {
		task = "references_to_image"
	}
	f.config.Models = append(f.config.Models, PublicModelRule{Model: "studio-edit-v1", TaskType: task, Target: testTarget(t), PriceVersion: "edit-v1", UnitPrice: money(t, "1"), GroupIDs: []int64{f.groupID}, Defaults: map[string]any{"size": "1024x1024", "quality": "standard"}})
	f.start(t)
	return f
}
func imageDataURL(data []byte) string {
	return "data:image/png;base64," + base64.StdEncoding.EncodeToString(data)
}
func jsonEdit(t *testing.T, sources []string, options any) string {
	t.Helper()
	images := []any{}
	for _, source := range sources {
		images = append(images, map[string]any{"image_url": source})
	}
	root := map[string]any{"model": "studio-edit-v1", "prompt": "synthetic edit", "images": images}
	if options != nil {
		root["workflow_options"] = options
	}
	raw, err := json.Marshal(root)
	if err != nil {
		t.Fatal(err)
	}
	return string(raw)
}

func TestAssetUploadPersistsOriginalTargetAndRecoversLostReply(t *testing.T) {
	f := compatibilitySetup(t)
	f.g.configure(func(g *recoveryGateway) { g.dropAssetAfter = true })
	body, kind := assetMultipart(t, fixtureImage)
	status, _, _ := f.call(t, "POST", "/v1/assets?model=studio-image-v1", body, map[string]string{"Content-Type": kind, "Idempotency-Key": "lost-upload"})
	if status != 502 {
		t.Fatal("lost upload reply was accepted", status)
	}
	if countRows(t, `SELECT count(*) FROM render_control_asset_uploads WHERE api_key_id=$1`, f.subject.keyID) != 1 || countRows(t, `SELECT count(*) FROM render_control_asset_bindings WHERE upload_id IN (SELECT id FROM render_control_asset_uploads WHERE api_key_id=$1)`, f.subject.keyID) != 0 {
		t.Fatal("upload uncertainty lost intent or invented binding")
	}
	assertUploadClean(t, f)
	newSeed := Intent{Instance: f.subject.instance, UserID: f.subject.userID, KeyID: f.subject.keyID, TrustedIP: f.subject.ip, TargetGatewayID: "gateway_" + strings.Repeat("e", 32)}
	other := coordinatorGateway(t, newSeed)
	newTarget, _ := NewTarget(newSeed.TargetGatewayID)
	f.config.Clients[newSeed.TargetGatewayID] = other.client
	f.config.Models[0].Target = newTarget
	f.start(t)
	asset := uploadFixtureAsset(t, f, "lost-upload")
	if f.g.count("/gateway/v1/assets") != 2 || other.count("/gateway/v1/assets") != 0 {
		t.Fatal("upload recovery moved target or retried blindly")
	}
	upload, err := f.s.loadAsset(ctx(), f.subject, asset.ID)
	if err != nil || upload.GatewayID != testTarget(t).gatewayID {
		t.Fatal("asset original target not retained", err)
	}
	balances(t, f.subject, "10.00000000", "0.00000000", "0.00000000")
}

func TestAssetUploadConcurrentReplayAndDifferentBytesConflict(t *testing.T) {
	f := compatibilitySetup(t)
	body, kind := assetMultipart(t, fixtureImage)
	var wg sync.WaitGroup
	ids := make(chan string, 8)
	for range 8 {
		wg.Go(func() {
			status, raw, _ := f.call(t, "POST", "/v1/assets?model=studio-image-v1", body, map[string]string{"Content-Type": kind, "Idempotency-Key": "parallel-upload"})
			if status == 429 {
				return
			}
			var a controlclient.UploadedAsset
			if status != 201 || json.Unmarshal(raw, &a) != nil {
				t.Error("parallel upload failed", status)
				return
			}
			ids <- a.ID
		})
	}
	wg.Wait()
	close(ids)
	id := ""
	for value := range ids {
		if id != "" && id != value {
			t.Fatal("upload replay duplicated assets")
		}
		id = value
	}
	if id == "" {
		t.Fatal("no upload progressed")
	}
	bad := append(append([]byte(nil), fixtureImage...), []byte("different-bytes")...)
	body, kind = assetMultipart(t, bad)
	status, _, _ := f.call(t, "POST", "/v1/assets?model=studio-image-v1", body, map[string]string{"Content-Type": kind, "Idempotency-Key": "parallel-upload"})
	if status != 409 {
		t.Fatal("upload key rebound different bytes", status)
	}
	assertUploadClean(t, f)
}

func TestAssetOwnershipCurrentPolicyAndStreamingHeaders(t *testing.T) {
	f := compatibilitySetup(t)
	asset := uploadFixtureAsset(t, f, "owned-upload")
	for name, request := range map[string]struct{ method, suffix, part string }{"metadata": {"GET", "", ""}, "full": {"GET", "/content", ""}, "head": {"HEAD", "/content", ""}, "range": {"GET", "/content", "bytes=1-3"}, "head_range": {"HEAD", "/content", "bytes=1-3"}} {
		t.Run(name, func(t *testing.T) {
			headers := map[string]string{}
			if request.part != "" {
				headers["Range"] = request.part
			}
			status, body, result := f.call(t, request.method, "/v1/assets/"+asset.ID+request.suffix, "", headers)
			want := 200
			if request.part != "" {
				want = 206
			}
			if status != want || result.Get("Set-Cookie") != "" || result.Get("Location") != "" || request.method == "HEAD" && len(body) != 0 {
				t.Fatal("asset authorization/stream headers failed", status)
			}
		})
	}
	foreign := f.subject
	foreign.instance = "foreign-instance"
	if _, err := f.s.loadAsset(ctx(), foreign, asset.ID); !errors.Is(err, ErrNotFound) {
		t.Fatal("asset crossed instance", err)
	}
	foreign = f.subject
	foreign.keyID++
	if _, err := f.s.loadAsset(ctx(), foreign, asset.ID); !errors.Is(err, ErrNotFound) {
		t.Fatal("asset crossed key", err)
	}
	f.g.configure(func(g *recoveryGateway) { g.assetDenied = true })
	if status, _, _ := f.call(t, "GET", "/v1/assets/"+asset.ID+"/content", "", nil); status != 403 {
		t.Fatal("asset permission revocation ignored", status)
	}
	f.g.configure(func(g *recoveryGateway) { g.assetDenied = false })
	runSQL(t, `UPDATE users SET balance=0 WHERE id=$1`, f.subject.userID)
	runSQL(t, `UPDATE api_keys SET expires_at=NOW()-INTERVAL '1 hour' WHERE id=$1`, f.subject.keyID)
	if status, _, _ := f.call(t, "HEAD", "/v1/assets/"+asset.ID+"/content", "", nil); status != 200 {
		t.Fatal("owned asset read required new generation funds", status)
	}
}

func TestImageEditInlineAndNativeShareDurableIdempotency(t *testing.T) {
	f := editSetup(t, false)
	body := jsonEdit(t, []string{imageDataURL(fixtureImage)}, map[string]any{"strength": 0.6})
	status, raw, _ := f.call(t, "POST", "/v1/images/edits/async", body, map[string]string{"Idempotency-Key": "edit-cross-entry"})
	var result map[string]any
	_ = json.Unmarshal(raw, &result)
	if status != 202 {
		t.Fatal("inline edit failed", status, string(raw))
	}
	id, _ := result["id"].(string)
	_, request, err := f.s.Recover(ctx(), id)
	if err != nil {
		t.Fatal(err)
	}
	// Client idempotency distinguishes omitted parameters from explicit values;
	// the service fills these defaults only after freezing the client input hash.
	delete(request.Parameters, "size")
	delete(request.Parameters, "quality")
	native, _ := json.Marshal(request)
	status, raw, _ = f.call(t, "POST", "/v1/jobs", string(native), map[string]string{"Idempotency-Key": "edit-cross-entry"})
	var replay publicJob
	_ = json.Unmarshal(raw, &replay)
	if status != 202 || replay.ID != id {
		t.Fatal("native edit replay created new work", status, string(raw))
	}
	status, raw, _ = f.call(t, "POST", "/v1/images/edits/async", body, map[string]string{"Idempotency-Key": "edit-cross-entry"})
	_ = json.Unmarshal(raw, &result)
	if status != 202 || result["id"] != id || f.g.count("/gateway/v1/assets") != 1 {
		t.Fatal("inline replay reuploaded or reserved again", status)
	}
	assertUploadClean(t, f)
}

func TestImageEditReferencesPreserveOrderRolesAndRejectChangedRequest(t *testing.T) {
	f := editSetup(t, true)
	second := append(append([]byte(nil), fixtureImage...), byte(0))
	sources := []string{imageDataURL(fixtureImage), imageDataURL(second)}
	roles := map[string]any{"image_roles": []any{map[string]any{"index": 1, "role": "scene"}, map[string]any{"index": 0, "role": "character"}}}
	body := jsonEdit(t, sources, roles)
	status, raw, _ := f.call(t, "POST", "/v1/images/edits/async", body, map[string]string{"Idempotency-Key": "ordered-edit"})
	if status != 202 {
		t.Fatal("ordered edit failed", status, string(raw))
	}
	var result map[string]any
	_ = json.Unmarshal(raw, &result)
	_, request, err := f.s.Recover(ctx(), result["id"].(string))
	if err != nil {
		t.Fatal(err)
	}
	refs := request.Input["references"].([]any)
	if refs[0].(map[string]any)["role"] != "character" || refs[1].(map[string]any)["role"] != "scene" {
		t.Fatal("reference roles/order changed")
	}
	changed := jsonEdit(t, []string{sources[1], sources[0]}, roles)
	status, _, _ = f.call(t, "POST", "/v1/images/edits/async", changed, map[string]string{"Idempotency-Key": "ordered-edit"})
	if status != 409 {
		t.Fatal("reordered references reused generation", status)
	}
	assertUploadClean(t, f)
}

func TestImageEditSyncTimeoutAndReplayUseOriginalUploadedAsset(t *testing.T) {
	f := editSetup(t, false)
	body := jsonEdit(t, []string{imageDataURL(fixtureImage)}, nil)
	status, raw, _ := f.call(t, "POST", "/v1/images/edits", body, map[string]string{"Idempotency-Key": "sync-edit-recovery"})
	var failure map[string]map[string]any
	if status != 504 || json.Unmarshal(raw, &failure) != nil {
		t.Fatal("sync edit did not retain recoverable timeout", status, string(raw))
	}
	id, _ := failure["error"]["job_id"].(string)
	i, err := f.s.Load(ctx(), f.subject, id)
	if err != nil {
		t.Fatal(err)
	}
	completeCompatibility(t, f, i, false)
	status, raw, _ = f.call(t, "POST", "/v1/images/edits", body, map[string]string{"Idempotency-Key": "sync-edit-recovery"})
	var result struct{ Data []map[string]string }
	if status != 200 || json.Unmarshal(raw, &result) != nil || len(result.Data) != 1 {
		t.Fatal("sync edit replay delivery failed", status, string(raw))
	}
	decoded, err := base64.StdEncoding.DecodeString(result.Data[0]["b64_json"])
	if err != nil || !bytes.Equal(decoded, fixtureImage) || f.g.count("/gateway/v1/assets") != 1 || countRows(t, `SELECT count(*) FROM render_control_intents WHERE api_key_id=$1`, f.subject.keyID) != 1 {
		t.Fatal("sync edit replay uploaded or reserved twice")
	}
	assertUploadClean(t, f)
}

type editPart struct {
	name string
	data []byte
	file bool
}

func editMultipart(t *testing.T, parts []editPart) (string, string) {
	t.Helper()
	var body bytes.Buffer
	writer := multipart.NewWriter(&body)
	for _, part := range parts {
		var target io.Writer
		var err error
		if part.file {
			target, err = writer.CreateFormFile(part.name, "untrusted-source.png")
		} else {
			target, err = writer.CreateFormField(part.name)
		}
		if err != nil {
			t.Fatal(err)
		}
		if _, err = target.Write(part.data); err != nil {
			t.Fatal(err)
		}
	}
	if writer.Close() != nil {
		t.Fatal("multipart fixture close failed")
	}
	return body.String(), writer.FormDataContentType()
}

func TestImageEditMultipartMaskAndPublicURLNormalizeToSameRequest(t *testing.T) {
	f := editSetup(t, false)
	body, kind := editMultipart(t, []editPart{{"image", fixtureImage, true}, {"mask", fixtureImage, true}, {"model", []byte("studio-edit-v1"), false}, {"prompt", []byte("synthetic edit"), false}, {"workflow_options", []byte(`{"strength":0.6}`), false}})
	status, raw, _ := f.call(t, "POST", "/v1/images/edits/async", body, map[string]string{"Content-Type": kind, "Idempotency-Key": "multipart-mask"})
	if status != 202 {
		t.Fatal("multipart masked edit failed", status, string(raw))
	}
	id := resultID(t, raw)
	_, request, err := f.s.Recover(ctx(), id)
	if err != nil {
		t.Fatal(err)
	}
	source := request.Input["source_image"].(map[string]any)["asset_id"].(string)
	mask := request.Input["mask"].(map[string]any)["asset_id"].(string)
	root := map[string]any{"model": "studio-edit-v1", "prompt": "synthetic edit", "source_image": map[string]any{"image_url": f.config.PublicOrigin + "/v1/assets/" + source + "/content"}, "mask": map[string]any{"asset_id": mask}, "workflow_options": map[string]any{"strength": 0.6}}
	encoded, _ := json.Marshal(root)
	status, raw, _ = f.call(t, "POST", "/v1/images/edits/async", string(encoded), map[string]string{"Idempotency-Key": "multipart-mask"})
	if status != 202 || resultID(t, raw) != id || f.g.count("/gateway/v1/assets") != 1 {
		t.Fatal("public URL or mask changed frozen edit", status, string(raw))
	}
	assertUploadClean(t, f)
}

func TestImageEditRejectsInvalidInputsBeforeUploadOrReservation(t *testing.T) {
	f := editSetup(t, true)
	image := imageDataURL(fixtureImage)
	for name, body := range map[string]string{
		"unknown_field":         `{"model":"studio-edit-v1","prompt":"x","images":[{"image_url":"` + image + `"}],"callback_url":"https://untrusted.invalid"}`,
		"duplicate_model":       `{"model":"studio-edit-v1","model":"other","prompt":"x","images":[]}`,
		"external_url":          jsonEdit(t, []string{"https://private.invalid/image.png"}, nil),
		"wrong_magic":           jsonEdit(t, []string{"data:image/png;base64," + base64.StdEncoding.EncodeToString([]byte("not-an-image"))}, nil),
		"bad_base64":            jsonEdit(t, []string{"data:image/png;base64,%%%"}, nil),
		"missing_roles":         jsonEdit(t, []string{image, image}, nil),
		"repeated_role_index":   jsonEdit(t, []string{image, image}, map[string]any{"image_roles": []any{map[string]any{"index": 0, "role": "scene"}, map[string]any{"index": 0, "role": "character"}}}),
		"invalid_role":          jsonEdit(t, []string{image, image}, map[string]any{"image_roles": []any{map[string]any{"index": 0, "role": "Invalid Role"}, map[string]any{"index": 1, "role": "scene"}}}),
		"single_model_mismatch": jsonEdit(t, []string{image}, nil),
		"strength_range":        jsonEdit(t, []string{image}, map[string]any{"strength": 1.01}),
		"mixed_input":           `{"model":"studio-edit-v1","prompt":"x","images":[{"image_url":"` + image + `"}],"source_image":{"asset_id":"asset_` + strings.Repeat("a", 32) + `"}}`,
	} {
		t.Run(name, func(t *testing.T) {
			status, _, _ := f.call(t, "POST", "/v1/images/edits/async", body, nil)
			if status != 400 && status != 403 && status != 404 {
				t.Fatal("invalid edit accepted", status)
			}
			assertUploadClean(t, f)
		})
	}
	for name, parts := range map[string][]editPart{
		"mixed_images":        {{"model", []byte("studio-edit-v1"), false}, {"prompt", []byte("x"), false}, {"image", fixtureImage, true}, {"image[]", fixtureImage, true}},
		"duplicate_prompt":    {{"model", []byte("studio-edit-v1"), false}, {"prompt", []byte("x"), false}, {"prompt", []byte("y"), false}, {"image", fixtureImage, true}},
		"invalid_second_file": {{"model", []byte("studio-edit-v1"), false}, {"prompt", []byte("x"), false}, {"image[]", fixtureImage, true}, {"image[]", []byte("not-an-image"), true}},
	} {
		t.Run(name, func(t *testing.T) {
			body, kind := editMultipart(t, parts)
			status, _, _ := f.call(t, "POST", "/v1/images/edits/async", body, map[string]string{"Content-Type": kind})
			if status != 400 {
				t.Fatal("invalid multipart accepted", status)
			}
			assertUploadClean(t, f)
		})
	}
	if f.g.count("/gateway/v1/assets") != 0 || countRows(t, `SELECT count(*) FROM render_control_asset_uploads WHERE api_key_id=$1`, f.subject.keyID) != 0 || countRows(t, `SELECT count(*) FROM render_control_intents WHERE api_key_id=$1`, f.subject.keyID) != 0 {
		t.Fatal("invalid edit caused side effects")
	}
}

func TestImageEditPreflightPermissionTokenAndCapacityHaveNoUploadSideEffects(t *testing.T) {
	f := editSetup(t, false)
	body := jsonEdit(t, []string{imageDataURL(fixtureImage)}, nil)
	for _, missing := range []string{"assets:upload", "images:create", "content:download"} {
		t.Run(missing, func(t *testing.T) {
			scopes := []string{}
			for _, scope := range []string{"assets:upload", "images:create", "jobs:create", "jobs:read", "artifacts:read", "content:read", "content:download"} {
				if scope != missing {
					scopes = append(scopes, scope)
				}
			}
			f.g.configure(func(g *recoveryGateway) { g.policyScopes = scopes })
			status, _, _ := f.call(t, "POST", "/v1/images/edits", body, nil)
			if status != 403 {
				t.Fatal("edit skipped permission preflight", status)
			}
			assertUploadClean(t, f)
		})
	}
	f.g.configure(func(g *recoveryGateway) { g.policyScopes = nil })
	if status, _, _ := f.call(t, "POST", "/v1/images/edits/async", body, map[string]string{"Idempotency-Key": "invalid token"}); status != 400 {
		t.Fatal("invalid token accepted", status)
	}
	for range cap(f.handler.uploadSlots) {
		f.handler.uploadSlots <- struct{}{}
	}
	status, _, headers := f.call(t, "POST", "/v1/images/edits/async", body, nil)
	for range cap(f.handler.uploadSlots) {
		<-f.handler.uploadSlots
	}
	if status != 429 || headers.Get("Retry-After") == "" {
		t.Fatal("upload capacity not enforced", status)
	}
	if f.g.count("/gateway/v1/assets") != 0 || countRows(t, `SELECT count(*) FROM render_control_asset_uploads WHERE api_key_id=$1`, f.subject.keyID) != 0 || countRows(t, `SELECT count(*) FROM render_control_intents WHERE api_key_id=$1`, f.subject.keyID) != 0 {
		t.Fatal("preflight rejection uploaded or reserved")
	}
	assertUploadClean(t, f)
}

func TestAssetPublicationFailureRollsBackAndDatabaseGuardsRetainOriginalOwner(t *testing.T) {
	f := compatibilitySetup(t)
	sum := sha256.Sum256(fixtureImage)
	sha := hex.EncodeToString(sum[:])
	upload, err := f.s.reserveAsset(ctx(), f.subject, testTarget(t), "studio-image-v1", "publication-failure", sha, "image/png", int64(len(fixtureImage)))
	if err != nil {
		t.Fatal(err)
	}
	id := "asset_" + strings.Repeat("f", 32)
	if _, err = testDB.Exec(`UPDATE render_control_asset_uploads SET asset_id=$2 WHERE id=$1`, upload.ID, id); err == nil {
		t.Fatal("upload published without binding")
	}
	asset := controlclient.UploadedAsset{ID: id, Object: "asset", Status: "ready", SHA256: sha, MediaType: "image/png", Bytes: int64(len(fixtureImage)), Width: 0, Height: 1, ExpiresAt: time.Now().Add(time.Hour).Unix()}
	if err = f.s.finishAsset(ctx(), f.subject, upload, asset); !errors.Is(err, ErrStorage) {
		t.Fatal("invalid binding publication accepted", err)
	}
	if countRows(t, `SELECT count(*) FROM render_control_asset_bindings WHERE upload_id=$1`, upload.ID) != 0 || countRows(t, `SELECT count(*) FROM render_control_asset_uploads WHERE id=$1 AND asset_id IS NOT NULL`, upload.ID) != 0 {
		t.Fatal("failed publication partially committed")
	}
	asset.Width = 1
	if err = f.s.finishAsset(ctx(), f.subject, upload, asset); err != nil {
		t.Fatal(err)
	}
	for name, query := range map[string]string{"upload_target": `UPDATE render_control_asset_uploads SET target_gateway_id='gateway_` + strings.Repeat("e", 32) + `' WHERE id=$1`, "upload_hash": `UPDATE render_control_asset_uploads SET file_sha256='` + strings.Repeat("0", 64) + `' WHERE id=$1`, "binding_expiry": `UPDATE render_control_asset_bindings SET expires_at=expires_at+1 WHERE upload_id=$1`} {
		t.Run(name, func(t *testing.T) {
			if _, err := testDB.Exec(query, upload.ID); err == nil {
				t.Fatal("immutable asset identity changed")
			}
		})
	}
	foreign := fixture(t, "10")
	foreign.instance = f.subject.instance
	other, err := f.s.reserveAsset(ctx(), foreign, testTarget(t), "studio-image-v1", "foreign-collision", sha, "image/png", asset.Bytes)
	if err != nil {
		t.Fatal(err)
	}
	if err = f.s.finishAsset(ctx(), foreign, other, asset); !errors.Is(err, ErrConflict) {
		t.Fatal("foreign asset collision rebound owner", err)
	}
	if _, err = f.s.loadAsset(ctx(), foreign, id); !errors.Is(err, ErrNotFound) {
		t.Fatal("foreign owner read collision")
	}
	if current, err := f.s.loadAsset(ctx(), f.subject, id); err != nil || current.ID != upload.ID {
		t.Fatal("original binding lost", err)
	}
}

func TestAssetCorruptReplyDoesNotInventBindingAndReplayRecovers(t *testing.T) {
	f := compatibilitySetup(t)
	f.g.configure(func(g *recoveryGateway) { g.corruptAssetResponse = true })
	body, kind := assetMultipart(t, fixtureImage)
	status, _, _ := f.call(t, "POST", "/v1/assets", body, map[string]string{"Content-Type": kind, "Idempotency-Key": "corrupt-reply"})
	if status != 502 || countRows(t, `SELECT count(*) FROM render_control_asset_bindings WHERE upload_id IN (SELECT id FROM render_control_asset_uploads WHERE api_key_id=$1)`, f.subject.keyID) != 0 {
		t.Fatal("corrupt upload reply published ownership", status)
	}
	assertUploadClean(t, f)
	f.g.configure(func(g *recoveryGateway) { g.corruptAssetResponse = false })
	asset := uploadFixtureAsset(t, f, "corrupt-reply")
	status, _, _ = f.call(t, "DELETE", "/v1/assets/"+asset.ID, "", map[string]string{"Idempotency-Key": "delete-upload"})
	if status != 202 {
		t.Fatal("asset delete failed", status)
	}
	if status, _, _ = f.call(t, "GET", "/v1/assets/"+asset.ID, "", nil); status != 404 {
		t.Fatal("deleted asset was readable", status)
	}
	balances(t, f.subject, "10.00000000", "0.00000000", "0.00000000")
}

func TestHostedEditFallbackReplaysOriginalBodyAndCleansSpool(t *testing.T) {
	directory := hostingDirectory(t)
	slots := make(chan struct{}, 2)
	stockCalls, providerCalls := 0, 0
	var original, contentType string
	h := &hostedHandler{uploadDirectory: directory, uploadSlots: slots, models: map[string]bool{"studio-edit-v1": true}, stock: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		stockCalls++
		if _, hasDeadline := r.Context().Deadline(); hasDeadline {
			t.Error("stock fallback acquired provider deadline")
		}
		body, err := io.ReadAll(r.Body)
		if err != nil || string(body) != original || r.Header.Get("Content-Type") != contentType || r.ContentLength != int64(len(original)) {
			t.Error("fallback changed multipart/JSON bytes or headers")
		}
		w.WriteHeader(204)
	}), provider: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		providerCalls++
		deadline, hasDeadline := r.Context().Deadline()
		if !hasDeadline || time.Until(deadline) > providerBudget(r) {
			t.Error("provider budget omitted shared-route input phase")
		}
		w.WriteHeader(401)
	})}
	for _, multipartBody := range []bool{false, true} {
		if multipartBody {
			original, contentType = editMultipart(t, []editPart{{"image", []byte{0, 255, 13, 10, 7}, true}, {"model", []byte("other-stock-model"), false}, {"prompt", []byte(" untouched whitespace "), false}})
		} else {
			original = "{\n  \"model\": \"other-stock-model\", \"custom_stock_field\": true\n}"
			contentType = "application/json"
		}
		request := httptest.NewRequest("POST", "/v1/images/edits", strings.NewReader(original))
		request.Header.Set("Content-Type", contentType)
		recorder := httptest.NewRecorder()
		h.ServeHTTP(recorder, request)
		if recorder.Code != 204 {
			t.Fatal("stock edit fallback failed", recorder.Code)
		}
	}
	original = jsonEdit(t, []string{imageDataURL(fixtureImage)}, nil)
	request := httptest.NewRequest("POST", "/v1/images/edits/async", strings.NewReader(original))
	request.Header.Set("Content-Type", "application/json")
	recorder := httptest.NewRecorder()
	h.ServeHTTP(recorder, request)
	if recorder.Code != 401 || providerCalls != 1 || stockCalls != 2 {
		t.Fatal("hosted edit skipped provider auth routing")
	}
	files, err := os.ReadDir(directory)
	if err != nil || len(files) != 0 || len(slots) != 0 {
		t.Fatal("hosted fallback leaked spool or slots", err)
	}
}

func TestAssetReadRejectsMetadataThatContradictsPersistedUpload(t *testing.T) {
	f := compatibilitySetup(t)
	asset := uploadFixtureAsset(t, f, "read-integrity")
	f.g.configure(func(g *recoveryGateway) {
		for token, value := range g.assets {
			value.SHA256 = strings.Repeat("0", 64)
			g.assets[token] = value
		}
	})
	for _, route := range []struct{ method, suffix, part string }{{"GET", "", ""}, {"GET", "/content", ""}, {"HEAD", "/content", "bytes=1-3"}} {
		status, _, _ := f.call(t, route.method, "/v1/assets/"+asset.ID+route.suffix, "", map[string]string{"Range": route.part})
		if status != 409 {
			t.Fatal("contradictory asset metadata was delivered", status)
		}
	}
	if len(f.handler.mediaSlots) != 0 {
		t.Fatal("rejected asset read leaked media capacity")
	}
}

type interruptedUploadReader struct{}

func (interruptedUploadReader) Read([]byte) (int, error) { return 0, io.ErrUnexpectedEOF }

func TestUploadSizeTruncationAndEncodingRejectionsCleanOwnedFiles(t *testing.T) {
	f := editSetup(t, false)
	for name, source := range map[string]io.Reader{"size": strings.NewReader(strings.Repeat("x", 17)), "truncated": io.MultiReader(bytes.NewReader(fixtureImage[:1]), interruptedUploadReader{})} {
		t.Run(name, func(t *testing.T) {
			if file, err := spoolFile(f.config.UploadDirectory, source, 16); err == nil || file != nil {
				t.Fatal("invalid stream was retained")
			}
			assertUploadClean(t, f)
		})
	}
	body, kind := assetMultipart(t, append(append([]byte(nil), fixtureImage...), bytes.Repeat([]byte{0}, int(controlclient.MaxAssetBytes)-len(fixtureImage)+1)...))
	status, _, _ := f.call(t, "POST", "/v1/assets?model=studio-image-v1", body, map[string]string{"Content-Type": kind})
	if status != 413 {
		t.Fatal("oversized asset accepted", status)
	}
	assertUploadClean(t, f)
	body, kind = assetMultipart(t, fixtureImage)
	body = body[:len(body)-10]
	status, _, _ = f.call(t, "POST", "/v1/assets", body, map[string]string{"Content-Type": kind})
	if status != 400 {
		t.Fatal("truncated multipart accepted", status)
	}
	assertUploadClean(t, f)
	status, _, _ = f.call(t, "POST", "/v1/images/edits/async", jsonEdit(t, []string{imageDataURL(fixtureImage)}, nil), map[string]string{"Content-Encoding": "gzip"})
	if status != 400 {
		t.Fatal("compressed edit accepted", status)
	}
	assertUploadClean(t, f)
	if f.g.count("/gateway/v1/assets") != 0 || countRows(t, `SELECT count(*) FROM render_control_asset_uploads WHERE api_key_id=$1`, f.subject.keyID) != 0 {
		t.Fatal("invalid upload created remote or durable work")
	}
}

func TestImageEditExistingAssetTargetCheckedBeforeInlineMaskUpload(t *testing.T) {
	f := editSetup(t, false)
	asset := uploadFixtureAsset(t, f, "original-source")
	seed := Intent{Instance: f.subject.instance, UserID: f.subject.userID, KeyID: f.subject.keyID, TrustedIP: f.subject.ip, TargetGatewayID: "gateway_" + strings.Repeat("e", 32)}
	other := coordinatorGateway(t, seed)
	target, _ := NewTarget(seed.TargetGatewayID)
	f.config.Clients[seed.TargetGatewayID] = other.client
	f.config.Models[1].Target = target
	f.start(t)
	body := `{"model":"studio-edit-v1","prompt":"x","source_image":{"asset_id":"` + asset.ID + `"},"mask":{"image_url":"` + imageDataURL(fixtureImage) + `"}}`
	status, _, _ := f.call(t, "POST", "/v1/images/edits/async", body, nil)
	if status != 404 || other.count("/gateway/v1/assets") != 0 || f.g.count("/gateway/v1/assets") != 1 || countRows(t, `SELECT count(*) FROM render_control_intents WHERE api_key_id=$1`, f.subject.keyID) != 0 {
		t.Fatal("cross-target input uploaded mask or reserved task", status)
	}
	assertUploadClean(t, f)
}

func TestImageEditRejectsInvalidUTF8AndAcceptsSplitUnicodeInSpool(t *testing.T) {
	f := editSetup(t, false)
	body := jsonEdit(t, []string{imageDataURL(fixtureImage)}, nil)
	body = strings.Replace(body, "synthetic edit", string([]byte{'x', 0xff}), 1)
	status, _, _ := f.call(t, "POST", "/v1/images/edits/async", body, nil)
	if status != 400 || f.g.count("/gateway/v1/assets") != 0 || countRows(t, `SELECT count(*) FROM render_control_intents WHERE api_key_id=$1`, f.subject.keyID) != 0 {
		t.Fatal("invalid UTF-8 normalized into billable edit", status)
	}
	assertUploadClean(t, f)
	// Place a three-byte rune across the 64 KiB streaming boundary.
	value := strings.Repeat("x", (64<<10)-len(`{"text":"`)-1) + "图"
	raw := `{"text":"` + value + `"}`
	file, err := spoolFile(f.config.UploadDirectory, strings.NewReader(raw), 128<<20)
	if err != nil {
		t.Fatal(err)
	}
	object, err := strictSpoolObject(file.file)
	file.close()
	if err != nil || object["text"] != value {
		t.Fatal("valid split UTF-8 rune rejected", err)
	}
	assertUploadClean(t, f)
}
