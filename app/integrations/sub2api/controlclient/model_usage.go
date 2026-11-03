package controlclient

import (
	"encoding/json"
	"errors"
	"strings"
)

// Usage is an additive public contract. The execution graph, device paths and
// dependency metadata must never travel through the public model catalog.
func validUsage(raw json.RawMessage, alias string) bool {
	if len(raw) == 0 || string(raw) == "null" {
		return true
	}
	if len(raw) > 1<<20 {
		return false
	}
	var doc map[string]any
	if json.Unmarshal(raw, &doc) != nil || doc == nil {
		return false
	}
	allowed := map[string]bool{}
	for _, name := range strings.Fields("schema_version display_name description notes tags task_type asynchronous endpoints request_content_type required_scopes defaults parameter_details references input_slots text_slots context presets output request_schema example_request output_retention_seconds availability_note") {
		allowed[name] = true
	}
	for name := range doc {
		if !allowed[name] {
			return false
		}
	}
	if doc["schema_version"] != "1.0" && doc["schema_version"] != "1.1" {
		return false
	}
	if doc["asynchronous"] != true || doc["request_content_type"] != "application/json" {
		return false
	}
	var safe func(any) bool
	safe = func(value any) bool {
		switch v := value.(type) {
		case map[string]any:
			for key, child := range v {
				switch key {
				case "workflow_api", "dependency_lock", "bindings", "runtime_profiles", "path_mode", "node_id", "input_name", "comfy_root", "credential", "secret", "base_url":
					return false
				}
				if !safe(child) {
					return false
				}
			}
		case []any:
			for _, child := range v {
				if !safe(child) {
					return false
				}
			}
		}
		return true
	}
	example, ok := doc["example_request"].(map[string]any)
	if !ok || example["model"] != alias || example["task_type"] != doc["task_type"] {
		return false
	}
	switch doc["task_type"] {
	case "text_to_image", "image_to_image", "references_to_image", "text_to_video", "references_to_video", "text_to_audio", "image_to_3d":
	default:
		return false
	}
	schema, ok := doc["request_schema"].(map[string]any)
	if !ok || schema["type"] != "object" {
		return false
	}
	return safe(doc)
}

// WithPublicTaskIDs projects the Provider's rc_ task identity into usage examples
// and the one constrained parent field. It leaves the authenticated native
// response untouched and never rewrites arbitrary text or identifiers.
func (model PublishedModel) WithPublicTaskIDs() (PublishedModel, error) {
	if !validUsage(model.Usage, model.ID) {
		return PublishedModel{}, errors.New("invalid model usage")
	}
	if len(model.Usage) == 0 || string(model.Usage) == "null" {
		return model, nil
	}
	var doc map[string]any
	_ = json.Unmarshal(model.Usage, &doc)
	context, ok := doc["context"].(map[string]any)
	if !ok || context["required"] != true {
		return model, nil
	}
	at := func(value any, keys ...string) map[string]any {
		for _, key := range keys {
			object, ok := value.(map[string]any)
			if !ok {
				return nil
			}
			value = object[key]
		}
		object, _ := value.(map[string]any)
		return object
	}
	parent := at(doc, "request_schema", "properties", "input", "properties", "context", "properties", "job_id")
	example := at(doc, "example_request", "input", "context")
	if parent == nil || example == nil {
		return PublishedModel{}, errors.New("invalid context usage")
	}
	parent["pattern"] = "^rc_[0-9a-f]{32}$"
	example["job_id"] = "rc_" + strings.Repeat("0", 32)
	raw, err := json.Marshal(doc)
	if err != nil {
		return PublishedModel{}, err
	}
	model.Usage = raw
	return model, nil
}
