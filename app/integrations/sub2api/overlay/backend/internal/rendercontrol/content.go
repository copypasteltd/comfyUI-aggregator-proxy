package rendercontrol

import (
	"context"
	"database/sql"
	"errors"
	"io"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/Wei-Shaw/sub2api/internal/rendercontrol/controlclient"
	"github.com/gin-gonic/gin"
)

var errDownloadCapacity = errors.New("provider download capacity")

type releasedMediaBody struct {
	io.ReadCloser
	once    sync.Once
	release func()
}

func (b *releasedMediaBody) Close() error {
	err := b.ReadCloser.Close()
	b.once.Do(b.release)
	return err
}

func (h *PublicHandler) openMedia(ctx context.Context, subject Subject, i Intent, id string, head bool, requestedRange string) (*controlclient.Download, error) {
	select {
	case h.mediaSlots <- struct{}{}:
	default:
		return nil, errDownloadCapacity
	}
	client := h.clients[i.TargetGatewayID]
	if client == nil {
		<-h.mediaSlots
		return nil, ErrStorage
	}
	current, _ := subject.GatewaySubject()
	file, err := client.OpenContent(ctx, current, id, head, requestedRange)
	if err != nil {
		<-h.mediaSlots
		return nil, err
	}
	file.Body = &releasedMediaBody{ReadCloser: file.Body, release: func() { <-h.mediaSlots }}
	return file, nil
}

// bindContent stores routing derived only from an authorized, validated Job.
// It does not grant read authority or change the generation/money state.
func (s *Store) bindContent(ctx context.Context, subject Subject, id string, artifacts []controlclient.Artifact) error {
	if len(artifacts) == 0 {
		return nil
	}
	if len(artifacts) > 100 {
		return ErrInvalid
	}
	ctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	tx, err := begin(ctx, s.db)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	i, err := scanIntent(tx.QueryRowContext(ctx, `SELECT `+columns+` FROM render_control_intents WHERE id=$1 AND instance=$2 AND user_id=$3 AND api_key_id=$4`, id, subject.instance, subject.userID, subject.keyID))
	if err != nil {
		return err
	}
	if i.RemoteJobID == "" {
		return ErrConflict
	}
	ids := make([]string, 0, len(artifacts))
	for _, artifact := range artifacts {
		ids = append(ids, artifact.ID)
	}
	if err := lockContentRoutes(ctx, tx, subject.instance, ids); err != nil {
		return err
	}
	for _, artifact := range artifacts {
		if !validID("content", artifact.ID) {
			return ErrInvalid
		}
		if err := verifyContentRoute(ctx, tx, i, artifact.ID); err != nil {
			return err
		}
		if _, err := tx.ExecContext(ctx, `INSERT INTO render_control_content_bindings(instance,content_id,intent_id) VALUES ($1,$2,$3) ON CONFLICT(instance,content_id) DO NOTHING`, subject.instance, artifact.ID, i.ID); err != nil {
			return ErrStorage
		}
		var owner string
		if tx.QueryRowContext(ctx, `SELECT intent_id FROM render_control_content_bindings WHERE instance=$1 AND content_id=$2`, subject.instance, artifact.ID).Scan(&owner) != nil {
			return ErrStorage
		}
		if owner != i.ID {
			return ErrConflict
		}
	}
	return commit(tx)
}

func (s *Store) loadContent(ctx context.Context, subject Subject, id string) (Intent, error) {
	if !validID("content", id) {
		return Intent{}, ErrNotFound
	}
	if _, err := subject.GatewaySubject(); err != nil {
		return Intent{}, err
	}
	ctx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	var intent string
	err := s.db.QueryRowContext(ctx, `SELECT b.intent_id FROM render_control_content_bindings b JOIN render_control_intents i ON i.id=b.intent_id WHERE b.instance=$1 AND b.content_id=$2 AND i.instance=$1 AND i.user_id=$3 AND i.api_key_id=$4`, subject.instance, id, subject.userID, subject.keyID).Scan(&intent)
	if errors.Is(err, sql.ErrNoRows) {
		return Intent{}, ErrNotFound
	}
	if err != nil {
		return Intent{}, ErrStorage
	}
	return s.Load(ctx, subject, intent)
}

