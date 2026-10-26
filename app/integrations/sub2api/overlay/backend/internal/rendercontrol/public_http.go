package rendercontrol

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"mime"
	"net/http"
	"net/netip"
	"net/url"
	"sync/atomic"
	"time"

	pkgip "github.com/Wei-Shaw/sub2api/internal/pkg/ip"
	"github.com/Wei-Shaw/sub2api/internal/rendercontrol/controlclient"
	"github.com/Wei-Shaw/sub2api/internal/server/middleware"
	"github.com/Wei-Shaw/sub2api/internal/service"
	"github.com/gin-gonic/gin"
)

type PublicModelRule struct {
	Catalog        *controlclient.CatalogStamp
	UsageAccountID int64
	Model          string
	TaskType       string
	Target         Target
	PriceVersion   string
	UnitPrice      Money
	GroupIDs       []int64
	Defaults       map[string]any
}

type PublicConfig struct {
	Instance        string
	PublicOrigin    string
	SyncWait        time.Duration
	UploadDirectory string
	Models          []PublicModelRule
	Clients         map[string]*controlclient.Client
}

func (PublicConfig) String() string   { return "rendercontrol.PublicConfig{redacted}" }
func (PublicConfig) GoString() string { return "rendercontrol.PublicConfig{redacted}" }

type publicRule struct {
	model  PublicModelRule
	groups map[int64]bool
}

type PublicHandler struct {
	store           *Store
	instance        string
	models          map[string]publicRule
	catalog         atomic.Pointer[catalogSnapshot]
	clients         map[string]*controlclient.Client
	publicOrigin    string
	syncWait        time.Duration
	syncSlots       chan struct{}
	mediaSlots      chan struct{}
	uploadSlots     chan struct{}
	catalogSlots    chan struct{}
	listSlots       chan struct{}
	uploadDirectory string
}

func (*PublicHandler) String() string   { return "rendercontrol.PublicHandler{redacted}" }
func (*PublicHandler) GoString() string { return "rendercontrol.PublicHandler{redacted}" }

func configurationInput(task string) map[string]any {
	input := map[string]any{"prompt": "configuration validation"}
	if task == "image_to_image" {
		input["source_image"] = map[string]any{"asset_id": "asset_configuration_a"}
	}
	if task == "references_to_image" || task == "references_to_video" {
		input["references"] = []any{map[string]any{"asset_id": "asset_configuration_a", "role": "character"}, map[string]any{"asset_id": "asset_configuration_b", "role": "scene"}}
	}
	return input
}

func NewPublicHandler(store *Store, config PublicConfig) (*PublicHandler, error) {
	if store == nil || !instancePattern.MatchString(config.Instance) || len(config.Models) > 256 || len(config.Clients) < 1 || len(config.Clients) > 64 {
		return nil, ErrInvalid
	}
	if config.SyncWait == 0 {
		config.SyncWait = 60 * time.Second
	}
	if config.SyncWait < time.Millisecond || config.SyncWait > 60*time.Second {
		return nil, ErrInvalid
	}
	if config.PublicOrigin != "" {
		origin, err := url.Parse(config.PublicOrigin)
		if err != nil || origin.Scheme != "https" || origin.Hostname() == "" || origin.User != nil || origin.RawQuery != "" || origin.ForceQuery || origin.Fragment != "" || origin.RawFragment != "" || origin.Opaque != "" || origin.Path != "" && origin.Path != "/" {
			return nil, ErrInvalid
		}
		config.PublicOrigin = origin.Scheme + "://" + origin.Host
	}
	if config.UploadDirectory != "" && prepareUploadDirectory(config.UploadDirectory) != nil {
		return nil, ErrInvalid
	}
	h := &PublicHandler{store: store, instance: config.Instance, models: map[string]publicRule{}, clients: map[string]*controlclient.Client{}, publicOrigin: config.PublicOrigin, syncWait: config.SyncWait, syncSlots: make(chan struct{}, 8), mediaSlots: make(chan struct{}, 8), uploadSlots: make(chan struct{}, 2), catalogSlots: make(chan struct{}, 8), listSlots: make(chan struct{}, 8), uploadDirectory: config.UploadDirectory}
	for id, client := range config.Clients {
		if client == nil || !gatewayPattern.MatchString(id) || client.GatewayID() != id || !client.HTTPS() {
			return nil, ErrInvalid
		}
		h.clients[id] = client
	}
	for _, rule := range config.Models {
		if _, duplicate := h.models[rule.Model]; duplicate || h.clients[rule.Target.gatewayID] == nil || !labelPattern.MatchString(rule.PriceVersion) || rule.UnitPrice <= 0 || rule.UnitPrice > maxMoney || len(rule.GroupIDs) < 1 || len(rule.GroupIDs) > 1024 {
			return nil, ErrInvalid
		}
		if rule.Defaults == nil {
			rule.Defaults = map[string]any{}
		}
		if !properties(rule.Defaults, "size", "quality", "seconds", "n") {
			return nil, ErrInvalid
		}
		raw, err := json.Marshal(controlclient.JobRequest{Model: rule.Model, TaskType: rule.TaskType, Input: configurationInput(rule.TaskType), Parameters: rule.Defaults})
		if err != nil {
			return nil, ErrInvalid
		}
		parsed, err := ParsePublicRequest(raw)
		if err != nil {
			return nil, ErrInvalid
		}
		if rule.TaskType == "text_to_image" || rule.TaskType == "image_to_image" || rule.TaskType == "references_to_image" {
			if _, present := parsed.Parameters["size"]; !present {
				parsed.Parameters["size"] = "1024x1024"
			}
		}
		if _, err := NewRequest(publicOperation(rule.TaskType), parsed); err != nil {
			return nil, ErrInvalid
		}
		rule.Defaults = parsed.Parameters // detached from caller-owned maps
		rule.GroupIDs = append([]int64(nil), rule.GroupIDs...)
		groups := map[int64]bool{}
		for _, id := range rule.GroupIDs {
			if id < 1 || groups[id] {
				return nil, ErrInvalid
			}
			groups[id] = true
		}
		h.models[rule.Model] = publicRule{rule, groups}
	}
	return h, nil
}

