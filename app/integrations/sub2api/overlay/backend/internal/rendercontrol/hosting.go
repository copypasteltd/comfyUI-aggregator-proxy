package rendercontrol

import (
	"bytes"
	"context"
	"crypto/x509"
	"database/sql"
	"encoding/base64"
	"encoding/json"
	"io"
	"log"
	"mime"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"time"
	_ "time/tzdata" // Native Windows hosts need the IANA database without OS packages.

	"github.com/Wei-Shaw/sub2api/internal/config"
	"github.com/Wei-Shaw/sub2api/internal/rendercontrol/controlclient"
	"github.com/Wei-Shaw/sub2api/internal/server/middleware"
)

const HostingConfigEnvironment = "SUB2API_RENDER_CONTROL_CONFIG"

type hostingFile struct {
	Version              int               `json:"version"`
	Instance             string            `json:"instance"`
	PublicOrigin         string            `json:"public_origin"`
	SyncWaitMilliseconds int               `json:"sync_wait_milliseconds"`
	UploadDirectory      string            `json:"upload_directory"`
	ActiveKey            string            `json:"active_key"`
	EncryptionKeys       map[string]string `json:"encryption_key_files"`
	Gateways             []hostingGateway  `json:"gateways"`
	Models               []hostingModel    `json:"models"`
	TrustedProxies       []string          `json:"trusted_proxies"`
	Runtime              hostingRuntime    `json:"runtime"`
}

type hostingGateway struct {
	ID             string `json:"id"`
	Origin         string `json:"origin"`
	CredentialFile string `json:"credential_file"`
	CAFile         string `json:"ca_file"`
}

type hostingModel struct {
	UsageAccountID int64          `json:"usage_account_id"`
	Model          string         `json:"model"`
	TaskType       string         `json:"task_type"`
	GatewayID      string         `json:"gateway_id"`
	PriceVersion   string         `json:"price_version"`
	UnitPrice      string         `json:"unit_price"`
	GroupIDs       []int64        `json:"group_ids"`
	Defaults       map[string]any `json:"defaults"`
}

type hostingRuntime struct {
	Workers                int `json:"workers"`
	PollMilliseconds       int `json:"poll_milliseconds"`
	SettlementMilliseconds int `json:"settlement_milliseconds"`
	CallMilliseconds       int `json:"call_milliseconds"`
	LeaseMilliseconds      int `json:"lease_milliseconds"`
}

func boundedHostingFile(path string, limit int64) ([]byte, error) {
	info, err := os.Stat(path)
	if err != nil || !info.Mode().IsRegular() || info.Size() < 1 || info.Size() > limit {
		return nil, ErrInvalid
	}
	file, err := os.Open(path)
	if err != nil {
		return nil, ErrInvalid
	}
	defer file.Close()
	info, err = file.Stat()
	if err != nil || !info.Mode().IsRegular() {
		return nil, ErrInvalid
	}
	data, err := io.ReadAll(io.LimitReader(file, limit+1))
	if err != nil || int64(len(data)) > limit {
		return nil, ErrInvalid
	}
	return data, nil
}

func (c hostingRuntime) configuration(instance, owner string) (RuntimeConfig, error) {
	if c.Workers < 0 || c.Workers > 16 || c.PollMilliseconds < 0 || c.PollMilliseconds > 60000 || c.SettlementMilliseconds < 0 || c.SettlementMilliseconds > 60000 || c.CallMilliseconds < 0 || c.CallMilliseconds > 60000 || c.LeaseMilliseconds < 0 || c.LeaseMilliseconds > 300000 {
		return RuntimeConfig{}, ErrInvalid
	}
	return RuntimeConfig{Instance: instance, Owner: owner, Workers: c.Workers, PollInterval: time.Duration(c.PollMilliseconds) * time.Millisecond, SettlementInterval: time.Duration(c.SettlementMilliseconds) * time.Millisecond, CallTimeout: time.Duration(c.CallMilliseconds) * time.Millisecond, Lease: time.Duration(c.LeaseMilliseconds) * time.Millisecond}, nil
}

type hostedHandler struct {
	provider        http.Handler
	stock           http.Handler
	stopping        atomic.Bool
	models          map[string]bool
	public          *PublicHandler
	uploadDirectory string
	uploadSlots     chan struct{}
}

