CREATE TABLE render_control_content_bindings (
    instance VARCHAR(64) NOT NULL,
    content_id VARCHAR(40) NOT NULL CHECK (content_id ~ '^content_[a-f0-9]{32}$'),
    intent_id VARCHAR(40) NOT NULL REFERENCES render_control_intents(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (instance, content_id)
);
CREATE INDEX render_control_content_intent ON render_control_content_bindings(intent_id);
CREATE FUNCTION render_control_content_binding_guard() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'UPDATE' OR NOT EXISTS (
        SELECT 1 FROM render_control_intents WHERE id=NEW.intent_id AND instance=NEW.instance AND remote_job_id IS NOT NULL
    ) THEN
        RAISE EXCEPTION 'render control content binding is immutable or invalid';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER render_control_content_binding_guard BEFORE INSERT OR UPDATE
    ON render_control_content_bindings FOR EACH ROW EXECUTE FUNCTION render_control_content_binding_guard();