func publicOperation(task string) string {
	switch task {
	case "text_to_image":
		return "images.generate"
	case "image_to_image", "references_to_image":
		return "images.edit"
	case "text_to_audio":
		return "audio.create"
	default:
		return "videos.create"
	}
}

// NewNativeRouter mounts the Provider task surface behind real upstream auth.
// The caller hosts it with TLS and explicit server I/O/shutdown limits. It does
// not mutate stock routes or start an untracked HTTP server or worker goroutine.
func (h *PublicHandler) NewNativeRouter(auth middleware.APIKeyAuthMiddleware, trustedProxies []string) (*gin.Engine, error) {
	if auth == nil || len(trustedProxies) > 64 {
		return nil, ErrInvalid
	}
	r := gin.New()
	if err := r.SetTrustedProxies(trustedProxies); err != nil {
		return nil, ErrInvalid
	}
	r.Use(func(c *gin.Context) {
		id, err := newID("req")
		if err != nil {
			publicError(c, ErrStorage)
			return
		}
		c.Header("X-Request-ID", id)
		c.Header("Cache-Control", "no-store")
		c.Header("X-Content-Type-Options", "nosniff")
		ctx, cancel := context.WithTimeout(c.Request.Context(), providerBudget(c.Request))
		defer cancel()
		ctx = context.WithValue(ctx, catalogContextKey{}, h.modelSnapshot(ctx))
		c.Request = c.Request.WithContext(ctx)
		limit := int64(2 << 20)
		if uploadRequest(c.Request) {
			limit = 128 << 20
		}
		c.Request.Body = http.MaxBytesReader(c.Writer, c.Request.Body, limit)
		c.Next()
	})
	r.Use(openAIAuthentication(auth))
	r.Use(func(c *gin.Context) { c.Set("render_control_authenticated", true); c.Next() })
	r.GET("/v1/models", h.modelsList)
	r.GET("/v1/models/:model", h.model)
	r.GET("/v1/capabilities", h.capabilities)
	r.POST("/v1/jobs", h.submit)
	r.GET("/v1/jobs", h.listJobs)
	r.GET("/v1/jobs/:id", h.get)
	r.POST("/v1/jobs/:id/cancel", h.cancel)
	r.POST("/v1/images/generations", h.generateImage)
	r.POST("/v1/images/generations/async", h.generateImageAsync)
	r.POST("/v1/images/edits", h.editImage)
	r.POST("/v1/images/edits/async", h.editImageAsync)
	r.POST("/v1/assets", h.uploadAsset)
	r.GET("/v1/assets/:asset", h.assetMetadata)
	r.DELETE("/v1/assets/:asset", h.assetDelete)
	r.GET("/v1/assets/:asset/content", h.assetDownload)
	r.HEAD("/v1/assets/:asset/content", h.assetDownload)
	r.GET("/v1/images/tasks/:id", h.imageTask)
	r.POST("/v1/videos", h.createVideo)
	r.GET("/v1/videos/:id", h.videoTask)
	r.GET("/v1/videos/:id/content", h.videoContent)
	r.HEAD("/v1/videos/:id/content", h.videoContent)
	r.GET("/v1/content/:content", h.contentMetadata)
	r.GET("/v1/content", h.listContent)
	r.DELETE("/v1/content/:content", h.contentDelete)
	r.GET("/v1/content/:content/download", h.contentDownload)
	r.HEAD("/v1/content/:content/download", h.contentDownload)
	return r, nil
}

