"""Add revisioned Admin content tags without changing owner or lifecycle."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b38a4fc10297"
down_revision = "f807a1c902e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("assets", "artifacts"):
        op.add_column(table, sa.Column("tags", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False))
        op.add_column(table, sa.Column("metadata_revision", sa.Integer(), server_default=sa.text("1"), nullable=False))
        op.add_column(table, sa.Column("metadata_updated_at", sa.DateTime(timezone=True), nullable=True))
        op.create_check_constraint(op.f(f"ck_{table}_content_metadata"), table, "jsonb_typeof(tags) = 'array' AND jsonb_array_length(tags) <= 16 AND metadata_revision >= 1")
        op.create_index(f"ix_{table}_tags", table, ["tags"], postgresql_using="gin", postgresql_ops={"tags": "jsonb_path_ops"})
    op.create_index("ix_assets_owner_created", "assets", ["tenant_id", "project_id", "key_id", "created_at", "id"])


def downgrade() -> None:
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM assets WHERE tags <> '[]'::jsonb OR metadata_revision <> 1) OR EXISTS (SELECT 1 FROM artifacts WHERE tags <> '[]'::jsonb OR metadata_revision <> 1) THEN RAISE EXCEPTION 'Content metadata requires forward recovery'; END IF; END $$")
    op.drop_index("ix_assets_owner_created", table_name="assets")
    for table in ("assets", "artifacts"):
        op.drop_index(f"ix_{table}_tags", table_name=table)
        op.drop_constraint(op.f(f"ck_{table}_content_metadata"), table, type_="check")
        for column in ("metadata_updated_at", "metadata_revision", "tags"):
            op.drop_column(table, column)
