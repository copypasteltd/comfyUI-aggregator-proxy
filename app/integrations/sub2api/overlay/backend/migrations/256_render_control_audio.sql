-- Forward-only audio metering on the existing Provider intent and usage log.
-- Stop old Provider writers before upgrading; legacy meters remain zero.
ALTER TABLE render_control_intents
    ADD COLUMN audio_milliseconds BIGINT NOT NULL DEFAULT 0
        CHECK (audio_milliseconds BETWEEN 0 AND 600000);

ALTER TABLE render_control_intents
    DROP CONSTRAINT render_control_intents_task_type_check,
    DROP CONSTRAINT rc_success_metering;

-- The original two zero-meter guards were unnamed. Remove only the reviewed
-- image/video expressions and fail closed if their shape/count has drifted.
DO $$
DECLARE item RECORD; removed INTEGER := 0;
BEGIN
    FOR item IN SELECT conname FROM pg_constraint
        WHERE conrelid='render_control_intents'::regclass AND contype='c'
          AND pg_get_constraintdef(oid) LIKE '%image_count%'
          AND pg_get_constraintdef(oid) LIKE '%video_milliseconds%'
          AND pg_get_constraintdef(oid) NOT LIKE '%task_type%'
    LOOP
        EXECUTE format('ALTER TABLE render_control_intents DROP CONSTRAINT %I', item.conname);
        removed := removed + 1;
    END LOOP;
    IF removed <> 2 THEN RAISE EXCEPTION 'unexpected render control zero-meter constraints'; END IF;
END $$;

ALTER TABLE render_control_intents
    ADD CONSTRAINT rc_audio_task_type CHECK (task_type IN
        ('text_to_image','image_to_image','references_to_image','text_to_video','references_to_video','text_to_audio')),
    ADD CONSTRAINT rc_audio_active_zero CHECK (state='settled' OR
        (image_count=0 AND video_milliseconds=0 AND audio_milliseconds=0)),
    ADD CONSTRAINT rc_audio_failure_zero CHECK (outcome='succeeded' OR
        (image_count=0 AND video_milliseconds=0 AND audio_milliseconds=0)),
    ADD CONSTRAINT rc_success_metering CHECK (outcome<>'succeeded' OR
        (((task_type IN ('text_to_video','references_to_video') AND image_count=0 AND video_milliseconds>0 AND audio_milliseconds=0) OR
          (task_type IN ('text_to_image','image_to_image','references_to_image') AND image_count>0 AND video_milliseconds=0 AND audio_milliseconds=0) OR
          (task_type='text_to_audio' AND image_count=0 AND video_milliseconds=0 AND audio_milliseconds>0))
         AND (CASE WHEN task_type='text_to_audio' THEN ceil(audio_milliseconds::numeric/1000)
                   WHEN task_type IN ('text_to_video','references_to_video') THEN ceil(video_milliseconds::numeric/1000)
                   ELSE image_count END)<=max_units
         AND actual_amount=round(unit_price*rate_multiplier*
             (CASE WHEN task_type='text_to_audio' THEN ceil(audio_milliseconds::numeric/1000)
                   WHEN task_type IN ('text_to_video','references_to_video') THEN ceil(video_milliseconds::numeric/1000)
                   ELSE image_count END),8)));

ALTER TABLE usage_logs
    ADD COLUMN audio_count SMALLINT NOT NULL DEFAULT 0 CHECK (audio_count BETWEEN 0 AND 1),
    ADD COLUMN audio_duration_milliseconds BIGINT NOT NULL DEFAULT 0
        CHECK (audio_duration_milliseconds BETWEEN 0 AND 600000),
    ADD CONSTRAINT rc_usage_audio_consistency CHECK ((audio_count=0)=(audio_duration_milliseconds=0));
