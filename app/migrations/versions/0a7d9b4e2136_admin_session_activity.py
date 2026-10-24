"""Persist explicit Admin interaction without inventing historical activity."""

import sqlalchemy as sa
from alembic import op

revision = "0a7d9b4e2136"
down_revision = "f96c8a3d1025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("admin_sessions", sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    # Never discard persisted security state during a code rollback.
    op.get_bind().execute(sa.text("LOCK TABLE admin_sessions IN ACCESS EXCLUSIVE MODE"))
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM admin_sessions WHERE last_activity_at IS NOT NULL)")):
        raise RuntimeError("Admin activity exists; retain the column and repair forward")
    op.drop_column("admin_sessions", "last_activity_at")
