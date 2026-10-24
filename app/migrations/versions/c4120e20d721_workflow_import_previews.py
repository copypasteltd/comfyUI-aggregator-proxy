"""Persist actor-bound Workflow import previews; no runtime configuration changes."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c4120e20d721"
down_revision = "bc284d51e790"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workflow_import_previews",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("actor_id", sa.UUID(), sa.ForeignKey("admin_users.id"), nullable=False),
        sa.Column("command_id", sa.UUID(), sa.ForeignKey("worker_commands.id"), nullable=False),
        sa.Column("channel_id", sa.UUID(), sa.ForeignKey("channels.id"), nullable=False),
        sa.Column("source_revision", sa.Integer(), nullable=False),
        sa.Column("workflow_id", sa.UUID(), sa.ForeignKey("workflows.id")),
        sa.Column("expected_revision", sa.Integer()),
        sa.Column("target", postgresql.JSONB(), nullable=False),
        sa.Column("candidate", postgresql.JSONB()),
        sa.Column("candidate_digest", sa.String(64), nullable=False),
        sa.Column("source_digest", sa.String(64), nullable=False),
        sa.Column("export_format", sa.String(32), nullable=False),
        sa.Column("diff", postgresql.JSONB(), nullable=False),
        sa.Column("validation", postgresql.JSONB(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("result", postgresql.JSONB()),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("source_revision >= 1 AND (expected_revision IS NULL OR expected_revision >= 1)", name="positive_revisions"),
    )
    op.create_index("ix_workflow_import_previews_actor_expiry", "workflow_import_previews", ["actor_id", "expires_at"])


def downgrade() -> None:
    op.drop_table("workflow_import_previews")
