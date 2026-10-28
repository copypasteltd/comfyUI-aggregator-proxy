"""Independent 3D mesh reservation and verified output ledger.

Revision ID: 03d128a00001
Revises: 02d124a00002
"""
import sqlalchemy as sa
from alembic import op

revision = "03d128a00001"
down_revision = "02d124a00002"
branch_labels = None
depends_on = None

QUOTA = "daily_jobs>=0 AND unfinished_jobs>=0 AND generating_jobs>=0 AND daily_image_count>=0 AND daily_video_milliseconds>=0 AND daily_audio_milliseconds>=0"
LEDGER = "image_count>=0 AND video_milliseconds>=0 AND audio_milliseconds>=0"


def upgrade() -> None:
    op.add_column("quota_buckets", sa.Column("daily_mesh_count", sa.BigInteger(), server_default="0", nullable=False))
    op.add_column("usage_ledger", sa.Column("mesh_count", sa.BigInteger(), server_default="0", nullable=False))
    op.drop_constraint(op.f("ck_quota_buckets_nonnegative"), "quota_buckets", type_="check")
    op.create_check_constraint(op.f("ck_quota_buckets_nonnegative"), "quota_buckets", QUOTA + " AND daily_mesh_count>=0")
    op.drop_constraint(op.f("ck_usage_ledger_nonnegative"), "usage_ledger", type_="check")
    op.create_check_constraint(op.f("ck_usage_ledger_nonnegative"), "usage_ledger", LEDGER + " AND mesh_count>=0")


def downgrade() -> None:
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT EXISTS(SELECT 1 FROM jobs WHERE task_type='image_to_3d') OR EXISTS(SELECT 1 FROM quota_buckets WHERE daily_mesh_count<>0) OR EXISTS(SELECT 1 FROM usage_ledger WHERE mesh_count<>0)")):
        raise RuntimeError("3D history exists; preserve the ledger and use a reviewed forward migration or verified backup restore")
    op.drop_constraint(op.f("ck_quota_buckets_nonnegative"), "quota_buckets", type_="check")
    op.create_check_constraint(op.f("ck_quota_buckets_nonnegative"), "quota_buckets", QUOTA)
    op.drop_constraint(op.f("ck_usage_ledger_nonnegative"), "usage_ledger", type_="check")
    op.create_check_constraint(op.f("ck_usage_ledger_nonnegative"), "usage_ledger", LEDGER)
    op.drop_column("usage_ledger", "mesh_count")
    op.drop_column("quota_buckets", "daily_mesh_count")
