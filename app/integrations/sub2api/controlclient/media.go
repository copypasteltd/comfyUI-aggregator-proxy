package controlclient

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"mime"
	"net/http"
	"regexp"
	"strconv"
	"strings"
	"time"
)

const MaxMediaBytes int64 = 2 << 30
const MaxInlineImageBytes int64 = 32 << 20

var sha256Pattern = regexp.MustCompile(`^[a-f0-9]{64}$`)
var rangePattern = regexp.MustCompile(`^bytes=([0-9]*)-([0-9]*)$`)
var contentRangePattern = regexp.MustCompile(`^bytes ([0-9]+)-([0-9]+)/([0-9]+)$`)

type DeliveryPolicy struct {
	Scopes []string `json:"scopes"`
}

func (c *Client) DeliveryPolicy(ctx context.Context, subject Subject) (DeliveryPolicy, error) {
	var result DeliveryPolicy
	err := c.call(ctx, http.MethodGet, "/gateway/v1/access-policy", &subject, nil, &result)
	if err == nil && (result.Scopes == nil || len(result.Scopes) > 128) {
		err = &CallError{Code: "invalid_response"}
	}
	seen := map[string]bool{}
	for _, scope := range result.Scopes {
		if !ascii(scope, 1, 64) || seen[scope] {
			return DeliveryPolicy{}, &CallError{Code: "invalid_response"}
		}
		seen[scope] = true
	}
	return result, err
}

func (p DeliveryPolicy) Allows(scopes ...string) bool {
	allowed := map[string]bool{}
	for _, scope := range p.Scopes {
		allowed[scope] = true
	}
	for _, scope := range scopes {
		if !allowed[scope] {
			return false
		}
	}
	return true
}

type ContentMetadata struct {
	ID        string `json:"id"`
	Bytes     int64  `json:"bytes"`
	MediaType string `json:"media_type"`
	SHA256    string `json:"sha256"`
	ExpiresAt int64  `json:"expires_at"`
}

// A task cannot relabel a different media family as its output.
func taskMediaMatches(task, media string) bool {
	if task == "text_to_audio" {
		return strings.HasPrefix(media, "audio/")
	}
	if task == "text_to_video" || task == "references_to_video" {
		return strings.HasPrefix(media, "video/")
	}
	return (task == "text_to_image" || task == "image_to_image" || task == "references_to_image") && strings.HasPrefix(media, "image/")
}

func allowedMedia(value string) bool {
	return value == "image/png" || value == "image/jpeg" || value == "image/webp" || value == "video/mp4" || value == "video/webm" || value == "audio/wav" || value == "audio/flac" || value == "audio/mpeg" || value == "audio/ogg"
}

func (c *Client) Content(ctx context.Context, subject Subject, id string) (ContentMetadata, error) {
	if !identifier("content", id) {
		return ContentMetadata{}, &CallError{Code: "invalid_identifier"}
	}
	var result ContentMetadata
	err := c.call(ctx, http.MethodGet, "/gateway/v1/content/"+id, &subject, nil, &result)
	if err == nil && (result.ID != id || result.Bytes < 1 || result.Bytes > MaxMediaBytes || !allowedMedia(result.MediaType) || !sha256Pattern.MatchString(result.SHA256) || result.ExpiresAt < 1) {
		err = &CallError{Code: "invalid_response"}
	}
	return result, err
}

type ContentDeletion struct {
	ID                     string `json:"id"`
	Status                 string `json:"status"`
	PhysicalDeleteComplete bool   `json:"physical_delete_complete"`
}

func (c *Client) DeleteContent(ctx context.Context, subject Subject, id, token string) (ContentDeletion, error) {
	if !identifier("content", id) || !ascii(token, 1, 128) {
		return ContentDeletion{}, &CallError{Code: "invalid_request"}
	}
	return c.deleteMedia(ctx, subject, id, token, "/gateway/v1/content/"+id)
}

