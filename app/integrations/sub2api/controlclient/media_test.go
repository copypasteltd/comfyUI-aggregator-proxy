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

var contentID = "content_" + strings.Repeat("c", 32)
var mediaSample = []byte("synthetic-small-media-sample")

func sampleETag() string {
	sum := sha256.Sum256(mediaSample)
	return `"` + hex.EncodeToString(sum[:]) + `"`
}

func mediaHeaders(w http.ResponseWriter) {
	w.Header().Set("Content-Type", "image/png")
	w.Header().Set("Content-Length", strconv.Itoa(len(mediaSample)))
	w.Header().Set("ETag", sampleETag())
}

func TestMediaUsesCurrentSubjectAndBoundedHeadRanges(t *testing.T) {
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/gateway/v1/content/"+contentID+"/download" || r.Header.Get("Authorization") != "Bearer "+testToken || r.Header.Get("X-External-Key-Ref") != "instance-one:key:72" || r.Header.Get("X-Client-IP") != "192.0.2.10" || r.Header.Get("Accept-Encoding") != "identity" || r.Header.Get("Cookie") != "" {
			t.Error("media request authority or target changed")
		}
		mediaHeaders(w)
		if r.Header.Get("Range") != "" {
			w.Header().Set("Content-Range", "bytes 1-3/"+strconv.Itoa(len(mediaSample)))
			w.Header().Set("Content-Length", "3")
			w.WriteHeader(206)
			if r.Method != http.MethodHead {
				_, _ = w.Write(mediaSample[1:4])
			}
			return
		}
		if r.Method != http.MethodHead {
			_, _ = w.Write(mediaSample)
		}
	}))
	defer server.Close()
	client := testClient(t, server, 1024)
	for name, request := range map[string]struct {
		head bool
		part string
	}{"full": {}, "head": {head: true}, "range": {part: "bytes=1-3"}, "head_range": {head: true, part: "bytes=1-3"}} {
		t.Run(name, func(t *testing.T) {
			file, err := client.OpenContent(context.Background(), testSubject(t), contentID, request.head, request.part)
			if err != nil {
				t.Fatal(err)
			}
			defer file.Body.Close()
			data, err := io.ReadAll(file.Body)
			if err != nil || file.ETag != sampleETag() {
				t.Fatal("valid media failed", err)
			}
			expected := mediaSample
			if request.part != "" {
				expected = expected[1:4]
				if file.Status != 206 || file.Length != 3 {
					t.Fatal("range metadata mismatch")
				}
			}
			if request.head {
				expected = nil
			}
			if !bytes.Equal(data, expected) {
				t.Fatal("head or range body mismatch")
			}
		})
	}
	file, err := client.OpenContent(context.Background(), testSubject(t), contentID, false, "")
	if err != nil {
		t.Fatal(err)
	}
	data, err := file.ReadImage()
	if err != nil || !bytes.Equal(data, mediaSample) {
		t.Fatal("bounded SHA-256 image read failed", err)
	}
}

func TestMediaRejectsUnsafeResponsesAndRedirects(t *testing.T) {
	var redirected atomic.Int32
	target := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		redirected.Add(1)
		t.Error("media credential followed a redirect")
	}))
	defer target.Close()
	for name, change := range map[string]func(http.ResponseWriter){
		"redirect":          func(w http.ResponseWriter) { w.Header().Set("Location", target.URL); w.WriteHeader(302) },
		"unbounded_length":  func(w http.ResponseWriter) { w.Header().Del("Content-Length"); w.(http.Flusher).Flush() },
		"too_large":         func(w http.ResponseWriter) { w.Header().Set("Content-Length", strconv.FormatInt(MaxMediaBytes+1, 10)) },
		"unsafe_type":       func(w http.ResponseWriter) { w.Header().Set("Content-Type", "text/html") },
		"type_parameters":   func(w http.ResponseWriter) { w.Header().Set("Content-Type", "image/png; unsafe=1") },
		"compressed":        func(w http.ResponseWriter) { w.Header().Set("Content-Encoding", "gzip") },
		"weak_etag":         func(w http.ResponseWriter) { w.Header().Set("ETag", "W/"+sampleETag()) },
		"missing_etag":      func(w http.ResponseWriter) { w.Header().Del("ETag") },
		"unrequested_range": func(w http.ResponseWriter) { w.Header().Set("Content-Range", "bytes 0-1/2"); w.WriteHeader(206) },
	} {
		t.Run(name, func(t *testing.T) {
			server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { mediaHeaders(w); change(w); _, _ = w.Write(mediaSample) }))
			defer server.Close()
			client := testClient(t, server, 1024)
			file, err := client.OpenContent(context.Background(), testSubject(t), contentID, false, "")
			if file != nil {
				_ = file.Body.Close()
			}
			if err == nil || strings.Contains(err.Error(), testToken) || strings.Contains(err.Error(), target.URL) {
				t.Fatal("unsafe response accepted or secret exposed")
			}
		})
	}
	if redirected.Load() != 0 {
		t.Fatal("redirect followed")
	}
}

