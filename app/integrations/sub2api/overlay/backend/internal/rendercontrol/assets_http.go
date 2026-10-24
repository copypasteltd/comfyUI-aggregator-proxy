package rendercontrol

import (
	"context"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"

	"github.com/Wei-Shaw/sub2api/internal/rendercontrol/controlclient"
	"github.com/Wei-Shaw/sub2api/internal/service"
	"github.com/gin-gonic/gin"
)

func (h *PublicHandler) assetRule(key *service.APIKey, model string) (publicRule, error) {
	if model != "" {
		rule, ok := h.models[model]
		if !ok {
			return publicRule{}, ErrNotFound
		}
		return h.rule(key, model, rule.model.TaskType)
	}
	var chosen publicRule
	for model, rule := range h.models {
		if _, err := h.rule(key, model, rule.model.TaskType); err != nil {
			continue
		}
		if chosen.model.Model != "" && chosen.model.Target.gatewayID != rule.model.Target.gatewayID {
			return publicRule{}, ErrInvalid
		}
		if chosen.model.Model == "" || model < chosen.model.Model {
			chosen = rule
		}
	}
	if chosen.model.Model == "" {
		return publicRule{}, ErrNotFound
	}
	return chosen, nil
}

func (h *PublicHandler) uploadAsset(c *gin.Context) {
	owned := c.Request.Context().Value(uploadOwnership{}) != true
	if !takeUpload(c, h) {
		return
	}
	defer releaseUpload(c, h, owned)
	subject, key, err := h.identity(c)
	if err != nil {
		publicError(c, err)
		return
	}
	query, err := url.ParseQuery(c.Request.URL.RawQuery)
	if err != nil || len(query) > 1 || len(query["model"]) > 1 || len(query) == 1 && query["model"] == nil {
		publicError(c, ErrInvalid)
		return
	}
	rule, err := h.assetRule(key, query.Get("model"))
	if err != nil {
		publicError(c, err)
		return
	}
	token, err := publicIdempotency(c)
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
	if !policy.Allows("assets:upload") {
		publicError(c, ErrDenied)
		return
	}
	reader, err := multipartReader(c.Request)
	if err != nil {
		publicError(c, err)
		return
	}
	part, err := reader.NextRawPart()
	if err != nil {
		publicError(c, ErrInvalid)
		return
	}
	if part.FormName() != "file" || part.FileName() == "" || part.Header.Get("Content-Transfer-Encoding") != "" {
		_ = part.Close()
		publicError(c, ErrInvalid)
		return
	}
	file, err := spoolFile(h.uploadDirectory, part, controlclient.MaxAssetBytes)
	_ = part.Close()
	if err != nil {
		publicError(c, err)
		return
	}
	defer file.close()
	if _, err = reader.NextRawPart(); err != io.EOF {
		publicError(c, ErrInvalid)
		return
	}
	asset, err := h.ingestAsset(c, subject, rule, file, "asset:"+digest([]byte(token)), false)
	if err != nil {
		publicError(c, err)
		return
	}
	c.JSON(http.StatusCreated, asset)
}

func (h *PublicHandler) assetOwner(c *gin.Context) (Subject, assetUpload, error) {
	subject, key, err := h.identity(c)
	if err != nil {
		return Subject{}, assetUpload{}, err
	}
	upload, err := h.store.loadAsset(c.Request.Context(), subject, c.Param("asset"))
	if err != nil {
		return Subject{}, assetUpload{}, err
	}
	if _, err = h.assetRule(key, upload.Model); err != nil {
		return Subject{}, assetUpload{}, err
	}
	if h.clients[upload.GatewayID] == nil {
		return Subject{}, assetUpload{}, ErrStorage
	}
	return subject, upload, nil
}

func (h *PublicHandler) assetMetadata(c *gin.Context) {
	subject, upload, err := h.assetOwner(c)
	if err != nil {
		publicError(c, err)
		return
	}
	current, _ := subject.GatewaySubject()
	metadata, err := h.clients[upload.GatewayID].Asset(c.Request.Context(), current, c.Param("asset"))
	if err == nil && (metadata.Bytes != upload.Bytes || metadata.SHA256 != upload.SHA256 || metadata.MediaType != upload.MediaType) {
		err = ErrConflict
	}
	if err != nil {
		publicError(c, err)
		return
	}
	c.JSON(http.StatusOK, metadata)
}

