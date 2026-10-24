-- Additive Provider ledger; run with the pinned upstream migration runner.
CREATE TABLE IF NOT EXISTS render_control_intents (
    id VARCHAR(64) PRIMARY KEY CHECK (id ~ '^rc_[a-f0-9]{32}$'),
    instance VARCHAR(64) NOT NULL,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    api_key_id BIGINT NOT NULL REFERENCES api_keys(id) ON DELETE RESTRICT,
    client_ip INET NOT NULL,
    operation VARCHAR(64) NOT NULL,
    idempotency_hash CHAR(64) NOT NULL,
    request_hash CHAR(64) NOT NULL,
    request_cipher BYTEA NOT NULL CHECK (octet_length(request_cipher) BETWEEN 29 AND 2097180),
    cipher_key_id VARCHAR(64) NOT NULL,
    task_type VARCHAR(32) NOT NULL CHECK (task_type IN ('text_to_image','image_to_image','references_to_image','text_to_video','references_to_video')),
    provider_request_id VARCHAR(64) NOT NULL UNIQUE,
    authorization_ref VARCHAR(64) NOT NULL UNIQUE,
    price_version VARCHAR(64) NOT NULL,
    unit_price NUMERIC(20,8) NOT NULL CHECK (unit_price > 0),
    max_units BIGINT NOT NULL CHECK (max_units BETWEEN 1 AND 86400),
    hold_amount NUMERIC(20,8) NOT NULL CHECK (hold_amount > 0 AND hold_amount <= 1000000),
    state VARCHAR(16) NOT NULL DEFAULT 'reserved' CHECK (state IN ('reserved','dispatching','admitted','released','settled')),
    dispatch_count INTEGER NOT NULL DEFAULT 0 CHECK (dispatch_count >= 0),
    admission_id VARCHAR(64),
    remote_job_id VARCHAR(64),
    terminal_event_id VARCHAR(64) UNIQUE,
    terminal_hash CHAR(64),
    outcome VARCHAR(16) CHECK (outcome IN ('succeeded','failed','cancelled','expired','not_dispatched')),
    actual_amount NUMERIC(20,8) CHECK (actual_amount >= 0 AND actual_amount <= hold_amount),
    image_count BIGINT NOT NULL DEFAULT 0 CHECK (image_count BETWEEN 0 AND 100),
    video_milliseconds BIGINT NOT NULL DEFAULT 0 CHECK (video_milliseconds BETWEEN 0 AND 86400000),
    ledger_ref VARCHAR(64) UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (instance, api_key_id, operation, idempotency_hash),
    CHECK (hold_amount = unit_price * max_units),
    CHECK ((state = 'reserved' AND dispatch_count = 0) OR (state = 'released' AND dispatch_count = 0) OR (state IN ('dispatching','admitted','settled') AND dispatch_count > 0)),
    CHECK ((state IN ('settled','released')) = (outcome IS NOT NULL AND actual_amount IS NOT NULL AND ledger_ref IS NOT NULL AND terminal_hash IS NOT NULL)),
    CHECK ((state = 'settled') = (terminal_event_id IS NOT NULL)),
    CHECK (state <> 'admitted' OR (admission_id IS NOT NULL AND remote_job_id IS NOT NULL)),
    CHECK (state <> 'released' OR (outcome = 'not_dispatched' AND actual_amount = 0)),
    CHECK (state = 'settled' OR (image_count = 0 AND video_milliseconds = 0)),
    CHECK (outcome = 'succeeded' OR (image_count = 0 AND video_milliseconds = 0)),
    CHECK (outcome NOT IN ('failed','cancelled','expired') OR actual_amount = 0),
    CHECK (outcome <> 'succeeded' OR (actual_amount > 0 AND
        ((task_type IN ('text_to_video','references_to_video') AND image_count = 0 AND video_milliseconds > 0) OR
         (task_type IN ('text_to_image','image_to_image','references_to_image') AND image_count > 0 AND video_milliseconds = 0)))),
    CHECK (remote_job_id IS NULL OR remote_job_id ~ '^job_[a-f0-9]{32}$'),
    CHECK (admission_id IS NULL OR admission_id ~ '^adm_[a-f0-9]{32}$'),
    CHECK (terminal_event_id IS NULL OR terminal_event_id ~ '^evt_[a-f0-9]{32}$')
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_render_control_remote_job ON render_control_intents(instance, remote_job_id) WHERE remote_job_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_render_control_admission ON render_control_intents(instance, admission_id) WHERE admission_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_render_control_active_key ON render_control_intents(api_key_id) WHERE state IN ('reserved','dispatching','admitted');
CREATE INDEX IF NOT EXISTS idx_render_control_recovery ON render_control_intents(updated_at, id) WHERE state IN ('reserved','dispatching','admitted');

CREATE TABLE IF NOT EXISTS render_control_effects (
    id BIGSERIAL PRIMARY KEY,
    intent_id VARCHAR(64) NOT NULL REFERENCES render_control_intents(id) ON DELETE RESTRICT,
    kind VARCHAR(16) NOT NULL CHECK (kind IN ('reserved','terminal')),
    user_id BIGINT NOT NULL,
    api_key_id BIGINT NOT NULL,
    actual_amount NUMERIC(20,8) NOT NULL CHECK (actual_amount >= 0),
    ledger_ref VARCHAR(64),
    lease_owner VARCHAR(64),
    lease_until TIMESTAMPTZ,
    fence BIGINT NOT NULL DEFAULT 0 CHECK (fence >= 0),
    acknowledged_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (intent_id, kind),
    CHECK ((kind = 'terminal') = (ledger_ref IS NOT NULL)),
    CHECK ((lease_owner IS NULL) = (lease_until IS NULL))
);
CREATE INDEX IF NOT EXISTS idx_render_control_effects_pending ON render_control_effects(id) WHERE acknowledged_at IS NULL;