func (h *PublicHandler) identity(c *gin.Context) (Subject, *service.APIKey, error) {
	key, ok := middleware.GetAPIKeyFromContext(c)
	auth, authenticated := middleware.GetAuthSubjectFromContext(c)
	if !ok || !authenticated || key == nil || key.User == nil || key.UserID != auth.UserID || key.User.ID != auth.UserID || key.ID < 1 {
		return Subject{}, nil, ErrDenied
	}
	address, err := netip.ParseAddr(pkgip.GetTrustedClientIP(c))
	if err != nil {
		return Subject{}, nil, ErrDenied
	}
	subject, err := NewSubject(h.instance, auth.UserID, key.ID, address)
	return subject, key, err
}

func (h *PublicHandler) rule(key *service.APIKey, model, task string) (publicRule, error) {
	return h.ruleAt(context.Background(), key, model, task)
}

func (h *PublicHandler) ruleAt(ctx context.Context, key *service.APIKey, model, task string) (publicRule, error) {
	rule, exists := h.modelSnapshot(ctx).models[model]
	return authorizePublicRule(key, model, task, rule, exists)
}

func authorizePublicRule(key *service.APIKey, model, task string, rule publicRule, exists bool) (publicRule, error) {
	if !exists || rule.model.TaskType != task || key.GroupID == nil || !rule.groups[*key.GroupID] || key.Group == nil || !key.Group.IsActive() || !key.Group.ModelAllowlist.Allows(model) {
		return publicRule{}, ErrNotFound
	}
	if (task == "text_to_image" || task == "image_to_image" || task == "references_to_image") && !service.GroupAllowsImageGeneration(key.Group) {
		return publicRule{}, ErrDenied
	}
	return rule, nil
}

type publicJob struct {
	ID                string                   `json:"id"`
	Object            string                   `json:"object"`
	Model             string                   `json:"model"`
	TaskType          string                   `json:"task_type"`
	Status            string                   `json:"status"`
	CreatedAt         int64                    `json:"created_at"`
	PollURL           string                   `json:"poll_url"`
	Artifacts         []controlclient.Artifact `json:"artifacts"`
	VideoMilliseconds int64                    `json:"-"`
	VideoSize         string                   `json:"-"`
}

func localPublicJob(i Intent) publicJob {
	status := "admission_pending"
	if i.Terminal() {
		status = i.Outcome
		if i.State == "released" {
			status = "cancelled"
		}
	}
	return publicJob{ID: i.ID, Object: "job", Model: i.Model, TaskType: i.TaskType, Status: status, CreatedAt: i.CreatedAt.Unix(), PollURL: "/v1/jobs/" + i.ID, Artifacts: []controlclient.Artifact{}, VideoMilliseconds: i.RequestedVideoMilliseconds, VideoSize: i.VideoSize}
}

func (h *PublicHandler) submit(c *gin.Context) {
	raw, ok := publicJSON(c)
	if !ok {
		return
	}
	native, err := ParsePublicRequest(raw)
	if err != nil {
		publicError(c, err)
		return
	}
	i, err := h.reserveRequest(c, native)
	if err != nil {
		publicError(c, err)
		return
	}
	result := localPublicJob(i)
	c.Header("Location", result.PollURL)
	c.Header("Retry-After", "2")
	c.JSON(http.StatusAccepted, result)
}

