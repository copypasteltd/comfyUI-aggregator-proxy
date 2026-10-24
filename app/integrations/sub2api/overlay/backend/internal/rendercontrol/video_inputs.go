package rendercontrol

import (
	"encoding/json"
	"io"
	"mime"
	"net/http"

	"github.com/Wei-Shaw/sub2api/internal/rendercontrol/controlclient"
	"github.com/gin-gonic/gin"
)

func (h *PublicHandler) parseVideo(c *gin.Context, subject Subject) (_ *editPlan, err error) {
	plan := &editPlan{}
	success := false
	defer func() {
		if !success {
			plan.close()
		}
	}()
	root := map[string]any{}
	var source map[string]any
	mediaType, _, err := mime.ParseMediaType(c.GetHeader("Content-Type"))
	if err != nil || c.GetHeader("Content-Encoding") != "" && c.GetHeader("Content-Encoding") != "identity" {
		return plan, ErrInvalid
	}
	if mediaType == "application/json" {
		file, err := spoolFile(h.uploadDirectory, c.Request.Body, 128<<20)
		if err != nil {
			return plan, err
		}
		defer file.close()
		root, err = strictSpoolObject(file.file)
		if err != nil || !properties(root, "model", "prompt", "size", "quality", "seconds", "references", "input_reference", "workflow_options") {
			return plan, ErrInvalid
		}
		if value, present := root["input_reference"]; present {
			source, err = h.editSource(c, subject, value, plan)
			if err != nil {
				return plan, err
			}
			delete(root, "input_reference")
		}
	} else if mediaType == "multipart/form-data" {
		reader, err := multipartReader(c.Request)
		if err != nil {
			return plan, err
		}
		for count := 0; ; count++ {
			part, err := reader.NextRawPart()
			if err == io.EOF {
				break
			}
			if err != nil || count >= 8 || part.Header.Get("Content-Transfer-Encoding") != "" {
				return plan, ErrInvalid
			}
			name := part.FormName()
			if part.FileName() != "" {
				if name != "input_reference" || source != nil {
					return plan, ErrInvalid
				}
				file, err := spoolFile(h.uploadDirectory, part, controlclient.MaxAssetBytes)
				if err != nil {
					return plan, err
				}
				source = map[string]any{"asset_id": "asset_inline_placeholder"}
				plan.bindings = append(plan.bindings, editBinding{value: source, file: file})
				if file.MediaType != "image/png" && file.MediaType != "image/jpeg" && file.MediaType != "image/webp" {
					return plan, ErrInvalid
				}
			} else {
				if _, duplicate := root[name]; duplicate || name != "model" && name != "prompt" && name != "size" && name != "quality" && name != "seconds" && name != "workflow_options" {
					return plan, ErrInvalid
				}
				raw, err := io.ReadAll(io.LimitReader(part, 65537))
				if err != nil || len(raw) > 65536 || !textValue(string(raw), 0, 65536) {
					return plan, ErrInvalid
				}
				root[name] = string(raw)
				if name == "workflow_options" {
					root[name], err = strictPublicObject(raw)
					if err != nil {
						return plan, err
					}
				}
			}
			_ = part.Close()
		}
	} else {
		return plan, ErrInvalid
	}
	encoded, err := json.Marshal(root)
	if err != nil {
		return plan, ErrInvalid
	}
	plan.request, _, err = parseGeneration(encoded, true)
	if err != nil {
		return plan, err
	}
	if source != nil {
		if _, mixed := plan.request.Input["references"]; mixed {
			return plan, ErrInvalid
		}
		source["role"] = "first_frame"
		plan.request.Input["references"] = []any{source}
		plan.request.TaskType = "references_to_video"
	}
	encoded, err = json.Marshal(plan.request)
	if err != nil {
		return plan, ErrInvalid
	}
	if _, err = ParsePublicRequest(encoded); err != nil {
		return plan, err
	}
	success = true
	return plan, nil
}

func (h *PublicHandler) videoGeneration(c *gin.Context) {
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
	plan, err := h.parseVideo(c, subject)
	if err != nil {
		publicError(c, err)
		return
	}
	defer plan.close()
	rule, err := h.rule(key, plan.request.Model, plan.request.TaskType)
	if err != nil {
		publicError(c, err)
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
	result := compatibilityView(localPublicJob(intent), true)
	c.Header("Location", result["poll_url"].(string))
	c.Header("Retry-After", "2")
	c.JSON(http.StatusAccepted, result)
}
