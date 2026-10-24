ALTER TABLE render_control_intents ADD COLUMN public_model VARCHAR(128)
    CHECK (public_model IS NULL OR length(public_model) BETWEEN 1 AND 128);
ALTER TABLE render_control_intents ADD COLUMN client_request_hash CHAR(64)
    CHECK (client_request_hash IS NULL OR client_request_hash ~ '^[a-f0-9]{64}$');
CREATE FUNCTION render_control_public_model_immutable() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.public_model IS DISTINCT FROM OLD.public_model OR NEW.client_request_hash IS DISTINCT FROM OLD.client_request_hash THEN
        RAISE EXCEPTION 'render control public model is immutable';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER render_control_public_model_immutable
    BEFORE UPDATE ON render_control_intents FOR EACH ROW EXECUTE FUNCTION render_control_public_model_immutable();
ALTER TABLE render_control_recovery
    ADD COLUMN cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN cancel_client_ip INET,
    ADD CONSTRAINT render_control_cancel_identity CHECK (cancel_requested = (cancel_client_ip IS NOT NULL));
