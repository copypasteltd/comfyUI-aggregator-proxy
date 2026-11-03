"""Owned, fenced H3 AV continuation contexts.

Revision ID: 04d133a00001
Revises: 04d137a00001
"""
import sqlalchemy as sa
from alembic import op

revision = "04d133a00001"
down_revision = "04d137a00001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workflow_contexts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("key_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_id", sa.Uuid(), nullable=False),
        sa.Column("object_id", sa.Uuid(), nullable=False),
        sa.Column("compatibility", sa.String(64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("attempt_id"), sa.UniqueConstraint("object_id"),
        sa.UniqueConstraint("tenant_id", "project_id", "id"),
        sa.UniqueConstraint("tenant_id", "project_id", "key_id", "id"),
        sa.ForeignKeyConstraint(["tenant_id", "project_id"], ["projects.tenant_id", "projects.id"]),
        sa.ForeignKeyConstraint(["key_id"], ["api_keys.id"]),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.ForeignKeyConstraint(["attempt_id"], ["attempts.id"]),
        sa.ForeignKeyConstraint(["object_id"], ["object_records.id"]),
        sa.ForeignKeyConstraint(["tenant_id", "project_id", "key_id", "job_id"], ["jobs.tenant_id", "jobs.project_id", "jobs.key_id", "jobs.id"]),
        sa.ForeignKeyConstraint(["job_id", "attempt_id"], ["attempts.job_id", "attempts.id"]),
        sa.ForeignKeyConstraint(["tenant_id", "project_id", "object_id"], ["object_records.tenant_id", "object_records.project_id", "object_records.id"]),
        sa.CheckConstraint("compatibility ~ '^[a-f0-9]{64}$'", name=op.f("ck_workflow_contexts_compatibility")),
    )
    op.create_table(
        "job_context_references",
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("key_id", sa.Uuid(), nullable=False),
        sa.Column("context_id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("job_id"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.ForeignKeyConstraint(["key_id"], ["api_keys.id"]),
        sa.ForeignKeyConstraint(["context_id"], ["workflow_contexts.id"]),
        sa.ForeignKeyConstraint(["tenant_id", "project_id", "key_id", "job_id"], ["jobs.tenant_id", "jobs.project_id", "jobs.key_id", "jobs.id"]),
        sa.ForeignKeyConstraint(["tenant_id", "project_id", "key_id", "context_id"], ["workflow_contexts.tenant_id", "workflow_contexts.project_id", "workflow_contexts.key_id", "workflow_contexts.id"]),
    )
    op.create_index("ix_job_context_references_context", "job_context_references", ["context_id"])


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT EXISTS(SELECT 1 FROM workflow_contexts) OR EXISTS(SELECT 1 FROM job_context_references)")):
        raise RuntimeError("Continuation history exists; preserve owned resources and use a reviewed forward migration or verified backup")
    op.drop_table("job_context_references")
    op.drop_table("workflow_contexts")
