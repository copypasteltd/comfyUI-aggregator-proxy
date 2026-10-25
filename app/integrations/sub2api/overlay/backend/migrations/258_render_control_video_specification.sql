-- Forward-only video response metadata. Back up and drain old Provider writers.
-- Existing ciphertext is never decrypted or guessed from the current catalog.
ALTER TABLE render_control_intents
    ADD COLUMN requested_video_milliseconds BIGINT,
    ADD COLUMN video_size VARCHAR(11),
    ADD CONSTRAINT rc_intent_video_specification CHECK (
        (requested_video_milliseconds IS NULL AND video_size IS NULL) OR
        (task_type IN ('text_to_video','references_to_video')
            AND requested_video_milliseconds IS NOT NULL
            AND requested_video_milliseconds BETWEEN 1 AND 86400000
            AND (video_size IS NULL OR video_size ~ '^[1-9][0-9]{1,4}x[1-9][0-9]{1,4}$'))
    );

CREATE FUNCTION preserve_render_control_video_specification() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (NEW.requested_video_milliseconds,NEW.video_size)
        IS DISTINCT FROM (OLD.requested_video_milliseconds,OLD.video_size) THEN
        RAISE EXCEPTION 'immutable Provider video specification';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER rc_intent_video_specification_immutable BEFORE UPDATE ON render_control_intents
    FOR EACH ROW EXECUTE FUNCTION preserve_render_control_video_specification();
