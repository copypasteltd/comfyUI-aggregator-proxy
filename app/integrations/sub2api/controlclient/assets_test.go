package controlclient

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

var assetID = "asset_" + strings.Repeat("a", 32)

func assetSum() string { sum := sha256.Sum256(mediaSample); return hex.EncodeToString(sum[:]) }
func assetResponse() UploadedAsset {
	return UploadedAsset{ID: assetID, Object: "asset", Status: "ready", Bytes: int64(len(mediaSample)), SHA256: assetSum(), MediaType: "image/png", Width: 32, Height: 32, ExpiresAt: time.Now().Add(time.Hour).Unix()}
}

func TestAssetUploadUsesOneBoundedMultipartAndCurrentSubject(t *testing.T) {
	var calls atomic.Int32
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		if r.URL.Path != "/gateway/v1/assets" || r.Method != "POST" || r.Header.Get("Idempotency-Key") != "persisted-upload" || r.Header.Get("Authorization") != "Bearer "+testToken || r.Header.Get("X-External-Key-Ref") != "instance-one:key:72" || r.Header.Get("Cookie") != "" || r.ContentLength <= int64(len(mediaSample)) {
			t.Error("upload authority/framing changed")
		}
		reader, err := r.MultipartReader()
		if err != nil {
			t.Error(err)
			return
		}
		part, err := reader.NextPart()
		if err != nil {
			t.Error(err)
			return
		}
		data, err := io.ReadAll(part)
		if err != nil || part.FormName() != "file" || part.FileName() != "input" || !bytes.Equal(data, mediaSample) {
			t.Error("upload bytes or safe filename changed")
		}
		if _, err = reader.NextPart(); err != io.EOF {
			t.Error("unexpected upload part")
		}
		w.WriteHeader(201)
		_ = json.NewEncoder(w).Encode(assetResponse())
	}))
	defer server.Close()
	client := testClient(t, server, 1024)
	result, err := client.UploadAsset(context.Background(), testSubject(t), bytes.NewReader(mediaSample), int64(len(mediaSample)), assetSum(), "image/png", "persisted-upload")
	if err != nil || result.ID != assetID || calls.Load() != 1 {
		t.Fatal("valid upload failed or retried", err)
	}
}

func TestAssetUploadRejectsUntrustedResponsesAndDoesNotRetry(t *testing.T) {
	for name, change := range map[string]func(*UploadedAsset){
		"foreign_id": func(a *UploadedAsset) { a.ID = "asset_private/path" }, "wrong_hash": func(a *UploadedAsset) { a.SHA256 = strings.Repeat("0", 64) }, "wrong_size": func(a *UploadedAsset) { a.Bytes++ }, "wrong_type": func(a *UploadedAsset) { a.MediaType = "video/mp4" }, "wrong_state": func(a *UploadedAsset) { a.Status = "pending" }, "zero_width": func(a *UploadedAsset) { a.Width = 0 }, "pixel_budget": func(a *UploadedAsset) { a.Width = 25000000; a.Height = 2 }, "missing_expiry": func(a *UploadedAsset) { a.ExpiresAt = 0 },
	} {
		t.Run(name, func(t *testing.T) {
			var calls atomic.Int32
			server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls.Add(1)
				_, _ = io.Copy(io.Discard, r.Body)
				result := assetResponse()
				change(&result)
				w.WriteHeader(201)
				_ = json.NewEncoder(w).Encode(result)
			}))
			defer server.Close()
			client := testClient(t, server, 1024)
			_, err := client.UploadAsset(context.Background(), testSubject(t), bytes.NewReader(mediaSample), int64(len(mediaSample)), assetSum(), "image/png", "persisted-upload")
			var call *CallError
			if !errors.As(err, &call) || !call.Uncertain || calls.Load() != 1 {
				t.Fatal("untrusted response accepted or retried", err)
			}
		})
	}
}

func TestAssetUploadRejectsInputsBeforeHTTP(t *testing.T) {
	var calls atomic.Int32
	server := httptest.NewTLSServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) { calls.Add(1) }))
	defer server.Close()
	client := testClient(t, server, 1024)
	for _, value := range []struct {
		size             int64
		sha, kind, token string
	}{{0, assetSum(), "image/png", "token"}, {MaxAssetBytes + 1, assetSum(), "image/png", "token"}, {1, "private-sha", "image/png", "token"}, {1, assetSum(), "video/mp4", "token"}, {1, assetSum(), "image/png", "bad token"}} {
		if _, err := client.UploadAsset(context.Background(), testSubject(t), bytes.NewReader(mediaSample), value.size, value.sha, value.kind, value.token); err == nil {
			t.Fatal("invalid upload accepted")
		}
	}
	if calls.Load() != 0 {
		t.Fatal("invalid upload made HTTP request")
	}
}