func (h *PublicHandler) contentOwner(c *gin.Context, ownerOnly bool) (Subject, Intent, error) {
	subject, key, err := h.identity(c)
	if err != nil {
		return Subject{}, Intent{}, err
	}
	var i Intent
	if ownerOnly {
		i, err = h.store.loadContent(c.Request.Context(), subject, c.Param("content"))
	} else {
		i, err = h.store.loadContentRoute(c.Request.Context(), subject, c.Param("content"))
	}
	if err != nil {
		return Subject{}, Intent{}, err
	}
	if _, err = h.rule(key, i.Model, i.TaskType); err != nil {
		return Subject{}, Intent{}, err
	}
	return subject, i, nil
}

func (h *PublicHandler) contentMetadata(c *gin.Context) {
	subject, i, err := h.contentOwner(c, false)
	if err != nil {
		publicError(c, err)
		return
	}
	client := h.clients[i.TargetGatewayID]
	if client == nil {
		publicError(c, ErrStorage)
		return
	}
	current, _ := subject.GatewaySubject()
	result, err := client.Content(c.Request.Context(), current, c.Param("content"))
	if err != nil {
		publicError(c, err)
		return
	}
	c.JSON(http.StatusOK, result)
}

func (h *PublicHandler) contentDelete(c *gin.Context) {
	subject, i, err := h.contentOwner(c, true)
	if err != nil {
		publicError(c, err)
		return
	}
	token, err := publicIdempotency(c)
	if err != nil {
		publicError(c, err)
		return
	}
	client := h.clients[i.TargetGatewayID]
	if client == nil {
		publicError(c, ErrStorage)
		return
	}
	current, _ := subject.GatewaySubject()
	result, err := client.DeleteContent(c.Request.Context(), current, c.Param("content"), token)
	if err != nil {
		publicError(c, err)
		return
	}
	c.JSON(http.StatusAccepted, result)
}

func (h *PublicHandler) contentDownload(c *gin.Context) {
	subject, i, err := h.contentOwner(c, false)
	if err != nil {
		publicError(c, err)
		return
	}
	h.download(c, subject, i, c.Param("content"))
}

func (h *PublicHandler) download(c *gin.Context, subject Subject, i Intent, contentID string) {
	requestedRange := c.Request.Header.Values("Range")
	if len(requestedRange) > 1 || len(requestedRange) == 1 && !controlclient.ValidMediaRange(requestedRange[0]) {
		c.AbortWithStatusJSON(416, gin.H{"error": gin.H{"type": "invalid_request_error", "code": "invalid_range", "message": "Use a single bytes range"}})
		return
	}
	result, err := h.openMedia(c.Request.Context(), subject, i, contentID, c.Request.Method == http.MethodHead, c.GetHeader("Range"))
	if err != nil {
		publicError(c, err)
		return
	}
	defer result.Body.Close()
	if strings.HasSuffix(i.TaskType, "video") != strings.HasPrefix(result.MediaType, "video/") {
		publicError(c, ErrConflict)
		return
	}
	c.Header("Cache-Control", "private, no-store")
	c.Header("Content-Type", result.MediaType)
	c.Header("Content-Length", strconv.FormatInt(result.Length, 10))
	c.Header("Accept-Ranges", "bytes")
	c.Header("ETag", result.ETag)
	c.Header("Content-Disposition", `attachment; filename="`+contentID+`"`)
	if result.ContentRange != "" {
		c.Header("Content-Range", result.ContentRange)
	}
	c.Status(result.Status)
	if c.Request.Method != http.MethodHead {
		c.Writer.WriteHeaderNow()
		// A streaming failure closes the response. A second JSON success/error
		// body must never be appended to a partially delivered media object.
		if _, err := io.CopyBuffer(c.Writer, result.Body, make([]byte, 64<<10)); err != nil {
			panic(http.ErrAbortHandler)
		}
	}
}
