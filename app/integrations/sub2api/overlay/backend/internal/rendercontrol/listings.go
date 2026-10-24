package rendercontrol

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/url"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/Wei-Shaw/sub2api/internal/rendercontrol/controlclient"
	"github.com/Wei-Shaw/sub2api/internal/service"
	"github.com/gin-gonic/gin"
	"github.com/lib/pq"
)

func listingQuery(c *gin.Context, jobs bool) (controlclient.ListQuery, error) {
	values, err := listingValues(c.Request.URL.RawQuery)
	if err != nil {
		return controlclient.ListQuery{}, err
	}
	query := controlclient.ListQuery{Limit: 50}
	for name, value := range values {
		if len(value) != 1 || value[0] == "" {
			return query, ErrInvalid
		}
		switch name {
		case "limit":
			query.Limit, err = strconv.Atoi(value[0])
			if err != nil || query.Limit < 1 || query.Limit > 100 || strconv.Itoa(query.Limit) != value[0] {
				return query, ErrInvalid
			}
		case "cursor":
			if !asciiPublic(value[0], 1, 8192) {
				return query, errCursorInvalid
			}
			query.Cursor = value[0]
		case "model":
			if len(value[0]) > 128 || !publicModelAlias(value[0]) {
				return query, ErrInvalid
			}
			query.Model = value[0]
		case "status":
			if !jobs || !listingStatus(value[0]) {
				return query, ErrInvalid
			}
			query.Status = value[0]
		default:
			return query, ErrInvalid
		}
	}
	return query, nil
}

func publicModelAlias(value string) bool {
	if len(value) < 1 || len(value) > 128 {
		return false
	}
	for i, ch := range value {
		if !(ch >= 'a' && ch <= 'z' || ch >= 'A' && ch <= 'Z' || ch >= '0' && ch <= '9' || i > 0 && (ch == '.' || ch == '_' || ch == '-')) {
			return false
		}
	}
	return true
}

func listingStatus(status string) bool {
	switch status {
	case "admission_pending", "queued", "leased", "preparing", "submitting", "running", "collecting", "uploading", "verifying", "reconciling", "retry_wait", "cancel_requested", "succeeded", "failed", "cancelled", "expired":
		return true
	}
	return false
}

type contentStream struct {
	Target string `json:"target"`
	Model  string `json:"model"`
	Task   string `json:"task"`
}

func (h *PublicHandler) collectionStreams(key *service.APIKey, model string) []contentStream {
	streams := []contentStream{}
	for _, rule := range h.models {
		if model != "" && model != rule.model.Model {
			continue
		}
		if _, err := h.rule(key, rule.model.Model, rule.model.TaskType); err == nil {
			streams = append(streams, contentStream{rule.model.Target.gatewayID, rule.model.Model, rule.model.TaskType})
		}
	}
	sort.Slice(streams, func(i, j int) bool {
		if streams[i].Target != streams[j].Target {
			return streams[i].Target < streams[j].Target
		}
		return streams[i].Model < streams[j].Model
	})
	return streams
}

func listingScope(query controlclient.ListQuery, streams []contentStream, targets []string) string {
	query.Cursor = ""
	value, _ := json.Marshal(struct {
		Version int
		Query   controlclient.ListQuery
		Streams []contentStream
		Targets []string
	}{1, query, streams, targets})
	return digest(value)
}

func (h *PublicHandler) listContext(c *gin.Context) (context.Context, func(), error) {
	select {
	case h.listSlots <- struct{}{}:
	default:
		return nil, nil, errListCapacity
	}
	ctx, cancel := context.WithTimeout(c.Request.Context(), 10*time.Second)
	return ctx, func() { cancel(); <-h.listSlots }, nil
}

type publicContent struct {
	ID        string `json:"id"`
	JobID     string `json:"job_id"`
	Bytes     int64  `json:"bytes"`
	MediaType string `json:"media_type"`
	SHA256    string `json:"sha256"`
	CreatedAt int64  `json:"created_at"`
	ExpiresAt int64  `json:"expires_at"`
}

