package controlclient

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"mime/multipart"
	"net/http"
	"strconv"
	"strings"
	"time"
)

const MaxAssetBytes int64 = 20 << 20

type UploadedAsset struct {
	ID        string `json:"id"`
	Object    string `json:"object"`
	Status    string `json:"status"`
	Bytes     int64  `json:"bytes"`
	SHA256    string `json:"sha256"`
	MediaType string `json:"media_type"`
	Width     int    `json:"width"`
	Height    int    `json:"height"`
	ExpiresAt int64  `json:"expires_at"`
}

func validAssetType(value string) bool {
	return value == "image/png" || value == "image/jpeg" || value == "image/webp"
}

// UploadAsset sends once, without a buffering goroutine, redirects or transport
// retries. The caller has persisted token/target/hash and owns the source file.
func (c *Client) UploadAsset(ctx context.Context, subject Subject, source io.Reader, length int64, sha, mediaType, token string) (UploadedAsset, error) {
	if source == nil || length < 1 || length > MaxAssetBytes || !sha256Pattern.MatchString(sha) || !validAssetType(mediaType) || !ascii(token, 1, 128) {
		return UploadedAsset{}, &CallError{Code: "invalid_request"}
	}
	var framing bytes.Buffer
	writer := multipart.NewWriter(&framing)
	if _, err := writer.CreateFormFile("file", "input"); err != nil {
		return UploadedAsset{}, &CallError{Code: "invalid_request"}
	}
	headerSize := framing.Len()
	if writer.Close() != nil {
		return UploadedAsset{}, &CallError{Code: "invalid_request"}
	}
	ctx, cancel := context.WithTimeout(ctx, 90*time.Second)
	defer cancel()
	request, err := c.mediaRequest(ctx, http.MethodPost, "/gateway/v1/assets", subject)
	if err != nil {
		return UploadedAsset{}, err
	}
	request.Body = io.NopCloser(io.MultiReader(bytes.NewReader(framing.Bytes()[:headerSize]), io.LimitReader(source, length), bytes.NewReader(framing.Bytes()[headerSize:])))
	request.ContentLength = int64(framing.Len()) + length
	request.Header.Set("Content-Type", writer.FormDataContentType())
	request.Header.Set("Idempotency-Key", token)
	uploadHTTP := &http.Client{Transport: c.http.Transport, CheckRedirect: c.http.CheckRedirect}
	response, err := uploadHTTP.Do(request)
	if err != nil {
		return UploadedAsset{}, &CallError{Code: "transport_error", Uncertain: true}
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusCreated {
		return UploadedAsset{}, &CallError{Code: "http_error", HTTPStatus: response.StatusCode, Uncertain: response.StatusCode >= 500 || response.StatusCode == 408 || response.StatusCode >= 300 && response.StatusCode < 400}
	}
	data, err := io.ReadAll(io.LimitReader(response.Body, c.limit+1))
	var result UploadedAsset
	if err != nil || int64(len(data)) > c.limit || json.Unmarshal(data, &result) != nil || !identifier("asset", result.ID) || result.Object != "asset" || result.Status != "ready" || result.Bytes != length || result.SHA256 != sha || result.MediaType != mediaType || result.Width < 1 || result.Height < 1 || int64(result.Width)*int64(result.Height) > 25000000 || result.Width > 25000000 || result.Height > 25000000 || result.ExpiresAt < 1 {
		return UploadedAsset{}, &CallError{Code: "invalid_response", Uncertain: true}
	}
	return result, nil
}

func (c *Client) Asset(ctx context.Context, subject Subject, id string) (ContentMetadata, error) {
	if !identifier("asset", id) {
		return ContentMetadata{}, &CallError{Code: "invalid_identifier"}
	}
	var result ContentMetadata
	err := c.call(ctx, http.MethodGet, "/gateway/v1/assets/"+id, &subject, nil, &result)
	if err == nil && (result.ID != id || result.Bytes < 1 || result.Bytes > MaxAssetBytes || !validAssetType(result.MediaType) || !sha256Pattern.MatchString(result.SHA256) || result.ExpiresAt < 1) {
		err = &CallError{Code: "invalid_response"}
	}
	return result, err
}

func (c *Client) DeleteAsset(ctx context.Context, subject Subject, id, token string) (ContentDeletion, error) {
	if !identifier("asset", id) || !ascii(token, 1, 128) {
		return ContentDeletion{}, &CallError{Code: "invalid_request"}
	}
	return c.deleteMedia(ctx, subject, id, token, "/gateway/v1/assets/"+id)
}

func (c *Client) OpenAsset(ctx context.Context, subject Subject, id string, head bool, requestedRange string) (*Download, error) {
	if !identifier("asset", id) || !ValidMediaRange(requestedRange) {
		return nil, &CallError{Code: "invalid_request"}
	}
	file, err := c.openMedia(ctx, subject, "/gateway/v1/assets/"+id+"/content", head, requestedRange)
	tooLarge := false
	if err == nil && file.ContentRange != "" {
		parts := strings.Split(file.ContentRange, "/")
		total, parseErr := strconv.ParseInt(parts[len(parts)-1], 10, 64)
		tooLarge = parseErr != nil || total > MaxAssetBytes
	}
	if err == nil && (file.Length > MaxAssetBytes || tooLarge || !validAssetType(file.MediaType)) {
		_ = file.Body.Close()
		return nil, &CallError{Code: "invalid_response"}
	}
	return file, err
}
