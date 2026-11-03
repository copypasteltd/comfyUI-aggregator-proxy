package rendercontrol

import (
	"encoding/json"
	"net/http"
	"strings"
	"testing"
)

func TestPublicContextResolvesOwnedTaskAndRetainsOriginalIdempotency(t *testing.T) {
	f := publicSetup(t)
	previous := f.submit(t, "context-parent")
	coordinator := testCoordinator(t, f.s, f.subject, f.g, "context-dispatch")
	stepCoordinator(t, coordinator, previous, false)
	stepCoordinator(t, coordinator, previous, false)
	// Preserve access to the parent's independently configured model/task.
	child := f.config.Models[0]
	child.Model = "video-context-child"
	child.TaskType = "text_to_video"
	child.Defaults = map[string]any{"seconds": json.Number("5"), "size": "640x384", "quality": "standard"}
	f.config.Models = append(f.config.Models, child)
	f.start(t)
	body := `{"model":"video-context-child","task_type":"text_to_video","input":{"prompt":"Synthetic continuation","context":{"job_id":"` + previous.ID + `"}},"parameters":{"seconds":5}}`
	status, raw, _ := f.call(t, http.MethodPost, "/v1/jobs", body, map[string]string{"Idempotency-Key": "context-child"})
	var result publicJob
	if status != 202 || json.Unmarshal(raw, &result) != nil {
		t.Fatal("owned continuation was not reserved", status, string(raw))
	}
	_, frozen, err := f.s.Recover(ctx(), result.ID)
	parent, ok := frozen.Input["context"].(map[string]any)
	if err != nil || !ok || parent["job_id"] != "job_"+previous.ID[3:] {
		t.Fatal("public parent was not frozen as its scoped internal task", err)
	}
	replay, raw, _ := f.call(t, http.MethodPost, "/v1/jobs", body, map[string]string{"Idempotency-Key": "context-child"})
	var repeated publicJob
	if replay != 202 || json.Unmarshal(raw, &repeated) != nil || repeated.ID != result.ID {
		t.Fatal("parent translation broke original idempotency", replay)
	}
	for _, id := range []string{"job_" + previous.ID[3:], "rc_" + strings.Repeat("f", 32)} {
		status, _, _ := f.call(t, http.MethodPost, "/v1/jobs", strings.Replace(body, previous.ID, id, 1), map[string]string{"Idempotency-Key": "reject-parent-" + id})
		if status != 404 {
			t.Fatal("unowned or internal parent identifier accepted", status)
		}
	}
}