func (h *PublicHandler) listContent(c *gin.Context) {
	subject, key, err := h.identity(c)
	if err != nil {
		publicError(c, err)
		return
	}
	query, err := listingQuery(c, false)
	if err != nil {
		publicError(c, err)
		return
	}
	ctx, release, err := h.listContext(c)
	if err != nil {
		publicError(c, err)
		return
	}
	defer release()
	streams := h.collectionStreams(key, query.Model)
	scope := listingScope(query, streams, nil)
	position, err := h.store.readListingCursor(subject, "content", scope, query.Cursor, time.Now())
	if err != nil || position.Stream > len(streams) {
		if err == nil {
			err = errCursorInvalid
		}
		publicError(c, err)
		return
	}
	current, _ := subject.GatewaySubject()
	items := []publicContent{}
	seen := map[string]bool{}
	for steps := 0; position.Stream < len(streams) && len(items) < query.Limit && steps < 8; steps++ {
		stream := streams[position.Stream]
		page, err := h.clients[stream.Target].ListContent(ctx, current, controlclient.ListQuery{Limit: query.Limit - len(items), Cursor: position.Remote, Model: stream.Model})
		var call *controlclient.CallError
		if err != nil && errors.As(err, &call) && (call.HTTPStatus == 401 || call.HTTPStatus == 403) {
			position.Stream++
			position.Remote = ""
			continue
		}
		if err != nil {
			publicError(c, err)
			return
		}
		for _, item := range page.Data {
			if item.TaskType != stream.Task || seen[item.ID] {
				publicError(c, ErrConflict)
				return
			}
			seen[item.ID] = true
		}
		if err := h.store.bindDiscoveredContent(ctx, subject, stream.Target, page.Data); err != nil {
			publicError(c, err)
			return
		}
		for _, item := range page.Data {
			jobID := item.JobID
			if validID("rc", item.ExternalTaskRef) {
				jobID = item.ExternalTaskRef
			}
			items = append(items, publicContent{item.ID, jobID, item.Bytes, item.MediaType, item.SHA256, item.CreatedAt, item.ExpiresAt})
		}
		if page.NextCursor != nil {
			position.Remote = *page.NextCursor
		} else {
			position.Stream++
			position.Remote = ""
		}
	}
	var next *string
	if position.Stream < len(streams) {
		next, err = h.store.writeListingCursor(subject, "content", scope, position)
		if err != nil {
			publicError(c, err)
			return
		}
	}
	c.Header("Cache-Control", "private, no-store")
	c.JSON(http.StatusOK, controlclient.CursorPage[publicContent]{Object: "list", Data: items, HasMore: next != nil, NextCursor: next})
}

func modelTasks(streams []contentStream) []string {
	values := make([]string, 0, len(streams))
	for _, stream := range streams {
		values = append(values, stream.Model+":"+stream.Task)
	}
	sort.Strings(values)
	return values
}

