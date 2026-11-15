"""Native panel grants, sessions, exclusive holds and durable submission journal."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "04d146a00001"
down_revision = "04d152a00001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "origin_grants",
        sa.Column("key_id", sa.Uuid(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("channel_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "revision >= 1 AND jsonb_typeof(channel_ids)='array'",
            name=op.f("ck_origin_grants_policy"),
        ),
        sa.ForeignKeyConstraint(
            ["key_id"], ["api_keys.id"], name=op.f("fk_origin_grants_key_id_api_keys")
        ),
        sa.PrimaryKeyConstraint("key_id", name=op.f("pk_origin_grants")),
    )
    op.create_table(
        "origin_sessions",
        sa.Column("identifier", sa.String(length=32), nullable=False),
        sa.Column("verifier", sa.String(length=64), nullable=False),
        sa.Column("credential_id", sa.Uuid(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["credential_id"],
            ["api_credentials.id"],
            name=op.f("fk_origin_sessions_credential_id_api_credentials"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_origin_sessions")),
        sa.UniqueConstraint("identifier", name=op.f("uq_origin_sessions_identifier")),
    )
    op.create_index(
        op.f("ix_origin_sessions_credential_id"), "origin_sessions", ["credential_id"], unique=False
    )
    op.create_table(
        "origin_reservations",
        sa.Column("key_id", sa.Uuid(), nullable=False),
        sa.Column("channel_id", sa.Uuid(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("config_digest", sa.String(length=64), nullable=False),
        sa.Column("gpu_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "state IN ('acquiring','active','closing','unknown','closed','failed') AND jsonb_typeof(gpu_ids)='array'",
            name=op.f("ck_origin_reservations_state"),
        ),
        sa.ForeignKeyConstraint(
            ["channel_id"], ["channels.id"], name=op.f("fk_origin_reservations_channel_id_channels")
        ),
        sa.ForeignKeyConstraint(
            ["key_id"], ["api_keys.id"], name=op.f("fk_origin_reservations_key_id_api_keys")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_origin_reservations")),
    )
    op.create_index(
        op.f("ix_origin_reservations_channel_id"),
        "origin_reservations",
        ["channel_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_origin_reservations_key_id"), "origin_reservations", ["key_id"], unique=False
    )
    op.create_table(
        "origin_prompts",
        sa.Column("key_id", sa.Uuid(), nullable=False),
        sa.Column("channel_id", sa.Uuid(), nullable=False),
        sa.Column("reservation_id", sa.Uuid(), nullable=False),
        sa.Column("association", sa.String(length=32), nullable=False),
        sa.Column("idempotency_digest", sa.String(length=64), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("prompt_id", sa.String(length=128), nullable=True),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("response", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "state IN ('intent','submitted','completed','rejected','unknown')",
            name=op.f("ck_origin_prompts_state"),
        ),
        sa.ForeignKeyConstraint(
            ["channel_id"], ["channels.id"], name=op.f("fk_origin_prompts_channel_id_channels")
        ),
        sa.ForeignKeyConstraint(
            ["key_id"], ["api_keys.id"], name=op.f("fk_origin_prompts_key_id_api_keys")
        ),
        sa.ForeignKeyConstraint(
            ["reservation_id"],
            ["origin_reservations.id"],
            name=op.f("fk_origin_prompts_reservation_id_origin_reservations"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_origin_prompts")),
        sa.UniqueConstraint("association", name=op.f("uq_origin_prompts_association")),
        sa.UniqueConstraint(
            "key_id",
            "channel_id",
            "idempotency_digest",
            name=op.f("uq_origin_prompts_key_id_channel_id_idempotency_digest"),
        ),
        sa.UniqueConstraint("prompt_id", name=op.f("uq_origin_prompts_prompt_id")),
    )
    op.create_index(
        op.f("ix_origin_prompts_channel_id"), "origin_prompts", ["channel_id"], unique=False
    )
    op.create_index(op.f("ix_origin_prompts_key_id"), "origin_prompts", ["key_id"], unique=False)
    op.create_index(
        op.f("ix_origin_prompts_reservation_id"), "origin_prompts", ["reservation_id"], unique=False
    )
    op.create_table(
        "origin_files",
        sa.Column("key_id", sa.Uuid(), nullable=False),
        sa.Column("channel_id", sa.Uuid(), nullable=False),
        sa.Column("file_type", sa.String(length=16), nullable=False),
        sa.Column("subfolder", sa.String(length=1024), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "file_type IN ('input','output','temp')", name=op.f("ck_origin_files_file_type")
        ),
        sa.ForeignKeyConstraint(
            ["channel_id"], ["channels.id"], name=op.f("fk_origin_files_channel_id_channels")
        ),
        sa.ForeignKeyConstraint(
            ["key_id"], ["api_keys.id"], name=op.f("fk_origin_files_key_id_api_keys")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_origin_files")),
        sa.UniqueConstraint(
            "key_id",
            "channel_id",
            "file_type",
            "subfolder",
            "filename",
            name=op.f("uq_origin_files_key_id_channel_id_file_type_subfolder_filename"),
        ),
    )
    op.create_index(
        op.f("ix_origin_files_channel_id"), "origin_files", ["channel_id"], unique=False
    )
    op.create_index(op.f("ix_origin_files_key_id"), "origin_files", ["key_id"], unique=False)
    op.create_table(
        "origin_user_data",
        sa.Column("key_id", sa.Uuid(), nullable=False),
        sa.Column("channel_id", sa.Uuid(), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["channel_id"], ["channels.id"], name=op.f("fk_origin_user_data_channel_id_channels")
        ),
        sa.ForeignKeyConstraint(
            ["key_id"], ["api_keys.id"], name=op.f("fk_origin_user_data_key_id_api_keys")
        ),
        sa.PrimaryKeyConstraint("key_id", "channel_id", name=op.f("pk_origin_user_data")),
    )


def downgrade() -> None:
    raise RuntimeError("Preserve native execution evidence; use a reviewed forward migration")
