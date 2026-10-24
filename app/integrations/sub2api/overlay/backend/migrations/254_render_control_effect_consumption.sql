-- Additive frozen accounting identity and durable, bounded effect progress.
ALTER TABLE render_control_intents
    ADD COLUMN usage_account_id BIGINT REFERENCES accounts(id) ON DELETE RESTRICT,
    ADD COLUMN billing_group_id BIGINT REFERENCES groups(id) ON DELETE RESTRICT;

CREATE FUNCTION rc_guard_accounting_identity() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.usage_account_id IS DISTINCT FROM OLD.usage_account_id
       OR NEW.billing_group_id IS DISTINCT FROM OLD.billing_group_id THEN
        RAISE EXCEPTION 'render control accounting identity is immutable';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER rc_guard_accounting_identity BEFORE UPDATE ON render_control_intents
    FOR EACH ROW EXECUTE FUNCTION rc_guard_accounting_identity();

ALTER TABLE render_control_effects
    ADD COLUMN available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    ADD COLUMN last_error_code VARCHAR(32) CHECK (last_error_code IN ('effect_storage','effect_conflict','effect_mapping','effect_cipher','effect_cache')),
    ADD COLUMN local_completed_at TIMESTAMPTZ,
    ADD COLUMN cache_pass SMALLINT NOT NULL DEFAULT 0 CHECK (cache_pass IN (0,1,2));
CREATE INDEX idx_rc_effects_available ON render_control_effects(available_at,id)
    WHERE acknowledged_at IS NULL;
