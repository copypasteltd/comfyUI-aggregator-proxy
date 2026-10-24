CREATE TABLE render_control_recovery (
    intent_id VARCHAR(64) PRIMARY KEY REFERENCES render_control_intents(id) ON DELETE RESTRICT,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    lease_owner VARCHAR(64),
    lease_until TIMESTAMPTZ,
    fence BIGINT NOT NULL DEFAULT 0 CHECK (fence >= 0),
    failures INTEGER NOT NULL DEFAULT 0 CHECK (failures BETWEEN 0 AND 31),
    close_requested BOOLEAN NOT NULL DEFAULT FALSE,
    last_error_code VARCHAR(64) NOT NULL DEFAULT '' CHECK (last_error_code='' OR last_error_code ~ '^[a-z0-9_]{1,64}$'),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK ((lease_owner IS NULL) = (lease_until IS NULL))
);
INSERT INTO render_control_recovery(intent_id)
    SELECT id FROM render_control_intents WHERE state IN ('reserved','dispatching','admitted');
CREATE INDEX idx_render_control_recovery_due ON render_control_recovery(next_attempt_at,intent_id);
CREATE INDEX idx_render_control_target_active ON render_control_intents(target_gateway_id,instance,id)
    WHERE state IN ('reserved','dispatching','admitted');
