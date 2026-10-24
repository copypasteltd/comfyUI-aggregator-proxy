"""Metadata revisions and archival for graphical Worker enrollment.

Revision ID: d62f7b08a9e1
Revises: c57a2d9f4106
"""
import sqlalchemy as sa
from alembic import op

revision = "d62f7b08a9e1"
down_revision = "c57a2d9f4106"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("nodes", sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("nodes", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(op.f("ck_nodes_lifecycle"), "nodes", "revision >= 1 AND (archived_at IS NULL OR NOT enabled)")


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT EXISTS(SELECT 1 FROM nodes WHERE revision <> 1 OR archived_at IS NOT NULL)")):
        raise RuntimeError("Worker node management history exists; preserve its revision and archive state with a reviewed forward migration")
    op.drop_constraint(op.f("ck_nodes_lifecycle"), "nodes", type_="check")
    op.drop_column("nodes", "archived_at")
    op.drop_column("nodes", "revision")
