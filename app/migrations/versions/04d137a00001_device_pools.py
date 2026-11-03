"""Freeze per-job device pools and persist atomic weighted assignment history."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "04d137a00001"
down_revision = "03d128a00001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("public_models", sa.Column("routing", postgresql.JSONB(), nullable=False, server_default=sa.text("'{\"policy\":\"first_ready\",\"weights\":{}}'::jsonb")))
    op.create_check_constraint(op.f("ck_public_models_routing_object"), "public_models", "jsonb_typeof(routing) = 'object' AND routing->>'policy' IN ('first_ready','balanced_ready') AND jsonb_typeof(routing->'weights') = 'object' AND (routing->>'policy' <> 'first_ready' OR routing->'weights' = '{}'::jsonb)")
    op.create_table("device_pools",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False), sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("model_id", sa.Uuid(), sa.ForeignKey("public_models.id"), nullable=False),
        sa.Column("release_id", sa.Uuid(), sa.ForeignKey("workflow_releases.id"), nullable=False),
        sa.Column("configuration", postgresql.JSONB(), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["tenant_id", "project_id"], ["projects.tenant_id", "projects.id"]),
        sa.ForeignKeyConstraint(["tenant_id", "project_id", "model_id"], ["public_models.tenant_id", "public_models.project_id", "public_models.id"]),
        sa.ForeignKeyConstraint(["tenant_id", "project_id", "release_id"], ["workflow_releases.tenant_id", "workflow_releases.project_id", "workflow_releases.id"]),
        sa.UniqueConstraint("tenant_id", "project_id", "id"),
        sa.CheckConstraint("sequence >= 0 AND jsonb_typeof(configuration) = 'object'", name=op.f("ck_device_pools_configuration")),
    )
    for name in ("tenant_id", "project_id"):
        op.create_index(op.f("ix_device_pools_"+name), "device_pools", [name])
    op.create_table("device_pool_members",
        sa.Column("pool_id", sa.String(64), sa.ForeignKey("device_pools.id"), primary_key=True),
        sa.Column("gpu_id", sa.Uuid(), sa.ForeignKey("gpu_resources.id"), primary_key=True),
        sa.Column("weight", sa.Integer(), nullable=False),
        sa.Column("credit", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("assigned_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("last_sequence", sa.BigInteger(), nullable=False, server_default="0"),
        sa.CheckConstraint("weight BETWEEN 1 AND 1000 AND assigned_count >= 0 AND last_sequence >= 0", name=op.f("ck_device_pool_members_accounting")),
    )
    op.add_column("jobs", sa.Column("device_pool_id", sa.String(64), nullable=True))
    op.create_foreign_key(op.f("fk_jobs_tenant_id_project_id_device_pool_id_device_pools"), "jobs", "device_pools", ["tenant_id", "project_id", "device_pool_id"], ["tenant_id", "project_id", "id"])
    op.create_index(op.f("ix_jobs_device_pool_id"), "jobs", ["device_pool_id"])
    op.execute("""CREATE FUNCTION preserve_device_pool() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF ROW(NEW.id,NEW.tenant_id,NEW.project_id,NEW.model_id,NEW.release_id,NEW.configuration)
         IS DISTINCT FROM ROW(OLD.id,OLD.tenant_id,OLD.project_id,OLD.model_id,OLD.release_id,OLD.configuration)
      THEN RAISE EXCEPTION 'device_pool_configuration_immutable' USING ERRCODE='23514'; END IF;
      RETURN NEW;
    END $$""")
    op.execute("CREATE TRIGGER device_pool_immutable BEFORE UPDATE ON device_pools FOR EACH ROW EXECUTE FUNCTION preserve_device_pool()")
    op.execute("""CREATE FUNCTION preserve_device_pool_member() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN IF ROW(NEW.pool_id,NEW.gpu_id,NEW.weight) IS DISTINCT FROM ROW(OLD.pool_id,OLD.gpu_id,OLD.weight) THEN
      RAISE EXCEPTION 'device_pool_member_immutable' USING ERRCODE='23514';
    END IF; RETURN NEW; END $$""")
    op.execute("CREATE TRIGGER device_pool_member_immutable BEFORE UPDATE ON device_pool_members FOR EACH ROW EXECUTE FUNCTION preserve_device_pool_member()")
    op.execute("""CREATE FUNCTION preserve_job_device_pool() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN IF NEW.device_pool_id IS DISTINCT FROM OLD.device_pool_id THEN
      RAISE EXCEPTION 'job_device_pool_immutable' USING ERRCODE='23514';
    END IF; RETURN NEW; END $$""")
    op.execute("CREATE TRIGGER job_device_pool_immutable BEFORE UPDATE ON jobs FOR EACH ROW EXECUTE FUNCTION preserve_job_device_pool()")


def downgrade() -> None:
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM device_pools WHERE sequence>0) OR EXISTS (SELECT 1 FROM jobs WHERE device_pool_id IS NOT NULL) THEN RAISE EXCEPTION 'Device-pool history requires forward recovery'; END IF; END $$")
    op.execute("DROP TRIGGER job_device_pool_immutable ON jobs")
    op.execute("DROP FUNCTION preserve_job_device_pool()")
    op.drop_index(op.f("ix_jobs_device_pool_id"), table_name="jobs")
    op.drop_constraint(op.f("fk_jobs_tenant_id_project_id_device_pool_id_device_pools"), "jobs", type_="foreignkey")
    op.drop_column("jobs", "device_pool_id")
    op.execute("DROP TRIGGER device_pool_immutable ON device_pools")
    op.execute("DROP FUNCTION preserve_device_pool()")
    op.execute("DROP TRIGGER device_pool_member_immutable ON device_pool_members")
    op.execute("DROP FUNCTION preserve_device_pool_member()")
    op.drop_table("device_pool_members")
    op.drop_table("device_pools")
    op.drop_constraint(op.f("ck_public_models_routing_object"), "public_models", type_="check")
    op.drop_column("public_models", "routing")
