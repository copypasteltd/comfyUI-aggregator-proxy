package rendercontrol

import (
	"encoding/base64"
	"encoding/json"
	"io"
	"mime"
	"net/http"
	"strings"

	"github.com/Wei-Shaw/sub2api/internal/rendercontrol/controlclient"
	"github.com/gin-gonic/gin"
)

type editBinding struct {
	value map[string]any
	file  *uploadFile
}
type editPlan struct {
	request  controlclient.JobRequest
	format   string
	bindings []editBinding
}

func (p *editPlan) close() {
	for _, binding := range p.bindings {
		binding.file.close()
	}
}

func (h *PublicHandler) editSource(c *gin.Context, subject Subject, value any, plan *editPlan) (map[string]any, error) {
	item, ok := value.(map[string]any)
	if !ok {
		return nil, ErrInvalid
	}
	if text, ok := item["image_url"].(string); ok && properties(item, "image_url") && strings.HasPrefix(text, "data:") {
		prefix, encoded, ok := strings.Cut(text, ",")
		if !ok || len(encoded) > base64.StdEncoding.EncodedLen(int(controlclient.MaxAssetBytes)) {
			return nil, ErrInvalid
		}
		declared := ""
		switch prefix {
		case "data:image/png;base64":
			declared = "image/png"
		case "data:image/jpeg;base64":
			declared = "image/jpeg"
		case "data:image/webp;base64":
			declared = "image/webp"
		default:
			return nil, ErrInvalid
		}
		if strings.ContainsAny(encoded, "\r\n\t ") {
			return nil, ErrInvalid
		}
		file, err := spoolFile(h.uploadDirectory, base64.NewDecoder(base64.StdEncoding.Strict(), strings.NewReader(encoded)), controlclient.MaxAssetBytes)
		if err != nil {
			return nil, err
		}
		if file.MediaType != declared {
			file.close()
			return nil, ErrInvalid
		}
		ref := map[string]any{"asset_id": "asset_inline_placeholder"}
		plan.bindings = append(plan.bindings, editBinding{ref, file})
		return ref, nil
	}
	id, err := h.inputAssetID(c.Request.Context(), subject, value)
	if err != nil {
		return nil, err
	}
	return map[string]any{"asset_id": id}, nil
}

