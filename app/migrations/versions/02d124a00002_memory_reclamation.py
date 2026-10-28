"""Durable ComfyUI HTTP idle memory operations (disabled until configured)."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "02d124a00002"
down_revision = "01d124a00001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("memory_policies",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("channel_id", sa.Uuid(), sa.ForeignKey("channels.id"), nullable=False, unique=True),
        sa.Column("origin_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("idle_seconds", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("idle_since", sa.DateTime(timezone=True)),
        sa.Column("observed_at", sa.DateTime(timezone=True)),
        sa.Column("next_check_at", sa.DateTime(timezone=True)),
        sa.Column("released", sa.Boolean(), nullable=False),
        sa.Column("activity_marker", sa.String(256), nullable=False),
        sa.Column("history_marker", sa.String(256), nullable=False),
        sa.Column("status", sa.String(64), nullable=False),
        sa.Column("active_operation_id", sa.Uuid()),
        sa.CheckConstraint("idle_seconds BETWEEN 600 AND 86400 AND revision >= 1", name=op.f("ck_memory_policies_bounds")))
    op.create_table("memory_operations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("policy_id", sa.Uuid(), sa.ForeignKey("memory_policies.id"), nullable=False),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("owner", sa.Uuid()), sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.CheckConstraint("stage IN ('preflight','free_intent','free_accepted','barrier1_intent','barrier1_wait','barrier2_intent','barrier2_wait','completed','skipped','unknown','failed')", name=op.f("ck_memory_operations_stage")))
    op.create_index("ix_memory_operations_policy_id", "memory_operations", ["policy_id"])
    op.create_index("ix_memory_operations_stage_updated", "memory_operations", ["stage", "updated_at"])


def downgrade():
    op.drop_table("memory_operations")
    op.drop_table("memory_policies")
