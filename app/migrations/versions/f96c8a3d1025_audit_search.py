"""Index bounded, exact management-audit searches without altering history."""
import sqlalchemy as sa
from alembic import op

revision = "f96c8a3d1025"
down_revision = "e85b6c2d9014"
branch_labels = None
depends_on = None
FIELDS = (None, "actor", "action", "resource", "request_id")


def upgrade() -> None:
    for field in FIELDS:
        columns = ([field] if field else []) + [sa.text("created_at DESC"), sa.text("id DESC")]
        op.create_index("ix_audit_events_" + (field + "_" if field else "") + "created", "audit_events", columns)


def downgrade() -> None:
    for field in reversed(FIELDS):
        op.drop_index("ix_audit_events_" + (field + "_" if field else "") + "created", table_name="audit_events")
