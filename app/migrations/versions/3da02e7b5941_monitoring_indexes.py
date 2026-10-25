"""Cover bounded monitoring windows and pending lifecycle age queries."""
from alembic import op

revision = "3da02e7b5941"
down_revision = "2c9f1d6a4830"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_jobs_monitoring_active", "jobs", ["state", "accepted_at"], postgresql_where="state NOT IN ('succeeded','failed','cancelled','expired')")
    op.create_index("ix_jobs_monitoring_terminal", "jobs", ["terminal_at"], postgresql_where="terminal_at IS NOT NULL", postgresql_include=["task_type", "state", "error_code", "is_validation", "accepted_at", "created_at"])
    op.create_index("ix_outbox_monitoring_pending", "outbox_events", ["created_at"], postgresql_where="delivered_at IS NULL", postgresql_include=["kind"])
    op.create_index("ix_objects_monitoring_pending_delete", "object_records", ["delete_requested_at"], postgresql_where="delete_requested_at IS NOT NULL AND deleted_at IS NULL", postgresql_include=["bytes"])


def downgrade() -> None:
    op.drop_index("ix_objects_monitoring_pending_delete", table_name="object_records")
    op.drop_index("ix_outbox_monitoring_pending", table_name="outbox_events")
    op.drop_index("ix_jobs_monitoring_terminal", table_name="jobs")
    op.drop_index("ix_jobs_monitoring_active", table_name="jobs")
