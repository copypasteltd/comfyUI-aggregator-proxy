package rendercontrol

import (
	"net/http"
	"strings"
	"time"

	"github.com/Wei-Shaw/sub2api/internal/server/middleware"
	"github.com/gin-gonic/gin"
)

func providerBudget(r *http.Request) time.Duration {
	if uploadRequest(r) {
		if r.URL.Path == "/v1/images/edits" {
			return 240 * time.Second
		}
		return 180 * time.Second
	}
	if r.Method == http.MethodPost && r.URL.Path == "/v1/images/generations" {
		return 90 * time.Second
	}
	if (r.Method == http.MethodGet || r.Method == http.MethodHead) && (strings.HasSuffix(r.URL.Path, "/download") || r.URL.Path != "/v1/content" && strings.HasSuffix(r.URL.Path, "/content")) {
		return 300 * time.Second
	}
	return 30 * time.Second
}

// Only auth rejections are captured. Once auth enters c.Next(), public route
// responses (including recoverable sync errors) keep their complete contract.
type authErrorWriter struct {
	gin.ResponseWriter
	context *gin.Context
	blocked bool
	status  int
}

func (w *authErrorWriter) WriteHeader(status int) {
	if !w.context.GetBool("render_control_authenticated") && status >= 400 {
		w.blocked, w.status = true, status
		return
	}
	w.ResponseWriter.WriteHeader(status)
}
func (w *authErrorWriter) Write(data []byte) (int, error) {
	if w.blocked {
		return len(data), nil
	}
	return w.ResponseWriter.Write(data)
}
func (w *authErrorWriter) WriteString(data string) (int, error) {
	if w.blocked {
		return len(data), nil
	}
	return w.ResponseWriter.WriteString(data)
}
func (w *authErrorWriter) WriteHeaderNow() {
	if !w.blocked {
		w.ResponseWriter.WriteHeaderNow()
	}
}
func (w *authErrorWriter) Status() int {
	if w.blocked {
		return w.status
	}
	return w.ResponseWriter.Status()
}

func openAIAuthentication(auth middleware.APIKeyAuthMiddleware) gin.HandlerFunc {
	return func(c *gin.Context) {
		original := c.Writer
		writer := &authErrorWriter{ResponseWriter: original, context: c}
		c.Writer = writer
		// Internal Gin context only; no request header can create this marker.
		// The stock billing exception is limited to exact owned POST routes.
		c.Set("render_control_owns_billing", true)
		auth(c)
		c.Writer = original
		if writer.blocked {
			kind, code := "api_error", "authentication_unavailable"
			switch writer.status {
			case 400:
				kind, code = "invalid_request_error", "invalid_authentication_request"
			case 401:
				kind, code = "authentication_error", "authentication_failed"
			case 403:
				kind, code = "permission_error", "permission_denied"
			case 429:
				kind, code = "rate_limit_error", "quota_exceeded"
			}
			c.AbortWithStatusJSON(writer.status, gin.H{"error": gin.H{"type": kind, "code": code, "message": "API key authentication was rejected"}})
		}
	}
}
