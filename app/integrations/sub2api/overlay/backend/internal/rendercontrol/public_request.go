package rendercontrol

import (
	"bytes"
	"encoding/json"
	"io"
	"math/big"
	"regexp"
	"strconv"
	"strings"
	"unicode/utf8"

	"github.com/Wei-Shaw/sub2api/internal/rendercontrol/controlclient"
)

var publicAssetPattern = regexp.MustCompile(`^asset_[A-Za-z0-9_-]{6,80}$`)
var publicRolePattern = regexp.MustCompile(`^[a-z][a-z0-9_]{0,31}$`)
var publicParentJobPattern = regexp.MustCompile(`^(job|rc)_[a-f0-9]{32}$`)
var publicSizePattern = regexp.MustCompile(`^[1-9][0-9]{1,4}x[1-9][0-9]{1,4}$`)

func properties(value map[string]any, names ...string) bool {
	if value == nil {
		return false
	}
	for key := range value {
		found := false
		for _, name := range names {
			found = found || key == name
		}
		if !found {
			return false
		}
	}
	return true
}

func textValue(value any, min, max int) bool {
	text, ok := value.(string)
	return ok && utf8.ValidString(text) && utf8.RuneCountInString(text) >= min && utf8.RuneCountInString(text) <= max
}

func publicAsset(value any) bool {
	asset, ok := value.(map[string]any)
	if !ok || !properties(asset, "asset_id") {
		return false
	}
	id, ok := asset["asset_id"].(string)
	return ok && publicAssetPattern.MatchString(id)
}

func exactPublicNumber(value any) (*big.Rat, bool) {
	number, ok := value.(json.Number)
	if !ok || len(number) > 64 {
		return nil, false
	}
	if index := strings.IndexAny(string(number), "eE"); index >= 0 {
		exponent := strings.TrimLeft(strings.TrimLeft(string(number)[index+1:], "+-"), "0")
		if len(exponent) > 3 {
			return nil, false
		}
	}
	n, ok := new(big.Rat).SetString(string(number))
	return n, ok
}

func boundedNumber(value any, min, max float64) bool {
	n, ok := exactPublicNumber(value)
	if !ok {
		return false
	}
	lower, _ := new(big.Rat).SetString(strconv.FormatFloat(min, 'f', -1, 64))
	upper, _ := new(big.Rat).SetString(strconv.FormatFloat(max, 'f', -1, 64))
	return n.Cmp(lower) >= 0 && n.Cmp(upper) <= 0
}

// Integer schema values such as 1.0 normalize to 1 before freezing the request.
func publicInteger(values map[string]any, name string, min, max int64) bool {
	value, present := values[name]
	if !present {
		return true
	}
	if !boundedNumber(value, float64(min), float64(max)) {
		return false
	}
	n, _ := exactPublicNumber(value)
	if !n.IsInt() {
		return false
	}
	values[name] = json.Number(n.Num().String())
	return true
}

