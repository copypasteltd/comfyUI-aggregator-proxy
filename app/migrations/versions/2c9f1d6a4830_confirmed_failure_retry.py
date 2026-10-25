"""Permit confirmed OOM retry and fence its original physical GPU for old writers."""
from alembic import op

revision = "2c9f1d6a4830"
down_revision = "1b8e0c5f3247"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(op.f("ck_jobs_retry_metadata"), "jobs", type_="check")
    op.create_check_constraint(op.f("ck_jobs_retry_metadata"), "jobs", "(retry_count = 0 AND retry_error_code IS NULL AND retry_source_attempt_id IS NULL) OR (retry_count > 0 AND retry_error_code IS NOT NULL AND retry_error_code IN ('preparation_transport_failure','comfy_oom') AND retry_source_attempt_id IS NOT NULL)")
    op.execute("""CREATE FUNCTION enforce_retry_gpu_fence() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          PERFORM 1 FROM jobs WHERE id=NEW.job_id FOR UPDATE;
          IF EXISTS (SELECT 1 FROM attempts a WHERE a.job_id=NEW.job_id AND a.id<>NEW.id
            AND a.gpu_id=NEW.gpu_id AND a.closed_at IS NOT NULL AND a.generation_released_at IS NOT NULL
            AND a.generation_evidence #>> '{release,error_code}' = 'comfy_oom'
            AND a.generation_evidence #> '{release,execution_stop_confirmed}' = 'true'::jsonb) THEN
            RAISE EXCEPTION 'retry_failed_physical_gpu' USING ERRCODE='23514';
          END IF;
          RETURN NEW;
        END $$""")
    op.execute("CREATE TRIGGER enforce_retry_gpu_fence BEFORE INSERT OR UPDATE OF job_id,gpu_id ON attempts FOR EACH ROW EXECUTE FUNCTION enforce_retry_gpu_fence()")


def downgrade() -> None:
    op.execute("LOCK TABLE jobs, attempts IN ACCESS EXCLUSIVE MODE")
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT 1 FROM jobs WHERE retry_error_code='comfy_oom')
        OR EXISTS (SELECT 1 FROM attempts a JOIN attempts b ON a.job_id=b.job_id AND a.id<>b.id
          WHERE a.generation_evidence #>> '{release,retry_evidence,kind}'='comfy-oom-v1') THEN
        RAISE EXCEPTION 'Confirmed OOM retry facts require forward recovery'; END IF; END $$""")
    op.execute("DROP TRIGGER enforce_retry_gpu_fence ON attempts")
    op.execute("DROP FUNCTION enforce_retry_gpu_fence()")
    op.drop_constraint(op.f("ck_jobs_retry_metadata"), "jobs", type_="check")
    op.create_check_constraint(op.f("ck_jobs_retry_metadata"), "jobs", "(retry_count = 0 AND retry_error_code IS NULL AND retry_source_attempt_id IS NULL) OR (retry_count > 0 AND retry_error_code IS NOT NULL AND retry_error_code = 'preparation_transport_failure' AND retry_source_attempt_id IS NOT NULL)")