func (h *PublicHandler) parseEdit(c *gin.Context, subject Subject) (plan *editPlan, err error) {
	plan = &editPlan{}
	success := false
	defer func() {
		if !success {
			plan.close()
		}
	}()
	mediaType, _, parseErr := mime.ParseMediaType(c.GetHeader("Content-Type"))
	if parseErr != nil || c.GetHeader("Content-Encoding") != "" && c.GetHeader("Content-Encoding") != "identity" {
		return plan, ErrInvalid
	}
	var root map[string]any
	var sources []map[string]any
	var mask map[string]any
	if mediaType == "application/json" {
		body, err := spoolFile(h.uploadDirectory, c.Request.Body, 128<<20)
		if err != nil {
			return plan, err
		}
		defer body.close()
		root, err = strictSpoolObject(body.file)
		if err != nil {
			return plan, err
		}
		if !properties(root, "model", "prompt", "n", "size", "quality", "response_format", "user", "images", "source_image", "mask", "references", "workflow_options") {
			return plan, ErrInvalid
		}
		_, images := root["images"]
		_, source := root["source_image"]
		_, refs := root["references"]
		if (images && (source || refs)) || (source && refs) || (!images && !source && !refs) {
			return plan, ErrInvalid
		}
		if images {
			items, ok := root["images"].([]any)
			if !ok || len(items) < 1 || len(items) > 4 {
				return plan, ErrInvalid
			}
			for _, item := range items {
				value, ok := item.(map[string]any)
				if !ok || !properties(value, "image_url") {
					return plan, ErrInvalid
				}
				ref, err := h.editSource(c, subject, item, plan)
				if err != nil {
					return plan, err
				}
				sources = append(sources, ref)
			}
		}
		if source {
			ref, err := h.editSource(c, subject, root["source_image"], plan)
			if err != nil {
				return plan, err
			}
			sources = append(sources, ref)
		}
		if value, present := root["mask"]; present {
			mask, err = h.editSource(c, subject, value, plan)
			if err != nil {
				return plan, err
			}
		}
	} else if mediaType == "multipart/form-data" {
		reader, err := multipartReader(c.Request)
		if err != nil {
			return plan, err
		}
		root = map[string]any{}
		style := ""
		for count := 0; ; count++ {
			part, err := reader.NextRawPart()
			if err == io.EOF {
				break
			}
			if err != nil || count >= 21 {
				return plan, ErrInvalid
			}
			name := part.FormName()
			if part.Header.Get("Content-Transfer-Encoding") != "" {
				_ = part.Close()
				return plan, ErrInvalid
			}
			if part.FileName() != "" {
				if name != "image" && name != "image[]" && name != "mask" {
					_ = part.Close()
					return plan, ErrInvalid
				}
				if name == "mask" && mask != nil || name != "mask" && (len(sources) >= 4 || style != "" && style != name || name == "image" && len(sources) > 0) {
					_ = part.Close()
					return plan, ErrInvalid
				}
				file, err := spoolFile(h.uploadDirectory, part, controlclient.MaxAssetBytes)
				_ = part.Close()
				if err != nil {
					return plan, err
				}
				if file.MediaType != "image/png" && file.MediaType != "image/jpeg" && file.MediaType != "image/webp" {
					file.close()
					return plan, ErrInvalid
				}
				ref := map[string]any{"asset_id": "asset_inline_placeholder"}
				plan.bindings = append(plan.bindings, editBinding{ref, file})
				if name == "mask" {
					mask = ref
				} else {
					style = name
					sources = append(sources, ref)
				}
			} else {
				if _, duplicate := root[name]; duplicate {
					_ = part.Close()
					return plan, ErrInvalid
				}
				switch name {
				case "model", "prompt", "n", "size", "quality", "response_format", "user", "workflow_options":
				default:
					_ = part.Close()
					return plan, ErrInvalid
				}
				data, err := io.ReadAll(io.LimitReader(part, 65537))
				_ = part.Close()
				if err != nil || len(data) > 65536 {
					return plan, ErrInvalid
				}
				if name == "n" {
					root[name] = json.Number(data)
				} else if name == "workflow_options" {
					value, err := strictPublicObject(data)
					if err != nil {
						return plan, err
					}
					root[name] = value
				} else {
					root[name] = string(data)
				}
			}
		}
		if len(sources) == 0 {
			return plan, ErrInvalid
		}
	} else {
		return plan, ErrInvalid
	}
	base := map[string]any{}
	for _, name := range []string{"model", "prompt", "n", "size", "quality", "response_format", "user"} {
		if value, ok := root[name]; ok {
			base[name] = value
		}
	}
	encoded, err := json.Marshal(base)
	if err != nil {
		return plan, ErrInvalid
	}
	plan.request, plan.format, err = parseGeneration(encoded, false)
	if err != nil {
		return plan, err
	}
	options := map[string]any{}
	if value, present := root["workflow_options"]; present {
		var ok bool
		options, ok = value.(map[string]any)
		if !ok || !properties(options, "image_roles", "strength", "seed", "negative_prompt") {
			return plan, ErrInvalid
		}
	}
	if value, present := options["seed"]; present {
		plan.request.Parameters["seed"] = value
	}
	if value, present := options["negative_prompt"]; present {
		plan.request.Input["negative_prompt"] = value
	}
	if refs, present := root["references"]; present {
		_, roles := options["image_roles"]
		_, strength := options["strength"]
		if len(sources) > 0 || mask != nil || roles || strength {
			return plan, ErrInvalid
		}
		items, ok := refs.([]any)
		if !ok || len(items) < 2 || len(items) > 4 {
			return plan, ErrInvalid
		}
		for _, item := range items {
			value, ok := item.(map[string]any)
			if !ok {
				return plan, ErrInvalid
			}
			if _, err = h.inputAssetID(c.Request.Context(), subject, map[string]any{"asset_id": value["asset_id"]}); err != nil {
				return plan, err
			}
		}
		plan.request.Input["references"] = refs
		plan.request.TaskType = "references_to_image"
	} else if len(sources) == 1 {
		if _, present := options["image_roles"]; present {
			return plan, ErrInvalid
		}
		plan.request.TaskType = "image_to_image"
		plan.request.Input["source_image"] = sources[0]
		if mask != nil {
			plan.request.Input["mask"] = mask
		}
		if strength, present := options["strength"]; present {
			plan.request.Parameters["strength"] = strength
		}
	} else {
		if len(sources) < 2 || len(sources) > 4 || mask != nil {
			return plan, ErrInvalid
		}
		if _, present := options["strength"]; present {
			return plan, ErrInvalid
		}
		roles, ok := options["image_roles"].([]any)
		if !ok || len(roles) != len(sources) {
			return plan, ErrInvalid
		}
		indexed := map[int]string{}
		for _, value := range roles {
			role, ok := value.(map[string]any)
			if !ok || !properties(role, "index", "role") || !publicInteger(role, "index", 0, int64(len(sources)-1)) || !textValue(role["role"], 1, 32) {
				return plan, ErrInvalid
			}
			number, ok := role["index"].(json.Number)
			if !ok {
				return plan, ErrInvalid
			}
			index, err := number.Int64()
			if err != nil || indexed[int(index)] != "" {
				return plan, ErrInvalid
			}
			indexed[int(index)] = role["role"].(string)
		}
		refs := []any{}
		for index, ref := range sources {
			ref["role"] = indexed[index]
			refs = append(refs, ref)
		}
		plan.request.TaskType = "references_to_image"
		plan.request.Input["references"] = refs
	}
	encoded, err = json.Marshal(plan.request)
	if err != nil {
		return plan, ErrInvalid
	}
	validated, err := ParsePublicRequest(encoded)
	if err != nil {
		return plan, err
	}
	// Preserve binding maps until inline uploads replace their placeholders.
	_ = validated
	success = true
	return plan, nil
}

