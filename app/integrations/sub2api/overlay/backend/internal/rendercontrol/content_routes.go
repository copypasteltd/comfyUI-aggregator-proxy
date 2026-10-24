package rendercontrol

import (
	"context"
	"database/sql"
	"errors"
	"sort"
	"time"

	"github.com/Wei-Shaw/sub2api/internal/rendercontrol/controlclient"
)

// Route data is derived from a freshly authorized gateway collection. It only
// selects the original destination; every later read still uses its caller.
func (s *Store) bindDiscoveredContent(ctx context.Context, subject Subject, target string, items []controlclient.ListedContent) error {
	if _, err := subject.GatewaySubject(); err != nil || !gatewayPattern.MatchString(target) || len(items) > 100 {
		return ErrInvalid
	}
	ctx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	tx, err := begin(ctx, s.db)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	ids := make([]string, 0, len(items))
	for _, item := range items {
		ids = append(ids, item.ID)
	}
	if err := lockContentRoutes(ctx, tx, subject.instance, ids); err != nil {
		return err
	}
	for _, item := range items {
		if !validID("content", item.ID) || !validID("job", item.JobID) || !asciiPublic(item.ExternalTaskRef, 1, 128) || !controlclientTask(item.TaskType) || item.Model == "" {
			return ErrInvalid
		}
		var original string
		err := tx.QueryRowContext(ctx, `SELECT b.intent_id FROM render_control_content_bindings b WHERE b.instance=$1 AND b.content_id=$2`, subject.instance, item.ID).Scan(&original)
		if err == nil {
			i, loadErr := scanIntent(tx.QueryRowContext(ctx, `SELECT `+columns+` FROM render_control_intents WHERE instance=$1 AND id=$2`, subject.instance, original))
			if loadErr != nil {
				return loadErr
			}
			if i.TargetGatewayID != target || i.RemoteJobID != item.JobID || i.ID != item.ExternalTaskRef || i.Model != item.Model || i.TaskType != item.TaskType {
				return ErrConflict
			}
		} else if !errors.Is(err, sql.ErrNoRows) {
			return ErrStorage
		}
		if _, err = tx.ExecContext(ctx, `INSERT INTO render_control_content_routes(instance,content_id,target_gateway_id,remote_job_id,external_task_ref,public_model,task_type) VALUES($1,$2,$3,$4,$5,$6,$7) ON CONFLICT(instance,content_id) DO NOTHING`, subject.instance, item.ID, target, item.JobID, item.ExternalTaskRef, item.Model, item.TaskType); err != nil {
			return ErrStorage
		}
		var remote, reference, model, task, destination string
		if tx.QueryRowContext(ctx, `SELECT target_gateway_id,remote_job_id,external_task_ref,public_model,task_type FROM render_control_content_routes WHERE instance=$1 AND content_id=$2`, subject.instance, item.ID).Scan(&destination, &remote, &reference, &model, &task) != nil {
			return ErrStorage
		}
		if destination != target || remote != item.JobID || reference != item.ExternalTaskRef || model != item.Model || task != item.TaskType {
			return ErrConflict
		}
	}
	return commit(tx)
}

func lockContentRoutes(ctx context.Context, tx *sql.Tx, instance string, ids []string) error {
	sort.Strings(ids)
	for _, id := range ids {
		if !validID("content", id) {
			return ErrInvalid
		}
		if _, err := tx.ExecContext(ctx, `SELECT pg_advisory_xact_lock(hashtextextended($1,0))`, "render_control_content:"+instance+":"+id); err != nil {
			return ErrStorage
		}
	}
	return nil
}

func controlclientTask(task string) bool {
	return task == "text_to_image" || task == "image_to_image" || task == "references_to_image" || task == "text_to_video" || task == "references_to_video"
}

func verifyContentRoute(ctx context.Context, tx *sql.Tx, i Intent, contentID string) error {
	var destination, remote, reference, model, task string
	err := tx.QueryRowContext(ctx, `SELECT target_gateway_id,remote_job_id,external_task_ref,public_model,task_type FROM render_control_content_routes WHERE instance=$1 AND content_id=$2`, i.Instance, contentID).Scan(&destination, &remote, &reference, &model, &task)
	if errors.Is(err, sql.ErrNoRows) {
		return nil
	}
	if err != nil {
		return ErrStorage
	}
	if destination != i.TargetGatewayID || remote != i.RemoteJobID || reference != i.ID || model != i.Model || task != i.TaskType {
		return ErrConflict
	}
	return nil
}

func (s *Store) loadContentRoute(ctx context.Context, subject Subject, id string) (Intent, error) {
	if !validID("content", id) {
		return Intent{}, ErrNotFound
	}
	if _, err := subject.GatewaySubject(); err != nil {
		return Intent{}, err
	}
	ctx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	var original string
	err := s.db.QueryRowContext(ctx, `SELECT intent_id FROM render_control_content_bindings WHERE instance=$1 AND content_id=$2`, subject.instance, id).Scan(&original)
	if err == nil {
		// No ownership predicate here: this selects a route, not authorization.
		return scanIntent(s.db.QueryRowContext(ctx, `SELECT `+columns+` FROM render_control_intents WHERE instance=$1 AND id=$2`, subject.instance, original))
	}
	if !errors.Is(err, sql.ErrNoRows) {
		return Intent{}, ErrStorage
	}
	i := Intent{Instance: subject.instance}
	err = s.db.QueryRowContext(ctx, `SELECT target_gateway_id,remote_job_id,external_task_ref,public_model,task_type FROM render_control_content_routes WHERE instance=$1 AND content_id=$2`, subject.instance, id).Scan(&i.TargetGatewayID, &i.RemoteJobID, &i.ID, &i.Model, &i.TaskType)
	if errors.Is(err, sql.ErrNoRows) {
		return Intent{}, ErrNotFound
	}
	if err != nil {
		return Intent{}, ErrStorage
	}
	return i, nil
}
