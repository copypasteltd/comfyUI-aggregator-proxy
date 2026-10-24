package rendercontrol

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"sort"
	"sync"
	"time"
	"unicode/utf8"

	"github.com/Wei-Shaw/sub2api/internal/rendercontrol/controlclient"
	"github.com/Wei-Shaw/sub2api/internal/service"
	"github.com/gin-gonic/gin"
)

// Only the in-process host can install this fallback. No client header or URL
// selects a handler, gateway, or customer identity.
type stockModelHandler struct{}

type discoveredCatalog struct {
	models       []controlclient.PublishedModel
	capabilities []controlclient.PublishedCapability
	partial      bool
}

func (h *PublicHandler) acquireCatalog(c *gin.Context) bool {
	select {
	case h.catalogSlots <- struct{}{}:
		return true
	default:
		c.Header("Retry-After", "2")
		c.AbortWithStatusJSON(429, gin.H{"error": gin.H{"type": "rate_limit_error", "code": "catalog_capacity", "message": "Catalog capacity is busy"}})
		return false
	}
}

func (h *PublicHandler) discover(ctx context.Context, subject Subject, key *service.APIKey, capabilities bool, model string) discoveredCatalog {
	allowed := map[string]publicRule{}
	targetSet := map[string]bool{}
	for id, candidate := range h.models {
		if model != "" && model != id {
			continue
		}
		if rule, err := h.rule(key, id, candidate.model.TaskType); err == nil {
			allowed[id] = rule
			targetSet[rule.model.Target.gatewayID] = true
		}
	}
	targets := make([]string, 0, len(targetSet))
	for id := range targetSet {
		targets = append(targets, id)
	}
	sort.Strings(targets)
	result := discoveredCatalog{models: []controlclient.PublishedModel{}, capabilities: []controlclient.PublishedCapability{}}
	if len(targets) == 0 {
		return result
	}
	type response struct {
		models       []controlclient.PublishedModel
		capabilities []controlclient.PublishedCapability
		err          error
	}
	responses := make([]response, len(targets))
	work := make(chan int, len(targets))
	for index := range targets {
		work <- index
	}
	close(work)
	ctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	remote, err := subject.GatewaySubject()
	if err != nil {
		result.partial = true
		return result
	}
	var workers sync.WaitGroup
	for range min(4, len(targets)) {
		workers.Go(func() {
			for index := range work {
				client := h.clients[targets[index]]
				if capabilities {
					catalog, err := client.Capabilities(ctx, remote)
					responses[index].err = err
					for _, item := range catalog.Data {
						if rule, exists := allowed[item.ID]; exists && rule.model.Target.gatewayID == targets[index] && rule.model.TaskType == item.TaskType {
							responses[index].capabilities = append(responses[index].capabilities, item)
						}
					}
				} else {
					catalog, err := client.Models(ctx, remote)
					responses[index].err = err
					for _, item := range catalog.Data {
						if rule, exists := allowed[item.ID]; exists && rule.model.Target.gatewayID == targets[index] {
							responses[index].models = append(responses[index].models, item)
						}
					}
				}
			}
		})
	}
	workers.Wait()
	for _, response := range responses {
		if response.err != nil {
			var call *controlclient.CallError
			if !errors.As(response.err, &call) || call.HTTPStatus != 401 && call.HTTPStatus != 403 {
				result.partial = true
			}
			continue
		}
		result.models = append(result.models, response.models...)
		result.capabilities = append(result.capabilities, response.capabilities...)
	}
	sort.Slice(result.models, func(i, j int) bool { return result.models[i].ID < result.models[j].ID })
	sort.Slice(result.capabilities, func(i, j int) bool { return result.capabilities[i].ID < result.capabilities[j].ID })
	return result
}

func catalogUnavailable(c *gin.Context) {
	c.Header("Retry-After", "2")
	c.AbortWithStatusJSON(503, gin.H{"error": gin.H{"type": "api_error", "code": "catalog_unavailable", "message": "The current model catalog could not be verified"}})
}

func (h *PublicHandler) capabilities(c *gin.Context) {
	if !h.acquireCatalog(c) {
		return
	}
	defer func() { <-h.catalogSlots }()
	subject, key, err := h.identity(c)
	if err != nil {
		publicError(c, err)
		return
	}
	result := h.discover(c.Request.Context(), subject, key, true, "")
	if result.partial {
		catalogUnavailable(c)
		return
	}
	c.Header("Cache-Control", "private, no-store")
	c.Header("X-Render-Control-Catalog", "complete")
	c.JSON(200, controlclient.CapabilityCatalog{Object: "list", Data: result.capabilities})
}

