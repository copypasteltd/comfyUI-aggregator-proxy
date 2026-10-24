-- Preserve unknown legacy targets. Never infer an old destination from current
-- routing configuration. Only newly reserved intents require a known target.
LOCK TABLE render_control_intents IN ACCESS EXCLUSIVE MODE;
ALTER TABLE render_control_intents ADD COLUMN target_gateway_id VARCHAR(64)
    CHECK (target_gateway_id ~ '^gateway_[a-f0-9]{32}$');

-- 247 used unnamed checks. Identify these three by their actual columns and
-- expressions, rather than relying on PostgreSQL's generated constraint names.
DO $$
DECLARE item RECORD; removed INTEGER := 0; outcome_attribute SMALLINT;
BEGIN
    SELECT attnum INTO STRICT outcome_attribute FROM pg_attribute
    WHERE attrelid='render_control_intents'::regclass AND attname='outcome';
    FOR item IN SELECT conname FROM pg_constraint
        WHERE conrelid='render_control_intents'::regclass AND contype='c' AND (
            (pg_get_constraintdef(oid) LIKE '%state%' AND pg_get_constraintdef(oid) LIKE '%dispatch_count%') OR
            (pg_get_constraintdef(oid) LIKE '%state%' AND pg_get_constraintdef(oid) LIKE '%released%' AND pg_get_constraintdef(oid) LIKE '%not_dispatched%') OR
            conkey=ARRAY[outcome_attribute]::SMALLINT[])
    LOOP
        EXECUTE format('ALTER TABLE render_control_intents DROP CONSTRAINT %I', item.conname);
        removed := removed+1;
    END LOOP;
    IF removed <> 3 THEN
        RAISE EXCEPTION 'render_control_legacy_constraints_unrecognized';
    END IF;
END $$;

ALTER TABLE render_control_intents
    ADD CONSTRAINT rc_intent_dispatch_state CHECK (
        (state='reserved' AND dispatch_count=0) OR
        (state='released' AND (dispatch_count=0 OR outcome='remote_closed')) OR
        (state IN ('dispatching','admitted','settled') AND dispatch_count>0)),
    ADD CONSTRAINT rc_intent_release_outcome CHECK (
        state<>'released' OR (outcome IN ('not_dispatched','remote_closed') AND actual_amount=0)),
    ADD CONSTRAINT rc_intent_outcome_values CHECK (
        outcome IN ('succeeded','failed','cancelled','expired','not_dispatched','remote_closed'));

CREATE TABLE render_control_closures (
    intent_id VARCHAR(64) PRIMARY KEY REFERENCES render_control_intents(id) ON DELETE RESTRICT,
    target_gateway_id VARCHAR(64) NOT NULL CHECK (target_gateway_id ~ '^gateway_[a-f0-9]{32}$'),
    closure_id VARCHAR(64) NOT NULL CHECK (closure_id ~ '^gcl_[a-f0-9]{32}$'),
    provider_request_id VARCHAR(64) NOT NULL,
    external_task_ref VARCHAR(64) NOT NULL CHECK (external_task_ref=intent_id),
    price_version VARCHAR(64) NOT NULL,
    authorization_ref VARCHAR(64) NOT NULL,
    external_tenant_ref VARCHAR(128) NOT NULL,
    external_key_ref VARCHAR(128) NOT NULL,
    proof_hash CHAR(64) NOT NULL CHECK (proof_hash ~ '^[a-f0-9]{64}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (target_gateway_id,closure_id)
);

CREATE FUNCTION rc_guard_intent_identity() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='INSERT' THEN
        IF NEW.target_gateway_id IS NULL THEN
            RAISE EXCEPTION 'render_control_target_required' USING ERRCODE='23514';
        END IF;
    ELSE
        IF ROW(NEW.id,NEW.instance,NEW.target_gateway_id,NEW.user_id,NEW.api_key_id,NEW.client_ip,
               NEW.operation,NEW.idempotency_hash,NEW.request_hash,NEW.task_type,NEW.provider_request_id,
               NEW.authorization_ref,NEW.price_version,NEW.unit_price,NEW.max_units,NEW.hold_amount)
           IS DISTINCT FROM
           ROW(OLD.id,OLD.instance,OLD.target_gateway_id,OLD.user_id,OLD.api_key_id,OLD.client_ip,
               OLD.operation,OLD.idempotency_hash,OLD.request_hash,OLD.task_type,OLD.provider_request_id,
               OLD.authorization_ref,OLD.price_version,OLD.unit_price,OLD.max_units,OLD.hold_amount) THEN
            RAISE EXCEPTION 'render_control_identity_immutable' USING ERRCODE='23514';
        END IF;
        IF NEW.dispatch_count>OLD.dispatch_count AND NEW.target_gateway_id IS NULL THEN
            RAISE EXCEPTION 'render_control_legacy_target_unknown' USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER rc_guard_intent_identity BEFORE INSERT OR UPDATE ON render_control_intents
    FOR EACH ROW EXECUTE FUNCTION rc_guard_intent_identity();

CREATE FUNCTION rc_preserve_closure() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'render_control_closure_immutable' USING ERRCODE='23514';
END $$;
CREATE TRIGGER rc_preserve_closure BEFORE UPDATE OR DELETE ON render_control_closures
    FOR EACH ROW EXECUTE FUNCTION rc_preserve_closure();

-- The final committed rows must agree even if several statements temporarily
-- insert a proof before settling funds. Both sides are checked at commit.
CREATE FUNCTION rc_check_closure_consistency() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE intent_key VARCHAR(64); intent_row render_control_intents%ROWTYPE;
        proof_row render_control_closures%ROWTYPE;
BEGIN
    IF TG_TABLE_NAME='render_control_intents' THEN intent_key:=NEW.id;
    ELSE intent_key:=NEW.intent_id;
    END IF;
    SELECT * INTO intent_row FROM render_control_intents WHERE id=intent_key;
    SELECT * INTO proof_row FROM render_control_closures WHERE intent_id=intent_key;
    IF intent_row.state='released' AND intent_row.outcome='remote_closed' THEN
        IF proof_row.intent_id IS NULL OR
           proof_row.target_gateway_id IS DISTINCT FROM intent_row.target_gateway_id OR
           proof_row.provider_request_id IS DISTINCT FROM intent_row.provider_request_id OR
           proof_row.external_task_ref IS DISTINCT FROM intent_row.id OR
           proof_row.price_version IS DISTINCT FROM intent_row.price_version OR
           proof_row.authorization_ref IS DISTINCT FROM intent_row.authorization_ref OR
           proof_row.external_tenant_ref IS DISTINCT FROM intent_row.instance||':user:'||intent_row.user_id::TEXT OR
           proof_row.external_key_ref IS DISTINCT FROM intent_row.instance||':key:'||intent_row.api_key_id::TEXT OR
           proof_row.proof_hash IS DISTINCT FROM intent_row.terminal_hash OR
           intent_row.admission_id IS NOT NULL OR intent_row.remote_job_id IS NOT NULL THEN
            RAISE EXCEPTION 'render_control_closure_mismatch' USING ERRCODE='23514';
        END IF;
    ELSIF proof_row.intent_id IS NOT NULL THEN
        RAISE EXCEPTION 'render_control_closure_requires_release' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER rc_intent_closure_consistency AFTER INSERT OR UPDATE ON render_control_intents
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION rc_check_closure_consistency();
CREATE CONSTRAINT TRIGGER rc_proof_closure_consistency AFTER INSERT ON render_control_closures
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION rc_check_closure_consistency();
