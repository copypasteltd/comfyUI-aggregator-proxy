"""Serialize admission creation against durable service request closure.

Revision ID: 6b7d9e51c420
Revises: 9a2cf05c7b11
"""

import sqlalchemy as sa
from alembic import op

revision = "6b7d9e51c420"
down_revision = "9a2cf05c7b11"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gateway_request_fences",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("gateway_id", sa.Uuid(), nullable=False),
        sa.Column("provider_request_id", sa.String(128), nullable=False),
        sa.Column("external_task_ref", sa.String(128), nullable=False),
        sa.Column("price_version", sa.String(64), nullable=False),
        sa.Column("authorization_ref", sa.String(128), nullable=False),
        sa.Column("external_tenant_ref", sa.String(128), nullable=False),
        sa.Column("external_key_ref", sa.String(128), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["gateway_id"], ["gateway_services.id"]),
        sa.UniqueConstraint("gateway_id", "provider_request_id"),
        sa.UniqueConstraint("gateway_id", "external_task_ref"),
    )
    # Reuse each immutable admission UUID for its fence. No random extension or
    # request/body reserialization is needed during an upgrade.
    op.execute(sa.text("""
        INSERT INTO gateway_request_fences
          (id, created_at, gateway_id, provider_request_id, external_task_ref,
           price_version, authorization_ref, external_tenant_ref, external_key_ref)
        SELECT a.id, a.created_at, a.gateway_id, a.provider_request_id,
               a.external_task_ref, a.price_version, a.authorization_ref,
               b.external_tenant_ref, b.external_key_ref
        FROM gateway_admissions a
        JOIN external_identity_bindings b ON b.id = a.binding_id
    """))


def downgrade() -> None:
    # Removing committed closures could revive delayed submissions. Downgrade
    # only in an empty/disposable environment; production uses forward fixes.
    op.execute(sa.text("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM gateway_request_fences WHERE closed_at IS NOT NULL) THEN
            RAISE EXCEPTION 'durable request closures prohibit downgrade';
          END IF;
        END $$
    """))
    op.drop_table("gateway_request_fences")
