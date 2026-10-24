-- Forward-only pricing snapshot and subscription reservations. No old migration
-- or historical balance is rewritten. Pause old Provider writers before upgrade.
ALTER TABLE render_control_intents
    ADD COLUMN rate_multiplier NUMERIC(10,4) NOT NULL DEFAULT 1 CHECK (rate_multiplier >= 0),
    ADD COLUMN rate_source VARCHAR(16) NOT NULL DEFAULT 'legacy' CHECK (rate_source IN ('legacy','group','user','image','video')),
    ADD COLUMN billing_type SMALLINT NOT NULL DEFAULT 0 CHECK (billing_type IN (0,1)),
    ADD COLUMN subscription_id BIGINT REFERENCES user_subscriptions(id) ON DELETE RESTRICT,
    ADD CONSTRAINT rc_billing_subscription CHECK ((billing_type=1) = (subscription_id IS NOT NULL));

ALTER TABLE render_control_intents DROP CONSTRAINT render_control_intents_hold_amount_check;
-- The first migration used unnamed table checks. Discover only the two reviewed
-- expressions, and fail migration rather than silently discarding extra checks.
DO $$
DECLARE item RECORD; removed INTEGER := 0;
BEGIN
    FOR item IN SELECT conname FROM pg_constraint
        WHERE conrelid='render_control_intents'::regclass AND contype='c'
          AND ((pg_get_constraintdef(oid) LIKE '%hold_amount = %' AND pg_get_constraintdef(oid) LIKE '%unit_price *%')
            OR (pg_get_constraintdef(oid) LIKE '%succeeded%' AND pg_get_constraintdef(oid) LIKE '%actual_amount > %'))
    LOOP
        EXECUTE format('ALTER TABLE render_control_intents DROP CONSTRAINT %I', item.conname);
        removed := removed + 1;
    END LOOP;
    IF removed <> 2 THEN RAISE EXCEPTION 'unexpected render control pricing constraints'; END IF;
END $$;

ALTER TABLE render_control_intents
    ADD CONSTRAINT rc_hold_bounds CHECK (hold_amount >= 0 AND hold_amount <= 1000000 AND unit_price*max_units <= 1000000),
    ADD CONSTRAINT rc_hold_price CHECK (hold_amount = round(unit_price*max_units*rate_multiplier,8)),
    ADD CONSTRAINT rc_success_metering CHECK (outcome <> 'succeeded' OR
        (((task_type IN ('text_to_video','references_to_video') AND image_count=0 AND video_milliseconds>0) OR
          (task_type IN ('text_to_image','image_to_image','references_to_image') AND image_count>0 AND video_milliseconds=0))
         AND (CASE WHEN task_type IN ('text_to_video','references_to_video') THEN ceil(video_milliseconds::numeric/1000) ELSE image_count END)<=max_units
         AND actual_amount=round(unit_price*rate_multiplier*(CASE WHEN task_type IN ('text_to_video','references_to_video')
             THEN ceil(video_milliseconds::numeric/1000) ELSE image_count END),8)));

CREATE OR REPLACE FUNCTION rc_guard_accounting_identity() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.usage_account_id IS DISTINCT FROM OLD.usage_account_id
       OR NEW.billing_group_id IS DISTINCT FROM OLD.billing_group_id
       OR NEW.unit_price IS DISTINCT FROM OLD.unit_price
       OR NEW.max_units IS DISTINCT FROM OLD.max_units
       OR NEW.price_version IS DISTINCT FROM OLD.price_version
       OR NEW.rate_multiplier IS DISTINCT FROM OLD.rate_multiplier
       OR NEW.rate_source IS DISTINCT FROM OLD.rate_source
       OR NEW.billing_type IS DISTINCT FROM OLD.billing_type
       OR NEW.subscription_id IS DISTINCT FROM OLD.subscription_id THEN
        RAISE EXCEPTION 'render control accounting identity is immutable' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE INDEX idx_rc_subscription_pending ON render_control_intents(subscription_id)
    WHERE subscription_id IS NOT NULL AND state IN ('reserved','dispatching','admitted');
