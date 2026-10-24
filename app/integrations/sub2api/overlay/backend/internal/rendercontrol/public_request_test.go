package rendercontrol

import (
	"bytes"
	_ "embed"
	"encoding/json"
	"strings"
	"testing"
)

//go:embed testdata/job-create-fixtures.json
var publicContractFixtures []byte

func TestPublicRequestMatchesFrozenFixtures(t *testing.T) {
	var fixtures struct {
		Cases []struct {
			ID    string          `json:"id"`
			Valid bool            `json:"valid"`
			Data  json.RawMessage `json:"data"`
		} `json:"cases"`
	}
	if json.Unmarshal(publicContractFixtures, &fixtures) != nil || len(fixtures.Cases) != 23 {
		t.Fatal("frozen fixtures missing")
	}
	for _, item := range fixtures.Cases {
		t.Run(item.ID, func(t *testing.T) {
			request, err := ParsePublicRequest(item.Data)
			if (err == nil) != item.Valid {
				t.Fatal("parser disagrees with frozen contract", err)
			}
			if item.Valid {
				raw, err := json.Marshal(request)
				if err != nil {
					t.Fatal(err)
				}
				if _, err := ParsePublicRequest(raw); err != nil {
					t.Fatal("valid request lost its contract on serialization")
				}
				if request.TaskType == "text_to_video" || request.TaskType == "references_to_video" {
					frozen, err := NewRequest("videos.create", request)
					if err != nil || frozen.units != 6 || bytes.Contains(frozen.body, []byte("duration_seconds")) {
						t.Fatal("video wire duration and billing diverged")
					}
				}
			}
		})
	}
}

func TestPublicRequestRejectsUnsafeOrAmbiguousJSON(t *testing.T) {
	valid := `{"model":"m","task_type":"text_to_image","input":{"prompt":"synthetic"}}`
	cases := map[string]string{
		"root_null": `null`, "root_array": `[]`, "duplicate_root": `{"model":"m","model":"other","task_type":"text_to_image","input":{"prompt":"synthetic"}}`,
		"duplicate_nested": `{"model":"m","task_type":"text_to_image","input":{"prompt":"a","prompt":"b"}}`,
		"case_alias":       strings.Replace(valid, `"model"`, `"Model"`, 1), "unknown_field": strings.TrimSuffix(valid, "}") + `,"authorization_ref":"forged"}`,
		"trailing_json": valid + `{}`, "invalid_utf8": strings.Replace(valid, "synthetic", string([]byte{0xff}), 1),
		"null_input": `{"model":"m","task_type":"text_to_image","input":null}`, "empty_prompt": strings.Replace(valid, "synthetic", "", 1),
		"long_prompt":                      strings.Replace(valid, "synthetic", strings.Repeat("中", 8001), 1),
		"null_parameters":                  strings.TrimSuffix(valid, "}") + `,"parameters":null}`,
		"legacy_duration":                  `{"model":"m","task_type":"text_to_video","input":{"prompt":"synthetic"},"parameters":{"duration_seconds":5}}`,
		"duration_exponent":                `{"model":"m","task_type":"text_to_video","input":{"prompt":"synthetic"},"parameters":{"seconds":5e0}}`,
		"duration_over_limit":              `{"model":"m","task_type":"text_to_video","input":{"prompt":"synthetic"},"parameters":{"seconds":120.001}}`,
		"fractional_n_rounded_by_float":    strings.TrimSuffix(valid, "}") + `,"parameters":{"n":1.0000000000000000001}}`,
		"fractional_seed_rounded_by_float": strings.TrimSuffix(valid, "}") + `,"parameters":{"seed":2147483646.9999999999}}`,
		"allocation_exponent":              strings.TrimSuffix(valid, "}") + `,"parameters":{"seed":1e999999999}}`,
		"image_fps":                        strings.TrimSuffix(valid, "}") + `,"parameters":{"fps":24}}`,
		"foreign_strength":                 strings.TrimSuffix(valid, "}") + `,"parameters":{"strength":0.5}}`,
		"source_url":                       `{"model":"m","task_type":"image_to_image","input":{"prompt":"synthetic","source_image":{"url":"https://invalid.test/private"}}}`,
		"reference_weight_precision":       `{"model":"m","task_type":"references_to_image","input":{"prompt":"synthetic","references":[{"asset_id":"asset_example01","role":"character","weight":2.0000000000000001},{"asset_id":"asset_example02","role":"scene"}]}}`,
		"depth_limit":                      strings.TrimSuffix(valid, "}") + `,"unknown":` + strings.Repeat("[", 34) + `0` + strings.Repeat("]", 34) + `}`,
		"byte_limit":                       strings.Repeat(" ", 2<<20) + valid,
	}
	for name, raw := range cases {
		t.Run(name, func(t *testing.T) {
			if _, err := ParsePublicRequest([]byte(raw)); err == nil {
				t.Fatal("unsafe or ambiguous public request accepted")
			}
		})
	}
}

func TestPublicRequestPreservesExactSupportedNumbersAndUnicode(t *testing.T) {
	raw := `{"model":"` + strings.Repeat("模", 128) + `","task_type":"text_to_image","input":{"prompt":"` + strings.Repeat("中", 8000) + `"},"parameters":{"seed":2147483647.0,"n":1e0}}`
	parsed, err := ParsePublicRequest([]byte(raw))
	if err != nil {
		t.Fatal("schema-valid unicode and integer forms rejected", err)
	}
	request, err := NewRequest("images.generate", parsed)
	if err != nil || request.units != 1 || parsed.Parameters["seed"] != json.Number("2147483647") {
		t.Fatal("integer normalization lost precision")
	}
	video, err := ParsePublicRequest([]byte(`{"model":"video","task_type":"text_to_video","input":{"prompt":"synthetic"}}`))
	if err != nil || len(video.Parameters) != 0 {
		t.Fatal("parser invented an unconfigured video default")
	}
}
