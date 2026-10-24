"""Append scoped, redacted HTTP access events without changing business state.

Revision ID: 9a2cf05c7b11
Revises: 4e5630a241a0
"""

import sqlalchemy as sa
from alembic import op

revision = "9a2cf05c7b11"
down_revision = "4e5630a241a0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint("uq_external_bindings_gateway_key_scope", "external_identity_bindings", ["gateway_id", "tenant_id", "project_id", "key_id"])
    op.create_table(
        "access_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("key_id", sa.Uuid(), nullable=True),
        sa.Column("gateway_id", sa.Uuid(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("entry", sa.String(16), nullable=False),
        sa.Column("method", sa.String(8), nullable=False),
        sa.Column("route", sa.String(256), nullable=False),
        sa.Column("peer_ip", sa.String(45), nullable=True),
        sa.Column("client_ip", sa.String(45), nullable=True),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("completion", sa.String(16), nullable=False),
        sa.Column("duration_milliseconds", sa.Integer(), nullable=False),
        sa.Column("request_bytes", sa.BigInteger(), nullable=False),
        sa.Column("response_bytes", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_id"),
        sa.ForeignKeyConstraint(["gateway_id"], ["gateway_services.id"]),
        sa.ForeignKeyConstraint(["tenant_id", "project_id", "key_id"], ["api_keys.tenant_id", "api_keys.project_id", "api_keys.id"], name="fk_access_events_key_scope"),
        sa.ForeignKeyConstraint(["gateway_id", "tenant_id", "project_id", "key_id"], ["external_identity_bindings.gateway_id", "external_identity_bindings.tenant_id", "external_identity_bindings.project_id", "external_identity_bindings.key_id"], name="fk_access_events_gateway_subject"),
        sa.CheckConstraint("(key_id IS NULL AND tenant_id IS NULL AND project_id IS NULL) OR (key_id IS NOT NULL AND tenant_id IS NOT NULL AND project_id IS NOT NULL)", name="scope_complete"),
        sa.CheckConstraint("entry IN ('direct','gateway') AND (entry='gateway' OR gateway_id IS NULL)", name="entry"),
        sa.CheckConstraint("key_id IS NULL OR entry='direct' OR gateway_id IS NOT NULL", name="gateway_subject"),
        sa.CheckConstraint("method IN ('GET','HEAD','POST','PUT','PATCH','DELETE','OPTIONS','TRACE','CONNECT','OTHER')", name="method"),
        sa.CheckConstraint("completion IN ('completed','disconnected','failed')", name="completion"),
        sa.CheckConstraint("duration_milliseconds BETWEEN 0 AND 86400000 AND request_bytes>=0 AND response_bytes>=0", name="counts"),
        sa.CheckConstraint("status_code IS NULL OR status_code BETWEEN 100 AND 599", name="status"),
    )
    op.create_index("ix_access_events_key_created_id", "access_events", ["key_id", "created_at", "id"])
    op.create_index("ix_access_events_created_id", "access_events", ["created_at", "id"])


def downgrade() -> None:
    op.drop_table("access_events")
    op.drop_constraint("uq_external_bindings_gateway_key_scope", "external_identity_bindings", type_="unique")
