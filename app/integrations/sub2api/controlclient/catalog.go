package controlclient

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"math"
	"net/http"
	"regexp"
	"unicode/utf8"
)

var modelAliasPattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`)
var presetSizePattern = regexp.MustCompile(`^[1-9][0-9]{1,4}x[1-9][0-9]{1,4}$`)

type PublishedModel struct {
	ID      string `json:"id"`
	Object  string `json:"object"`
	Created int64  `json:"created"`
	OwnedBy string `json:"owned_by"`
}

type ModelCatalog struct {
	Object string           `json:"object"`
	Data   []PublishedModel `json:"data"`
}

type ReleaseIdentity struct {
	Version int64  `json:"version"`
	Digest  string `json:"digest"`
}

type PublicPreset struct {
	Quality     string   `json:"quality"`
	Size        string   `json:"size,omitempty"`
	AudioFormat string   `json:"audio_format,omitempty"`
	SampleRate  int      `json:"sample_rate,omitempty"`
	Channels    int      `json:"channels,omitempty"`
	Seconds     *float64 `json:"seconds,omitempty"`
}

type PublicOutput struct {
	MediaTypes    []string `json:"media_types"`
	ExpectedCount int      `json:"expected_count"`
	MaxBytes      int64    `json:"max_bytes"`
}

type PublishedCapability struct {
	PublishedModel
	TaskType            string          `json:"task_type"`
	Asynchronous        bool            `json:"asynchronous"`
	Release             ReleaseIdentity `json:"release"`
	SupportedParameters []string        `json:"supported_parameters"`
	Presets             []PublicPreset  `json:"presets"`
	Output              PublicOutput    `json:"output"`
}

type CapabilityCatalog struct {
	Object string                `json:"object"`
	Data   []PublishedCapability `json:"data"`
}

func validPublishedModel(model PublishedModel) bool {
	return modelAliasPattern.MatchString(model.ID) && model.Object == "model" && model.Created > 0 && model.Created <= 253402300799 && model.OwnedBy == "render-control"
}

// Validate JSON keys before projecting known fields. Ordinary encoding/json
// accepts duplicate keys, which can give two consumers different identities.
func catalogJSON(raw []byte) error {
	if !utf8.Valid(raw) {
		return errors.New("catalog JSON is not UTF-8")
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	nodes := 0
	var visit func(int) error
	visit = func(depth int) error {
		nodes++
		if depth > 16 || nodes > 100000 {
			return errors.New("catalog JSON exceeds structural limits")
		}
		token, err := decoder.Token()
		if err != nil {
			return err
		}
		switch token {
		case json.Delim('{'):
			seen := map[string]bool{}
			for decoder.More() {
				key, err := decoder.Token()
				name, ok := key.(string)
				if err != nil || !ok || seen[name] {
					return errors.New("catalog JSON has duplicate or invalid keys")
				}
				seen[name] = true
				if err = visit(depth + 1); err != nil {
					return err
				}
			}
			_, err = decoder.Token()
		case json.Delim('['):
			for decoder.More() {
				if err = visit(depth + 1); err != nil {
					return err
				}
			}
			_, err = decoder.Token()
		}
		return err
	}
	if err := visit(0); err != nil {
		return err
	}
	if _, err := decoder.Token(); err != io.EOF {
		return errors.New("catalog JSON contains trailing data")
	}
	return nil
}

func (c *Client) Models(ctx context.Context, subject Subject) (ModelCatalog, error) {
	var raw json.RawMessage
	if err := c.call(ctx, http.MethodGet, "/gateway/v1/models", &subject, nil, &raw); err != nil {
		return ModelCatalog{}, err
	}
	var result ModelCatalog
	if catalogJSON(raw) != nil || json.Unmarshal(raw, &result) != nil || result.Object != "list" || result.Data == nil || len(result.Data) > 4096 {
		return ModelCatalog{}, &CallError{Code: "invalid_response"}
	}
	seen := map[string]bool{}
	for _, model := range result.Data {
		if !validPublishedModel(model) || seen[model.ID] {
			return ModelCatalog{}, &CallError{Code: "invalid_response"}
		}
		seen[model.ID] = true
	}
	return result, nil
}

func validCapability(model PublishedCapability) bool {
	if !validPublishedModel(model.PublishedModel) || !model.Asynchronous || model.Release.Version < 1 || !sha256Pattern.MatchString(model.Release.Digest) || model.SupportedParameters == nil || len(model.SupportedParameters) > 8 || len(model.Presets) < 1 || len(model.Presets) > 128 || model.Output.ExpectedCount != 1 || model.Output.MaxBytes < 1 || model.Output.MaxBytes > MaxMediaBytes || len(model.Output.MediaTypes) < 1 || len(model.Output.MediaTypes) > 5 {
		return false
	}
	switch model.TaskType {
	case "text_to_image", "image_to_image", "references_to_image", "text_to_video", "references_to_video", "text_to_audio":
	default:
		return false
	}
	seen := map[string]bool{}
	for _, parameter := range model.SupportedParameters {
		if seen[parameter] {
			return false
		}
		seen[parameter] = true
		if model.TaskType == "text_to_audio" && (parameter == "size" || parameter == "fps" || parameter == "strength") {
			return false
		}
		switch parameter {
		case "size", "quality", "seed", "n", "seconds", "fps", "strength", "negative_prompt":
		default:
			return false
		}
	}
	seen = map[string]bool{}
	for _, preset := range model.Presets {
		if preset.Quality != "draft" && preset.Quality != "standard" && preset.Quality != "high" {
			return false
		}
		if model.TaskType == "text_to_audio" {
			media := map[string]string{"wav": "audio/wav", "flac": "audio/flac", "mp3": "audio/mpeg", "opus": "audio/ogg"}[preset.AudioFormat]
			if media == "" || preset.Size != "" || preset.Seconds == nil || *preset.Seconds < 0.001 || *preset.Seconds > 600 || math.Abs(*preset.Seconds*1000-math.Round(*preset.Seconds*1000)) > 0.0000001 || preset.SampleRate < 8000 || preset.SampleRate > 96000 || preset.Channels < 1 || preset.Channels > 2 || preset.AudioFormat == "opus" && preset.SampleRate != 48000 || model.Output.MaxBytes > 256<<20 {
				return false
			}
			found := false
			for _, candidate := range model.Output.MediaTypes {
				if candidate == media {
					found = true
				}
			}
			if !found {
				return false
			}
		} else if !presetSizePattern.MatchString(preset.Size) || preset.Seconds != nil && (*preset.Seconds <= 0 || *preset.Seconds > 3600) || preset.AudioFormat != "" || preset.SampleRate != 0 || preset.Channels != 0 {
			return false
		}
		raw, err := json.Marshal(preset)
		if err != nil || seen[string(raw)] {
			return false
		}
		seen[string(raw)] = true
	}
	seen = map[string]bool{}
	for _, media := range model.Output.MediaTypes {
		if !allowedMedia(media) || seen[media] || !taskMediaMatches(model.TaskType, media) {
			return false
		}
		seen[media] = true
	}
	return true
}

func (c *Client) Capabilities(ctx context.Context, subject Subject) (CapabilityCatalog, error) {
	var raw json.RawMessage
	if err := c.call(ctx, http.MethodGet, "/gateway/v1/capabilities", &subject, nil, &raw); err != nil {
		return CapabilityCatalog{}, err
	}
	var result CapabilityCatalog
	if catalogJSON(raw) != nil || json.Unmarshal(raw, &result) != nil || result.Object != "list" || result.Data == nil || len(result.Data) > 4096 {
		return CapabilityCatalog{}, &CallError{Code: "invalid_response"}
	}
	seen := map[string]bool{}
	for _, model := range result.Data {
		if !validCapability(model) || seen[model.ID] {
			return CapabilityCatalog{}, &CallError{Code: "invalid_response"}
		}
		seen[model.ID] = true
	}
	return result, nil
}