func (h *hostedHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if h.public != nil {
		r = r.WithContext(context.WithValue(r.Context(), catalogContextKey{}, h.public.modelSnapshot(r.Context())))
	}
	requestStarted := time.Now()
	owned := r.URL.Path == "/v1/jobs" || strings.HasPrefix(r.URL.Path, "/v1/jobs/") || r.URL.Path == "/v1/assets" || strings.HasPrefix(r.URL.Path, "/v1/assets/") || r.URL.Path == "/v1/content" || strings.HasPrefix(r.URL.Path, "/v1/content/") || strings.HasPrefix(r.URL.Path, "/v1/images/tasks/rc_") || strings.HasPrefix(r.URL.Path, "/v1/videos/rc_")
	if r.Method == http.MethodGet && (r.URL.Path == "/v1/capabilities" || r.URL.Path == "/v1/models" && r.URL.Query().Get("client_version") == "" || strings.HasPrefix(r.URL.Path, "/v1/models/") && h.ownsModel(r.Context(), strings.TrimPrefix(r.URL.Path, "/v1/models/"))) {
		owned = true
		if r.URL.Path == "/v1/models" {
			r = r.WithContext(context.WithValue(r.Context(), stockModelHandler{}, h.stock))
		}
	}
	if !owned && r.Method == http.MethodPost && (r.URL.Path == "/v1/images/edits" || r.URL.Path == "/v1/images/edits/async" || r.URL.Path == "/v1/videos") {
		if h.uploadDirectory == "" || h.uploadSlots == nil {
			hostRoutingError(w, 503, "upload_unavailable")
			return
		}
		select {
		case h.uploadSlots <- struct{}{}:
			defer func() { <-h.uploadSlots }()
		default:
			hostRoutingError(w, 429, "upload_capacity")
			return
		}
		if r.Header.Get("Content-Encoding") != "" && r.Header.Get("Content-Encoding") != "identity" {
			hostRoutingError(w, 415, "unsupported_media_type")
			return
		}
		controller := http.NewResponseController(w)
		_ = controller.SetReadDeadline(time.Now().Add(60 * time.Second))
		defer controller.SetReadDeadline(time.Time{})
		file, err := spoolFile(h.uploadDirectory, r.Body, 128<<20)
		_ = r.Body.Close()
		if err != nil {
			status := 400
			if err == errUploadSize {
				status = 413
			}
			hostRoutingError(w, status, "invalid_upload")
			return
		}
		defer file.close()
		model, err := routingUploadModel(file.file, r)
		if err != nil {
			hostRoutingError(w, 400, "invalid_request")
			return
		}
		owned = h.ownsModel(r.Context(), model)
		if _, err = file.file.Seek(0, 0); err != nil {
			hostRoutingError(w, 503, "upload_unavailable")
			return
		}
		r.Body = io.NopCloser(file.file)
		r.ContentLength = file.Bytes
		r = r.WithContext(context.WithValue(r.Context(), uploadOwnership{}, true))
	}
	if !owned && r.Method == http.MethodPost && (r.URL.Path == "/v1/images/generations" || r.URL.Path == "/v1/images/generations/async") {
		mediaType, _, err := mime.ParseMediaType(r.Header.Get("Content-Type"))
		if err == nil && mediaType == "application/json" {
			controller := http.NewResponseController(w)
			_ = controller.SetReadDeadline(time.Now().Add(10 * time.Second))
			defer controller.SetReadDeadline(time.Time{})
			raw, err := io.ReadAll(http.MaxBytesReader(w, r.Body, 2<<20))
			_ = r.Body.Close()
			if err != nil {
				hostRoutingError(w, http.StatusRequestEntityTooLarge, "request_too_large")
				return
			}
			object, err := strictPublicObject(raw)
			if err != nil || !textValue(object["model"], 1, 128) {
				hostRoutingError(w, http.StatusBadRequest, "invalid_request")
				return
			}
			owned = h.ownsModel(r.Context(), object["model"].(string))
			r.Body = io.NopCloser(bytes.NewReader(raw))
			r.ContentLength = int64(len(raw))
		}
	}
	if !owned {
		h.stock.ServeHTTP(w, r)
		return
	}
	if h.stopping.Load() {
		w.Header().Set("Cache-Control", "no-store")
		w.Header().Set("Retry-After", "2")
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusServiceUnavailable)
		_, _ = io.WriteString(w, `{"error":{"type":"server_error","code":"provider_stopping","message":"Provider is stopping"}}`)
		return
	}
	// Only this namespace has short metadata I/O deadlines. The stock server
	// retains its existing behavior for long-running streaming responses.
	// Include shared-route body spooling in the Provider's total budget.
	requestContext, cancelRequest := context.WithDeadline(r.Context(), requestStarted.Add(providerBudget(r)))
	defer cancelRequest()
	r = r.WithContext(requestContext)
	controller := http.NewResponseController(w)
	readBudget := 10 * time.Second
	if uploadRequest(r) {
		readBudget = 60 * time.Second
	}
	_ = controller.SetReadDeadline(time.Now().Add(readBudget))
	_ = controller.SetWriteDeadline(time.Now().Add(providerBudget(r) + 5*time.Second))
	defer controller.SetReadDeadline(time.Time{})
	defer controller.SetWriteDeadline(time.Time{})
	h.provider.ServeHTTP(w, r)
}

