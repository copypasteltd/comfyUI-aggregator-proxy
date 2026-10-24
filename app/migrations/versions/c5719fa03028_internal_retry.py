"""Persist evidence-backed preparation retries without duplicating admission."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c5719fa03028"
down_revision = "b38a4fc10297"
branch_labels = None
depends_on = None

CHECKS = {
    "retry_count": "retry_count BETWEEN 0 AND 2",
    "retry_metadata": "(retry_count = 0 AND retry_error_code IS NULL AND retry_source_attempt_id IS NULL) OR (retry_count > 0 AND retry_error_code IS NOT NULL AND retry_error_code = 'preparation_transport_failure' AND retry_source_attempt_id IS NOT NULL)",
    "retry_schedule": "(state = 'retry_wait' AND retry_count > 0 AND retry_after IS NOT NULL AND current_attempt_id IS NULL) OR (state <> 'retry_wait' AND retry_after IS NULL)",
}


def upgrade() -> None:
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM jobs WHERE state='retry_wait') THEN RAISE EXCEPTION 'Legacy retry_wait requires evidence review before migration'; END IF; END $$")
    op.add_column("jobs", sa.Column("retry_count", sa.Integer(), nullable=False, server_default=sa.text("0")))
    op.add_column("jobs", sa.Column("retry_after", sa.DateTime(timezone=True), nullable=True))
    op.add_column("jobs", sa.Column("retry_error_code", sa.String(32), nullable=True))
    op.add_column("jobs", sa.Column("retry_source_attempt_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_jobs_retry_source_owner", "jobs", "attempts", ["id", "retry_source_attempt_id"], ["job_id", "id"])
    for name, expression in CHECKS.items():
        op.create_check_constraint(op.f("ck_jobs_" + name), "jobs", expression)
    op.create_index("ix_jobs_retry_due", "jobs", ["state", "retry_after", "id"], postgresql_where=sa.text("state = 'retry_wait'"))


def downgrade() -> None:
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM jobs WHERE retry_count <> 0 OR retry_after IS NOT NULL OR retry_source_attempt_id IS NOT NULL OR retry_error_code IS NOT NULL) THEN RAISE EXCEPTION 'Internal retry facts require forward recovery'; END IF; END $$")
    op.drop_index("ix_jobs_retry_due", table_name="jobs")
    for name in CHECKS:
        op.drop_constraint(op.f("ck_jobs_" + name), "jobs", type_="check")
    op.drop_constraint("fk_jobs_retry_source_owner", "jobs", type_="foreignkey")
    for name in ("retry_source_attempt_id", "retry_error_code", "retry_after", "retry_count"):
        op.drop_column("jobs", name)
