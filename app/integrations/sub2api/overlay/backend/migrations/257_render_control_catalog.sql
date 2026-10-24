-- Forward-only Provider catalog persistence. Back up and drain old writers.
CREATE TABLE render_control_catalogs (
    instance VARCHAR(64) NOT NULL CHECK (instance ~ '^[a-z0-9][a-z0-9_-]{1,63}$'),
    target_gateway_id VARCHAR(40) NOT NULL CHECK (target_gateway_id ~ '^gateway_[a-f0-9]{32}$'),
    confirmed_revision INTEGER CHECK (confirmed_revision BETWEEN 1 AND 2147483647),
    PRIMARY KEY (instance,target_gateway_id)
);
CREATE TABLE render_control_catalog_releases (
    instance VARCHAR(64) NOT NULL,
    target_gateway_id VARCHAR(40) NOT NULL,
    revision INTEGER NOT NULL CHECK (revision BETWEEN 1 AND 2147483647),
    digest CHAR(64) NOT NULL CHECK (digest ~ '^[a-f0-9]{64}$'),
    configuration_json TEXT NOT NULL,
    release_json TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (instance,target_gateway_id,revision),
    UNIQUE (instance,target_gateway_id,revision,digest),
    FOREIGN KEY (instance,target_gateway_id) REFERENCES render_control_catalogs(instance,target_gateway_id),
    CHECK (octet_length(configuration_json) BETWEEN 2 AND 524288
        AND digest=encode(sha256(convert_to(configuration_json,'UTF8')),'hex')),
    CHECK (octet_length(release_json) BETWEEN 2 AND 1048576)
);
ALTER TABLE render_control_catalogs ADD CONSTRAINT rc_catalog_confirmed
    FOREIGN KEY (instance,target_gateway_id,confirmed_revision)
    REFERENCES render_control_catalog_releases(instance,target_gateway_id,revision);
CREATE TABLE render_control_model_owners (
    instance VARCHAR(64) NOT NULL,
    model VARCHAR(100) NOT NULL CHECK (model ~ '^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,99}$'),
    target_gateway_id VARCHAR(40) NOT NULL,
    read_revision INTEGER NOT NULL DEFAULT 0 CHECK (read_revision BETWEEN 0 AND 2147483647),
    task_type VARCHAR(32),
    group_ids BIGINT[] NOT NULL DEFAULT '{}' CHECK (cardinality(group_ids) <= 1024
        AND array_position(group_ids,NULL) IS NULL AND 0 < ALL(group_ids)),
    CHECK ((task_type IS NULL AND cardinality(group_ids)=0) OR
        (task_type IS NOT NULL AND task_type IN ('text_to_image','image_to_image','references_to_image','text_to_video','references_to_video','text_to_audio') AND cardinality(group_ids)>0)),
    PRIMARY KEY (instance,model),
    FOREIGN KEY (instance,target_gateway_id) REFERENCES render_control_catalogs(instance,target_gateway_id)
);
ALTER TABLE render_control_intents
    ADD COLUMN catalog_revision INTEGER,
    ADD COLUMN catalog_digest CHAR(64),
    ADD CONSTRAINT rc_intent_catalog_complete CHECK ((catalog_revision IS NULL)=(catalog_digest IS NULL)),
    ADD CONSTRAINT rc_intent_catalog FOREIGN KEY (instance,target_gateway_id,catalog_revision,catalog_digest)
        REFERENCES render_control_catalog_releases(instance,target_gateway_id,revision,digest);

CREATE FUNCTION preserve_render_control_catalog() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='DELETE' OR TG_TABLE_NAME='render_control_catalog_releases' THEN
        RAISE EXCEPTION 'immutable Provider catalog fact';
    ELSIF TG_TABLE_NAME='render_control_model_owners' THEN
        IF (NEW.instance,NEW.model,NEW.target_gateway_id) IS DISTINCT FROM (OLD.instance,OLD.model,OLD.target_gateway_id)
            OR NEW.read_revision < OLD.read_revision
            OR OLD.read_revision>0 AND NEW.read_revision=OLD.read_revision
                AND (NEW.task_type,NEW.group_ids) IS DISTINCT FROM (OLD.task_type,OLD.group_ids) THEN
            RAISE EXCEPTION 'immutable Provider model ownership';
        END IF;
    ELSIF TG_TABLE_NAME='render_control_catalogs' THEN
        IF (NEW.instance,NEW.target_gateway_id) IS DISTINCT FROM (OLD.instance,OLD.target_gateway_id)
            OR OLD.confirmed_revision IS NOT NULL AND (NEW.confirmed_revision IS NULL OR NEW.confirmed_revision < OLD.confirmed_revision) THEN
            RAISE EXCEPTION 'Provider catalog cannot move backwards';
        END IF;
    ELSIF (NEW.catalog_revision,NEW.catalog_digest) IS DISTINCT FROM (OLD.catalog_revision,OLD.catalog_digest) THEN
        RAISE EXCEPTION 'immutable Provider intent catalog';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER rc_catalog_release_immutable BEFORE UPDATE OR DELETE ON render_control_catalog_releases
    FOR EACH ROW EXECUTE FUNCTION preserve_render_control_catalog();
CREATE TRIGGER rc_catalog_owner_immutable BEFORE UPDATE OR DELETE ON render_control_model_owners
    FOR EACH ROW EXECUTE FUNCTION preserve_render_control_catalog();
CREATE TRIGGER rc_catalog_root_monotonic BEFORE UPDATE OR DELETE ON render_control_catalogs
    FOR EACH ROW EXECUTE FUNCTION preserve_render_control_catalog();
CREATE TRIGGER rc_intent_catalog_immutable BEFORE UPDATE ON render_control_intents
    FOR EACH ROW EXECUTE FUNCTION preserve_render_control_catalog();
