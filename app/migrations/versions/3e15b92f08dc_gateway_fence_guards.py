"""Enforce durable gateway request fences for legacy and direct writers.

Revision ID: 3e15b92f08dc
Revises: 6b7d9e51c420
"""

import sqlalchemy as sa
from alembic import op

revision = "3e15b92f08dc"
down_revision = "6b7d9e51c420"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Block writers during backfill and trigger installation. This also closes
    # the gap for databases that previously applied the table-only revision.
    op.execute(sa.text("LOCK TABLE gateway_request_fences, gateway_admissions IN ACCESS EXCLUSIVE MODE"))
    op.execute(sa.text("""
        INSERT INTO gateway_request_fences
          (id, created_at, gateway_id, provider_request_id, external_task_ref,
           price_version, authorization_ref, external_tenant_ref, external_key_ref)
        SELECT a.id, a.created_at, a.gateway_id, a.provider_request_id,
               a.external_task_ref, a.price_version, a.authorization_ref,
               b.external_tenant_ref, b.external_key_ref
        FROM gateway_admissions a JOIN external_identity_bindings b ON b.id=a.binding_id
        ON CONFLICT DO NOTHING
    """))
    op.execute(sa.text("""
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM gateway_admissions a
            JOIN external_identity_bindings b ON b.id=a.binding_id
            LEFT JOIN gateway_request_fences f
              ON f.gateway_id=a.gateway_id AND f.provider_request_id=a.provider_request_id
            WHERE f.id IS NULL OR f.closed_at IS NOT NULL
              OR f.external_task_ref<>a.external_task_ref OR f.price_version<>a.price_version
              OR f.authorization_ref<>a.authorization_ref
              OR f.external_tenant_ref<>b.external_tenant_ref OR f.external_key_ref<>b.external_key_ref
          ) THEN
            RAISE EXCEPTION 'gateway_request_backfill_conflict';
          END IF;
        END $$
    """))
    op.execute(sa.text("""
        CREATE FUNCTION enforce_gateway_admission_fence() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE f RECORD;
        BEGIN
          IF TG_OP='UPDATE' THEN
            IF ROW(NEW.id,NEW.gateway_id,NEW.binding_id,NEW.tenant_id,NEW.project_id,NEW.key_id,
                   NEW.job_id,NEW.provider_request_id,NEW.external_task_ref,NEW.intent_digest,
                   NEW.price_version,NEW.authorization_ref,NEW.key_revision,NEW.binding_revision)
               IS DISTINCT FROM
               ROW(OLD.id,OLD.gateway_id,OLD.binding_id,OLD.tenant_id,OLD.project_id,OLD.key_id,
                   OLD.job_id,OLD.provider_request_id,OLD.external_task_ref,OLD.intent_digest,
                   OLD.price_version,OLD.authorization_ref,OLD.key_revision,OLD.binding_revision) THEN
              RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='gateway_admission_identity_immutable';
            END IF;
            RETURN NEW;
          END IF;
          SELECT * INTO f FROM gateway_request_fences
            WHERE gateway_id=NEW.gateway_id AND provider_request_id=NEW.provider_request_id
            FOR NO KEY UPDATE;
          IF NOT FOUND THEN
            RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='gateway_request_fence_required';
          END IF;
          IF f.closed_at IS NOT NULL OR f.external_task_ref<>NEW.external_task_ref
             OR f.price_version<>NEW.price_version OR f.authorization_ref<>NEW.authorization_ref
             OR NOT EXISTS (SELECT 1 FROM external_identity_bindings b
               WHERE b.id=NEW.binding_id AND b.gateway_id=NEW.gateway_id
                 AND b.external_tenant_ref=f.external_tenant_ref AND b.external_key_ref=f.external_key_ref) THEN
            RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='gateway_request_fence_required';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER enforce_gateway_admission_fence
          BEFORE INSERT OR UPDATE ON gateway_admissions
          FOR EACH ROW EXECUTE FUNCTION enforce_gateway_admission_fence();
        CREATE FUNCTION preserve_gateway_request_fence() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP='DELETE' THEN
            RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='gateway_request_fence_immutable';
          END IF;
          IF ROW(NEW.id,NEW.gateway_id,NEW.provider_request_id,NEW.external_task_ref,
                 NEW.price_version,NEW.authorization_ref,NEW.external_tenant_ref,NEW.external_key_ref)
             IS DISTINCT FROM
             ROW(OLD.id,OLD.gateway_id,OLD.provider_request_id,OLD.external_task_ref,
                 OLD.price_version,OLD.authorization_ref,OLD.external_tenant_ref,OLD.external_key_ref)
             OR (OLD.closed_at IS NOT NULL AND NEW.closed_at IS DISTINCT FROM OLD.closed_at) THEN
            RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='gateway_request_fence_immutable';
          END IF;
          IF NEW.closed_at IS NOT NULL AND EXISTS (SELECT 1 FROM gateway_admissions a
              WHERE a.gateway_id=NEW.gateway_id AND a.provider_request_id=NEW.provider_request_id) THEN
            RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='gateway_request_already_admitted';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER preserve_gateway_request_fence
          BEFORE UPDATE OR DELETE ON gateway_request_fences
          FOR EACH ROW EXECUTE FUNCTION preserve_gateway_request_fence();
    """))


def downgrade() -> None:
    op.execute(sa.text("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM gateway_request_fences WHERE closed_at IS NOT NULL) THEN
            RAISE EXCEPTION 'durable request closures prohibit downgrade';
          END IF;
        END $$;
        DROP TRIGGER preserve_gateway_request_fence ON gateway_request_fences;
        DROP FUNCTION preserve_gateway_request_fence();
        DROP TRIGGER enforce_gateway_admission_fence ON gateway_admissions;
        DROP FUNCTION enforce_gateway_admission_fence();
    """))