func (h *PublicHandler) assetDelete(c *gin.Context) {
	subject, upload, err := h.assetOwner(c)
	if err != nil {
		publicError(c, err)
		return
	}
	token, err := publicIdempotency(c)
	if err != nil {
		publicError(c, err)
		return
	}
	current, _ := subject.GatewaySubject()
	result, err := h.clients[upload.GatewayID].DeleteAsset(c.Request.Context(), current, c.Param("asset"), token)
	if err != nil {
		publicError(c, err)
		return
	}
	c.JSON(http.StatusAccepted, result)
}

func (h *PublicHandler) assetDownload(c *gin.Context) {
	subject, upload, err := h.assetOwner(c)
	if err != nil {
		publicError(c, err)
		return
	}
	ranges := c.Request.Header.Values("Range")
	if len(ranges) > 1 || len(ranges) == 1 && !controlclient.ValidMediaRange(ranges[0]) {
		c.AbortWithStatusJSON(416, gin.H{"error": gin.H{"type": "invalid_request_error", "code": "invalid_range", "message": "Use a single bytes range"}})
		return
	}
	select {
	case h.mediaSlots <- struct{}{}:
		defer func() { <-h.mediaSlots }()
	default:
		publicError(c, errDownloadCapacity)
		return
	}
	current, _ := subject.GatewaySubject()
	file, err := h.clients[upload.GatewayID].OpenAsset(c.Request.Context(), current, c.Param("asset"), c.Request.Method == http.MethodHead, c.GetHeader("Range"))
	if err != nil {
		publicError(c, err)
		return
	}
	defer file.Body.Close()
	if file.MediaType != upload.MediaType || file.ETag != `"`+upload.SHA256+`"` || file.ContentRange == "" && file.Length != upload.Bytes || file.ContentRange != "" && !strings.HasSuffix(file.ContentRange, "/"+strconv.FormatInt(upload.Bytes, 10)) {
		publicError(c, ErrConflict)
		return
	}
	c.Header("Content-Type", file.MediaType)
	c.Header("Content-Length", strconv.FormatInt(file.Length, 10))
	c.Header("Cache-Control", "private, no-store")
	c.Header("ETag", file.ETag)
	c.Header("Accept-Ranges", "bytes")
	c.Header("Content-Disposition", `attachment; filename="`+c.Param("asset")+`"`)
	if file.ContentRange != "" {
		c.Header("Content-Range", file.ContentRange)
	}
	c.Status(file.Status)
	if c.Request.Method != http.MethodHead {
		c.Writer.WriteHeaderNow()
		if _, err = io.CopyBuffer(c.Writer, file.Body, make([]byte, 64<<10)); err != nil {
			panic(http.ErrAbortHandler)
		}
	}
}

func (h *PublicHandler) inputAssetID(ctx context.Context, subject Subject, value any) (string, error) {
	item, ok := value.(map[string]any)
	if !ok {
		return "", ErrInvalid
	}
	id := ""
	if properties(item, "asset_id") {
		id, ok = item["asset_id"].(string)
		if !ok {
			return "", ErrInvalid
		}
	} else if properties(item, "image_url") {
		text, ok := item["image_url"].(string)
		if !ok || h.publicOrigin == "" {
			return "", ErrInvalid
		}
		parsed, err := url.Parse(text)
		origin, originErr := url.Parse(h.publicOrigin)
		if err != nil || originErr != nil || parsed.Scheme != origin.Scheme || parsed.Host != origin.Host || parsed.User != nil || parsed.RawQuery != "" || parsed.ForceQuery || parsed.Fragment != "" || parsed.RawPath != "" || !strings.HasPrefix(parsed.Path, "/v1/assets/") || !strings.HasSuffix(parsed.Path, "/content") {
			return "", ErrInvalid
		}
		id = strings.TrimSuffix(strings.TrimPrefix(parsed.Path, "/v1/assets/"), "/content")
	} else {
		return "", ErrInvalid
	}
	if !validID("asset", id) {
		return "", ErrInvalid
	}
	if _, err := h.store.loadAsset(ctx, subject, id); err != nil {
		return "", err
	}
	return id, nil
}
