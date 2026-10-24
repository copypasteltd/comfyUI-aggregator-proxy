package controlclient

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/url"
	"strconv"
)

type ListQuery struct {
	Limit  int
	Cursor string
	Model  string
	Status string
}

type CursorPage[T any] struct {
	Object     string  `json:"object"`
	Data       []T     `json:"data"`
	HasMore    bool    `json:"has_more"`
	NextCursor *string `json:"next_cursor"`
}

type ListedJob struct {
	Job
	Object    string `json:"object"`
	CreatedAt int64  `json:"created_at"`
}

type ListedContent struct {
	ContentMetadata
	JobID           string `json:"job_id"`
	CreatedAt       int64  `json:"created_at"`
	ExternalTaskRef string `json:"external_task_ref"`
	Model           string `json:"model"`
	TaskType        string `json:"task_type"`
}

func (q ListQuery) query(jobs bool) (string, error) {
	if q.Limit < 1 || q.Limit > 100 || q.Cursor != "" && !ascii(q.Cursor, 1, 4096) || q.Model != "" && !modelAliasPattern.MatchString(q.Model) || q.Status != "" && (!jobs || !jobStatus(q.Status)) {
		return "", &CallError{Code: "invalid_request"}
	}
	values := url.Values{"limit": {strconv.Itoa(q.Limit)}}
	if q.Cursor != "" {
		values.Set("cursor", q.Cursor)
	}
	if q.Model != "" {
		values.Set("model", q.Model)
	}
	if q.Status != "" {
		values.Set("status", q.Status)
	}
	return "?" + values.Encode(), nil
}

func readCursorPage[T any](c *Client, ctx context.Context, subject Subject, path string, query ListQuery, jobs bool) (CursorPage[T], error) {
	var empty CursorPage[T]
	parameters, err := query.query(jobs)
	if err != nil {
		return empty, err
	}
	var raw json.RawMessage
	if err = c.call(ctx, http.MethodGet, path+parameters, &subject, nil, &raw); err != nil {
		return empty, err
	}
	if catalogJSON(raw) != nil {
		return empty, &CallError{Code: "invalid_response"}
	}
	var fields map[string]json.RawMessage
	if json.Unmarshal(raw, &fields) != nil || fields == nil {
		return empty, &CallError{Code: "invalid_response"}
	}
	for _, field := range []string{"object", "data", "has_more", "next_cursor"} {
		if fields[field] == nil {
			return empty, &CallError{Code: "invalid_response"}
		}
	}
	flag := string(bytes.TrimSpace(fields["has_more"]))
	if flag != "true" && flag != "false" {
		return empty, &CallError{Code: "invalid_response"}
	}
	var result CursorPage[T]
	if json.Unmarshal(raw, &result) != nil || result.Object != "list" || result.Data == nil || len(result.Data) > query.Limit || result.HasMore && len(result.Data) == 0 || result.HasMore != (result.NextCursor != nil) || result.NextCursor != nil && (!ascii(*result.NextCursor, 1, 4096) || *result.NextCursor == query.Cursor) {
		return empty, &CallError{Code: "invalid_response"}
	}
	return result, nil
}

func (c *Client) ListJobs(ctx context.Context, subject Subject, query ListQuery) (CursorPage[ListedJob], error) {
	result, err := readCursorPage[ListedJob](c, ctx, subject, "/gateway/v1/jobs", query, true)
	if err != nil {
		return CursorPage[ListedJob]{}, err
	}
	seen := map[string]bool{}
	for _, job := range result.Data {
		if !identifier("job", job.ID) || seen[job.ID] || job.Object != "generation.job" || job.CreatedAt < 1 || job.CreatedAt > 253402300799 || job.UpdatedRevision < 1 || !jobStatus(job.Status) || query.Status != "" && query.Status != job.Status || !modelAliasPattern.MatchString(job.Model) || query.Model != "" && query.Model != job.Model || !knownTask(job.TaskType) || len(job.Artifacts) > 100 || len(job.Artifacts) > 0 && job.Status != "succeeded" {
			return CursorPage[ListedJob]{}, &CallError{Code: "invalid_response"}
		}
		artifacts := map[string]bool{}
		for _, artifact := range job.Artifacts {
			if !identifier("content", artifact.ID) || artifacts[artifact.ID] {
				return CursorPage[ListedJob]{}, &CallError{Code: "invalid_response"}
			}
			artifacts[artifact.ID] = true
		}
		seen[job.ID] = true
	}
	return result, nil
}

func knownTask(task string) bool {
	return task == "text_to_image" || task == "image_to_image" || task == "references_to_image" || task == "text_to_video" || task == "references_to_video" || task == "text_to_audio"
}

func (c *Client) ListContent(ctx context.Context, subject Subject, query ListQuery) (CursorPage[ListedContent], error) {
	result, err := readCursorPage[ListedContent](c, ctx, subject, "/gateway/v1/content", query, false)
	if err != nil {
		return CursorPage[ListedContent]{}, err
	}
	seen := map[string]bool{}
	for _, item := range result.Data {
		if !identifier("content", item.ID) || seen[item.ID] || !identifier("job", item.JobID) || !ascii(item.ExternalTaskRef, 1, 128) || !modelAliasPattern.MatchString(item.Model) || query.Model != "" && query.Model != item.Model || !knownTask(item.TaskType) || item.Bytes < 1 || item.Bytes > MaxMediaBytes || !allowedMedia(item.MediaType) || !taskMediaMatches(item.TaskType, item.MediaType) || !sha256Pattern.MatchString(item.SHA256) || item.CreatedAt < 1 || item.CreatedAt > 253402300799 || item.ExpiresAt <= item.CreatedAt || item.ExpiresAt > 253402300799 {
			return CursorPage[ListedContent]{}, &CallError{Code: "invalid_response"}
		}
		seen[item.ID] = true
	}
	return result, nil
}
