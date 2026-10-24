"""Persist first control-plane phase observations and index statistical windows."""
import sqlalchemy as sa
from alembic import op

revision = "e85b6c2d9014"
down_revision = "d62f7b08a9e1"
branch_labels = None
depends_on = None
PHASES = ("preparing_at", "submit_intent_at", "submitted_at", "outputs_collected_at")


def upgrade() -> None:
    for name in PHASES:
        op.add_column("attempts", sa.Column(name, sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_jobs_accepted_time", "jobs", ["accepted_at", "id"], postgresql_where=sa.text("accepted_at IS NOT NULL"))
    op.create_index("ix_jobs_terminal_time", "jobs", ["terminal_at", "id"], postgresql_where=sa.text("terminal_at IS NOT NULL"))


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT EXISTS(SELECT 1 FROM attempts WHERE preparing_at IS NOT NULL OR submit_intent_at IS NOT NULL OR submitted_at IS NOT NULL OR outputs_collected_at IS NOT NULL)")):
        raise RuntimeError("Phase observation history exists; preserve it with a reviewed forward migration")
    op.drop_index("ix_jobs_terminal_time", table_name="jobs")
    op.drop_index("ix_jobs_accepted_time", table_name="jobs")
    for name in reversed(PHASES):
        op.drop_column("attempts", name)