func (c *Client) deleteMedia(ctx context.Context, subject Subject, id, token, path string) (ContentDeletion, error) {
	// The control-plane deletion is already idempotent by immutable object ID.
	// Its HTTP contract requires an Idempotency-Key as an explicit caller token.
	request, err := c.mediaRequest(ctx, http.MethodDelete, path, subject)
	if err != nil {
		return ContentDeletion{}, err
	}
	request.Header.Set("Idempotency-Key", token)
	response, err := c.http.Do(request)
	if err != nil {
		return ContentDeletion{}, &CallError{Code: "transport_error", Uncertain: true}
	}
	defer response.Body.Close()
	var result ContentDeletion
	if response.StatusCode != http.StatusAccepted {
		return result, &CallError{Code: "http_error", HTTPStatus: response.StatusCode, Uncertain: response.StatusCode >= 500 || response.StatusCode >= 300 && response.StatusCode < 400 || response.StatusCode == 408}
	}
	data, err := io.ReadAll(io.LimitReader(response.Body, c.limit+1))
	if err != nil || int64(len(data)) > c.limit || json.Unmarshal(data, &result) != nil || result.ID != id || result.Status != "delete_requested" {
		return ContentDeletion{}, &CallError{Code: "invalid_response", Uncertain: true}
	}
	return result, nil
}

// Download exposes only validated delivery metadata. The caller owns Body and
// must close it on every path; cancellation also terminates the underlying read.
type Download struct {
	Body         io.ReadCloser
	Status       int
	Length       int64
	MediaType    string
	ETag         string
	ContentRange string
}

func (*Download) String() string   { return "controlclient.Download{redacted}" }
func (*Download) GoString() string { return "controlclient.Download{redacted}" }

func rangeNumbers(value string) (int64, int64, bool) {
	match := rangePattern.FindStringSubmatch(value)
	if len(value) > 64 || match == nil || match[1] == "" && match[2] == "" {
		return 0, 0, false
	}
	parse := func(text string) (int64, bool) {
		if text == "" {
			return -1, true
		}
		n, err := strconv.ParseInt(text, 10, 64)
		return n, err == nil && n >= 0 && n <= MaxMediaBytes
	}
	start, a := parse(match[1])
	end, b := parse(match[2])
	return start, end, a && b && (start >= 0 && (end == -1 || end >= start) || start == -1 && end > 0)
}

func ValidMediaRange(value string) bool {
	if value == "" {
		return true
	}
	_, _, ok := rangeNumbers(value)
	return ok
}

func matchesRange(value, requested string, length int64) bool {
	match := contentRangePattern.FindStringSubmatch(value)
	if len(value) > 96 || match == nil {
		return false
	}
	parts := [3]int64{}
	for index := range parts {
		n, err := strconv.ParseInt(match[index+1], 10, 64)
		if err != nil || n < 0 || n > MaxMediaBytes {
			return false
		}
		parts[index] = n
	}
	start, end, total := parts[0], parts[1], parts[2]
	a, b, valid := rangeNumbers(requested)
	if !valid || total < 1 || start > end || end >= total || end-start+1 != length {
		return false
	}
	if a == -1 {
		a, b = max(int64(0), total-b), total-1
	} else if b == -1 || b >= total {
		b = total - 1
	}
	return start == a && end == b
}

func (c *Client) mediaRequest(ctx context.Context, method, path string, subject Subject) (*http.Request, error) {
	if subject.tenant == "" || subject.key == "" || subject.ip == "" {
		return nil, &CallError{Code: "invalid_subject"}
	}
	request, err := http.NewRequestWithContext(ctx, method, c.origin+path, nil)
	if err != nil {
		return nil, &CallError{Code: "invalid_request"}
	}
	request.Header.Set("Authorization", "Bearer "+c.secret.value)
	request.Header.Set("X-External-Tenant-Ref", subject.tenant)
	request.Header.Set("X-External-Key-Ref", subject.key)
	request.Header.Set("X-Client-IP", subject.ip)
	request.Header.Set("Accept-Encoding", "identity")
	return request, nil
}

