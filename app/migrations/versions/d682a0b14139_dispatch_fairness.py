"""Persist tenant virtual service and dispatch sequence outside business quotas."""
import sqlalchemy as sa
from alembic import op

revision = "d682a0b14139"
down_revision = "c5719fa03028"
branch_labels = None
depends_on = None

SCOPE = "(dispatch_virtual_milliseconds = 0 AND dispatch_sequence = 0) OR (period = 'active' AND (scope_kind = '1_tenant' OR (scope_kind = '0_global' AND scope_id = '00000000-0000-0000-0000-000000000000')))"


def upgrade() -> None:
    for name in ("dispatch_virtual_milliseconds", "dispatch_sequence"):
        op.add_column("quota_buckets", sa.Column(name, sa.BigInteger(), nullable=False, server_default=sa.text("0")))
    op.create_check_constraint(op.f("ck_quota_buckets_dispatch_nonnegative"), "quota_buckets", "dispatch_virtual_milliseconds >= 0 AND dispatch_sequence >= 0")
    op.create_check_constraint(op.f("ck_quota_buckets_dispatch_scope"), "quota_buckets", SCOPE)


def downgrade() -> None:
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM quota_buckets WHERE dispatch_virtual_milliseconds <> 0 OR dispatch_sequence <> 0) THEN RAISE EXCEPTION 'Dispatch history requires forward recovery'; END IF; END $$")
    for name in ("dispatch_scope", "dispatch_nonnegative"):
        op.drop_constraint(op.f("ck_quota_buckets_" + name), "quota_buckets", type_="check")
    for name in ("dispatch_sequence", "dispatch_virtual_milliseconds"):
        op.drop_column("quota_buckets", name)