func hostRoutingError(w http.ResponseWriter, status int, code string) {
	id, _ := newID("req")
	w.Header().Set("X-Request-ID", id)
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	if status == 429 || status == 503 {
		w.Header().Set("Retry-After", "2")
	}
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]any{"error": map[string]string{"type": "invalid_request_error", "code": code, "message": "Generation request could not be parsed"}})
}

func (h *hostedHandler) ownsModel(ctx context.Context, model string) bool {
	if h.public == nil {
		return h.models[model]
	}
	_, owned := h.public.modelSnapshot(ctx).owned[model]
	return owned
}

// InstallFromEnvironment is called by the reviewed stock composition patch.
// It installs no migrations and creates no second balance or HTTP listener.
func InstallFromEnvironment(db *sql.DB, auth middleware.APIKeyAuthMiddleware, server *http.Server, mode string, caches ...BillingInvalidator) (func(), error) {
	path := os.Getenv(HostingConfigEnvironment)
	if path == "" {
		return func() {}, nil
	}
	if mode != config.RunModeStandard || db == nil || auth == nil || server == nil || server.Handler == nil || !filepath.IsAbs(path) {
		return nil, ErrInvalid
	}
	if _, duplicate := server.Handler.(*hostedHandler); duplicate {
		return nil, ErrConflict
	}
	if len(caches) != 1 || caches[0] == nil {
		return nil, ErrInvalid
	}
	data, err := boundedHostingFile(path, 256<<10)
	if err != nil || !json.Valid(data) || validateJSON(json.NewDecoder(bytes.NewReader(data)), 0, new(int)) != nil {
		return nil, ErrInvalid
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.UseNumber()
	decoder.DisallowUnknownFields()
	var file hostingFile
	if decoder.Decode(&file) != nil || file.Version != 1 || !instancePattern.MatchString(file.Instance) || len(file.EncryptionKeys) < 1 || len(file.EncryptionKeys) > 4 || len(file.Gateways) < 1 || len(file.Gateways) > 8 || file.SyncWaitMilliseconds < 0 || file.SyncWaitMilliseconds > 60000 {
		return nil, ErrInvalid
	}
	resolve := func(name string) string {
		if name == "" {
			return ""
		}
		if filepath.IsAbs(name) {
			return name
		}
		return filepath.Join(filepath.Dir(path), name)
	}
	keys := map[string][]byte{}
	for id, keyPath := range file.EncryptionKeys {
		raw, err := boundedHostingFile(resolve(keyPath), 256)
		if err != nil {
			return nil, ErrInvalid
		}
		key, err := base64.StdEncoding.Strict().DecodeString(strings.TrimSpace(string(raw)))
		if err != nil || len(key) != 32 {
			return nil, ErrInvalid
		}
		keys[id] = key
	}
	store, err := NewStore(db, file.ActiveKey, keys)
	if err != nil {
		return nil, err
	}
	clients := map[string]*controlclient.Client{}
	closeClients := func() {
		for _, client := range clients {
			client.Close()
		}
	}
	installed := false
	defer func() {
		if !installed {
			closeClients()
		}
	}()
	for _, gateway := range file.Gateways {
		if clients[gateway.ID] != nil {
			return nil, ErrInvalid
		}
		raw, err := boundedHostingFile(resolve(gateway.CredentialFile), 512)
		if err != nil {
			return nil, ErrInvalid
		}
		credential, err := controlclient.ParseCredential(strings.TrimSpace(string(raw)))
		if err != nil {
			return nil, ErrInvalid
		}
		var roots *x509.CertPool
		if gateway.CAFile != "" {
			pem, err := boundedHostingFile(resolve(gateway.CAFile), 1<<20)
			if err != nil {
				return nil, ErrInvalid
			}
			roots = x509.NewCertPool()
			if !roots.AppendCertsFromPEM(pem) {
				return nil, ErrInvalid
			}
		}
		client, err := controlclient.New(controlclient.Config{GatewayID: gateway.ID, Origin: gateway.Origin, Credential: credential, RootCAs: roots, MaxResponseBytes: 4 << 20})
		if err != nil {
			return nil, ErrInvalid
		}
		if !client.HTTPS() || client.GatewayID() != gateway.ID {
			client.Close()
			return nil, ErrInvalid
		}
		clients[gateway.ID] = client
	}
	uploadDirectory := resolve(file.UploadDirectory)
	if uploadDirectory == "" {
		uploadDirectory = filepath.Join(filepath.Dir(path), "render-control-spool")
	}
	public := PublicConfig{Instance: file.Instance, Clients: clients, PublicOrigin: file.PublicOrigin, SyncWait: time.Duration(file.SyncWaitMilliseconds) * time.Millisecond, UploadDirectory: uploadDirectory}
	for _, model := range file.Models {
		if model.UsageAccountID < 1 || len(model.Model) > 100 {
			return nil, ErrInvalid
		}
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		var managed bool
		err := db.QueryRowContext(ctx, `SELECT EXISTS(SELECT 1 FROM render_control_catalogs WHERE instance=$1 AND target_gateway_id=$2 AND confirmed_revision IS NOT NULL)`, file.Instance, model.GatewayID).Scan(&managed)
		if err != nil {
			cancel()
			return nil, ErrStorage
		}
		if err == nil && !managed {
			err = checkUsageAccount(ctx, db, model.UsageAccountID)
		}
		cancel()
		if err != nil {
			return nil, err
		}
		price, err := ParseMoney(model.UnitPrice)
		if err != nil {
			return nil, ErrInvalid
		}
		target, err := NewTarget(model.GatewayID)
		if err != nil {
			return nil, err
		}
		public.Models = append(public.Models, PublicModelRule{UsageAccountID: model.UsageAccountID, Model: model.Model, TaskType: model.TaskType, Target: target, PriceVersion: model.PriceVersion, UnitPrice: price, GroupIDs: model.GroupIDs, Defaults: model.Defaults})
	}
	handler, err := NewPublicHandler(store, public)
	if err != nil {
		return nil, err
	}
	router, err := handler.NewNativeRouter(auth, file.TrustedProxies)
	if err != nil {
		return nil, err
	}
	owner, err := newID("rcp")
	if err != nil {
		return nil, err
	}
	runtimeConfig, err := file.Runtime.configuration(file.Instance, owner)
	if err != nil {
		return nil, err
	}
	runtimeConfig.Effects, err = NewEffectConsumer(store, caches[0], file.Instance, owner+"_effects")
	if err != nil {
		return nil, err
	}
	runtime, err := NewRuntime(store, clients, runtimeConfig)
	if err != nil {
		return nil, err
	}
	// Schema probe is read-only and bounded. Missing migrations fail startup;
	// installation must never silently initialize a database or mutate history.
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	rows, err := db.QueryContext(ctx, `SELECT i.public_model,i.client_request_hash,i.usage_account_id,i.billing_group_id,i.rate_multiplier,i.rate_source,i.billing_type,i.subscription_id,r.cancel_requested,r.cancel_client_ip,e.acknowledged_at,e.available_at,e.attempts,e.last_error_code,e.local_completed_at,e.cache_pass,c.closure_id,b.content_id,a.asset_id,u.id,cr.content_id FROM render_control_intents i JOIN render_control_recovery r ON r.intent_id=i.id JOIN render_control_effects e ON e.intent_id=i.id LEFT JOIN render_control_closures c ON c.intent_id=i.id LEFT JOIN render_control_content_bindings b ON b.intent_id=i.id LEFT JOIN render_control_asset_bindings a ON FALSE LEFT JOIN render_control_asset_uploads u ON FALSE LEFT JOIN render_control_content_routes cr ON FALSE LIMIT 0`)
	if err == nil {
		err = rows.Close()
	}
	cancel()
	if err != nil {
		return nil, ErrStorage
	}
	ctx, cancel = context.WithTimeout(context.Background(), 20*time.Second)
	err = handler.initializeCatalogs(ctx)
	cancel()
	if err != nil {
		return nil, err
	}
	if err = runtime.Start(context.Background()); err != nil {
		return nil, err
	}
	catalogs, err := startCatalogConsumer(handler, 5*time.Second)
	if err != nil {
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		_ = runtime.Stop(ctx)
		return nil, err
	}
	ownedModels := map[string]bool{}
	for _, model := range file.Models {
		ownedModels[model.Model] = true
	}
	mux := &hostedHandler{provider: router, stock: server.Handler, models: ownedModels, public: handler, uploadDirectory: handler.uploadDirectory, uploadSlots: handler.uploadSlots}
	server.Handler = mux
	var once sync.Once
	cleanup := func() {
		once.Do(func() {
			mux.stopping.Store(true)
			ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
			defer cancel()
			if catalogs.Stop(ctx) != nil {
				log.Print("RenderControl catalog shutdown did not finish within its limit")
			}
			if runtime.Stop(ctx) != nil {
				log.Print("RenderControl shutdown did not finish within its limit")
			}
			closeClients()
		})
	}
	installed = true
	return cleanup, nil
}
