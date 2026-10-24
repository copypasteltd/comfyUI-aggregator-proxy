"""Version tenant and project administrative configuration."""
import sqlalchemy as sa
from alembic import op

revision = "e94a0c637b20"
down_revision = "d682a0b14139"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("tenants", "projects"):
        op.add_column(table, sa.Column("revision", sa.Integer(), nullable=False, server_default=sa.text("1")))
        op.create_check_constraint(op.f("ck_" + table + "_revision_positive"), table, "revision >= 1")


def downgrade() -> None:
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM tenants WHERE revision <> 1) OR EXISTS (SELECT 1 FROM projects WHERE revision <> 1) THEN RAISE EXCEPTION 'Scope configuration revisions require forward recovery'; END IF; END $$")
    for table in ("projects", "tenants"):
        op.drop_constraint(op.f("ck_" + table + "_revision_positive"), table, type_="check")
        op.drop_column(table, "revision")
