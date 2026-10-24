package rendercontrol

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/Wei-Shaw/sub2api/internal/rendercontrol/controlclient"
	"github.com/gin-gonic/gin"
)

func strictPublicObject(raw []byte) (map[string]any, error) {
	if len(raw) == 0 || len(raw) > 2<<20 || !utf8.Valid(raw) {
		return nil, ErrInvalid
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	if validateJSON(decoder, 0, new(int)) != nil {
		return nil, ErrInvalid
	}
	if _, err := decoder.Token(); err != io.EOF {
		return nil, ErrInvalid
	}
	decoder = json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	var object map[string]any
	if decoder.Decode(&object) != nil || object == nil {
		return nil, ErrInvalid
	}
	return object, nil
}

func parseGeneration(raw []byte, video bool) (controlclient.JobRequest, string, error) {
	root, err := strictPublicObject(raw)
	if err != nil {
		return controlclient.JobRequest{}, "", err
	}
	fields := []string{"model", "prompt", "n", "size", "quality", "response_format", "user", "workflow_options"}
	task, format := "text_to_image", "b64_json"
	if video {
		fields = []string{"model", "prompt", "size", "quality", "seconds", "references", "workflow_options"}
		task = "text_to_video"
	}
	if !properties(root, fields...) {
		return controlclient.JobRequest{}, "", ErrInvalid
	}
	input := map[string]any{"prompt": root["prompt"]}
	parameters := map[string]any{}
	for _, name := range []string{"n", "size", "quality", "seconds"} {
		if value, present := root[name]; present {
			if video && name == "seconds" {
				if text, ok := value.(string); ok {
					value = json.Number(text)
				}
			}
			parameters[name] = value
		}
	}
	options := map[string]any{}
	if value, present := root["workflow_options"]; present {
		var valid bool
		options, valid = value.(map[string]any)
		names := []string{"seed", "negative_prompt"}
		if video {
			names = append(names, "fps", "references")
		}
		if !valid || !properties(options, names...) {
			return controlclient.JobRequest{}, "", ErrInvalid
		}
	}
	for _, name := range []string{"seed", "fps"} {
		if value, present := options[name]; present {
			parameters[name] = value
		}
	}
	if value, present := options["negative_prompt"]; present {
		input["negative_prompt"] = value
	}
	if refs, present := options["references"]; present {
		items, valid := refs.([]any)
		if _, duplicate := root["references"]; duplicate || !valid || len(items) < 2 || len(items) > 4 {
			return controlclient.JobRequest{}, "", ErrInvalid
		}
		root["references"] = refs
	}
	native := map[string]any{"model": root["model"], "task_type": task, "input": input, "parameters": parameters}
	if video {
		if references, present := root["references"]; present {
			input["references"], native["task_type"] = references, "references_to_video"
		}
	} else {
		if value, present := root["response_format"]; present {
			if value != "url" && value != "b64_json" {
				return controlclient.JobRequest{}, "", ErrInvalid
			}
			format = value.(string)
		}
		if user, present := root["user"]; present {
			native["client_request_id"] = user
		}
	}
	encoded, err := json.Marshal(native)
	if err != nil {
		return controlclient.JobRequest{}, "", ErrInvalid
	}
	request, err := ParsePublicRequest(encoded)
	return request, format, err
}

func compatibilityStatus(status string, video bool) string {
	if status == "succeeded" {
		return "completed"
	}
	if status == "failed" || status == "cancelled" || status == "expired" {
		return "failed"
	}
	if video {
		if status == "admission_pending" || status == "queued" || status == "leased" {
			return "queued"
		}
		return "in_progress"
	}
	return "processing"
}

func compatibilityView(job publicJob, video bool) gin.H {
	path, object := "/v1/images/tasks/", "image.generation.task"
	if video {
		path, object = "/v1/videos/", "video"
	}
	result := gin.H{"id": job.ID, "object": object, "model": job.Model, "status": compatibilityStatus(job.Status, video), "job_status": job.Status, "created_at": job.CreatedAt, "poll_url": path + job.ID}
	if !video {
		result["task_id"] = job.ID
	}
	return result
}

func (h *PublicHandler) generateImageAsync(c *gin.Context) {
	h.createGeneration(c, false)
}

func (h *PublicHandler) createVideo(c *gin.Context) {
	h.videoGeneration(c)
}

func (h *PublicHandler) createGeneration(c *gin.Context, video bool) {
	raw, ok := publicJSON(c)
	if !ok {
		return
	}
	request, _, err := parseGeneration(raw, video)
	if err != nil {
		publicError(c, err)
		return
	}
	i, err := h.reserveRequest(c, request)
	if err != nil {
		publicError(c, err)
		return
	}
	result := compatibilityView(localPublicJob(i), video)
	c.Header("Location", result["poll_url"].(string))
	c.Header("Retry-After", "2")
	c.JSON(http.StatusAccepted, result)
}

func (h *PublicHandler) imageTask(c *gin.Context) { h.compatibilityTask(c, false) }
func (h *PublicHandler) videoTask(c *gin.Context) { h.compatibilityTask(c, true) }

func (h *PublicHandler) compatibilityTask(c *gin.Context, video bool) {
	subject, i, err := h.load(c)
	if err == nil && strings.HasSuffix(i.TaskType, "video") != video {
		err = ErrNotFound
	}
	if err != nil {
		publicError(c, err)
		return
	}
	job, err := h.project(c.Request.Context(), subject, i)
	if err != nil {
		publicError(c, err)
		return
	}
	result := compatibilityView(job, video)
	if !video && job.Status == "succeeded" && len(job.Artifacts) == 1 && h.publicOrigin != "" {
		link, err := h.authorizedImageURL(c.Request.Context(), subject, i, job.Artifacts[0].ID)
		if err != nil {
			var call *controlclient.CallError
			if !errors.As(err, &call) || call.HTTPStatus != 401 && call.HTTPStatus != 403 && call.HTTPStatus != 404 {
				publicError(c, err)
				return
			}
		} else {
			result["http_status"], result["image_url"] = 200, link
			result["result"] = gin.H{"created": job.CreatedAt, "data": []gin.H{{"url": link}}}
		}
	}
	c.JSON(http.StatusOK, result)
}

func (h *PublicHandler) authorizedImageURL(ctx context.Context, subject Subject, i Intent, id string) (string, error) {
	if h.publicOrigin == "" {
		return "", ErrInvalid
	}
	file, err := h.openMedia(ctx, subject, i, id, true, "")
	if err != nil {
		return "", err
	}
	defer file.Body.Close()
	if !strings.HasPrefix(file.MediaType, "image/") {
		return "", ErrConflict
	}
	return h.publicOrigin + "/v1/content/" + id + "/download", nil
}

func (h *PublicHandler) generateImage(c *gin.Context) {
	raw, ok := publicJSON(c)
	if !ok {
		return
	}
	request, format, err := parseGeneration(raw, false)
	if err != nil || format == "url" && h.publicOrigin == "" {
		publicError(c, ErrInvalid)
		return
	}
	h.waitImage(c, request, format, nil)
}

func (h *PublicHandler) waitImage(c *gin.Context, request controlclient.JobRequest, format string, prepare func(Subject, publicRule) (controlclient.JobRequest, error)) {
	select {
	case h.syncSlots <- struct{}{}:
		defer func() { <-h.syncSlots }()
	default:
		c.Header("Retry-After", "2")
		c.AbortWithStatusJSON(429, gin.H{"error": gin.H{"type": "rate_limit_error", "code": "sync_capacity", "message": "Synchronous capacity is full"}})
		return
	}
	subject, key, err := h.identity(c)
	if err != nil {
		publicError(c, err)
		return
	}
	rule, err := h.requestRule(c, key, request.Model, request.TaskType)
	if err != nil {
		publicError(c, err)
		return
	}
	current, _ := subject.GatewaySubject()
	policy, err := h.clients[rule.model.Target.gatewayID].DeliveryPolicy(c.Request.Context(), current)
	if err != nil {
		publicError(c, err)
		return
	}
	if !policy.Allows("images:create", "jobs:create", "jobs:read", "artifacts:read", "content:read", "content:download") {
		publicError(c, ErrDenied)
		return
	}
	if prepare != nil {
		request, err = prepare(subject, rule)
		if err != nil {
			publicError(c, err)
			return
		}
	}
	i, err := h.reserveRequest(c, request)
	if err != nil {
		publicError(c, err)
		return
	}
	publicID := i.ID
	ctx, cancel := context.WithTimeout(c.Request.Context(), h.syncWait)
	defer cancel()
	var job publicJob
	for {
		var loaded Intent
		loaded, err = h.store.Load(ctx, subject, publicID)
		if err == nil {
			i = loaded
			job, err = h.project(ctx, subject, i)
		}
		if err != nil {
			if ctx.Err() != nil {
				h.syncTimeout(c, publicID)
			} else {
				publicError(c, err)
			}
			return
		}
		if job.Status == "succeeded" {
			break
		}
		if compatibilityStatus(job.Status, false) == "failed" {
			c.AbortWithStatusJSON(502, gin.H{"error": gin.H{"type": "api_error", "code": "generation_failed", "message": "Generation did not complete", "job_id": i.ID, "poll_url": "/v1/jobs/" + i.ID}})
			return
		}
		timer := time.NewTimer(min(time.Second, h.syncWait))
		select {
		case <-ctx.Done():
			timer.Stop()
			h.syncTimeout(c, i.ID)
			return
		case <-timer.C:
		}
	}
	if len(job.Artifacts) != 1 {
		publicError(c, ErrDenied)
		return
	}
	id := job.Artifacts[0].ID
	data := gin.H{}
	if format == "url" {
		link, err := h.authorizedImageURL(c.Request.Context(), subject, i, id)
		if err != nil {
			publicError(c, err)
			return
		}
		data["url"] = link
	} else {
		file, err := h.openMedia(c.Request.Context(), subject, i, id, false, "")
		if err != nil {
			publicError(c, err)
			return
		}
		image, err := file.ReadImage()
		if err != nil {
			publicError(c, err)
			return
		}
		data["b64_json"] = base64.StdEncoding.EncodeToString(image)
	}
	c.JSON(http.StatusOK, gin.H{"created": job.CreatedAt, "data": []gin.H{data}})
}

func (h *PublicHandler) syncTimeout(c *gin.Context, id string) {
	c.Header("Location", "/v1/jobs/"+id)
	c.AbortWithStatusJSON(http.StatusGatewayTimeout, gin.H{"error": gin.H{"type": "api_error", "code": "sync_wait_timeout", "message": "Generation continues asynchronously", "job_id": id, "poll_url": "/v1/jobs/" + id}})
}

func (h *PublicHandler) videoContent(c *gin.Context) {
	subject, i, err := h.load(c)
	if err == nil && !strings.HasSuffix(i.TaskType, "video") {
		err = ErrNotFound
	}
	if err != nil {
		publicError(c, err)
		return
	}
	job, err := h.project(c.Request.Context(), subject, i)
	if err != nil {
		publicError(c, err)
		return
	}
	if job.Status != "succeeded" || len(job.Artifacts) != 1 {
		publicError(c, ErrConflict)
		return
	}
	h.download(c, subject, i, job.Artifacts[0].ID)
}
