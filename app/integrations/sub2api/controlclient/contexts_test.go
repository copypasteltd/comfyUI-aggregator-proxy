package controlclient

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestWorkflowContextProjectionRejectsMalformedMetadata(t *testing.T) {
	for _, item := range []struct {
		name, status, family, compatibility string
		expires                             int64
		valid                               bool
	}{
		{"available", "succeeded", "h3-av-v1", strings.Repeat("a", 64), 2000000000, true},
		{"wrong_family", "succeeded", "pickle", strings.Repeat("a", 64), 2000000000, false},
		{"not_successful", "running", "h3-av-v1", strings.Repeat("a", 64), 2000000000, false},
		{"wrong_digest", "succeeded", "h3-av-v1", "../private", 2000000000, false},
		{"missing_expiry", "succeeded", "h3-av-v1", strings.Repeat("a", 64), 0, false},
	} {
		t.Run(item.name, func(t *testing.T) {
			server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				_ = json.NewEncoder(w).Encode(Job{ID: jobID, Status: item.status, UpdatedRevision: 1, Context: &WorkflowContext{Available: true, Family: item.family, Compatibility: item.compatibility, ExpiresAt: item.expires}})
			}))
			defer server.Close()
			client := testClient(t, server, 2048)
			result, err := client.Job(context.Background(), testSubject(t), jobID)
			if (err == nil) != item.valid || item.valid && (result.Context == nil || !result.Context.Available) {
				t.Fatal("context availability contract was not enforced", err)
			}
		})
	}
}