// ParsePublicRequest enforces the frozen job-create contract before any money
// is reserved. The control plane still checks actual assets and release policy.
func ParsePublicRequest(raw []byte) (controlclient.JobRequest, error) {
	if len(raw) == 0 || len(raw) > 2<<20 || !utf8.Valid(raw) {
		return controlclient.JobRequest{}, ErrInvalid
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	count := 0
	if validateJSON(decoder, 0, &count) != nil {
		return controlclient.JobRequest{}, ErrInvalid
	}
	if _, err := decoder.Token(); err != io.EOF {
		return controlclient.JobRequest{}, ErrInvalid
	}
	decoder = json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	var root map[string]any
	if decoder.Decode(&root) != nil || !properties(root, "model", "task_type", "input", "parameters", "client_request_id") || !textValue(root["model"], 1, 128) {
		return controlclient.JobRequest{}, ErrInvalid
	}
	task, ok := root["task_type"].(string)
	if !ok {
		return controlclient.JobRequest{}, ErrInvalid
	}
	input, ok := root["input"].(map[string]any)
	if !ok || !properties(input, "prompt", "negative_prompt", "source_image", "mask", "references", "media", "texts", "context") {
		return controlclient.JobRequest{}, ErrInvalid
	}
	if value, present := input["context"]; present {
		context, valid := value.(map[string]any)
		if !valid || !properties(context, "job_id") || task != "text_to_video" && task != "references_to_video" {
			return controlclient.JobRequest{}, ErrInvalid
		}
		parent, valid := context["job_id"].(string)
		if !valid || !publicParentJobPattern.MatchString(parent) {
			return controlclient.JobRequest{}, ErrInvalid
		}
	}
	_, prompt := input["prompt"]
	if prompt && !textValue(input["prompt"], 1, 8000) {
		return controlclient.JobRequest{}, ErrInvalid
	}
	_, media := input["media"]
	if media {
		items, valid := input["media"].([]any)
		if !valid || len(items) < 1 || len(items) > 16 {
			return controlclient.JobRequest{}, ErrInvalid
		}
		seen := map[string]bool{}
		for _, item := range items {
			value, valid := item.(map[string]any)
			if !valid || !properties(value, "slot", "asset_id") {
				return controlclient.JobRequest{}, ErrInvalid
			}
			slot, valid := value["slot"].(string)
			id, validID := value["asset_id"].(string)
			if !valid || !validID || !publicRolePattern.MatchString(slot) || !publicAssetPattern.MatchString(id) || seen[slot] {
				return controlclient.JobRequest{}, ErrInvalid
			}
			seen[slot] = true
		}
	}
	_, texts := input["texts"]
	if texts {
		values, valid := input["texts"].(map[string]any)
		if !valid || len(values) < 1 || len(values) > 16 {
			return controlclient.JobRequest{}, ErrInvalid
		}
		total := 0
		for name, value := range values {
			if !publicRolePattern.MatchString(name) || !textValue(value, 1, 32000) {
				return controlclient.JobRequest{}, ErrInvalid
			}
			total += utf8.RuneCountInString(value.(string))
		}
		if total > 32000 {
			return controlclient.JobRequest{}, ErrInvalid
		}
	}
	if !prompt && !texts && !media && task != "image_to_3d" {
		return controlclient.JobRequest{}, ErrInvalid
	}
	if value, present := input["negative_prompt"]; present && !textValue(value, 0, 8000) {
		return controlclient.JobRequest{}, ErrInvalid
	}
	_, source := input["source_image"]
	_, mask := input["mask"]
	_, references := input["references"]
	if source && !publicAsset(input["source_image"]) || mask && (!source || !publicAsset(input["mask"])) {
		return controlclient.JobRequest{}, ErrInvalid
	}
	if references {
		items, ok := input["references"].([]any)
		minimum := 2
		if task == "references_to_video" {
			minimum = 1
		}
		if !ok || len(items) < minimum || len(items) > 4 {
			return controlclient.JobRequest{}, ErrInvalid
		}
		for _, item := range items {
			ref, ok := item.(map[string]any)
			if !ok || !properties(ref, "asset_id", "role", "weight") {
				return controlclient.JobRequest{}, ErrInvalid
			}
			id, okID := ref["asset_id"].(string)
			role, okRole := ref["role"].(string)
			if !okID || !publicAssetPattern.MatchString(id) || !okRole || !publicRolePattern.MatchString(role) {
				return controlclient.JobRequest{}, ErrInvalid
			}
			if len(items) == 1 && role != "first_frame" {
				return controlclient.JobRequest{}, ErrInvalid
			}
			if weight, present := ref["weight"]; present && !boundedNumber(weight, 0, 2) {
				return controlclient.JobRequest{}, ErrInvalid
			}
		}
	}
	parameters := map[string]any{}
	if value, present := root["parameters"]; present {
		parameters, ok = value.(map[string]any)
		if !ok || !properties(parameters, "size", "quality", "seed", "n", "seconds", "fps", "strength") {
			return controlclient.JobRequest{}, ErrInvalid
		}
	}
	if size, present := parameters["size"]; present {
		text, ok := size.(string)
		if !ok || !publicSizePattern.MatchString(text) {
			return controlclient.JobRequest{}, ErrInvalid
		}
	}
	if quality, present := parameters["quality"]; present && quality != "draft" && quality != "standard" && quality != "high" {
		return controlclient.JobRequest{}, ErrInvalid
	}
	if !publicInteger(parameters, "seed", 0, 2147483647) || !publicInteger(parameters, "n", 1, 1) || !publicInteger(parameters, "fps", 1, 120) {
		return controlclient.JobRequest{}, ErrInvalid
	}
	_, seconds := parameters["seconds"]
	_, fps := parameters["fps"]
	_, strength := parameters["strength"]
	if strength && !boundedNumber(parameters["strength"], 0, 1) {
		return controlclient.JobRequest{}, ErrInvalid
	}
	switch task {
	case "text_to_image", "text_to_video", "text_to_audio":
		if source || mask || references {
			return controlclient.JobRequest{}, ErrInvalid
		}
	case "image_to_image":
		if !source && !media || references {
			return controlclient.JobRequest{}, ErrInvalid
		}
	case "references_to_image", "references_to_video":
		if !references && !media || source || mask {
			return controlclient.JobRequest{}, ErrInvalid
		}
	case "image_to_3d":
		if !source && !media || prompt || input["negative_prompt"] != nil || references || mask {
			return controlclient.JobRequest{}, ErrInvalid
		}
	default:
		return controlclient.JobRequest{}, ErrInvalid
	}
	video := task == "text_to_video" || task == "references_to_video"
	audio := task == "text_to_audio"
	_, size := parameters["size"]
	if task == "image_to_3d" && (size || seconds || fps || strength) {
		return controlclient.JobRequest{}, ErrInvalid
	}
	if !video && !audio && seconds || !video && fps || audio && size || task != "image_to_image" && strength {
		return controlclient.JobRequest{}, ErrInvalid
	}
	if (video || audio) && seconds {
		number, ok := parameters["seconds"].(json.Number)
		maximum := 120.0
		if audio {
			maximum = 600
		}
		if !ok || !durationPattern.MatchString(string(number)) || !boundedNumber(number, 0.001, maximum) {
			return controlclient.JobRequest{}, ErrInvalid
		}
	}
	clientID := ""
	if value, present := root["client_request_id"]; present {
		if !textValue(value, 1, 128) {
			return controlclient.JobRequest{}, ErrInvalid
		}
		clientID = value.(string)
	}
	return controlclient.JobRequest{Model: root["model"].(string), TaskType: task, Input: input, Parameters: parameters, ClientRequestID: clientID}, nil
}
