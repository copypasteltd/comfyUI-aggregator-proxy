"""Independent audio reservation and verified-duration ledger.

Revision ID: c57a2d9f4106
Revises: e94a0c637b20
"""
import sqlalchemy as sa
from alembic import op

revision = "c57a2d9f4106"
down_revision = "e94a0c637b20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("quota_buckets", sa.Column("daily_audio_milliseconds", sa.BigInteger(), server_default="0", nullable=False))
    op.add_column("usage_ledger", sa.Column("audio_milliseconds", sa.BigInteger(), server_default="0", nullable=False))
    op.drop_constraint(op.f("ck_quota_buckets_nonnegative"), "quota_buckets", type_="check")
    op.create_check_constraint(op.f("ck_quota_buckets_nonnegative"), "quota_buckets", "daily_jobs>=0 AND unfinished_jobs>=0 AND generating_jobs>=0 AND daily_image_count>=0 AND daily_video_milliseconds>=0 AND daily_audio_milliseconds>=0")
    op.drop_constraint(op.f("ck_usage_ledger_nonnegative"), "usage_ledger", type_="check")
    op.create_check_constraint(op.f("ck_usage_ledger_nonnegative"), "usage_ledger", "image_count>=0 AND video_milliseconds>=0 AND audio_milliseconds>=0")


def downgrade() -> None:
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT EXISTS(SELECT 1 FROM jobs WHERE task_type = 'text_to_audio') OR EXISTS(SELECT 1 FROM quota_buckets WHERE daily_audio_milliseconds <> 0) OR EXISTS(SELECT 1 FROM usage_ledger WHERE audio_milliseconds <> 0)")):
        raise RuntimeError("Audio history exists; preserve its ledger and use a reviewed forward migration or verified backup restore")
    op.drop_constraint(op.f("ck_quota_buckets_nonnegative"), "quota_buckets", type_="check")
    op.create_check_constraint(op.f("ck_quota_buckets_nonnegative"), "quota_buckets", "daily_jobs>=0 AND unfinished_jobs>=0 AND generating_jobs>=0 AND daily_image_count>=0 AND daily_video_milliseconds>=0")
    op.drop_constraint(op.f("ck_usage_ledger_nonnegative"), "usage_ledger", type_="check")
    op.create_check_constraint(op.f("ck_usage_ledger_nonnegative"), "usage_ledger", "image_count>=0 AND video_milliseconds>=0")
    op.drop_column("usage_ledger", "audio_milliseconds")
    op.drop_column("quota_buckets", "daily_audio_milliseconds")
