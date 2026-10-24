package controlclient

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
)

func listedContentSample() ListedContent {
	return ListedContent{ContentMetadata: ContentMetadata{ID: "content_" + strings.Repeat("a", 32), Bytes: 124, MediaType: "image/png", SHA256: strings.Repeat("f", 64), ExpiresAt: 2000000100}, JobID: jobID, CreatedAt: 2000000000, ExternalTaskRef: "rc_" + strings.Repeat("b", 32), Model: "studio-image-v1", TaskType: "text_to_image"}
}

func listedJobSample() ListedJob {
	return ListedJob{Job: Job{ID: jobID, Status: "queued", Model: "studio-image-v1", TaskType: "text_to_image", UpdatedRevision: 1}, Object: "generation.job", CreatedAt: 2000000000}
}

func TestCollectionsUseFixedRoutesCurrentIdentityAndSafeProjection(t *testing.T) {
	subject := testSubject(t)
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet || r.Header.Get("Authorization") != "Bearer "+testToken || r.Header.Get("X-External-Tenant-Ref") != subject.TenantRef() || r.Header.Get("X-External-Key-Ref") != subject.KeyRef() || r.Header.Get("X-Client-IP") != "192.0.2.10" || r.URL.Query().Get("limit") != "1" || r.URL.Query().Get("model") != "studio-image-v1" || r.URL.Query().Get("cursor") != "cursor+/=encoded" {
			t.Error("collection changed its fixed query or trusted authority")
		}
		var raw []byte
		if r.URL.Path == "/gateway/v1/jobs" {
			if r.URL.Query().Get("status") != "queued" {
				t.Error("job status filter omitted")
			}
			raw, _ = json.Marshal(CursorPage[ListedJob]{Object: "list", Data: []ListedJob{listedJobSample()}})
		} else if r.URL.Path == "/gateway/v1/content" {
			raw, _ = json.Marshal(CursorPage[ListedContent]{Object: "list", Data: []ListedContent{listedContentSample()}})
		} else {
			t.Error("arbitrary collection destination")
		}
		raw = append(raw[:len(raw)-1], []byte(`,"object_key":"must-not-forward","input":{"prompt":"must-not-forward"}}`)...)
		_, _ = w.Write(raw)
	}))
	defer server.Close()
	client := testClient(t, server, 0)
	query := ListQuery{Limit: 1, Model: "studio-image-v1", Cursor: "cursor+/=encoded", Status: "queued"}
	jobs, err := client.ListJobs(context.Background(), subject, query)
	if err != nil || len(jobs.Data) != 1 || jobs.Data[0].ID != jobID {
		t.Fatal("authorized jobs collection failed", err)
	}
	query.Status = ""
	content, err := client.ListContent(context.Background(), subject, query)
	raw, _ := json.Marshal(content)
	if err != nil || len(content.Data) != 1 || bytes.Contains(raw, []byte("must-not-forward")) {
		t.Fatal("authorized content lost its safe projection", err)
	}
}

func TestCollectionRejectsInvalidQueryBeforeNetwork(t *testing.T) {
	var calls atomic.Int32
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { calls.Add(1) }))
	defer server.Close()
	client := testClient(t, server, 0)
	for name, query := range map[string]ListQuery{
		"zero": {Limit: 0}, "negative": {Limit: -1}, "large": {Limit: 101},
		"cursor_control": {Limit: 1, Cursor: "x\n"}, "cursor_large": {Limit: 1, Cursor: strings.Repeat("x", 4097)},
		"model": {Limit: 1, Model: "../private"}, "status": {Limit: 1, Status: "anything"},
	} {
		t.Run(name, func(t *testing.T) {
			if _, err := client.ListJobs(context.Background(), testSubject(t), query); err == nil {
				t.Fatal("invalid collection query accepted")
			}
		})
	}
	if _, err := client.ListContent(context.Background(), testSubject(t), ListQuery{Limit: 1, Status: "queued"}); err == nil || calls.Load() != 0 {
		t.Fatal("unsupported content status or invalid query reached HTTP")
	}
}

func TestCollectionRejectsAmbiguousContinuationAndEnvelope(t *testing.T) {
	item, _ := json.Marshal(listedContentSample())
	valid := `{"object":"list","data":[` + string(item) + `],"has_more":true,"next_cursor":"next"}`
	for name, raw := range map[string]string{
		"null": `null`, "wrong_object": strings.Replace(valid, `"list"`, `"other"`, 1),
		"missing_bool":        strings.Replace(valid, `"has_more":true,`, "", 1),
		"bool_null":           strings.Replace(valid, `"has_more":true`, `"has_more":null`, 1),
		"missing_cursor":      strings.Replace(valid, `,"next_cursor":"next"`, "", 1),
		"empty_cursor":        strings.Replace(valid, `"next"`, `""`, 1),
		"loop_cursor":         strings.Replace(valid, `"next"`, `"current"`, 1),
		"cursor_without_more": strings.Replace(valid, `"has_more":true`, `"has_more":false`, 1),
		"more_without_cursor": strings.Replace(valid, `"next_cursor":"next"`, `"next_cursor":null`, 1),
		"null_data":           `{"object":"list","data":null,"has_more":false,"next_cursor":null}`,
		"empty_more":          `{"object":"list","data":[],"has_more":true,"next_cursor":"next"}`,
		"duplicate_ids":       strings.Replace(valid, string(item), string(item)+","+string(item), 1),
		"duplicate_keys":      strings.Replace(valid, `"object":"list"`, `"object":"list","object":"list"`, 1),
		"duplicate_nested":    strings.Replace(valid, `"bytes":124`, `"bytes":124,"bytes":1`, 1),
		"wrong_number":        strings.Replace(valid, `"bytes":124`, `"bytes":"124"`, 1),
		"trailing":            valid + " {}", "depth": strings.TrimSuffix(valid, "}") + `,"private":` + strings.Repeat("[", 18) + "0" + strings.Repeat("]", 18) + "}",
		"utf8":     strings.TrimSuffix(valid, "}") + `,"private":"` + string([]byte{0xff}) + `"}`,
		"oversize": strings.TrimSuffix(valid, "}") + `,"private":"` + strings.Repeat("x", 2<<20) + `"}`,
	} {
		t.Run(name, func(t *testing.T) {
			server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { _, _ = w.Write([]byte(raw)) }))
			defer server.Close()
			if _, err := testClient(t, server, 0).ListContent(context.Background(), testSubject(t), ListQuery{Limit: 1, Cursor: "current"}); err == nil {
				t.Fatal("ambiguous or oversized collection accepted")
			}
		})
	}
}

