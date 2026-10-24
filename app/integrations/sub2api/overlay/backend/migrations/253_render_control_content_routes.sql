CREATE TABLE render_control_content_routes (
    instance VARCHAR(64) NOT NULL CHECK (instance ~ '^[a-z0-9][a-z0-9_-]{1,63}$'),
    content_id VARCHAR(40) NOT NULL CHECK (content_id ~ '^content_[a-f0-9]{32}$'),
    target_gateway_id VARCHAR(40) NOT NULL CHECK (target_gateway_id ~ '^gateway_[a-f0-9]{32}$'),
    remote_job_id VARCHAR(40) NOT NULL CHECK (remote_job_id ~ '^job_[a-f0-9]{32}$'),
    external_task_ref VARCHAR(128) NOT NULL CHECK (external_task_ref ~ '^[!-~]{1,128}$'),
    public_model VARCHAR(128) NOT NULL CHECK (public_model ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'),
    task_type VARCHAR(32) NOT NULL CHECK (task_type IN ('text_to_image','image_to_image','references_to_image','text_to_video','references_to_video')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (instance, content_id)
);
CREATE FUNCTION render_control_content_route_guard() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='UPDATE' THEN
        RAISE EXCEPTION 'render control content route is immutable';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended('render_control_content:' || NEW.instance || ':' || NEW.content_id,0));
    IF EXISTS (
        SELECT 1 FROM render_control_content_bindings b JOIN render_control_intents i ON i.id=b.intent_id
        WHERE b.instance=NEW.instance AND b.content_id=NEW.content_id
          AND (i.target_gateway_id,i.remote_job_id,i.id,i.public_model,i.task_type)
              IS DISTINCT FROM (NEW.target_gateway_id,NEW.remote_job_id,NEW.external_task_ref,NEW.public_model,NEW.task_type)
    ) THEN
        RAISE EXCEPTION 'render control content route conflicts with original intent';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER render_control_content_route_guard BEFORE INSERT OR UPDATE
    ON render_control_content_routes FOR EACH ROW EXECUTE FUNCTION render_control_content_route_guard();
CREATE FUNCTION render_control_content_destination_guard() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('render_control_content:' || NEW.instance || ':' || NEW.content_id,0));
    IF EXISTS (
        SELECT 1 FROM render_control_content_routes r JOIN render_control_intents i ON i.id=NEW.intent_id
        WHERE r.instance=NEW.instance AND r.content_id=NEW.content_id
          AND (i.target_gateway_id,i.remote_job_id,i.id,i.public_model,i.task_type)
              IS DISTINCT FROM (r.target_gateway_id,r.remote_job_id,r.external_task_ref,r.public_model,r.task_type)
    ) THEN
        RAISE EXCEPTION 'render control content binding conflicts with original route';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER render_control_content_destination_guard BEFORE INSERT
    ON render_control_content_bindings FOR EACH ROW EXECUTE FUNCTION render_control_content_destination_guard();
CREATE INDEX render_control_intents_owner_cursor
    ON render_control_intents(instance,user_id,api_key_id,created_at,id);
