"""Index current-owner/project keyset queries without changing business rows.

Revision ID: bc284d51e790
Revises: 3e15b92f08dc
"""

from alembic import op

revision = "bc284d51e790"
down_revision = "3e15b92f08dc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_jobs_owner_cursor", "jobs", ["tenant_id", "project_id", "key_id", "created_at", "id"])
    op.create_index("ix_artifacts_project_cursor", "artifacts", ["tenant_id", "project_id", "created_at", "id"])
    op.create_index("ix_artifacts_owner_cursor", "artifacts", ["tenant_id", "project_id", "key_id", "created_at", "id"])


def downgrade() -> None:
    op.drop_index("ix_artifacts_owner_cursor", table_name="artifacts")
    op.drop_index("ix_artifacts_project_cursor", table_name="artifacts")
    op.drop_index("ix_jobs_owner_cursor", table_name="jobs")
