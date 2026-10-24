package rendercontrol

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"mime"
	"mime/multipart"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"unicode/utf8"

	"github.com/Wei-Shaw/sub2api/internal/rendercontrol/controlclient"
	"github.com/gin-gonic/gin"
)

type uploadOwnership struct{}

var errUploadCapacity = errors.New("provider upload capacity")
var errUploadSize = errors.New("provider upload too large")

func uploadRequest(r *http.Request) bool {
	return r.Method == http.MethodPost && (r.URL.Path == "/v1/assets" || r.URL.Path == "/v1/images/edits" || r.URL.Path == "/v1/images/edits/async" || r.URL.Path == "/v1/videos")
}

func prepareUploadDirectory(path string) error {
	if !filepath.IsAbs(path) {
		return ErrInvalid
	}
	if os.MkdirAll(path, 0700) != nil {
		return ErrStorage
	}
	info, err := os.Lstat(path)
	if err != nil || !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
		return ErrInvalid
	}
	resolved, err := filepath.EvalSymlinks(path)
	if err != nil || !strings.EqualFold(filepath.Clean(resolved), filepath.Clean(path)) {
		return ErrInvalid
	}
	return nil
}

func takeUpload(c *gin.Context, h *PublicHandler) bool {
	if c.Request.Context().Value(uploadOwnership{}) == true {
		return true
	}
	select {
	case h.uploadSlots <- struct{}{}:
		c.Request = c.Request.WithContext(context.WithValue(c.Request.Context(), uploadOwnership{}, true))
		return true
	default:
		publicError(c, errUploadCapacity)
		return false
	}
}

func releaseUpload(c *gin.Context, h *PublicHandler, owned bool) {
	if owned {
		<-h.uploadSlots
	}
}

type uploadFile struct {
	file              *os.File
	SHA256, MediaType string
	Bytes             int64
}

func (u *uploadFile) close() {
	if u != nil && u.file != nil {
		_ = u.file.Close()
		_ = os.Remove(u.file.Name())
	}
}

func spoolFile(directory string, source io.Reader, maximum int64) (*uploadFile, error) {
	if directory == "" {
		return nil, ErrStorage
	}
	file, err := os.CreateTemp(directory, "rc-upload-*")
	if err != nil {
		return nil, ErrStorage
	}
	result := &uploadFile{file: file}
	ok := false
	defer func() {
		if !ok {
			result.close()
		}
	}()
	hash := sha256.New()
	size, err := io.CopyBuffer(io.MultiWriter(file, hash), io.LimitReader(source, maximum+1), make([]byte, 64<<10))
	var bodyLimit *http.MaxBytesError
	if size > maximum || errors.As(err, &bodyLimit) {
		return nil, errUploadSize
	}
	if err != nil || size < 1 {
		return nil, ErrInvalid
	}
	result.Bytes = size
	result.SHA256 = hex.EncodeToString(hash.Sum(nil))
	if _, err = file.Seek(0, 0); err != nil {
		return nil, ErrStorage
	}
	magic := make([]byte, 512)
	n, err := file.Read(magic)
	if err != nil && err != io.EOF {
		return nil, ErrStorage
	}
	result.MediaType = http.DetectContentType(magic[:n])
	_, err = file.Seek(0, 0)
	if err != nil {
		return nil, ErrStorage
	}
	ok = true
	return result, nil
}

func multipartReader(r *http.Request) (*multipart.Reader, error) {
	mediaType, params, err := mime.ParseMediaType(r.Header.Get("Content-Type"))
	if err != nil || mediaType != "multipart/form-data" || params["boundary"] == "" || len(params["boundary"]) > 70 || r.Header.Get("Content-Encoding") != "" && r.Header.Get("Content-Encoding") != "identity" {
		return nil, ErrInvalid
	}
	return multipart.NewReader(r.Body, params["boundary"]), nil
}

