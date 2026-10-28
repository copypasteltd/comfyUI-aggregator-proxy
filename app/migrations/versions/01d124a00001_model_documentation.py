"""Add editable public model documentation without modifying runtime releases."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "01d124a00001"
down_revision = "3da02e7b5941"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("public_models", sa.Column("documentation", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False))
    op.create_check_constraint(op.f("ck_public_models_documentation_object"), "public_models", "jsonb_typeof(documentation) = 'object'")


def downgrade() -> None:
    op.drop_constraint(op.f("ck_public_models_documentation_object"), "public_models", type_="check")
    op.drop_column("public_models", "documentation")
