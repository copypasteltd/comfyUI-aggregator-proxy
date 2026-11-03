package controlclient

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"
)

func TestCatalogPreservesPublicUsageAndProjectsContinuationIdentity(t *testing.T) {
	raw, err := os.ReadFile("testdata/model-usage-context.json")
	if err != nil {
		t.Fatal(err)
	}
	var compact bytes.Buffer
	if err = json.Compact(&compact, raw); err != nil {
		t.Fatal(err)
	}
	raw = compact.Bytes()
	original := append([]byte(nil), raw...)
	model := catalogSample().PublishedModel
	model.Usage = raw
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(ModelCatalog{Object: "list", Data: []PublishedModel{model}})
	}))
	defer server.Close()
	result, err := testClient(t, server, 0).Models(context.Background(), testSubject(t))
	if err != nil || len(result.Data) != 1 || !bytes.Equal(result.Data[0].Usage, raw) {
		t.Fatal("public usage lost", err)
	}
	projected, err := result.Data[0].WithPublicTaskIDs()
	if err != nil || !bytes.Contains(projected.Usage, []byte(`^rc_[0-9a-f]{32}$`)) || !bytes.Contains(projected.Usage, []byte(`"job_id":"rc_00000000000000000000000000000000"`)) || !bytes.Equal(raw, original) {
		t.Fatal("public context schema/example or native isolation is wrong", err)
	}
	var doc map[string]json.RawMessage
	_ = json.Unmarshal(raw, &doc)
	doc["workflow_api"] = json.RawMessage(`{"private":"never-forward"}`)
	model.Usage, _ = json.Marshal(doc)
	if _, err = testClient(t, server, 0).Models(context.Background(), testSubject(t)); err == nil {
		t.Fatal("private graph accepted as usage")
	}
}
