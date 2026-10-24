"""Version and archive the immutable model-file catalog.

Revision ID: 4e5630a241a0
Revises: 6b61d820e7ab
"""

import sqlalchemy as sa
from alembic import op

revision = "4e5630a241a0"
down_revision = "6b61d820e7ab"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("model_artifacts", sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))
    op.alter_column("model_artifacts", "revision", server_default=None)
    op.add_column("model_artifacts", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("uq_model_artifacts_live_identity", "model_artifacts", ["tenant_id", "project_id", sa.text("lower(relative_path)"), "sha256"], unique=True, postgresql_where=sa.text("deleted_at IS NULL"))


def downgrade() -> None:
    op.drop_index("uq_model_artifacts_live_identity", table_name="model_artifacts")
    op.drop_column("model_artifacts", "deleted_at")
    op.drop_column("model_artifacts", "revision")