type mediaBody struct {
	io.Reader
	body   io.ReadCloser
	cancel context.CancelFunc
}

func (b *mediaBody) Close() error {
	b.cancel()
	return b.body.Close()
}

func (c *Client) OpenContent(ctx context.Context, subject Subject, id string, head bool, requestedRange string) (*Download, error) {
	if !identifier("content", id) || !ValidMediaRange(requestedRange) {
		return nil, &CallError{Code: "invalid_request"}
	}
	return c.openMedia(ctx, subject, "/gateway/v1/content/"+id+"/download", head, requestedRange)
}

func (c *Client) openMedia(ctx context.Context, subject Subject, path string, head bool, requestedRange string) (*Download, error) {
	ctx, cancel := context.WithTimeout(ctx, 300*time.Second)
	owned := false
	defer func() {
		if !owned {
			cancel()
		}
	}()
	method := http.MethodGet
	if head {
		method = http.MethodHead
	}
	request, err := c.mediaRequest(ctx, method, path, subject)
	if err != nil {
		return nil, err
	}
	if requestedRange != "" {
		request.Header.Set("Range", requestedRange)
	}
	// Share the bounded TLS transport, but use the explicit media deadline;
	// metadata calls retain their short http.Client timeout.
	mediaHTTP := &http.Client{Transport: c.http.Transport, CheckRedirect: c.http.CheckRedirect}
	response, err := mediaHTTP.Do(request)
	if err != nil {
		return nil, &CallError{Code: "transport_error"}
	}
	defer func() {
		if !owned {
			_ = response.Body.Close()
		}
	}()
	if response.StatusCode != http.StatusOK && response.StatusCode != http.StatusPartialContent {
		return nil, &CallError{Code: "http_error", HTTPStatus: response.StatusCode}
	}
	mediaType, parameters, parseErr := mime.ParseMediaType(response.Header.Get("Content-Type"))
	etag := response.Header.Get("ETag")
	if parseErr != nil || len(parameters) != 0 || !allowedMedia(mediaType) || response.ContentLength < 1 || response.ContentLength > MaxMediaBytes || response.Uncompressed || response.Header.Get("Content-Encoding") != "" && response.Header.Get("Content-Encoding") != "identity" || len(etag) != 66 || etag[0] != '"' || etag[65] != '"' || !sha256Pattern.MatchString(etag[1:65]) {
		return nil, &CallError{Code: "invalid_response"}
	}
	contentRange := response.Header.Get("Content-Range")
	if requestedRange == "" && (response.StatusCode != 200 || contentRange != "") || requestedRange != "" && (response.StatusCode != 206 || !matchesRange(contentRange, requestedRange, response.ContentLength)) {
		return nil, &CallError{Code: "invalid_response"}
	}
	owned = true
	return &Download{Body: &mediaBody{Reader: io.LimitReader(response.Body, response.ContentLength), body: response.Body, cancel: cancel}, Status: response.StatusCode, Length: response.ContentLength, MediaType: mediaType, ETag: etag, ContentRange: contentRange}, nil
}

func (d *Download) ReadImage() ([]byte, error) {
	defer d.Body.Close()
	if d.Status != 200 || !strings.HasPrefix(d.MediaType, "image/") || d.Length < 1 || d.Length > MaxInlineImageBytes {
		return nil, &CallError{Code: "invalid_response"}
	}
	data, err := io.ReadAll(io.LimitReader(d.Body, MaxInlineImageBytes+1))
	sum := sha256.Sum256(data)
	if err != nil || int64(len(data)) != d.Length || d.ETag != `"`+hex.EncodeToString(sum[:])+`"` {
		return nil, &CallError{Code: "invalid_response"}
	}
	return data, nil
}
