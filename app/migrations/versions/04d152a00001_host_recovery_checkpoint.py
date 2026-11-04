"""Persist a separate host-recovery checkpoint without altering output evidence.

Revision ID: 04d152a00001
Revises: 04d133a00001
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "04d152a00001"
down_revision = "04d133a00001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("attempts", sa.Column("recovery_checkpoint", JSONB(none_as_null=True), nullable=True))
    op.create_check_constraint(op.f("ck_attempts_recovery_checkpoint"), "attempts", "recovery_checkpoint IS NULL OR jsonb_typeof(recovery_checkpoint) = 'object'")


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT EXISTS(SELECT 1 FROM attempts WHERE recovery_checkpoint IS NOT NULL)")):
        raise RuntimeError("Recovery evidence exists; preserve checkpoints and use a reviewed forward migration")
    op.drop_constraint(op.f("ck_attempts_recovery_checkpoint"), "attempts", type_="check")
    op.drop_column("attempts", "recovery_checkpoint")