func validateSpoolUTF8(file *os.File) error {
	if _, err := file.Seek(0, 0); err != nil {
		return ErrStorage
	}
	buffer := make([]byte, 64<<10)
	carry := 0
	for {
		n, err := file.Read(buffer[carry:])
		if err != nil && err != io.EOF {
			return ErrStorage
		}
		data := buffer[:carry+n]
		end := len(data)
		if err != io.EOF && end > 0 {
			start := end - 1
			for start >= 0 && !utf8.RuneStart(data[start]) {
				start--
			}
			if start >= 0 && !utf8.FullRune(data[start:]) {
				end = start
			}
		}
		if !utf8.Valid(data[:end]) {
			return ErrInvalid
		}
		carry = copy(buffer, data[end:])
		if err == io.EOF {
			return nil
		}
	}
}

func strictSpoolObject(file *os.File) (map[string]any, error) {
	// encoding/json replaces invalid UTF-8; reject it before parsing or routing.
	// Retain at most one incomplete rune between fixed-size reads.
	if err := validateSpoolUTF8(file); err != nil {
		return nil, err
	}
	if _, err := file.Seek(0, 0); err != nil {
		return nil, ErrStorage
	}
	decoder := json.NewDecoder(file)
	decoder.UseNumber()
	if validateJSON(decoder, 0, new(int)) != nil {
		return nil, ErrInvalid
	}
	if _, err := decoder.Token(); err != io.EOF {
		return nil, ErrInvalid
	}
	if _, err := file.Seek(0, 0); err != nil {
		return nil, ErrStorage
	}
	decoder = json.NewDecoder(file)
	decoder.UseNumber()
	var result map[string]any
	if decoder.Decode(&result) != nil || result == nil {
		return nil, ErrInvalid
	}
	return result, nil
}

func routingUploadModel(file *os.File, r *http.Request) (string, error) {
	mediaType, params, err := mime.ParseMediaType(r.Header.Get("Content-Type"))
	if err != nil {
		return "", ErrInvalid
	}
	if mediaType == "application/json" {
		root, err := strictSpoolObject(file)
		if err != nil || !textValue(root["model"], 1, 128) {
			return "", ErrInvalid
		}
		return root["model"].(string), nil
	}
	if mediaType != "multipart/form-data" || params["boundary"] == "" || len(params["boundary"]) > 70 {
		return "", ErrInvalid
	}
	if _, err = file.Seek(0, 0); err != nil {
		return "", ErrStorage
	}
	reader := multipart.NewReader(file, params["boundary"])
	model := ""
	for count := 0; ; count++ {
		part, err := reader.NextRawPart()
		if err == io.EOF {
			break
		}
		if err != nil || count >= 32 {
			return "", ErrInvalid
		}
		if part.FormName() == "model" {
			if model != "" || part.FileName() != "" {
				return "", ErrInvalid
			}
			raw, err := io.ReadAll(io.LimitReader(part, 129))
			if err != nil || !textValue(string(raw), 1, 128) {
				return "", ErrInvalid
			}
			model = string(raw)
		}
		_ = part.Close()
	}
	if model == "" {
		return "", ErrInvalid
	}
	return model, nil
}

func (h *PublicHandler) ingestAsset(c *gin.Context, subject Subject, rule publicRule, file *uploadFile, token string, inline bool) (controlclient.UploadedAsset, error) {
	upload, err := h.store.reserveAsset(c.Request.Context(), subject, rule.model.Target, rule.model.Model, token, file.SHA256, file.MediaType, file.Bytes)
	if err != nil {
		return controlclient.UploadedAsset{}, err
	}
	if inline && upload.AssetID != "" {
		return controlclient.UploadedAsset{ID: upload.AssetID}, nil
	}
	client := h.clients[upload.GatewayID]
	if client == nil {
		return controlclient.UploadedAsset{}, ErrStorage
	}
	current, _ := subject.GatewaySubject()
	if _, err = file.file.Seek(0, 0); err != nil {
		return controlclient.UploadedAsset{}, ErrStorage
	}
	asset, err := client.UploadAsset(c.Request.Context(), current, file.file, file.Bytes, file.SHA256, file.MediaType, upload.ID)
	if err != nil {
		return controlclient.UploadedAsset{}, err
	}
	if err = h.store.finishAsset(c.Request.Context(), subject, upload, asset); err != nil {
		return controlclient.UploadedAsset{}, err
	}
	return asset, nil
}