func (h *PublicHandler) model(c *gin.Context) {
	if !h.acquireCatalog(c) {
		return
	}
	defer func() { <-h.catalogSlots }()
	subject, key, err := h.identity(c)
	if err != nil {
		publicError(c, err)
		return
	}
	result := h.discover(c.Request.Context(), subject, key, false, c.Param("model"))
	if result.partial {
		catalogUnavailable(c)
		return
	}
	if len(result.models) != 1 {
		publicError(c, ErrNotFound)
		return
	}
	c.Header("Cache-Control", "private, no-store")
	c.JSON(200, result.models[0])
}

func (h *PublicHandler) modelsList(c *gin.Context) {
	if !h.acquireCatalog(c) {
		return
	}
	defer func() { <-h.catalogSlots }()
	subject, key, err := h.identity(c)
	if err != nil {
		publicError(c, err)
		return
	}
	// Fetch the stock list before discovery. Its rejection remains authoritative
	// and never becomes a successful response containing only Provider models.
	root := map[string]json.RawMessage{"object": json.RawMessage(`"list"`)}
	items := []json.RawMessage{}
	if stock, ok := c.Request.Context().Value(stockModelHandler{}).(http.Handler); ok {
		request := c.Request.Clone(c.Request.Context())
		request.Header = c.Request.Header.Clone()
		for _, header := range []string{"If-None-Match", "If-Modified-Since", "If-Match", "If-Unmodified-Since", "If-Range", "Range", "Accept-Encoding"} {
			request.Header.Del(header)
		}
		capture := &catalogCapture{header: make(http.Header)}
		stock.ServeHTTP(capture, request)
		if capture.tooLarge || capture.status == 304 {
			catalogUnavailable(c)
			return
		}
		if capture.status != 0 && capture.status != 200 {
			for _, header := range []string{"Retry-After", "WWW-Authenticate"} {
				if value := capture.header.Get(header); value != "" {
					c.Header(header, value)
				}
			}
			c.Data(capture.status, capture.header.Get("Content-Type"), capture.body.Bytes())
			return
		}
		if root, items, err = decodeStockCatalog(capture.body.Bytes()); err != nil {
			catalogUnavailable(c)
			return
		}
	}
	result := h.discover(c.Request.Context(), subject, key, false, "")
	merged := make([]json.RawMessage, 0, len(items)+len(result.models))
	seen := map[string]bool{}
	for _, item := range items {
		var model struct {
			ID string `json:"id"`
		}
		_ = json.Unmarshal(item, &model) // checked by decodeStockCatalog
		if _, owned := h.models[model.ID]; owned || seen[model.ID] {
			continue
		}
		seen[model.ID] = true
		merged = append(merged, item)
	}
	for _, model := range result.models {
		raw, _ := json.Marshal(model)
		merged = append(merged, raw)
	}
	root["data"], _ = json.Marshal(merged)
	c.Header("Cache-Control", "private, no-store")
	c.Header("X-Render-Control-Catalog", "complete")
	if result.partial {
		c.Header("X-Render-Control-Catalog", "partial")
	}
	c.JSON(200, root)
}

// A bounded writer for this metadata route only; never used for stock streams.
type catalogCapture struct {
	header   http.Header
	body     bytes.Buffer
	status   int
	tooLarge bool
}

func (w *catalogCapture) Header() http.Header { return w.header }
func (w *catalogCapture) WriteHeader(status int) {
	if w.status == 0 {
		w.status = status
	}
}
func (w *catalogCapture) Write(data []byte) (int, error) {
	w.WriteHeader(200)
	if w.tooLarge || int64(w.body.Len())+int64(len(data)) > 4<<20 {
		w.tooLarge = true
		return 0, errors.New("stock catalog exceeds metadata limit")
	}
	return w.body.Write(data)
}

func decodeStockCatalog(raw []byte) (map[string]json.RawMessage, []json.RawMessage, error) {
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	count := 0
	if !utf8.Valid(raw) || validateJSON(decoder, 0, &count) != nil {
		return nil, nil, ErrInvalid
	}
	if _, err := decoder.Token(); err != io.EOF {
		return nil, nil, ErrInvalid
	}
	var root map[string]json.RawMessage
	var items []json.RawMessage
	var object string
	if json.Unmarshal(raw, &root) != nil || json.Unmarshal(root["object"], &object) != nil || object != "list" || json.Unmarshal(root["data"], &items) != nil || items == nil || len(items) > 16384 {
		return nil, nil, ErrInvalid
	}
	for _, item := range items {
		var model struct {
			ID string `json:"id"`
		}
		if json.Unmarshal(item, &model) != nil || !textValue(model.ID, 1, 256) {
			return nil, nil, ErrInvalid
		}
	}
	return root, items, nil
}