func TestMediaRangeValidationAndResponseBinding(t *testing.T) {
	for name, item := range map[string]struct {
		value string
		valid bool
	}{
		"none": {"", true}, "closed": {"bytes=0-3", true}, "open": {"bytes=2-", true}, "suffix": {"bytes=-3", true},
		"multi": {"bytes=0-1,3-4", false}, "wrong_unit": {"items=0-3", false}, "empty": {"bytes=-", false}, "zero_suffix": {"bytes=-0", false}, "backwards": {"bytes=4-3", false}, "overflow": {"bytes=999999999999999999999-", false}, "negative": {"bytes=-1-2", false}, "space": {"bytes= 1-2", false},
	} {
		t.Run(name, func(t *testing.T) {
			if ValidMediaRange(item.value) != item.valid {
				t.Fatal("range grammar mismatch")
			}
		})
	}
	for name, item := range map[string]struct {
		content, request string
		length           int64
		valid            bool
	}{
		"closed": {"bytes 1-3/10", "bytes=1-3", 3, true}, "open": {"bytes 3-9/10", "bytes=3-", 7, true}, "suffix": {"bytes 7-9/10", "bytes=-3", 3, true}, "clamp": {"bytes 0-9/10", "bytes=0-100", 10, true}, "oversuffix": {"bytes 0-9/10", "bytes=-100", 10, true},
		"mismatch": {"bytes 2-4/10", "bytes=1-3", 3, false}, "length": {"bytes 1-3/10", "bytes=1-3", 4, false}, "total": {"bytes 1-3/3", "bytes=1-3", 3, false}, "unknown": {"bytes 1-3/*", "bytes=1-3", 3, false}, "overflow": {"bytes 0-2/999999999999999999999", "bytes=0-2", 3, false},
	} {
		t.Run(name, func(t *testing.T) {
			if matchesRange(item.content, item.request, item.length) != item.valid {
				t.Fatal("content range did not bind requested bytes")
			}
		})
	}
}

func TestMediaRejectsRangeDowngradeAndImageCorruption(t *testing.T) {
	for name, mode := range map[string]string{"downgrade": "downgrade", "range_mismatch": "range_mismatch", "hash": "hash", "truncated": "truncated", "video_inline": "video_inline", "inline_size": "inline_size"} {
		t.Run(name, func(t *testing.T) {
			server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				mediaHeaders(w)
				switch mode {
				case "range_mismatch":
					w.Header().Set("Content-Range", "bytes 1-3/100")
					w.Header().Set("Content-Length", "3")
					w.WriteHeader(206)
				case "hash":
					w.Header().Set("ETag", `"`+strings.Repeat("0", 64)+`"`)
				case "truncated":
					w.Header().Set("Content-Length", strconv.Itoa(len(mediaSample)+1))
				case "video_inline":
					w.Header().Set("Content-Type", "video/mp4")
				case "inline_size":
					w.Header().Set("Content-Length", strconv.FormatInt(MaxInlineImageBytes+1, 10))
				}
				_, _ = w.Write(mediaSample)
			}))
			defer server.Close()
			client := testClient(t, server, 1024)
			part := ""
			if mode == "downgrade" || mode == "range_mismatch" {
				part = "bytes=0-2"
			}
			file, err := client.OpenContent(context.Background(), testSubject(t), contentID, false, part)
			if err == nil {
				_, err = file.ReadImage()
			}
			if err == nil {
				t.Fatal("invalid range or corrupt image accepted")
			}
		})
	}
}

