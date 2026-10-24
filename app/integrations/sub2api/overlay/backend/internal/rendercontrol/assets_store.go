package rendercontrol

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"time"

	"github.com/Wei-Shaw/sub2api/internal/rendercontrol/controlclient"
)

type assetUpload struct {
	ID, GatewayID, Model, SHA256, MediaType, AssetID string
	Bytes                                            int64
}

const assetUploadColumns = `id,target_gateway_id,public_model,file_sha256,file_bytes,media_type,COALESCE(asset_id,'')`

func scanUpload(row scanner) (assetUpload, error) {
	var result assetUpload
	err := row.Scan(&result.ID, &result.GatewayID, &result.Model, &result.SHA256, &result.Bytes, &result.MediaType, &result.AssetID)
	if errors.Is(err, sql.ErrNoRows) {
		return result, ErrNotFound
	}
	if err != nil {
		return result, ErrStorage
	}
	return result, nil
}

func (s *Store) reserveAsset(ctx context.Context, subject Subject, target Target, model, token, sha, mediaType string, length int64) (assetUpload, error) {
	if _, err := subject.GatewaySubject(); err != nil || !gatewayPattern.MatchString(target.gatewayID) || !textValue(model, 1, 128) || !asciiPublic(token, 1, 128) || !shaPattern.MatchString(sha) || length < 1 || length > controlclient.MaxAssetBytes || (mediaType != "image/png" && mediaType != "image/jpeg" && mediaType != "image/webp") {
		return assetUpload{}, ErrInvalid
	}
	ctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	tx, err := begin(ctx, s.db)
	if err != nil {
		return assetUpload{}, err
	}
	defer tx.Rollback()
	if err = lockOwner(ctx, tx, subject.userID, subject.keyID, true); err != nil {
		return assetUpload{}, err
	}
	previous, err := scanUpload(tx.QueryRowContext(ctx, `SELECT `+assetUploadColumns+` FROM render_control_asset_uploads WHERE instance=$1 AND user_id=$2 AND api_key_id=$3 AND idempotency_hash=$4 FOR UPDATE`, subject.instance, subject.userID, subject.keyID, digest([]byte(token))))
	if err == nil {
		if previous.SHA256 != sha || previous.Bytes != length || previous.MediaType != mediaType || previous.Model != model {
			return assetUpload{}, ErrConflict
		}
		return previous, commit(tx)
	}
	if !errors.Is(err, ErrNotFound) {
		return assetUpload{}, err
	}
	id, err := newID("rcu")
	if err != nil {
		return assetUpload{}, ErrStorage
	}
	if _, err = tx.ExecContext(ctx, `INSERT INTO render_control_asset_uploads(id,instance,user_id,api_key_id,idempotency_hash,target_gateway_id,public_model,file_sha256,file_bytes,media_type) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)`, id, subject.instance, subject.userID, subject.keyID, digest([]byte(token)), target.gatewayID, model, sha, length, mediaType); err != nil {
		return assetUpload{}, ErrStorage
	}
	return assetUpload{ID: id, GatewayID: target.gatewayID, Model: model, SHA256: sha, Bytes: length, MediaType: mediaType}, commit(tx)
}

func (s *Store) finishAsset(ctx context.Context, subject Subject, upload assetUpload, asset controlclient.UploadedAsset) error {
	if !validID("asset", asset.ID) || asset.SHA256 != upload.SHA256 || asset.Bytes != upload.Bytes || asset.MediaType != upload.MediaType {
		return ErrInvalid
	}
	ctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	tx, err := begin(ctx, s.db)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	current, err := scanUpload(tx.QueryRowContext(ctx, `SELECT `+assetUploadColumns+` FROM render_control_asset_uploads WHERE id=$1 AND instance=$2 AND user_id=$3 AND api_key_id=$4 FOR UPDATE`, upload.ID, subject.instance, subject.userID, subject.keyID))
	if err != nil {
		return err
	}
	if current.GatewayID != upload.GatewayID || current.SHA256 != upload.SHA256 || current.AssetID != "" && current.AssetID != asset.ID {
		return ErrConflict
	}
	if _, err = tx.ExecContext(ctx, `INSERT INTO render_control_asset_bindings(instance,asset_id,upload_id,file_sha256,file_bytes,media_type,width,height,expires_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9) ON CONFLICT(instance,asset_id) DO NOTHING`, subject.instance, asset.ID, upload.ID, asset.SHA256, asset.Bytes, asset.MediaType, asset.Width, asset.Height, asset.ExpiresAt); err != nil {
		return ErrStorage
	}
	var gateway, sha string
	var owner, key int64
	if tx.QueryRowContext(ctx, `SELECT u.target_gateway_id,u.user_id,u.api_key_id,b.file_sha256 FROM render_control_asset_bindings b JOIN render_control_asset_uploads u ON u.id=b.upload_id WHERE b.instance=$1 AND b.asset_id=$2`, subject.instance, asset.ID).Scan(&gateway, &owner, &key, &sha) != nil {
		return ErrStorage
	}
	if gateway != upload.GatewayID || owner != subject.userID || key != subject.keyID || sha != upload.SHA256 {
		return ErrConflict
	}
	if _, err = tx.ExecContext(ctx, `UPDATE render_control_asset_uploads SET asset_id=$2 WHERE id=$1`, upload.ID, asset.ID); err != nil {
		return ErrStorage
	}
	return commit(tx)
}

func (s *Store) loadAsset(ctx context.Context, subject Subject, id string) (assetUpload, error) {
	if !validID("asset", id) {
		return assetUpload{}, ErrNotFound
	}
	ctx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	return scanUpload(s.db.QueryRowContext(ctx, `SELECT u.id,u.target_gateway_id,u.public_model,u.file_sha256,u.file_bytes,u.media_type,b.asset_id FROM render_control_asset_bindings b JOIN render_control_asset_uploads u ON u.id=b.upload_id WHERE b.instance=$1 AND b.asset_id=$2 AND u.user_id=$3 AND u.api_key_id=$4`, subject.instance, id, subject.userID, subject.keyID))
}

func requireAssetBindings(ctx context.Context, tx *sql.Tx, subject Subject, target Target, body []byte) error {
	var request controlclient.JobRequest
	if json.Unmarshal(body, &request) != nil {
		return ErrInvalid
	}
	ids := []string{}
	for _, field := range []string{"source_image", "mask"} {
		if item, ok := request.Input[field].(map[string]any); ok {
			id, _ := item["asset_id"].(string)
			ids = append(ids, id)
		}
	}
	if refs, ok := request.Input["references"].([]any); ok {
		for _, ref := range refs {
			item, _ := ref.(map[string]any)
			id, _ := item["asset_id"].(string)
			ids = append(ids, id)
		}
	}
	for _, id := range ids {
		var valid bool
		if !validID("asset", id) {
			return ErrInvalid
		}
		if tx.QueryRowContext(ctx, `SELECT EXISTS(SELECT 1 FROM render_control_asset_bindings b JOIN render_control_asset_uploads u ON u.id=b.upload_id WHERE b.instance=$1 AND b.asset_id=$2 AND u.user_id=$3 AND u.api_key_id=$4 AND u.target_gateway_id=$5)`, subject.instance, id, subject.userID, subject.keyID, target.gatewayID).Scan(&valid) != nil {
			return ErrStorage
		}
		if !valid {
			return ErrNotFound
		}
	}
	return nil
}
