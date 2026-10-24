package middleware

import (
	"net/http"
	"regexp"
	"strings"

	"github.com/gin-gonic/gin"
)

var renderControlOwnedTaskPath = regexp.MustCompile(`^/v1/jobs/rc_[a-f0-9]{32}$`)
var renderControlOwnedCompatibilityPath = regexp.MustCompile(`^/v1/(images/tasks/rc_[a-f0-9]{32}|videos/rc_[a-f0-9]{32}(/content)?|content/content_[a-f0-9]{32}(/download)?|assets/asset_[a-f0-9]{32}(/content)?)$`)
var renderControlOwnedDownloadPath = regexp.MustCompile(`^/v1/(videos/rc_[a-f0-9]{32}/content|content/content_[a-f0-9]{32}/download|assets/asset_[a-f0-9]{32}/content)$`)

func isRenderControlOwnedBilling(c *gin.Context) bool {
	if !c.GetBool("render_control_owns_billing") || c.Request.Method != http.MethodPost {
		return false
	}
	switch c.Request.URL.Path {
	case "/v1/jobs", "/v1/images/generations", "/v1/images/generations/async", "/v1/images/edits", "/v1/images/edits/async", "/v1/videos", "/v1/assets":
		return true
	}
	return false
}

// This is a billing exception only. The pinned middleware still authenticates
// the current Key/user/group/IP. Handlers check current delegated permissions;
// shared content routing never grants ownership or deletion authority.
func isRenderControlTaskRead(method, path string) bool {
	if method == http.MethodGet {
		return path == "/v1/jobs" || path == "/v1/content" || renderControlOwnedTaskPath.MatchString(path) || renderControlOwnedCompatibilityPath.MatchString(path)
	}
	if method == http.MethodHead {
		return renderControlOwnedDownloadPath.MatchString(path)
	}
	if method == http.MethodPost && strings.HasSuffix(path, "/cancel") {
		return renderControlOwnedTaskPath.MatchString(strings.TrimSuffix(path, "/cancel"))
	}
	return false
}
