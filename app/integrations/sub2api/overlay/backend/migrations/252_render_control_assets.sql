CREATE TABLE render_control_asset_uploads (
    id VARCHAR(40) PRIMARY KEY CHECK (id ~ '^rcu_[a-f0-9]{32}$'),
    instance VARCHAR(64) NOT NULL,
    user_id BIGINT NOT NULL REFERENCES users(id),
    api_key_id BIGINT NOT NULL REFERENCES api_keys(id),
    idempotency_hash CHAR(64) NOT NULL CHECK (idempotency_hash ~ '^[a-f0-9]{64}$'),
    target_gateway_id VARCHAR(40) NOT NULL CHECK (target_gateway_id ~ '^gateway_[a-f0-9]{32}$'),
    public_model VARCHAR(128) NOT NULL,
    file_sha256 CHAR(64) NOT NULL CHECK (file_sha256 ~ '^[a-f0-9]{64}$'),
    file_bytes BIGINT NOT NULL CHECK (file_bytes BETWEEN 1 AND 20971520),
    media_type VARCHAR(32) NOT NULL CHECK (media_type IN ('image/png','image/jpeg','image/webp')),
    asset_id VARCHAR(40) CHECK (asset_id ~ '^asset_[a-f0-9]{32}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(instance, api_key_id, idempotency_hash)
);
CREATE TABLE render_control_asset_bindings (
    instance VARCHAR(64) NOT NULL,
    asset_id VARCHAR(40) NOT NULL CHECK (asset_id ~ '^asset_[a-f0-9]{32}$'),
    upload_id VARCHAR(40) NOT NULL REFERENCES render_control_asset_uploads(id),
    file_sha256 CHAR(64) NOT NULL CHECK (file_sha256 ~ '^[a-f0-9]{64}$'),
    file_bytes BIGINT NOT NULL CHECK (file_bytes BETWEEN 1 AND 20971520),
    media_type VARCHAR(32) NOT NULL CHECK (media_type IN ('image/png','image/jpeg','image/webp')),
    width INTEGER NOT NULL CHECK (width BETWEEN 1 AND 25000000),
    height INTEGER NOT NULL CHECK (height BETWEEN 1 AND 25000000),
    expires_at BIGINT NOT NULL CHECK (expires_at>0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY(instance, asset_id),
    CHECK (width::BIGINT*height::BIGINT<=25000000)
);
CREATE INDEX render_control_asset_upload ON render_control_asset_bindings(upload_id);
CREATE FUNCTION render_control_asset_upload_guard() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='INSERT' THEN
        IF NEW.asset_id IS NOT NULL OR NOT EXISTS (SELECT 1 FROM api_keys WHERE id=NEW.api_key_id AND user_id=NEW.user_id) THEN
            RAISE EXCEPTION 'render control asset owner mismatch';
        END IF;
    ELSIF (to_jsonb(NEW)-'asset_id') IS DISTINCT FROM (to_jsonb(OLD)-'asset_id')
        OR (OLD.asset_id IS NOT NULL AND NEW.asset_id IS DISTINCT FROM OLD.asset_id) THEN
        RAISE EXCEPTION 'render control asset upload is immutable';
    END IF;
    IF TG_OP='UPDATE' AND NEW.asset_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM render_control_asset_bindings WHERE instance=NEW.instance
        AND asset_id=NEW.asset_id AND upload_id=NEW.id
    ) THEN
        RAISE EXCEPTION 'render control asset publication requires matching binding';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER render_control_asset_upload_guard BEFORE INSERT OR UPDATE
    ON render_control_asset_uploads FOR EACH ROW EXECUTE FUNCTION render_control_asset_upload_guard();
CREATE FUNCTION render_control_asset_binding_guard() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='UPDATE' OR NOT EXISTS (
        SELECT 1 FROM render_control_asset_uploads WHERE id=NEW.upload_id AND instance=NEW.instance
        AND file_sha256=NEW.file_sha256 AND file_bytes=NEW.file_bytes AND media_type=NEW.media_type
    ) THEN
        RAISE EXCEPTION 'render control asset binding is immutable or invalid';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER render_control_asset_binding_guard BEFORE INSERT OR UPDATE
    ON render_control_asset_bindings FOR EACH ROW EXECUTE FUNCTION render_control_asset_binding_guard();