func (s *Store) listingTargets(ctx context.Context, subject Subject, streams []contentStream) ([]string, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT DISTINCT target_gateway_id FROM render_control_intents WHERE instance=$1 AND user_id=$2 AND api_key_id=$3 AND public_model||':'||task_type=ANY($4) ORDER BY target_gateway_id LIMIT 65`, subject.instance, subject.userID, subject.keyID, pq.Array(modelTasks(streams)))
	if err != nil {
		return nil, ErrStorage
	}
	defer rows.Close()
	targets := []string{}
	for rows.Next() {
		var target string
		if rows.Scan(&target) != nil || !gatewayPattern.MatchString(target) {
			return nil, ErrStorage
		}
		targets = append(targets, target)
	}
	if rows.Err() != nil || len(targets) > 64 {
		return nil, ErrStorage
	}
	return targets, nil
}

func (s *Store) listingCandidates(ctx context.Context, subject Subject, streams []contentStream, targets []string, position listingPosition) ([]Intent, error) {
	statement := `SELECT ` + columns + ` FROM render_control_intents WHERE instance=$1 AND user_id=$2 AND api_key_id=$3 AND public_model||':'||task_type=ANY($4) AND target_gateway_id=ANY($5) AND created_at<=$6`
	args := []any{subject.instance, subject.userID, subject.keyID, pq.Array(modelTasks(streams)), pq.Array(targets), position.Through}
	if position.ID != "" {
		statement += ` AND (created_at,id)<($7,$8)`
		args = append(args, position.After, position.ID)
	}
	rows, err := s.db.QueryContext(ctx, statement+` ORDER BY created_at DESC,id DESC LIMIT 101`, args...)
	if err != nil {
		return nil, ErrStorage
	}
	defer rows.Close()
	items := []Intent{}
	for rows.Next() {
		item, err := scanIntent(rows)
		if err != nil {
			return nil, err
		}
		items = append(items, item)
	}
	if rows.Err() != nil {
		return nil, ErrStorage
	}
	return items, nil
}

func (h *PublicHandler) listJobs(c *gin.Context) {
	subject, key, err := h.identity(c)
	if err != nil {
		publicError(c, err)
		return
	}
	query, err := listingQuery(c, true)
	if err != nil {
		publicError(c, err)
		return
	}
	ctx, release, err := h.listContext(c)
	if err != nil {
		publicError(c, err)
		return
	}
	defer release()
	streams := h.collectionStreams(key, query.Model)
	targets, err := h.store.listingTargets(ctx, subject, streams)
	if err != nil {
		publicError(c, err)
		return
	}
	targetSet := map[string]bool{}
	for _, target := range targets {
		targetSet[target] = true
	}
	for _, stream := range streams {
		targetSet[stream.Target] = true
	}
	targets = targets[:0]
	for target := range targetSet {
		targets = append(targets, target)
	}
	sort.Strings(targets)
	current, _ := subject.GatewaySubject()
	allowed := []string{}
	for _, target := range targets {
		client := h.clients[target]
		if client == nil {
			publicError(c, ErrStorage)
			return
		}
		policy, err := client.DeliveryPolicy(ctx, current)
		var call *controlclient.CallError
		if err != nil && errors.As(err, &call) && (call.HTTPStatus == 401 || call.HTTPStatus == 403) {
			continue
		}
		if err != nil {
			publicError(c, err)
			return
		}
		if policy.Allows("jobs:read") {
			allowed = append(allowed, target)
		}
	}
	scope := listingScope(query, streams, allowed)
	position, err := h.store.readListingCursor(subject, "jobs", scope, query.Cursor, time.Now())
	if err != nil {
		publicError(c, err)
		return
	}
	rows, err := h.store.listingCandidates(ctx, subject, streams, allowed, position)
	if err != nil {
		publicError(c, err)
		return
	}
	items := []publicJob{}
	processed := 0
	for _, row := range rows {
		if processed == 100 || len(items) == query.Limit {
			break
		}
		item, err := h.project(ctx, subject, row)
		if err != nil {
			publicError(c, err)
			return
		}
		position.After, position.ID = row.CreatedAt, row.ID
		processed++
		if query.Status == "" || query.Status == item.Status {
			items = append(items, item)
		}
	}
	var next *string
	if processed < len(rows) {
		next, err = h.store.writeListingCursor(subject, "jobs", scope, position)
		if err != nil {
			publicError(c, err)
			return
		}
	}
	c.Header("Cache-Control", "private, no-store")
	c.JSON(http.StatusOK, controlclient.CursorPage[publicJob]{Object: "list", Data: items, HasMore: next != nil, NextCursor: next})
}

// Keep URL decoding explicit: URL.Query silently discards malformed parameters.
func listingValues(raw string) (url.Values, error) {
	if len(raw) > 16384 || strings.Contains(raw, ";") {
		return nil, ErrInvalid
	}
	values, err := url.ParseQuery(raw)
	if err != nil {
		return nil, ErrInvalid
	}
	return values, nil
}
