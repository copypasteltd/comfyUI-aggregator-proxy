"""Persist gateway configuration publications independently from runtime receipts."""

import sqlalchemy as sa
from alembic import op

revision = "1b8e0c5f3247"
down_revision = "0a7d9b4e2136"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gateway_catalog_releases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("gateway_id", sa.Uuid(), sa.ForeignKey("gateway_services.id"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("instance_id", sa.String(64), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("configuration_json", sa.Text(), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("source_revision", sa.Integer()),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("admin_users.id"), nullable=False),
        sa.UniqueConstraint("gateway_id", "revision"),
        sa.UniqueConstraint("gateway_id", "revision", "digest", "instance_id", name="uq_gateway_catalog_release_digest"),
        sa.ForeignKeyConstraint(["gateway_id", "source_revision"], ["gateway_catalog_releases.gateway_id", "gateway_catalog_releases.revision"], name="fk_gateway_catalog_source", deferrable=True, initially="DEFERRED"),
        sa.CheckConstraint("revision BETWEEN 1 AND 2147483647", name="revision"),
        sa.CheckConstraint("action IN ('publish','retry','rollback') AND ((action = 'publish' AND source_revision IS NULL) OR (action <> 'publish' AND source_revision IS NOT NULL AND source_revision BETWEEN 1 AND revision - 1))", name="action_source"),
        sa.CheckConstraint("octet_length(configuration_json) BETWEEN 2 AND 524288 AND digest = encode(sha256(convert_to(configuration_json, 'UTF8')), 'hex')", name="payload"),
    )
    op.create_table(
        "gateway_catalogs",
        sa.Column("gateway_id", sa.Uuid(), sa.ForeignKey("gateway_services.id"), primary_key=True),
        sa.Column("instance_id", sa.String(64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("confirmed_revision", sa.Integer()),
        sa.ForeignKeyConstraint(["gateway_id", "revision"], ["gateway_catalog_releases.gateway_id", "gateway_catalog_releases.revision"], name="fk_gateway_catalog_desired", deferrable=True, initially="DEFERRED"),
        sa.ForeignKeyConstraint(["gateway_id", "confirmed_revision"], ["gateway_catalog_releases.gateway_id", "gateway_catalog_releases.revision"], name="fk_gateway_catalog_confirmed", deferrable=True, initially="DEFERRED"),
        sa.CheckConstraint("revision BETWEEN 1 AND 2147483647 AND (confirmed_revision IS NULL OR confirmed_revision BETWEEN 1 AND revision)", name="revision"),
    )
    op.create_table(
        "gateway_catalog_receipts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("gateway_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("instance_id", sa.String(64), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error_code", sa.String(32)),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("gateway_id", "revision"),
        sa.ForeignKeyConstraint(["gateway_id", "revision", "digest", "instance_id"], ["gateway_catalog_releases.gateway_id", "gateway_catalog_releases.revision", "gateway_catalog_releases.digest", "gateway_catalog_releases.instance_id"], name="fk_gateway_catalog_receipt_release"),
        sa.CheckConstraint("(status = 'applied' AND error_code IS NULL) OR (status = 'rejected' AND error_code IS NOT NULL AND error_code IN ('unsupported_configuration','account_unavailable','group_unavailable','storage_unavailable','apply_failed'))", name="status"),
    )
    op.add_column("gateway_admissions", sa.Column("catalog_revision", sa.Integer()))
    op.add_column("gateway_admissions", sa.Column("catalog_digest", sa.String(64)))
    op.add_column("gateway_admissions", sa.Column("catalog_instance_id", sa.String(64)))
    op.create_foreign_key("fk_gateway_admission_catalog", "gateway_admissions", "gateway_catalog_releases", ["gateway_id", "catalog_revision", "catalog_digest", "catalog_instance_id"], ["gateway_id", "revision", "digest", "instance_id"])
    op.create_check_constraint("catalog_stamp", "gateway_admissions", "(catalog_revision IS NULL AND catalog_digest IS NULL AND catalog_instance_id IS NULL) OR (catalog_revision IS NOT NULL AND catalog_digest IS NOT NULL AND catalog_instance_id IS NOT NULL)")
    op.execute("""CREATE FUNCTION preserve_gateway_catalog_release() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'Gateway catalog releases are immutable'; END $$""")
    op.execute("CREATE TRIGGER preserve_gateway_catalog_release BEFORE UPDATE ON gateway_catalog_releases FOR EACH ROW EXECUTE FUNCTION preserve_gateway_catalog_release()")
    op.execute("""CREATE FUNCTION preserve_gateway_catalog_state() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_TABLE_NAME = 'gateway_catalogs' THEN
            IF ROW(NEW.gateway_id,NEW.instance_id) IS DISTINCT FROM ROW(OLD.gateway_id,OLD.instance_id)
              OR NEW.revision < OLD.revision OR NEW.revision::bigint > OLD.revision::bigint + 1
              OR (OLD.confirmed_revision IS NOT NULL AND (NEW.confirmed_revision IS NULL OR NEW.confirmed_revision < OLD.confirmed_revision))
              OR (NEW.confirmed_revision IS DISTINCT FROM OLD.confirmed_revision AND NEW.confirmed_revision IS DISTINCT FROM NEW.revision) THEN
                RAISE EXCEPTION 'gateway_catalog_state_immutable';
            END IF;
          ELSIF TG_TABLE_NAME = 'gateway_catalog_receipts' THEN
            IF ROW(NEW.id,NEW.gateway_id,NEW.revision,NEW.instance_id,NEW.digest,NEW.created_at) IS DISTINCT FROM ROW(OLD.id,OLD.gateway_id,OLD.revision,OLD.instance_id,OLD.digest,OLD.created_at)
              OR (OLD.status = 'applied' AND ROW(NEW.status,NEW.error_code,NEW.observed_at) IS DISTINCT FROM ROW(OLD.status,OLD.error_code,OLD.observed_at)) THEN
                RAISE EXCEPTION 'gateway_catalog_receipt_immutable';
            END IF;
          ELSE
            IF ROW(NEW.catalog_revision,NEW.catalog_digest,NEW.catalog_instance_id) IS DISTINCT FROM ROW(OLD.catalog_revision,OLD.catalog_digest,OLD.catalog_instance_id) THEN
                RAISE EXCEPTION 'gateway_admission_catalog_immutable';
            END IF;
          END IF;
          RETURN NEW;
        END $$""")
    for table in ("gateway_catalogs", "gateway_catalog_receipts", "gateway_admissions"):
        op.execute(sa.text(f"CREATE TRIGGER preserve_gateway_catalog_state BEFORE UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION preserve_gateway_catalog_state()"))


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("LOCK TABLE gateway_admissions, gateway_catalogs, gateway_catalog_receipts, gateway_catalog_releases IN ACCESS EXCLUSIVE MODE"))
    if connection.scalar(sa.text("SELECT EXISTS (SELECT 1 FROM gateway_catalog_releases) OR EXISTS (SELECT 1 FROM gateway_admissions WHERE catalog_revision IS NOT NULL)")):
        raise RuntimeError("Gateway catalog facts exist; retain this schema and repair forward")
    for table in ("gateway_catalogs", "gateway_catalog_receipts", "gateway_admissions"):
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS preserve_gateway_catalog_state ON {table}"))
    op.execute("DROP FUNCTION IF EXISTS preserve_gateway_catalog_state()")
    op.drop_constraint("fk_gateway_admission_catalog", "gateway_admissions", type_="foreignkey")
    op.drop_constraint(op.f("ck_gateway_admissions_catalog_stamp"), "gateway_admissions", type_="check")
    for name in ("catalog_instance_id", "catalog_digest", "catalog_revision"):
        op.drop_column("gateway_admissions", name)
    op.drop_table("gateway_catalog_receipts")
    op.drop_table("gateway_catalogs")
    op.drop_table("gateway_catalog_releases")
    op.execute("DROP FUNCTION preserve_gateway_catalog_release()")