func TestMediaBodyCloseCancelsInflightAndInvalidInputsDoNotCall(t *testing.T) {
	closed := make(chan struct{}, 1)
	var calls atomic.Int32
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		mediaHeaders(w)
		w.(http.Flusher).Flush()
		<-r.Context().Done()
		closed <- struct{}{}
	}))
	defer server.Close()
	client := testClient(t, server, 1024)
	for _, id := range []string{"content_bad", "../../etc", "job_" + strings.Repeat("c", 32)} {
		if _, err := client.OpenContent(context.Background(), testSubject(t), id, false, ""); err == nil {
			t.Fatal("invalid ID accepted")
		}
	}
	if _, err := client.OpenContent(context.Background(), Subject{}, contentID, false, ""); err == nil {
		t.Fatal("empty subject accepted")
	}
	if calls.Load() != 0 {
		t.Fatal("invalid input reached gateway")
	}
	file, err := client.OpenContent(context.Background(), testSubject(t), contentID, false, "")
	if err != nil {
		t.Fatal(err)
	}
	_ = file.Body.Close()
	select {
	case <-closed:
	case <-time.After(2 * time.Second):
		t.Fatal("closing media left its transfer alive")
	}
}

func TestDeliveryMetadataPolicyAndIdempotentDeletion(t *testing.T) {
	var deletes atomic.Int32
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("X-External-Tenant-Ref") != "instance-one:user:24" || r.Header.Get("Authorization") != "Bearer "+testToken {
			t.Error("metadata lost authenticated identity")
		}
		switch {
		case r.URL.Path == "/gateway/v1/access-policy":
			_ = json.NewEncoder(w).Encode(DeliveryPolicy{Scopes: []string{"jobs:read", "content:read", "content:download"}})
		case r.Method == http.MethodDelete:
			if r.Header.Get("Idempotency-Key") != "owned-delete" {
				t.Error("delete omitted explicit token")
			}
			deletes.Add(1)
			w.WriteHeader(202)
			_ = json.NewEncoder(w).Encode(ContentDeletion{ID: contentID, Status: "delete_requested"})
		default:
			_ = json.NewEncoder(w).Encode(ContentMetadata{ID: contentID, Bytes: int64(len(mediaSample)), MediaType: "image/png", SHA256: sampleETag()[1:65], ExpiresAt: 2000000000})
		}
	}))
	defer server.Close()
	client := testClient(t, server, 1024)
	policy, err := client.DeliveryPolicy(context.Background(), testSubject(t))
	if err != nil || !policy.Allows("jobs:read", "content:download") || policy.Allows("content:delete") {
		t.Fatal("policy projection failed")
	}
	metadata, err := client.Content(context.Background(), testSubject(t), contentID)
	if err != nil || metadata.ID != contentID {
		t.Fatal("valid metadata rejected", err)
	}
	for range 2 {
		result, err := client.DeleteContent(context.Background(), testSubject(t), contentID, "owned-delete")
		if err != nil || result.ID != contentID {
			t.Fatal("idempotent delete failed", err)
		}
	}
	if deletes.Load() != 2 {
		t.Fatal("unexpected implicit deletion retry")
	}
}

func TestMediaErrorsAreSafeAndMutatingUncertaintyIsPreserved(t *testing.T) {
	for _, status := range []int{302, 401, 403, 404, 409, 416, 429, 503} {
		t.Run(strconv.Itoa(status), func(t *testing.T) {
			server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.WriteHeader(status)
				_, _ = w.Write([]byte(testToken + " synthetic-private-body"))
			}))
			defer server.Close()
			client := testClient(t, server, 1024)
			_, err := client.OpenContent(context.Background(), testSubject(t), contentID, false, "")
			var call *CallError
			if !errors.As(err, &call) || call.HTTPStatus != status || call.Uncertain || strings.Contains(err.Error(), "private") || strings.Contains(err.Error(), testToken) {
				t.Fatal("unsafe media error")
			}
			_, err = client.DeleteContent(context.Background(), testSubject(t), contentID, "owned-delete")
			if !errors.As(err, &call) || call.Uncertain != (status == 302 || status >= 500) {
				t.Fatal("delete uncertainty changed")
			}
		})
	}
}

func TestJobArtifactsRequireSuccessfulUniquePublication(t *testing.T) {
	for name, item := range map[string]struct {
		status    string
		artifacts []Artifact
		valid     bool
	}{
		"succeeded":   {"succeeded", []Artifact{{ID: contentID}}, true},
		"unpublished": {"running", []Artifact{{ID: contentID}}, false},
		"duplicate":   {"succeeded", []Artifact{{ID: contentID}, {ID: contentID}}, false},
	} {
		t.Run(name, func(t *testing.T) {
			server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				_ = json.NewEncoder(w).Encode(Job{ID: jobID, Status: item.status, UpdatedRevision: 1, Artifacts: item.artifacts})
			}))
			defer server.Close()
			client := testClient(t, server, 1024)
			_, err := client.Job(context.Background(), testSubject(t), jobID)
			if (err == nil) != item.valid {
				t.Fatal("artifact publication invariant failed")
			}
		})
	}
}