func TestCollectionRejectsInvalidAuthorizedContent(t *testing.T) {
	for name, change := range map[string]func(*ListedContent){
		"id": func(i *ListedContent) { i.ID = "../private" }, "job": func(i *ListedContent) { i.JobID = "rc_" + strings.Repeat("a", 32) },
		"reference": func(i *ListedContent) { i.ExternalTaskRef = "private\n" }, "empty_reference": func(i *ListedContent) { i.ExternalTaskRef = "" },
		"model": func(i *ListedContent) { i.Model = "other-model" }, "task": func(i *ListedContent) { i.TaskType = "unknown" },
		"media_task": func(i *ListedContent) { i.TaskType = "text_to_video" }, "media": func(i *ListedContent) { i.MediaType = "text/html" },
		"length": func(i *ListedContent) { i.Bytes = 0 }, "max_length": func(i *ListedContent) { i.Bytes = MaxMediaBytes + 1 },
		"sha": func(i *ListedContent) { i.SHA256 = "private" }, "created": func(i *ListedContent) { i.CreatedAt = 0 },
		"expiry": func(i *ListedContent) { i.ExpiresAt = i.CreatedAt }, "date_limit": func(i *ListedContent) { i.ExpiresAt = 253402300800 },
	} {
		t.Run(name, func(t *testing.T) {
			item := listedContentSample()
			change(&item)
			server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				_ = json.NewEncoder(w).Encode(CursorPage[ListedContent]{Object: "list", Data: []ListedContent{item}})
			}))
			defer server.Close()
			if _, err := testClient(t, server, 0).ListContent(context.Background(), testSubject(t), ListQuery{Limit: 1, Model: "studio-image-v1"}); err == nil {
				t.Fatal("invalid content route entered provider")
			}
		})
	}
}

func TestCollectionRejectsInvalidAuthorizedJobs(t *testing.T) {
	for name, change := range map[string]func(*ListedJob){
		"id": func(i *ListedJob) { i.ID = "rc_" + strings.Repeat("a", 32) }, "object": func(i *ListedJob) { i.Object = "job" },
		"created": func(i *ListedJob) { i.CreatedAt = 0 }, "revision": func(i *ListedJob) { i.UpdatedRevision = 0 },
		"status": func(i *ListedJob) { i.Status = "running" }, "model": func(i *ListedJob) { i.Model = "other" },
		"task": func(i *ListedJob) { i.TaskType = "private" }, "premature_artifacts": func(i *ListedJob) { i.Artifacts = []Artifact{{ID: "content_" + strings.Repeat("a", 32)}} },
	} {
		t.Run(name, func(t *testing.T) {
			item := listedJobSample()
			change(&item)
			server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				_ = json.NewEncoder(w).Encode(CursorPage[ListedJob]{Object: "list", Data: []ListedJob{item}})
			}))
			defer server.Close()
			if _, err := testClient(t, server, 0).ListJobs(context.Background(), testSubject(t), ListQuery{Limit: 1, Model: "studio-image-v1", Status: "queued"}); err == nil {
				t.Fatal("invalid job summary accepted")
			}
		})
	}
}

func TestCollectionCancellationDenialAndEmptyTerminalPage(t *testing.T) {
	for _, status := range []int{401, 403, 503} {
		server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(status)
			_, _ = w.Write([]byte(`{"error":{"code":"not_available","message":"synthetic-private"}}`))
		}))
		_, err := testClient(t, server, 0).ListContent(context.Background(), testSubject(t), ListQuery{Limit: 1})
		var call *CallError
		if !errors.As(err, &call) || call.HTTPStatus != status || call.Uncertain || strings.Contains(err.Error(), "synthetic-private") {
			t.Fatal("collection denial lost safe bounded semantics")
		}
		server.Close()
	}
	var calls atomic.Int32
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		_, _ = w.Write([]byte(`{"object":"list","data":[],"has_more":false,"next_cursor":null}`))
	}))
	defer server.Close()
	client := testClient(t, server, 0)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := client.ListJobs(ctx, testSubject(t), ListQuery{Limit: 1}); err == nil || calls.Load() != 0 {
		t.Fatal("cancelled collection reached gateway")
	}
	page, err := client.ListContent(context.Background(), testSubject(t), ListQuery{Limit: 100})
	if err != nil || len(page.Data) != 0 || page.HasMore || page.NextCursor != nil {
		t.Fatal("empty terminal content page rejected", err)
	}
}