func publicJSON(c *gin.Context) ([]byte, bool) {
	mediaType, _, err := mime.ParseMediaType(c.GetHeader("Content-Type"))
	if err != nil || mediaType != "application/json" || c.GetHeader("Content-Encoding") != "" && c.GetHeader("Content-Encoding") != "identity" {
		c.AbortWithStatusJSON(http.StatusUnsupportedMediaType, gin.H{"error": gin.H{"type": "invalid_request_error", "code": "unsupported_media_type", "message": "Use an uncompressed application/json request"}})
		return nil, false
	}
	raw, err := io.ReadAll(c.Request.Body)
	if err != nil {
		var limit *http.MaxBytesError
		if errors.As(err, &limit) {
			c.AbortWithStatusJSON(http.StatusRequestEntityTooLarge, gin.H{"error": gin.H{"type": "invalid_request_error", "code": "request_too_large", "message": "Request exceeds the configured limit"}})
			return nil, false
		}
		publicError(c, ErrInvalid)
		return nil, false
	}
	return raw, true
}

func (h *PublicHandler) reserveRequest(c *gin.Context, native controlclient.JobRequest) (Intent, error) {
	subject, key, err := h.identity(c)
	if err != nil {
		return Intent{}, err
	}
	requested, marshalErr := json.Marshal(native)
	if marshalErr != nil {
		return Intent{}, ErrInvalid
	}
	token, err := publicIdempotency(c)
	if err != nil {
		return Intent{}, err
	}
	existing, replayErr := h.store.replayCatalogRequest(c.Request.Context(), subject, publicOperation(native.TaskType), token, digest(requested))
	if replayErr == nil {
		return existing, h.authorizeIntent(c.Request.Context(), key, existing)
	}
	if !errors.Is(replayErr, ErrNotFound) {
		return Intent{}, replayErr
	}
	rule, err := h.ruleAt(c.Request.Context(), key, native.Model, native.TaskType)
	if err != nil {
		return Intent{}, err
	}
	parameters := map[string]any{}
	for name, value := range rule.model.Defaults {
		parameters[name] = value
	}
	for name, value := range native.Parameters {
		parameters[name] = value
	}
	native.Parameters = parameters
	request, err := NewRequest(publicOperation(native.TaskType), native)
	if err != nil {
		return Intent{}, err
	}
	request.inputHash = digest(requested)
	request.requireAssets = c.GetBool("render_control_asset_inputs")
	request.usageAccountID = rule.model.UsageAccountID
	request.catalog = rule.model.Catalog
	return h.store.Reserve(c.Request.Context(), subject, rule.model.Target, token, request, Quote{Version: rule.model.PriceVersion, UnitPrice: rule.model.UnitPrice, MaxUnits: request.units})
}

func publicIdempotency(c *gin.Context) (string, error) {
	idempotency := c.Request.Header.Values("Idempotency-Key")
	token := ""
	var err error
	if len(idempotency) == 0 {
		token, err = newID("idem")
	} else if len(idempotency) == 1 && asciiPublic(idempotency[0], 1, 128) {
		token = idempotency[0]
	} else {
		err = ErrInvalid
	}
	return token, err
}

func asciiPublic(value string, min, max int) bool {
	if len(value) < min || len(value) > max {
		return false
	}
	for _, char := range value {
		if char < 0x21 || char > 0x7e {
			return false
		}
	}
	return true
}

func (h *PublicHandler) load(c *gin.Context) (Subject, Intent, error) {
	subject, key, err := h.identity(c)
	if err != nil {
		return Subject{}, Intent{}, err
	}
	i, err := h.store.Load(c.Request.Context(), subject, c.Param("id"))
	if err != nil {
		return Subject{}, Intent{}, err
	}
	if i.Model == "" {
		return Subject{}, Intent{}, ErrNotFound
	}
	if err = h.authorizeIntent(c.Request.Context(), key, i); err != nil {
		return Subject{}, Intent{}, err
	}
	return subject, i, nil
}

func (h *PublicHandler) get(c *gin.Context) {
	subject, i, err := h.load(c)
	if err != nil {
		publicError(c, err)
		return
	}
	result, err := h.project(c.Request.Context(), subject, i)
	if err != nil {
		publicError(c, err)
		return
	}
	c.JSON(http.StatusOK, result)
}

