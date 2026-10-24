"""Admin retry lineage and job/attempt/artifact ownership constraints.

Revision ID: 6b61d820e7ab
Revises: 08adf2d194a6
"""

import sqlalchemy as sa
from alembic import op

revision = "6b61d820e7ab"
down_revision = "08adf2d194a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("retry_of", sa.Uuid(), nullable=True))
    op.create_index("ix_jobs_retry_of", "jobs", ["retry_of"])
    op.create_unique_constraint("uq_attempts_job_id_id", "attempts", ["job_id", "id"])
    op.create_foreign_key("fk_jobs_current_attempt_owner", "jobs", "attempts", ["id", "current_attempt_id"], ["job_id", "id"])
    op.create_foreign_key("fk_jobs_retry_owner", "jobs", "jobs", ["tenant_id", "project_id", "key_id", "retry_of"], ["tenant_id", "project_id", "key_id", "id"])
    op.create_foreign_key("fk_artifacts_job_owner", "artifacts", "jobs", ["tenant_id", "project_id", "key_id", "job_id"], ["tenant_id", "project_id", "key_id", "id"])
    op.create_check_constraint(op.f("ck_jobs_retry_parent"), "jobs", "retry_of IS NULL OR retry_of <> id")
    op.create_check_constraint(op.f("ck_jobs_priority"), "jobs", "priority BETWEEN -100 AND 100")


def downgrade() -> None:
    op.drop_constraint(op.f("ck_jobs_priority"), "jobs", type_="check")
    op.drop_constraint(op.f("ck_jobs_retry_parent"), "jobs", type_="check")
    op.drop_constraint("fk_artifacts_job_owner", "artifacts", type_="foreignkey")
    op.drop_constraint("fk_jobs_retry_owner", "jobs", type_="foreignkey")
    op.drop_constraint("fk_jobs_current_attempt_owner", "jobs", type_="foreignkey")
    op.drop_constraint("uq_attempts_job_id_id", "attempts", type_="unique")
    op.drop_index("ix_jobs_retry_of", table_name="jobs")
    op.drop_column("jobs", "retry_of")
