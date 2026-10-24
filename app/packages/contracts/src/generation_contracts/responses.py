from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from generation_contracts.admin_sessions import SessionLifetime
from generation_contracts.alert_queries import AlertFailure, AlertKind
from generation_contracts.capacity import WorkerCapacity
from generation_contracts.content_management import ContentState
from generation_contracts.dispatch import DispatchPolicy
from generation_contracts.model_files import ModelFilesView
from generation_contracts.operational import AttemptTimingView, JobTimingView
from generation_contracts.retries import RetryInfo
from generation_contracts.validation import KeyPolicy


class UserView(BaseModel):
    id: str
    username: str
    role: str


class AdminUserView(UserView):
    enabled: bool
    revision: int
    created_at: int


class AdminJobView(BaseModel):
    id: str
    model: str
    task_type: str
    status: str
    created_at: int
    updated_revision: int
    error: dict[str, str] | None
    completed_at: int | None = None
    tenant_id: str
    project_id: str
    key_id: str
    priority: int
    is_validation: bool
    cancellation_requested: bool
    retry_of: str | None
    release_id: str | None = None
    accepted_at: int | None = None
    first_assigned_at: int | None = None
    queue_wait_seconds: int | None = None
    retry: RetryInfo | None = None


class AdminJobsPage(BaseModel):
    data: list[AdminJobView]
    page: int
    has_more: bool
    snapshot_at: int
    filters: dict[str, str | int]


class AttemptView(BaseModel):
    id: str
    node_id: str
    channel_id: str
    gpu_id: str
    fence: int
    lease_expires_at: int
    generation_released_at: int | None
    closed_at: int | None
    has_prompt_id: bool
    has_generation_evidence: bool
    gpu_occupied: bool
    occupancy_uncertain: bool
    timing: AttemptTimingView | None = None


class AdminJobDetail(AdminJobView):
    current_attempt_id: str | None
    release_id: str
    attempts: list[AttemptView]
    retries: list[str]
    artifacts: list[str]
    input: dict[str, Any] | None = None
    timing: JobTimingView | None = None
    attempts_has_more: bool = False


class ScopeView(BaseModel):
    tenant_id: str
    tenant_name: str
    project_id: str
    project_name: str


class SessionView(BaseModel):
    user: UserView
    csrf_token: str
    permissions: list[str]
    scopes: list[ScopeView]
    environment: str | None = None
    storage_backend: str | None = None
    lifetime: SessionLifetime | None = None


class ChannelView(BaseModel):
    id: str
    tenant_id: str
    project_id: str
    name: str
    node_id: str
    gpu_id: str
    base_url: str
    has_authorization: bool
    enabled: bool
    draining: bool
    revision: int
    desired_revision: int
    applied_revision: int | None
    verified_revision: int | None
    deleted_at: int | None
    observed_at: int | None


class ChannelRevisionView(BaseModel):
    revision: int
    created_at: int
    base_url: str
    has_authorization: bool


class ChannelDetailView(ChannelView):
    revisions: list[ChannelRevisionView]


class ChannelMutation(BaseModel):
    channel: ChannelView
    operation_id: str | None = None
    status: str | None = None


class Page[T](BaseModel):
    data: list[T]
    page: int
    has_more: bool


class ContentView(BaseModel):
    id: str
    kind: Literal["input", "output"]
    tenant_id: str
    project_id: str
    key_id: str
    job_id: str | None
    bytes: int
    media_type: str
    sha256: str
    expires_at: int
    created_at: int
    deleted_at: int | None
    object_deleted_at: int | None
    metadata: dict[str, Any]

    tags: list[str] = Field(default_factory=list)
    metadata_revision: int = 1
    metadata_updated_at: int | None = None
    status: ContentState | None = None
    preview_supported: bool = False


class AdminContentPage(BaseModel):
    data: list[ContentView]
    page: int
    has_more: bool
    snapshot_at: int
    filters: dict[str, str | int]


class ContentDeletion(BaseModel):
    id: str
    status: Literal["delete_requested"]
    physical_delete_complete: bool


class GpuView(BaseModel):
    id: str
    name: str
    hardware_id: str
    memory_bytes: int | None
    enabled: bool


class NodeView(BaseModel):
    id: str
    name: str
    enabled: bool
    observed_at: int | None
    gpus: list[GpuView]