func (h *PublicHandler) project(ctx context.Context, subject Subject, i Intent) (publicJob, error) {
	result := localPublicJob(i)
	if i.RemoteJobID != "" {
		client := h.clients[i.TargetGatewayID]
		if client == nil {
			return publicJob{}, ErrStorage
		}
		current, err := subject.GatewaySubject()
		if err != nil {
			return publicJob{}, err
		}
		job, err := client.Job(ctx, current, i.RemoteJobID)
		if err != nil {
			return publicJob{}, err
		}
		if job.Model != i.Model || job.TaskType != i.TaskType || i.State == "settled" && job.Status != i.Outcome {
			return publicJob{}, ErrConflict
		}
		if err := h.store.bindContent(ctx, subject, i.ID, job.Artifacts); err != nil {
			return publicJob{}, err
		}
		result.Status = job.Status
		if job.Artifacts != nil {
			result.Artifacts = job.Artifacts
		}
	}
	return result, nil
}

func (h *PublicHandler) cancel(c *gin.Context) {
	subject, i, err := h.load(c)
	if err != nil {
		publicError(c, err)
		return
	}
	i, err = h.store.RequestCancellation(c.Request.Context(), subject, i.ID)
	if err != nil {
		publicError(c, err)
		return
	}
	result := localPublicJob(i)
	if !i.Terminal() {
		result.Status = "cancel_requested"
	}
	c.Header("Retry-After", "2")
	c.JSON(http.StatusAccepted, result)
}

func publicError(c *gin.Context, err error) {
	status, code, kind := http.StatusServiceUnavailable, "provider_unavailable", "api_error"
	var call *controlclient.CallError
	switch {
	case errors.Is(err, errCursorInvalid):
		status, code, kind = 400, "invalid_cursor", "invalid_request_error"
	case errors.Is(err, errCursorExpired):
		status, code, kind = 400, "cursor_expired", "invalid_request_error"
	case errors.Is(err, errListCapacity):
		status, code, kind = 429, "collection_capacity", "rate_limit_error"
		c.Header("Retry-After", "2")
	case errors.Is(err, errUploadCapacity):
		status, code, kind = 429, "upload_capacity", "rate_limit_error"
		c.Header("Retry-After", "2")
	case errors.Is(err, errUploadSize):
		status, code, kind = 413, "request_too_large", "invalid_request_error"
	case errors.Is(err, errDownloadCapacity):
		status, code, kind = 429, "download_capacity", "rate_limit_error"
		c.Header("Retry-After", "2")
	case errors.As(err, &call):
		status, code = http.StatusBadGateway, "gateway_unavailable"
		if call.HTTPStatus == http.StatusServiceUnavailable {
			status, code = http.StatusServiceUnavailable, "dependency_unavailable"
		}
		if call.HTTPStatus == 401 || call.HTTPStatus == 403 {
			status, code, kind = 403, "permission_denied", "permission_error"
		}
		if call.HTTPStatus == 404 {
			status, code, kind = 404, "not_found", "invalid_request_error"
		}
		if call.HTTPStatus == 409 || call.HTTPStatus == 410 || call.HTTPStatus == 413 || call.HTTPStatus == 422 || call.HTTPStatus == 416 || call.HTTPStatus == 429 {
			status, code, kind = call.HTTPStatus, "gateway_request_rejected", "invalid_request_error"
			if status == 429 {
				kind = "rate_limit_error"
			}
		}
	case errors.Is(err, ErrInvalid):
		status, code, kind = 400, "invalid_request", "invalid_request_error"
	case errors.Is(err, ErrNotFound):
		status, code, kind = 404, "not_found", "invalid_request_error"
	case errors.Is(err, ErrDenied):
		status, code, kind = 403, "permission_denied", "permission_error"
	case errors.Is(err, ErrBalance):
		status, code, kind = 403, "insufficient_balance", "permission_error"
	case errors.Is(err, ErrQuota):
		status, code, kind = 429, "quota_exceeded", "rate_limit_error"
	case errors.Is(err, ErrSubscription):
		status, code, kind = 403, "subscription_required", "permission_error"
	case errors.Is(err, ErrSubscriptionQuota):
		status, code, kind = 429, "subscription_quota_exceeded", "rate_limit_error"
	case errors.Is(err, ErrUnsupported):
		status, code, kind = 422, "billing_mode_unsupported", "invalid_request_error"
	case errors.Is(err, ErrConflict):
		status, code, kind = 409, "task_conflict", "invalid_request_error"
	}
	if status == http.StatusServiceUnavailable {
		c.Header("Retry-After", "2")
		c.Header("Cache-Control", "no-store")
	}
	c.AbortWithStatusJSON(status, gin.H{"error": gin.H{"type": kind, "code": code, "message": "Provider request could not be completed"}})
}
