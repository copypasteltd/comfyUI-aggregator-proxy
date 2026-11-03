package rendercontrol

import (
	"bytes"
	_ "embed"
	"encoding/json"
	"testing"

	"github.com/Wei-Shaw/sub2api/internal/rendercontrol/controlclient"
)

//go:embed testdata/model-usage-context.json
var publicUsageContextFixture []byte

func TestPublicDiscoveryRetainsUsageAndUsesScopedPublicParentIdentity(t *testing.T) {
	f := publicSetup(t)
	f.config.Models[0].TaskType = "text_to_video"
	f.config.Models[0].Defaults["seconds"] = json.Number("0.5")
	f.start(t)
	item := syntheticCapability("studio-image-v1", "text_to_video")
	item.Usage = publicUsageContextFixture
	f.g.configure(func(g *recoveryGateway) {
		g.catalogModels = []controlclient.PublishedModel{item.PublishedModel}
		g.catalogCapabilities = []controlclient.PublishedCapability{item}
	})
	for _, path := range []string{"/v1/models", "/v1/models/studio-image-v1", "/v1/capabilities"} {
		status, body, _ := f.call(t, "GET", path, "", nil)
		if status != 200 || !bytes.Contains(body, []byte(`"display_name"`)) || !bytes.Contains(body, []byte(`^rc_[0-9a-f]{32}$`)) || bytes.Contains(body, []byte(`^job_[0-9a-f]{32}$`)) {
			t.Fatal("public workflow usage lost or leaked internal task identity", status, string(body))
		}
	}
	if f.g.count(admissionPath) != 0 || countRows(t, `SELECT count(*) FROM render_control_intents WHERE instance=$1`, f.subject.instance) != 0 {
		t.Fatal("usage discovery caused generation/billing")
	}
}
