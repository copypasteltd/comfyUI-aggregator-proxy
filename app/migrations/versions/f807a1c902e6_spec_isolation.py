"""Persist generation-spec isolation without changing resource ownership."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "f807a1c902e6"
down_revision = "e6215c902f30"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "spec_isolations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("gpu_id", sa.Uuid(), nullable=False),
        sa.Column("release_id", sa.Uuid(), nullable=False),
        sa.Column("spec_digest", sa.String(64), nullable=False),
        sa.Column("specification", postgresql.JSONB(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("failure_count", sa.Integer(), nullable=False),
        sa.Column("last_failure_at", sa.DateTime(timezone=True)),
        sa.Column("last_failure_job_id", sa.Uuid()),
        sa.Column("last_error_code", sa.String(32)),
        sa.Column("last_success_generation_at", sa.DateTime(timezone=True)),
        sa.Column("isolated_at", sa.DateTime(timezone=True)),
        sa.Column("reason", sa.String(32)),
        sa.Column("cleared_validation_job_id", sa.Uuid()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_spec_isolations")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["tenant_id", "project_id"], ["projects.tenant_id", "projects.id"]),
        sa.ForeignKeyConstraint(["gpu_id"], ["gpu_resources.id"]),
        sa.ForeignKeyConstraint(["tenant_id", "project_id", "release_id"], ["workflow_releases.tenant_id", "workflow_releases.project_id", "workflow_releases.id"]),
        sa.ForeignKeyConstraint(["tenant_id", "project_id", "last_failure_job_id"], ["jobs.tenant_id", "jobs.project_id", "jobs.id"]),
        sa.ForeignKeyConstraint(["tenant_id", "project_id", "cleared_validation_job_id"], ["jobs.tenant_id", "jobs.project_id", "jobs.id"]),
        sa.UniqueConstraint("tenant_id", "project_id", "id"),
        sa.UniqueConstraint("gpu_id", "release_id", "spec_digest"),
        sa.CheckConstraint("failure_count BETWEEN 0 AND 1000000 AND revision >= 1", name=op.f("ck_spec_isolations_counters")),
        sa.CheckConstraint("last_error_code IS NULL OR last_error_code IN ('comfy_oom','comfy_output_invalid')", name=op.f("ck_spec_isolations_failure_code")),
        sa.CheckConstraint("reason IS NULL OR reason IN ('repeated_failure','manual_investigation')", name=op.f("ck_spec_isolations_reason")),
        sa.CheckConstraint("NOT is_active OR (isolated_at IS NOT NULL AND reason IS NOT NULL)", name=op.f("ck_spec_isolations_active_evidence")),
    )
    op.create_index("ix_spec_isolations_tenant_id", "spec_isolations", ["tenant_id"])
    op.create_index("ix_spec_isolations_project_id", "spec_isolations", ["project_id"])
    op.create_index("ix_spec_isolations_activity", "spec_isolations", ["is_active", "updated_at"])


def downgrade() -> None:
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM spec_isolations) THEN RAISE EXCEPTION 'Isolation evidence requires forward recovery'; END IF; END $$")
    op.drop_table("spec_isolations")
