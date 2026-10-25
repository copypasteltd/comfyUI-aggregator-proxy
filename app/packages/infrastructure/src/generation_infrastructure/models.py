"""PostgreSQL persistence. UUIDs are internal; API projections own public identifiers."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    MetaData,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention={"ix": "ix_%(table_name)s_%(column_0_name)s", "uq": "uq_%(table_name)s_%(column_0_N_name)s", "ck": "ck_%(table_name)s_%(constraint_name)s", "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s", "pk": "pk_%(table_name)s"})


class Entity:
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Scoped:
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    project_id: Mapped[UUID] = mapped_column(index=True)


def scope_constraints(*extra: Any) -> tuple[Any, ...]:
    return (ForeignKeyConstraint(["tenant_id", "project_id"], ["projects.tenant_id", "projects.id"]), UniqueConstraint("tenant_id", "project_id", "id"), *extra)


class Tenant(Entity, Base):
    __tablename__ = "tenants"
    name: Mapped[str] = mapped_column(String(128))
    enabled: Mapped[bool] = mapped_column(default=True)
    limits: Mapped[dict[str, Any]] = mapped_column(JSONB)
    revision: Mapped[int] = mapped_column(default=1, server_default=text("1"))
    __table_args__ = (CheckConstraint("revision >= 1", name="revision_positive"),)


class Project(Entity, Base):
    __tablename__ = "projects"
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"))
    name: Mapped[str] = mapped_column(String(128))
    enabled: Mapped[bool] = mapped_column(default=True)
    limits: Mapped[dict[str, Any]] = mapped_column(JSONB)
    revision: Mapped[int] = mapped_column(default=1, server_default=text("1"))
    __table_args__ = (UniqueConstraint("tenant_id", "id"), CheckConstraint("revision >= 1", name="revision_positive"))


class AdminUser(Entity, Base):
    __tablename__ = "admin_users"
    username: Mapped[str] = mapped_column(String(128), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(32))
    enabled: Mapped[bool] = mapped_column(default=True)
    revision: Mapped[int] = mapped_column(default=1)
    __table_args__ = (CheckConstraint("role IN ('administrator','operator','workflow_maintainer','observer')", name="role"),)


class AdminSession(Entity, Base):
    __tablename__ = "admin_sessions"
    identifier: Mapped[str] = mapped_column(String(32), unique=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("admin_users.id"))
    verifier: Mapped[str] = mapped_column(String(64))
    pepper_version: Mapped[int] = mapped_column(default=1)
    csrf_verifier: Mapped[str] = mapped_column(String(64))
    user_revision: Mapped[int] = mapped_column()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ApiKey(Entity, Scoped, Base):
    __tablename__ = "api_keys"
    name: Mapped[str] = mapped_column(String(128))
    enabled: Mapped[bool] = mapped_column(default=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revision: Mapped[int] = mapped_column(default=1)
    policy: Mapped[dict[str, Any]] = mapped_column(JSONB)
    issuer: Mapped[str] = mapped_column(String(128), default="local")
    external_ref: Mapped[str | None] = mapped_column(String(256))
    __table_args__ = scope_constraints(UniqueConstraint("issuer", "external_ref"))


class ApiCredential(Entity, Base):
    __tablename__ = "api_credentials"
    key_id: Mapped[UUID] = mapped_column(ForeignKey("api_keys.id"), index=True)
    identifier: Mapped[str] = mapped_column(String(32), unique=True)
    verifier: Mapped[str] = mapped_column(String(64))
    pepper_version: Mapped[int] = mapped_column(default=1)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AccessEvent(Entity, Base):
    __tablename__ = "access_events"
    request_id: Mapped[UUID] = mapped_column(unique=True)
    tenant_id: Mapped[UUID | None] = mapped_column()
    project_id: Mapped[UUID | None] = mapped_column()
    key_id: Mapped[UUID | None] = mapped_column()
    gateway_id: Mapped[UUID | None] = mapped_column(ForeignKey("gateway_services.id"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    entry: Mapped[str] = mapped_column(String(16))
    method: Mapped[str] = mapped_column(String(8))
    route: Mapped[str] = mapped_column(String(256))
    peer_ip: Mapped[str | None] = mapped_column(String(45))
    client_ip: Mapped[str | None] = mapped_column(String(45))
    status_code: Mapped[int | None] = mapped_column()
    error_code: Mapped[str | None] = mapped_column(String(64))
    completion: Mapped[str] = mapped_column(String(16))
    duration_milliseconds: Mapped[int] = mapped_column()
    request_bytes: Mapped[int] = mapped_column(BigInteger)
    response_bytes: Mapped[int] = mapped_column(BigInteger)
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "project_id", "key_id"], ["api_keys.tenant_id", "api_keys.project_id", "api_keys.id"], name="fk_access_events_key_scope"),
        ForeignKeyConstraint(["gateway_id", "tenant_id", "project_id", "key_id"], ["external_identity_bindings.gateway_id", "external_identity_bindings.tenant_id", "external_identity_bindings.project_id", "external_identity_bindings.key_id"], name="fk_access_events_gateway_subject"),
        CheckConstraint("(key_id IS NULL AND tenant_id IS NULL AND project_id IS NULL) OR (key_id IS NOT NULL AND tenant_id IS NOT NULL AND project_id IS NOT NULL)", name="scope_complete"),
        CheckConstraint("entry IN ('direct','gateway') AND (entry='gateway' OR gateway_id IS NULL)", name="entry"),
        CheckConstraint("key_id IS NULL OR entry='direct' OR gateway_id IS NOT NULL", name="gateway_subject"),
        CheckConstraint("method IN ('GET','HEAD','POST','PUT','PATCH','DELETE','OPTIONS','TRACE','CONNECT','OTHER')", name="method"),
        CheckConstraint("completion IN ('completed','disconnected','failed')", name="completion"),
        CheckConstraint("duration_milliseconds BETWEEN 0 AND 86400000 AND request_bytes>=0 AND response_bytes>=0", name="counts"),
        CheckConstraint("status_code IS NULL OR status_code BETWEEN 100 AND 599", name="status"),
        Index("ix_access_events_key_created_id", "key_id", "created_at", "id"),
        Index("ix_access_events_created_id", "created_at", "id"),
    )


class Node(Entity, Base):
    __tablename__ = "nodes"
    name: Mapped[str] = mapped_column(String(128))
    revision: Mapped[int] = mapped_column(default=1, server_default="1")
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    credential_identifier: Mapped[str] = mapped_column(String(32), unique=True)
    credential_verifier: Mapped[str] = mapped_column(String(64))
    pepper_version: Mapped[int] = mapped_column(default=1)
    current_boot_id: Mapped[UUID | None] = mapped_column()
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    enabled: Mapped[bool] = mapped_column(default=True)
    inventory: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    __table_args__ = (CheckConstraint("revision >= 1 AND (archived_at IS NULL OR NOT enabled)", name="lifecycle"),)


class Gpu(Entity, Base):
    __tablename__ = "gpu_resources"
    node_id: Mapped[UUID] = mapped_column(ForeignKey("nodes.id"))
    hardware_id: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(128))
    memory_bytes: Mapped[int | None] = mapped_column(BigInteger)
    enabled: Mapped[bool] = mapped_column(default=True)
    fence: Mapped[int] = mapped_column(BigInteger, default=0)
    management_hold: Mapped[UUID | None] = mapped_column()
    __table_args__ = (UniqueConstraint("node_id", "hardware_id"), UniqueConstraint("node_id", "id"))


class Channel(Entity, Scoped, Base):
    __tablename__ = "channels"
    revision: Mapped[int] = mapped_column(default=1)
    name: Mapped[str] = mapped_column(String(128))
    node_id: Mapped[UUID] = mapped_column(ForeignKey("nodes.id"))
    gpu_id: Mapped[UUID] = mapped_column(ForeignKey("gpu_resources.id"))
    base_url: Mapped[str] = mapped_column(String(2048))
    encrypted_auth: Mapped[str | None] = mapped_column(Text)
    desired_revision: Mapped[int] = mapped_column(default=1)
    applied_revision: Mapped[int | None] = mapped_column()
    verified_revision: Mapped[int | None] = mapped_column()
    enabled: Mapped[bool] = mapped_column(default=False)
    draining: Mapped[bool] = mapped_column(default=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    observation: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    __table_args__ = scope_constraints(ForeignKeyConstraint(["node_id", "gpu_id"], ["gpu_resources.node_id", "gpu_resources.id"]))


class ChannelRevision(Entity, Base):
    __tablename__ = "channel_revisions"
    channel_id: Mapped[UUID] = mapped_column(ForeignKey("channels.id"))
    revision: Mapped[int] = mapped_column()
    config: Mapped[dict[str, Any]] = mapped_column(JSONB)
    __table_args__ = (UniqueConstraint("channel_id", "revision"),)


class Workflow(Entity, Scoped, Base):
    __tablename__ = "workflows"
    name: Mapped[str] = mapped_column(String(128))
    task_type: Mapped[str] = mapped_column(String(32))
    revision: Mapped[int] = mapped_column(default=1)
    draft: Mapped[dict[str, Any]] = mapped_column(JSONB)
    archived: Mapped[bool] = mapped_column(default=False)
    __table_args__ = scope_constraints()


class WorkflowRelease(Entity, Scoped, Base):
    __tablename__ = "workflow_releases"
    workflow_id: Mapped[UUID] = mapped_column(ForeignKey("workflows.id"))
    version: Mapped[int] = mapped_column()
    digest: Mapped[str] = mapped_column(String(64))
    manifest: Mapped[dict[str, Any]] = mapped_column(JSONB)
    validated: Mapped[bool] = mapped_column(default=False)
    validation: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    __table_args__ = scope_constraints(UniqueConstraint("workflow_id", "version"), ForeignKeyConstraint(["tenant_id", "project_id", "workflow_id"], ["workflows.tenant_id", "workflows.project_id", "workflows.id"]))


class WorkflowBinding(Entity, Scoped, Base):
    __tablename__ = "workflow_bindings"
    channel_id: Mapped[UUID] = mapped_column(ForeignKey("channels.id"))
    release_id: Mapped[UUID] = mapped_column(ForeignKey("workflow_releases.id"))
    desired_digest: Mapped[str] = mapped_column(String(64))
    applied_digest: Mapped[str | None] = mapped_column(String(64))
    verified_digest: Mapped[str | None] = mapped_column(String(64))
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    runtime_approval: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    __table_args__ = scope_constraints(UniqueConstraint("channel_id", "release_id"), ForeignKeyConstraint(["tenant_id", "project_id", "channel_id"], ["channels.tenant_id", "channels.project_id", "channels.id"]), ForeignKeyConstraint(["tenant_id", "project_id", "release_id"], ["workflow_releases.tenant_id", "workflow_releases.project_id", "workflow_releases.id"]))


class ModelArtifact(Entity, Scoped, Base):
    __tablename__ = "model_artifacts"
    name: Mapped[str] = mapped_column(String(256))
    kind: Mapped[str] = mapped_column(String(32))
    sha256: Mapped[str | None] = mapped_column(String(64))
    bytes: Mapped[int | None] = mapped_column(BigInteger)
    relative_path: Mapped[str] = mapped_column(String(1024))
    enabled: Mapped[bool] = mapped_column(default=True)
    revision: Mapped[int] = mapped_column(default=1)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = scope_constraints()


Index("uq_model_artifacts_live_identity", ModelArtifact.tenant_id, ModelArtifact.project_id, func.lower(ModelArtifact.relative_path), ModelArtifact.sha256, unique=True, postgresql_where=ModelArtifact.deleted_at.is_(None))


class PublicModel(Entity, Scoped, Base):
    __tablename__ = "public_models"
    alias: Mapped[str] = mapped_column(String(128))
    task_type: Mapped[str] = mapped_column(String(32))
    release_id: Mapped[UUID] = mapped_column(ForeignKey("workflow_releases.id"))
    channel_ids: Mapped[list[str]] = mapped_column(JSONB)
    defaults: Mapped[dict[str, Any]] = mapped_column(JSONB)
    revision: Mapped[int] = mapped_column(default=1)
    published: Mapped[bool] = mapped_column(default=False)
    __table_args__ = scope_constraints(UniqueConstraint("tenant_id", "project_id", "alias"), ForeignKeyConstraint(["tenant_id", "project_id", "release_id"], ["workflow_releases.tenant_id", "workflow_releases.project_id", "workflow_releases.id"]))


class Idempotency(Entity, Scoped, Base):
    __tablename__ = "idempotency"
    key_id: Mapped[UUID] = mapped_column(ForeignKey("api_keys.id"))
    operation: Mapped[str] = mapped_column(String(64))
    token_digest: Mapped[str] = mapped_column(String(64))
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    resource_id: Mapped[UUID | None] = mapped_column()
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = scope_constraints(UniqueConstraint("key_id", "operation", "token_digest"), ForeignKeyConstraint(["tenant_id", "project_id", "key_id"], ["api_keys.tenant_id", "api_keys.project_id", "api_keys.id"]))


class Job(Entity, Scoped, Base):
    __tablename__ = "jobs"
    key_id: Mapped[UUID] = mapped_column(ForeignKey("api_keys.id"))
    model_alias: Mapped[str] = mapped_column(String(128))
    task_type: Mapped[str] = mapped_column(String(32))
    state: Mapped[str] = mapped_column(String(32), index=True)
    release_id: Mapped[UUID] = mapped_column(ForeignKey("workflow_releases.id"))
    request: Mapped[dict[str, Any]] = mapped_column(JSONB)
    execution: Mapped[dict[str, Any]] = mapped_column(JSONB)
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    execution_fingerprint: Mapped[str] = mapped_column(String(64))
    eligible_channels: Mapped[list[str]] = mapped_column(JSONB)
    current_attempt_id: Mapped[UUID | None] = mapped_column()
    retry_of: Mapped[UUID | None] = mapped_column(index=True)
    retry_count: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    retry_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retry_error_code: Mapped[str | None] = mapped_column(String(32))
    retry_source_attempt_id: Mapped[UUID | None] = mapped_column()
    priority: Mapped[int] = mapped_column(default=0)
    is_validation: Mapped[bool] = mapped_column(default=False)
    cancellation_requested: Mapped[bool] = mapped_column(default=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revision: Mapped[int] = mapped_column(default=1)
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_period: Mapped[str] = mapped_column(String(10))
    terminal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    result_manifest: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    __table_args__ = scope_constraints(ForeignKeyConstraint(["tenant_id", "project_id", "key_id"], ["api_keys.tenant_id", "api_keys.project_id", "api_keys.id"]), ForeignKeyConstraint(["tenant_id", "project_id", "release_id"], ["workflow_releases.tenant_id", "workflow_releases.project_id", "workflow_releases.id"]), ForeignKeyConstraint(["id", "current_attempt_id"], ["attempts.job_id", "attempts.id"], use_alter=True, name="fk_jobs_current_attempt_owner"), ForeignKeyConstraint(["tenant_id", "project_id", "key_id", "retry_of"], ["jobs.tenant_id", "jobs.project_id", "jobs.key_id", "jobs.id"], name="fk_jobs_retry_owner"), CheckConstraint("state IN ('admission_pending','queued','leased','preparing','submitting','running','collecting','uploading','verifying','reconciling','retry_wait','cancel_requested','succeeded','failed','cancelled','expired')", name="state"), CheckConstraint("retry_of IS NULL OR retry_of <> id", name="retry_parent"), CheckConstraint("priority BETWEEN -100 AND 100", name="priority"), Index("ix_jobs_dispatch", "state", "priority", "created_at"), UniqueConstraint("tenant_id", "project_id", "key_id", "id"))


    __table_args__ += (
        ForeignKeyConstraint(["id", "retry_source_attempt_id"], ["attempts.job_id", "attempts.id"], use_alter=True, name="fk_jobs_retry_source_owner"),
        CheckConstraint("retry_count BETWEEN 0 AND 2", name="retry_count"),
        CheckConstraint("(retry_count = 0 AND retry_error_code IS NULL AND retry_source_attempt_id IS NULL) OR (retry_count > 0 AND retry_error_code IS NOT NULL AND retry_error_code IN ('preparation_transport_failure','comfy_oom') AND retry_source_attempt_id IS NOT NULL)", name="retry_metadata"),
        CheckConstraint("(state = 'retry_wait' AND retry_count > 0 AND retry_after IS NOT NULL AND current_attempt_id IS NULL) OR (state <> 'retry_wait' AND retry_after IS NULL)", name="retry_schedule"),
        Index("ix_jobs_retry_due", "state", "retry_after", "id", postgresql_where=text("state = 'retry_wait'")),
    )


class SpecIsolation(Entity, Scoped, Base):
    __tablename__ = "spec_isolations"
    gpu_id: Mapped[UUID] = mapped_column(ForeignKey("gpu_resources.id"))
    release_id: Mapped[UUID] = mapped_column()
    spec_digest: Mapped[str] = mapped_column(String(64))
    specification: Mapped[dict[str, Any]] = mapped_column(JSONB)
    is_active: Mapped[bool] = mapped_column(default=False)
    revision: Mapped[int] = mapped_column(default=1)
    failure_count: Mapped[int] = mapped_column(default=0)
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_failure_job_id: Mapped[UUID | None] = mapped_column()
    last_error_code: Mapped[str | None] = mapped_column(String(32))
    last_success_generation_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    isolated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str | None] = mapped_column(String(32))
    cleared_validation_job_id: Mapped[UUID | None] = mapped_column()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = scope_constraints(
        UniqueConstraint("gpu_id", "release_id", "spec_digest"),
        ForeignKeyConstraint(["tenant_id", "project_id", "release_id"], ["workflow_releases.tenant_id", "workflow_releases.project_id", "workflow_releases.id"]),
        ForeignKeyConstraint(["tenant_id", "project_id", "last_failure_job_id"], ["jobs.tenant_id", "jobs.project_id", "jobs.id"]),
        ForeignKeyConstraint(["tenant_id", "project_id", "cleared_validation_job_id"], ["jobs.tenant_id", "jobs.project_id", "jobs.id"]),
        CheckConstraint("failure_count BETWEEN 0 AND 1000000 AND revision >= 1", name="counters"),
        CheckConstraint("last_error_code IS NULL OR last_error_code IN ('comfy_oom','comfy_output_invalid')", name="failure_code"),
        CheckConstraint("reason IS NULL OR reason IN ('repeated_failure','manual_investigation')", name="reason"),
        CheckConstraint("NOT is_active OR (isolated_at IS NOT NULL AND reason IS NOT NULL)", name="active_evidence"),
        Index("ix_spec_isolations_activity", "is_active", "updated_at"),
    )


class Attempt(Entity, Base):
    __tablename__ = "attempts"
    job_id: Mapped[UUID] = mapped_column(ForeignKey("jobs.id"), index=True)
    channel_id: Mapped[UUID] = mapped_column(ForeignKey("channels.id"))
    channel_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    node_id: Mapped[UUID] = mapped_column(ForeignKey("nodes.id"))
    boot_id: Mapped[UUID] = mapped_column()
    gpu_id: Mapped[UUID] = mapped_column(ForeignKey("gpu_resources.id"))
    fence: Mapped[int] = mapped_column(BigInteger)
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    generation_released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    preparing_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submit_intent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outputs_collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    prompt_id: Mapped[str | None] = mapped_column(String(128))
    association_id: Mapped[UUID | None] = mapped_column()
    journal_seq: Mapped[int] = mapped_column(BigInteger, default=0)
    generation_evidence: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    __table_args__ = (UniqueConstraint("gpu_id", "fence"), UniqueConstraint("job_id", "id"))


class GpuOccupancy(Base):
    __tablename__ = "gpu_occupancies"
    gpu_id: Mapped[UUID] = mapped_column(ForeignKey("gpu_resources.id"), primary_key=True)
    attempt_id: Mapped[UUID] = mapped_column(ForeignKey("attempts.id"), unique=True)
    job_id: Mapped[UUID] = mapped_column(ForeignKey("jobs.id"))
    uncertain: Mapped[bool] = mapped_column(default=False)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class QuotaBucket(Base):
    __tablename__ = "quota_buckets"
    scope_kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    scope_id: Mapped[UUID] = mapped_column(primary_key=True)
    period: Mapped[str] = mapped_column(String(10), primary_key=True)
    daily_jobs: Mapped[int] = mapped_column(BigInteger, default=0)
    unfinished_jobs: Mapped[int] = mapped_column(BigInteger, default=0)
    generating_jobs: Mapped[int] = mapped_column(BigInteger, default=0)
    daily_image_count: Mapped[int] = mapped_column(BigInteger, default=0)
    daily_video_milliseconds: Mapped[int] = mapped_column(BigInteger, default=0)
    daily_audio_milliseconds: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    dispatch_virtual_milliseconds: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    dispatch_sequence: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    __table_args__ = (
        CheckConstraint("daily_jobs>=0 AND unfinished_jobs>=0 AND generating_jobs>=0 AND daily_image_count>=0 AND daily_video_milliseconds>=0 AND daily_audio_milliseconds>=0", name="nonnegative"),
        CheckConstraint("dispatch_virtual_milliseconds >= 0 AND dispatch_sequence >= 0", name="dispatch_nonnegative"),
        CheckConstraint("(dispatch_virtual_milliseconds = 0 AND dispatch_sequence = 0) OR (period = 'active' AND (scope_kind = '1_tenant' OR (scope_kind = '0_global' AND scope_id = '00000000-0000-0000-0000-000000000000')))", name="dispatch_scope"),
    )


class WorkerInbox(Entity, Base):
    __tablename__ = "worker_inbox"
    event_id: Mapped[UUID] = mapped_column(unique=True)
    attempt_id: Mapped[UUID] = mapped_column(ForeignKey("attempts.id"))
    journal_seq: Mapped[int] = mapped_column(BigInteger)
    digest: Mapped[str] = mapped_column(String(64))
    outcome: Mapped[dict[str, Any]] = mapped_column(JSONB)
    __table_args__ = (UniqueConstraint("attempt_id", "journal_seq"),)


class ObjectRecord(Entity, Scoped, Base):
    __tablename__ = "object_records"
    object_key: Mapped[str] = mapped_column(String(1024), unique=True)
    version_id: Mapped[str | None] = mapped_column(String(1024))
    bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    media_type: Mapped[str] = mapped_column(String(128))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    delete_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    gc_claim_token: Mapped[UUID | None] = mapped_column()
    gc_claim_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = scope_constraints(CheckConstraint("bytes>0", name="positive_bytes"))


class Asset(Entity, Scoped, Base):
    __tablename__ = "assets"
    key_id: Mapped[UUID] = mapped_column(ForeignKey("api_keys.id"))
    object_id: Mapped[UUID] = mapped_column(ForeignKey("object_records.id"))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tags: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    metadata_revision: Mapped[int] = mapped_column(default=1, server_default=text("1"))
    metadata_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = scope_constraints(CheckConstraint("jsonb_typeof(tags) = 'array' AND jsonb_array_length(tags) <= 16 AND metadata_revision >= 1", name="content_metadata"), UniqueConstraint("tenant_id", "project_id", "key_id", "id"), ForeignKeyConstraint(["tenant_id", "project_id", "key_id"], ["api_keys.tenant_id", "api_keys.project_id", "api_keys.id"]), ForeignKeyConstraint(["tenant_id", "project_id", "object_id"], ["object_records.tenant_id", "object_records.project_id", "object_records.id"]))


class Artifact(Entity, Scoped, Base):
    __tablename__ = "artifacts"
    key_id: Mapped[UUID] = mapped_column(ForeignKey("api_keys.id"))
    job_id: Mapped[UUID] = mapped_column(ForeignKey("jobs.id"))
    object_id: Mapped[UUID] = mapped_column(ForeignKey("object_records.id"))
    position: Mapped[int] = mapped_column()
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tags: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    metadata_revision: Mapped[int] = mapped_column(default=1, server_default=text("1"))
    metadata_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = scope_constraints(CheckConstraint("jsonb_typeof(tags) = 'array' AND jsonb_array_length(tags) <= 16 AND metadata_revision >= 1", name="content_metadata"), UniqueConstraint("job_id", "position"), ForeignKeyConstraint(["tenant_id", "project_id", "job_id"], ["jobs.tenant_id", "jobs.project_id", "jobs.id"]), ForeignKeyConstraint(["tenant_id", "project_id", "key_id", "job_id"], ["jobs.tenant_id", "jobs.project_id", "jobs.key_id", "jobs.id"], name="fk_artifacts_job_owner"), ForeignKeyConstraint(["tenant_id", "project_id", "object_id"], ["object_records.tenant_id", "object_records.project_id", "object_records.id"]), ForeignKeyConstraint(["tenant_id", "project_id", "key_id"], ["api_keys.tenant_id", "api_keys.project_id", "api_keys.id"]))


Index("ix_assets_tags", Asset.tags, postgresql_using="gin", postgresql_ops={"tags": "jsonb_path_ops"})
Index("ix_artifacts_tags", Artifact.tags, postgresql_using="gin", postgresql_ops={"tags": "jsonb_path_ops"})
Index("ix_assets_owner_created", Asset.tenant_id, Asset.project_id, Asset.key_id, Asset.created_at, Asset.id)
Index("ix_jobs_owner_cursor", Job.tenant_id, Job.project_id, Job.key_id, Job.created_at, Job.id)
Index("ix_jobs_accepted_time", Job.accepted_at, Job.id, postgresql_where=Job.accepted_at.is_not(None))
Index("ix_jobs_terminal_time", Job.terminal_at, Job.id, postgresql_where=Job.terminal_at.is_not(None))
Index("ix_artifacts_project_cursor", Artifact.tenant_id, Artifact.project_id, Artifact.created_at, Artifact.id)
Index("ix_artifacts_owner_cursor", Artifact.tenant_id, Artifact.project_id, Artifact.key_id, Artifact.created_at, Artifact.id)


class JobAssetReference(Scoped, Base):
    __tablename__ = "job_asset_references"
    job_id: Mapped[UUID] = mapped_column(ForeignKey("jobs.id"), primary_key=True)
    asset_id: Mapped[UUID] = mapped_column(ForeignKey("assets.id"), primary_key=True)
    key_id: Mapped[UUID] = mapped_column(ForeignKey("api_keys.id"))
    __table_args__ = (ForeignKeyConstraint(["tenant_id", "project_id", "key_id", "job_id"], ["jobs.tenant_id", "jobs.project_id", "jobs.key_id", "jobs.id"]), ForeignKeyConstraint(["tenant_id", "project_id", "key_id", "asset_id"], ["assets.tenant_id", "assets.project_id", "assets.key_id", "assets.id"]), Index("ix_job_asset_references_asset", "asset_id"))


class ManagementOperation(Entity, Base):
    __tablename__ = "management_operations"
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("admin_users.id"))
    command: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(32), default="pending")
    revision: Mapped[int] = mapped_column(default=1)
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class OutputUpload(Entity, Base):
    __tablename__ = "output_uploads"
    attempt_id: Mapped[UUID] = mapped_column(ForeignKey("attempts.id"))
    position: Mapped[int] = mapped_column()
    specification: Mapped[dict[str, Any]] = mapped_column(JSONB)
    object_key: Mapped[str] = mapped_column(String(1024), unique=True)
    object_id: Mapped[UUID | None] = mapped_column(ForeignKey("object_records.id"))
    __table_args__ = (UniqueConstraint("attempt_id", "position"), CheckConstraint("position>=0 AND position<100", name="position"))


class WorkerCommand(Entity, Base):
    __tablename__ = "worker_commands"
    operation_id: Mapped[UUID] = mapped_column(ForeignKey("management_operations.id"))
    node_id: Mapped[UUID] = mapped_column(ForeignKey("nodes.id"))
    channel_id: Mapped[UUID] = mapped_column(ForeignKey("channels.id"))
    command: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    boot_id: Mapped[UUID | None] = mapped_column()
    state: Mapped[str] = mapped_column(String(32), default="pending")
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    receipt: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    authorization_snapshot: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint("command IN ('probe_channel','scan_inventory','pull_workflow','apply_workflow_release','apply_channel_config','collect_diagnostics','inspect_model_file')", name="allowlisted_command"),)


class WorkflowImportPreview(Entity, Base):
    __tablename__ = "workflow_import_previews"
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("admin_users.id"))
    command_id: Mapped[UUID] = mapped_column(ForeignKey("worker_commands.id"))
    channel_id: Mapped[UUID] = mapped_column(ForeignKey("channels.id"))
    source_revision: Mapped[int] = mapped_column()
    workflow_id: Mapped[UUID | None] = mapped_column(ForeignKey("workflows.id"))
    expected_revision: Mapped[int | None] = mapped_column()
    target: Mapped[dict[str, Any]] = mapped_column(JSONB)
    candidate: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    candidate_digest: Mapped[str] = mapped_column(String(64))
    source_digest: Mapped[str] = mapped_column(String(64))
    export_format: Mapped[str] = mapped_column(String(32))
    diff: Mapped[dict[str, Any]] = mapped_column(JSONB)
    validation: Mapped[dict[str, Any]] = mapped_column(JSONB)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    __table_args__ = (Index("ix_workflow_import_previews_actor_expiry", "actor_id", "expires_at"), CheckConstraint("source_revision >= 1 AND (expected_revision IS NULL OR expected_revision >= 1)", name="positive_revisions"))


class AuditEvent(Entity, Base):
    __tablename__ = "audit_events"
    actor: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(128))
    resource: Mapped[str] = mapped_column(String(128))
    request_id: Mapped[str] = mapped_column(String(128))
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


for _audit_field in (None, "actor", "action", "resource", "request_id"):
    Index("ix_audit_events_" + (_audit_field + "_" if _audit_field else "") + "created", *([getattr(AuditEvent, _audit_field)] if _audit_field else []), AuditEvent.created_at.desc(), AuditEvent.id.desc())


class OutboxEvent(Entity, Base):
    __tablename__ = "outbox_events"
    kind: Mapped[str] = mapped_column(String(64), default="invalidate")
    aggregate_id: Mapped[UUID] = mapped_column(index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claimed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claim_token: Mapped[UUID | None] = mapped_column()
    __table_args__ = (Index("uq_gateway_settlement_job", "aggregate_id", unique=True, postgresql_where=text("kind = 'gateway.settlement'")),)


class UsageLedger(Entity, Base):
    __tablename__ = "usage_ledger"
    job_id: Mapped[UUID] = mapped_column(ForeignKey("jobs.id"), unique=True)
    key_id: Mapped[UUID] = mapped_column(ForeignKey("api_keys.id"))
    image_count: Mapped[int] = mapped_column(default=0)
    video_milliseconds: Mapped[int] = mapped_column(BigInteger, default=0)
    audio_milliseconds: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    outcome: Mapped[str] = mapped_column(String(32))
    __table_args__ = (CheckConstraint("image_count>=0 AND video_milliseconds>=0 AND audio_milliseconds>=0", name="nonnegative"),)


class RateState(Base):
    __tablename__ = "rate_states"
    identity: Mapped[str] = mapped_column(String(160), primary_key=True)
    tokens: Mapped[Decimal] = mapped_column(Numeric(20, 6))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recent: Mapped[list[int]] = mapped_column(JSONB, default=list)


class AdminMutation(Entity, Base):
    __tablename__ = "admin_mutations"
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("admin_users.id"))
    operation: Mapped[str] = mapped_column(String(128))
    token_digest: Mapped[str] = mapped_column(String(64))
    request_digest: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    __table_args__ = (UniqueConstraint("actor_id", "operation", "token_digest"),)


class NodeBoot(Entity, Base):
    __tablename__ = "node_boots"
    node_id: Mapped[UUID] = mapped_column(ForeignKey("nodes.id"))
    boot_id: Mapped[UUID] = mapped_column(unique=True)


class GatewayService(Entity, Base):
    __tablename__ = "gateway_services"
    name: Mapped[str] = mapped_column(String(128))
    issuer: Mapped[str] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(default=True)
    revision: Mapped[int] = mapped_column(default=1)
    allowed_cidrs: Mapped[list[str]] = mapped_column(JSONB)
    credential_identifier: Mapped[str] = mapped_column(String(32), unique=True)
    credential_verifier: Mapped[str] = mapped_column(String(64))
    pepper_version: Mapped[int] = mapped_column(default=1)


class GatewayCatalogRelease(Entity, Base):
    __tablename__ = "gateway_catalog_releases"
    gateway_id: Mapped[UUID] = mapped_column(ForeignKey("gateway_services.id"))
    revision: Mapped[int] = mapped_column()
    instance_id: Mapped[str] = mapped_column(String(64))
    digest: Mapped[str] = mapped_column(String(64))
    configuration_json: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(String(16))
    source_revision: Mapped[int | None] = mapped_column()
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("admin_users.id"))
    __table_args__ = (
        UniqueConstraint("gateway_id", "revision"),
        UniqueConstraint("gateway_id", "revision", "digest", "instance_id", name="uq_gateway_catalog_release_digest"),
        ForeignKeyConstraint(["gateway_id", "source_revision"], ["gateway_catalog_releases.gateway_id", "gateway_catalog_releases.revision"], name="fk_gateway_catalog_source", deferrable=True, initially="DEFERRED"),
        CheckConstraint("revision BETWEEN 1 AND 2147483647", name="revision"),
        CheckConstraint("action IN ('publish','retry','rollback') AND ((action = 'publish' AND source_revision IS NULL) OR (action <> 'publish' AND source_revision IS NOT NULL AND source_revision BETWEEN 1 AND revision - 1))", name="action_source"),
        CheckConstraint("octet_length(configuration_json) BETWEEN 2 AND 524288 AND digest = encode(sha256(convert_to(configuration_json, 'UTF8')), 'hex')", name="payload"),
    )


class GatewayCatalog(Base):
    __tablename__ = "gateway_catalogs"
    gateway_id: Mapped[UUID] = mapped_column(ForeignKey("gateway_services.id"), primary_key=True)
    instance_id: Mapped[str] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column()
    confirmed_revision: Mapped[int | None] = mapped_column()
    __table_args__ = (
        ForeignKeyConstraint(["gateway_id", "revision"], ["gateway_catalog_releases.gateway_id", "gateway_catalog_releases.revision"], name="fk_gateway_catalog_desired", deferrable=True, initially="DEFERRED"),
        ForeignKeyConstraint(["gateway_id", "confirmed_revision"], ["gateway_catalog_releases.gateway_id", "gateway_catalog_releases.revision"], name="fk_gateway_catalog_confirmed", deferrable=True, initially="DEFERRED"),
        CheckConstraint("revision BETWEEN 1 AND 2147483647 AND (confirmed_revision IS NULL OR confirmed_revision BETWEEN 1 AND revision)", name="revision"),
    )


class GatewayCatalogReceipt(Entity, Base):
    __tablename__ = "gateway_catalog_receipts"
    gateway_id: Mapped[UUID] = mapped_column()
    revision: Mapped[int] = mapped_column()
    instance_id: Mapped[str] = mapped_column(String(64))
    digest: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    error_code: Mapped[str | None] = mapped_column(String(32))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("gateway_id", "revision"),
        ForeignKeyConstraint(["gateway_id", "revision", "digest", "instance_id"], ["gateway_catalog_releases.gateway_id", "gateway_catalog_releases.revision", "gateway_catalog_releases.digest", "gateway_catalog_releases.instance_id"], name="fk_gateway_catalog_receipt_release"),
        CheckConstraint("(status = 'applied' AND error_code IS NULL) OR (status = 'rejected' AND error_code IS NOT NULL AND error_code IN ('unsupported_configuration','account_unavailable','group_unavailable','storage_unavailable','apply_failed'))", name="status"),
    )


class ExternalIdentityBinding(Entity, Scoped, Base):
    __tablename__ = "external_identity_bindings"
    gateway_id: Mapped[UUID] = mapped_column(ForeignKey("gateway_services.id"))
    key_id: Mapped[UUID] = mapped_column(ForeignKey("api_keys.id"), unique=True)
    external_tenant_ref: Mapped[str] = mapped_column(String(128))
    external_key_ref: Mapped[str] = mapped_column(String(128))
    enabled: Mapped[bool] = mapped_column(default=True)
    revision: Mapped[int] = mapped_column(default=1)
    __table_args__ = scope_constraints(UniqueConstraint("gateway_id", "external_tenant_ref", "external_key_ref"), UniqueConstraint("gateway_id", "tenant_id", "project_id", "key_id", "id"), UniqueConstraint("gateway_id", "tenant_id", "project_id", "key_id", name="uq_external_bindings_gateway_key_scope"), ForeignKeyConstraint(["tenant_id", "project_id", "key_id"], ["api_keys.tenant_id", "api_keys.project_id", "api_keys.id"]))


class GatewayRequestFence(Entity, Base):
    __tablename__ = "gateway_request_fences"
    gateway_id: Mapped[UUID] = mapped_column(ForeignKey("gateway_services.id"))
    provider_request_id: Mapped[str] = mapped_column(String(128))
    external_task_ref: Mapped[str] = mapped_column(String(128))
    price_version: Mapped[str] = mapped_column(String(64))
    authorization_ref: Mapped[str] = mapped_column(String(128))
    external_tenant_ref: Mapped[str] = mapped_column(String(128))
    external_key_ref: Mapped[str] = mapped_column(String(128))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("gateway_id", "provider_request_id"), UniqueConstraint("gateway_id", "external_task_ref"))


class GatewayAdmission(Entity, Scoped, Base):
    __tablename__ = "gateway_admissions"
    gateway_id: Mapped[UUID] = mapped_column(ForeignKey("gateway_services.id"))
    binding_id: Mapped[UUID] = mapped_column(ForeignKey("external_identity_bindings.id"))
    key_id: Mapped[UUID] = mapped_column(ForeignKey("api_keys.id"))
    job_id: Mapped[UUID] = mapped_column(ForeignKey("jobs.id"), unique=True)
    provider_request_id: Mapped[str] = mapped_column(String(128))
    external_task_ref: Mapped[str] = mapped_column(String(128))
    intent_digest: Mapped[str] = mapped_column(String(64))
    price_version: Mapped[str] = mapped_column(String(64))
    catalog_revision: Mapped[int | None] = mapped_column()
    catalog_digest: Mapped[str | None] = mapped_column(String(64))
    catalog_instance_id: Mapped[str | None] = mapped_column(String(64))
    authorization_ref: Mapped[str] = mapped_column(String(128))
    key_revision: Mapped[int] = mapped_column()
    binding_revision: Mapped[int] = mapped_column()
    state: Mapped[str] = mapped_column(String(16), default="pending")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    __table_args__ = scope_constraints(UniqueConstraint("gateway_id", "provider_request_id"), UniqueConstraint("gateway_id", "external_task_ref"), ForeignKeyConstraint(["gateway_id", "tenant_id", "project_id", "key_id", "binding_id"], ["external_identity_bindings.gateway_id", "external_identity_bindings.tenant_id", "external_identity_bindings.project_id", "external_identity_bindings.key_id", "external_identity_bindings.id"]), ForeignKeyConstraint(["tenant_id", "project_id", "key_id", "job_id"], ["jobs.tenant_id", "jobs.project_id", "jobs.key_id", "jobs.id"]), CheckConstraint("state IN ('pending','confirmed','aborted','expired','rejected')", name="state"), ForeignKeyConstraint(["gateway_id", "catalog_revision", "catalog_digest", "catalog_instance_id"], ["gateway_catalog_releases.gateway_id", "gateway_catalog_releases.revision", "gateway_catalog_releases.digest", "gateway_catalog_releases.instance_id"], name="fk_gateway_admission_catalog"), CheckConstraint("(catalog_revision IS NULL AND catalog_digest IS NULL AND catalog_instance_id IS NULL) OR (catalog_revision IS NOT NULL AND catalog_digest IS NOT NULL AND catalog_instance_id IS NOT NULL)", name="catalog_stamp"))


class ClaimRequest(Entity, Base):
    __tablename__ = "claim_requests"
    node_id: Mapped[UUID] = mapped_column(ForeignKey("nodes.id"))
    boot_id: Mapped[UUID] = mapped_column()
    request_id: Mapped[str] = mapped_column(String(128))
    attempt_id: Mapped[UUID | None] = mapped_column(ForeignKey("attempts.id"))
    empty: Mapped[bool] = mapped_column(default=False)
    __table_args__ = (UniqueConstraint("node_id", "boot_id", "request_id"),)