class NodesView(BaseModel):
    data: list[NodeView]


class NodeDetail(NodeView):
    channels: list[ChannelView]
    gpus_truncated: bool
    channels_truncated: bool


class AlertView(BaseModel):
    id: str
    kind: AlertKind
    severity: Literal["critical", "warning"]
    resource_id: str
    recorded_at: int
    capacity: WorkerCapacity | None = None
    capacity_observed_at: int | None = None
    failure: AlertFailure | None = None


class AlertSnapshot(BaseModel):
    snapshot_at: int
    data: list[AlertView]
    total: int
    page: int
    has_more: bool


class KeyView(BaseModel):
    id: str
    tenant_id: str
    project_id: str
    name: str
    enabled: bool
    revision: int
    deleted_at: int | None
    expires_at: int | None
    issuer: str
    external_ref: str | None
    policy: KeyPolicy
    created_at: int


class KeysView(Page[KeyView]):
    default_policy: KeyPolicy


class KeyMutation(BaseModel):
    key: KeyView
    credential_id: str | None = None
    secret: str | None = None
    secret_available: bool | None = None


class ChannelQueueView(ChannelView):
    shared_candidates: int
    assigned_unfinished: int
    gpu_occupied: bool
    gpu_occupancy_uncertain: bool
    gpu_management_hold: bool
    observation_status: str
    queue_observed_at: int | None
    comfy_queue_running: int | None
    comfy_queue_pending: int | None


class BindingStatus(BaseModel):
    release_id: str
    desired_digest: str
    applied_digest: str | None
    verified_digest: str | None
    observed_at: int | None
    runtime_approved: bool


class InventoryView(BaseModel):
    model_files: ModelFilesView | None = None
    channel_id: str
    observed_at: int | None
    stale: bool
    inventory: dict[str, Any]
    bindings: list[BindingStatus]


class QueueView(BaseModel):
    dispatch_policy: DispatchPolicy | None = None
    snapshot_id: str
    snapshot_at: int
    server_time: int
    observed_at: int
    stale: bool
    filters: dict[str, Any]
    global_unassigned_queued: int
    unfinished: int
    occupied_physical_gpus: int
    states: dict[str, int]
    channels: list[ChannelQueueView]
    channels_page: int
    channels_has_more: bool
    candidate_counts_overlap: bool


class OperationView(BaseModel):
    id: str
    command: str
    status: str
    revision: int
    created_at: int
    summary: dict[str, Any]


class OperationReceipt(BaseModel):
    success: bool | None = None
    applied_revision: int | None = None
    environment_verified: bool | None = None
    release_digest: str | None = None
    error_code: str | None = None
    source_digest: str | None = None
    export_format: str | None = None
    source_bytes: int | None = None


class OperationRecovery(BaseModel):
    resolution: Literal["result_requires_revalidation"]
    reconciled_at: int


class OperationTarget(BaseModel):
    id: str
    channel_id: str
    status: str
    receipt: OperationReceipt | None
    node_id: str | None = None
    lease_expires_at: int | None = None
    completed_at: int | None = None
    needs_reconciliation: bool = False
    reason: Literal["expired", "previous_boot", "unknown_lease"] | None = None
    recovery: OperationRecovery | None = None


class OperationDetail(BaseModel):
    id: str
    status: str
    command: str
    targets: list[OperationTarget]
    has_more: bool
    snapshot_at: int | None = None
    retry_of: str | None = None


class AuditView(BaseModel):
    id: str
    actor: str
    action: str
    resource: str
    request_id: str
    created_at: int
    detail: dict[str, Any]
    actor_name: str | None = None
    revision_before: int | None = None
    revision_after: int | None = None
    operation_id: str | None = None
    detail_redacted: bool = False


class AuditSnapshot(BaseModel):
    data: list[AuditView]
    page: int
    has_more: bool
    total: int
    snapshot_at: int
    filters: dict[str, str | int]


class WorkflowView(BaseModel):
    id: str
    tenant_id: str
    project_id: str
    name: str
    task_type: str
    revision: int
    archived: bool
    created_at: int


class WorkflowDetail(WorkflowView):
    draft: dict[str, Any]
    releases: list[dict[str, Any]]