func TestAssetUploadResponseLossAndRedirectStayUncertain(t *testing.T) {
	for _, status := range []int{0, 302, 503, 403} {
		t.Run(strconv.Itoa(status), func(t *testing.T) {
			var calls atomic.Int32
			server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls.Add(1)
				_, _ = io.Copy(io.Discard, r.Body)
				if status == 0 {
					connection, _, err := w.(http.Hijacker).Hijack()
					if err != nil {
						t.Error(err)
						return
					}
					_ = connection.Close()
					return
				}
				w.Header().Set("Location", "https://private.invalid/secret")
				w.WriteHeader(status)
			}))
			defer server.Close()
			client := testClient(t, server, 1024)
			_, err := client.UploadAsset(context.Background(), testSubject(t), bytes.NewReader(mediaSample), int64(len(mediaSample)), assetSum(), "image/png", "persisted-upload")
			var call *CallError
			if !errors.As(err, &call) || call.Uncertain != (status != 403) || calls.Load() != 1 || strings.Contains(err.Error(), "private.invalid") {
				t.Fatal("upload uncertainty or secret boundary failed", err)
			}
		})
	}
}

func TestAssetMetadataDeleteAndMediaUseFixedOriginalPaths(t *testing.T) {
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.HasPrefix(r.URL.Path, "/gateway/v1/assets/"+assetID) || r.Header.Get("Authorization") != "Bearer "+testToken || r.Header.Get("X-External-Key-Ref") != "instance-one:key:72" {
			t.Error("asset request target/subject changed")
		}
		if r.Method == "DELETE" {
			if r.Header.Get("Idempotency-Key") != "delete-upload" {
				t.Error("delete token omitted")
			}
			w.WriteHeader(202)
			_ = json.NewEncoder(w).Encode(ContentDeletion{ID: assetID, Status: "delete_requested"})
			return
		}
		if r.URL.Path == "/gateway/v1/assets/"+assetID {
			_ = json.NewEncoder(w).Encode(ContentMetadata{ID: assetID, Bytes: int64(len(mediaSample)), MediaType: "image/png", SHA256: assetSum(), ExpiresAt: time.Now().Add(time.Hour).Unix()})
			return
		}
		mediaHeaders(w)
		data := mediaSample
		if r.Header.Get("Range") != "" {
			w.Header().Set("Content-Range", "bytes 1-3/"+strconv.Itoa(len(data)))
			w.Header().Set("Content-Length", "3")
			w.WriteHeader(206)
			data = data[1:4]
		}
		if r.Method != "HEAD" {
			_, _ = w.Write(data)
		}
	}))
	defer server.Close()
	client := testClient(t, server, 1024)
	if _, err := client.Asset(context.Background(), testSubject(t), assetID); err != nil {
		t.Fatal(err)
	}
	if _, err := client.DeleteAsset(context.Background(), testSubject(t), assetID, "delete-upload"); err != nil {
		t.Fatal(err)
	}
	for name, value := range map[string]struct {
		head bool
		part string
	}{"full": {}, "head": {head: true}, "range": {part: "bytes=1-3"}, "head_range": {head: true, part: "bytes=1-3"}} {
		t.Run(name, func(t *testing.T) {
			file, err := client.OpenAsset(context.Background(), testSubject(t), assetID, value.head, value.part)
			if err != nil {
				t.Fatal(err)
			}
			defer file.Body.Close()
			data, err := io.ReadAll(file.Body)
			if err != nil || value.head && len(data) != 0 || !value.head && len(data) == 0 {
				t.Fatal("asset stream failed", err)
			}
		})
	}
}

func TestAssetRangeCannotHideOversizedOrNonImageObject(t *testing.T) {
	for name, kind := range map[string]string{"oversized_range": "image/png", "not_image": "video/mp4"} {
		t.Run(name, func(t *testing.T) {
			server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				mediaHeaders(w)
				w.Header().Set("Content-Type", kind)
				w.Header().Set("Content-Length", "3")
				total := int64(len(mediaSample))
				if name == "oversized_range" {
					total = MaxAssetBytes + 1
				}
				w.Header().Set("Content-Range", "bytes 1-3/"+strconv.FormatInt(total, 10))
				w.WriteHeader(206)
				_, _ = w.Write(mediaSample[1:4])
			}))
			defer server.Close()
			client := testClient(t, server, 1024)
			if file, err := client.OpenAsset(context.Background(), testSubject(t), assetID, false, "bytes=1-3"); err == nil {
				_ = file.Body.Close()
				t.Fatal("unsafe asset stream accepted")
			}
		})
	}
}