func (h *PublicHandler) prepareEdit(c *gin.Context, plan *editPlan, subject Subject, rule publicRule) (controlclient.JobRequest, error) {
	current, _ := subject.GatewaySubject()
	policy, err := h.clients[rule.model.Target.gatewayID].DeliveryPolicy(c.Request.Context(), current)
	if err != nil {
		return controlclient.JobRequest{}, err
	}
	operation := "images:create"
	if strings.HasSuffix(plan.request.TaskType, "video") {
		operation = "videos:create"
	}
	if !policy.Allows(operation, "jobs:create") || len(plan.bindings) > 0 && !policy.Allows("assets:upload") {
		return controlclient.JobRequest{}, ErrDenied
	}
	// Check every existing input's target before any inline upload has a side effect.
	inputs := []map[string]any{}
	for _, name := range []string{"source_image", "mask"} {
		if value, ok := plan.request.Input[name].(map[string]any); ok {
			inputs = append(inputs, value)
		}
	}
	if refs, ok := plan.request.Input["references"].([]any); ok {
		for _, ref := range refs {
			if value, ok := ref.(map[string]any); ok {
				inputs = append(inputs, value)
			}
		}
	}
	for _, value := range inputs {
		id, _ := value["asset_id"].(string)
		if id == "asset_inline_placeholder" {
			continue
		}
		upload, err := h.store.loadAsset(c.Request.Context(), subject, id)
		if err != nil {
			return controlclient.JobRequest{}, err
		}
		if upload.GatewayID != rule.model.Target.gatewayID {
			return controlclient.JobRequest{}, ErrNotFound
		}
	}
	for _, binding := range plan.bindings {
		asset, err := h.ingestAsset(c, subject, rule, binding.file, "inline:"+digest([]byte(plan.request.Model+"\x00"+binding.file.SHA256)), true)
		if err != nil {
			return controlclient.JobRequest{}, err
		}
		binding.value["asset_id"] = asset.ID
	}
	encoded, err := json.Marshal(plan.request)
	if err != nil {
		return controlclient.JobRequest{}, ErrInvalid
	}
	request, err := ParsePublicRequest(encoded)
	if err == nil {
		c.Set("render_control_asset_inputs", true)
	}
	return request, err
}

func (h *PublicHandler) editImage(c *gin.Context)      { h.imageEdit(c, false) }
func (h *PublicHandler) editImageAsync(c *gin.Context) { h.imageEdit(c, true) }
func (h *PublicHandler) imageEdit(c *gin.Context, asynchronous bool) {
	owned := c.Request.Context().Value(uploadOwnership{}) != true
	if !takeUpload(c, h) {
		return
	}
	defer releaseUpload(c, h, owned)
	subject, key, err := h.identity(c)
	if err != nil {
		publicError(c, err)
		return
	}
	token, err := publicIdempotency(c)
	if err != nil {
		publicError(c, err)
		return
	}
	c.Request.Header.Set("Idempotency-Key", token)
	plan, err := h.parseEdit(c, subject)
	if err != nil {
		publicError(c, err)
		return
	}
	defer plan.close()
	if !asynchronous && plan.format == "url" && h.publicOrigin == "" {
		publicError(c, ErrInvalid)
		return
	}
	rule, err := h.requestRule(c, key, plan.request.Model, plan.request.TaskType)
	if err != nil {
		publicError(c, err)
		return
	}
	if !asynchronous {
		h.waitImage(c, plan.request, plan.format, func(subject Subject, rule publicRule) (controlclient.JobRequest, error) {
			return h.prepareEdit(c, plan, subject, rule)
		})
		return
	}
	request, err := h.prepareEdit(c, plan, subject, rule)
	if err != nil {
		publicError(c, err)
		return
	}
	intent, err := h.reserveRequest(c, request)
	if err != nil {
		publicError(c, err)
		return
	}
	result := compatibilityView(localPublicJob(intent), false)
	c.Header("Location", result["poll_url"].(string))
	c.Header("Retry-After", "2")
	c.JSON(http.StatusAccepted, result)
}
