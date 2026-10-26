import asyncio
import logging
import os
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, File, Header, Query, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from generation_application.admin_access import require_permission
from generation_application.ports import (
    AdminIdentity,
    BusinessIdentity,
    GatewayIdentity,
    GenerationService,
)
from generation_contracts.access_events import AccessResult, KeyAccessSnapshot
from generation_contracts.admin import (
    ROLE_PERMISSIONS,
    AdminUserCreate,
    AdminUserPatch,
    ChannelAction,
    ChannelCreate,
    ChannelPatch,
    ExpectedRevision,
    JobPriority,
    KeyAction,
    KeyCreate,
    KeyPatch,
    Login,
    WorkflowCreate,
    WorkflowPatch,
)
from generation_contracts.admin_sessions import SessionActivity, SessionLifetime
from generation_contracts.alert_queries import AlertQuery
from generation_contracts.audit_queries import AuditQuery
from generation_contracts.audit_retention import AuditRetentionView
from generation_contracts.catalog import (
    ApproveRuntime,
    BindingView,
    BindRelease,
    FrozenReleaseView,
    ModelAction,
    ModelCreate,
    ModelPatch,
    ModelView,
    OperationAccepted,
    ReleaseValidation,
    RuntimeApprovalView,
    ValidationAccepted,
    ValidationJob,
    WorkflowSync,
)
from generation_contracts.channel_deletion import ChannelDeletionCheck
from generation_contracts.channel_queries import ChannelQuery
from generation_contracts.collections import CollectionQuery, ContentSummary, CursorPage, JobSummary
from generation_contracts.command_recovery import (
    CommandNext,
    CommandReconciliation,
    CommandReconciliationResult,
)
from generation_contracts.commands import CommandClaim, CommandReceipt
from generation_contracts.content_management import ContentOptionQuery, ContentQuery, ContentTags
from generation_contracts.discovery import PublishedCapabilities, PublishedModel, PublishedModels
from generation_contracts.job_cancellations import JobCancellationBatch, JobCancellationView
from generation_contracts.key_generation_options import KeyGenerationOptions, KeyGenerationQuery
from generation_contracts.key_policy_options import KeyPolicyOptionQuery, KeyPolicyOptions
from generation_contracts.model_artifacts import (
    ModelArtifactCreate,
    ModelArtifactDeletion,
    ModelArtifactPatch,
    ModelArtifactView,
)
from generation_contracts.model_catalog import (
    ModelCatalogPage,
    ModelCatalogQuery,
    ModelMappingOptions,
    ModelMappingQuery,
)
from generation_contracts.model_files import (
    ModelFileReadRequest,
    ModelFileReadView,
    ModelFileRegistration,
)
from generation_contracts.monitoring import MonitoringConfiguration
from generation_contracts.operational import QueueStatistics, QueueStatisticsQuery
from generation_contracts.outputs import Complete, UploadAuthorization
from generation_contracts.queue_queries import QueueOptionQuery, QueueOptions, QueueQuery
from generation_contracts.recovery import Reconciliation
from generation_contracts.responses import (
    AdminContentPage,
    AdminJobDetail,
    AdminJobsPage,
    AdminJobView,
    AdminUserView,
    AlertSnapshot,
    AuditSnapshot,
    ChannelDetailView,
    ChannelMutation,
    ChannelQueueView,
    ChannelView,
    ContentDeletion,
    ContentView,
    InventoryView,
    KeyMutation,
    KeysView,
    KeyView,
    NodeDetail,
    NodesView,
    OperationDetail,
    OperationView,
    Page,
    QueueView,
    SessionView,
    WorkflowDetail,
    WorkflowView,
)
from generation_contracts.scope_administration import (
    ProjectCreate,
    ProjectView,
    ScopeOption,
    ScopePage,
    ScopePatch,
    ScopeQuery,
    TenantCreate,
    TenantView,
)
from generation_contracts.spec_isolation import (
    SpecIsolationAction,
    SpecIsolationCreate,
    SpecIsolationPage,
    SpecIsolationView,
)
from generation_contracts.validation import JobCreate, WorkerEvent
from generation_contracts.wait_estimates import WaitEstimate
from generation_contracts.worker import AttemptStatus, Claim, Heartbeat, Register, Renew
from generation_contracts.worker_nodes import (
    WorkerNodeAction,
    WorkerNodeCreate,
    WorkerNodeDetail,
    WorkerNodeMutation,
    WorkerNodePage,
    WorkerNodePatch,
)
from generation_contracts.workflow_catalogs import (
    WorkflowCatalogRequest,
    WorkflowCatalogView,
    WorkflowExportStatus,
)
from generation_contracts.workflow_directory import (
    WorkflowDirectoryPage,
    WorkflowDirectoryQuery,
    WorkflowSyncOptions,
    WorkflowSyncOptionsQuery,
)
from generation_contracts.workflow_history import (
    WorkflowDiff,
    WorkflowDiffQuery,
    WorkflowHistoryPage,
    WorkflowHistoryQuery,
)
from generation_contracts.workflow_imports import (
    WorkflowImportCommit,
    WorkflowImportPreview,
    WorkflowImportPreviewView,
    WorkflowImportResult,
    WorkflowPull,
)
from generation_contracts.workflow_lifecycle import WorkflowLifecycle, WorkflowLifecycleResult
from generation_contracts.workflow_restoration import WorkflowDraftRestore, WorkflowDraftRestored
from generation_contracts.workflow_retries import WorkflowSyncRetry, WorkflowSyncRetryOptions
from generation_domain.errors import DomainError
from generation_domain.values import parse_id, public_id
from generation_infrastructure.access_events import (
    AccessEventQueries,
    AccessEventWriter,
    AccessSubject,
)
from generation_infrastructure.admin_alerts import AlertQueries
from generation_infrastructure.admin_audit import AuditQueries
from generation_infrastructure.admin_catalog import CatalogAdministration
from generation_infrastructure.admin_channels import ChannelAdministration
from generation_infrastructure.admin_gateways import GatewayAdministration
from generation_infrastructure.admin_jobs import JobAdministration
from generation_infrastructure.admin_keys import KeyAdministration
from generation_infrastructure.admin_model_artifacts import ModelArtifactAdministration
from generation_infrastructure.admin_model_files import ModelFileAdministration
from generation_infrastructure.admin_nodes import NodeAdministration
from generation_infrastructure.admin_queries import AdminQueries
from generation_infrastructure.admin_scopes import ScopeAdministration
from generation_infrastructure.admin_spec_isolation import SpecIsolationAdministration
from generation_infrastructure.admin_users import UserAdministration
from generation_infrastructure.admin_workflow_catalogs import WorkflowCatalogAdministration
from generation_infrastructure.admin_workflow_imports import WorkflowImportAdministration
from generation_infrastructure.admin_workflow_retries import WorkflowSyncRetries
from generation_infrastructure.admin_workflows import WorkflowAdministration
from generation_infrastructure.audit_retention import retention_snapshot
from generation_infrastructure.auth import AuthRepository
from generation_infrastructure.commands import CommandRepository
from generation_infrastructure.content import ContentRepository
from generation_infrastructure.cursors import CursorCodec
from generation_infrastructure.database import make_sessions
from generation_infrastructure.database_diagnostics import (
    database_dependency_unavailable,
    record_database_failure,
)
from generation_infrastructure.gateway import GatewayRepository
from generation_infrastructure.job_cancellations import JobCancellations
from generation_infrastructure.jobs import JobRepository
from generation_infrastructure.key_generation_options import KeyGenerationDirectory
from generation_infrastructure.key_policy_options import KeyPolicyDirectory
from generation_infrastructure.leases import LeaseRepository
from generation_infrastructure.model_mapping_options import ModelMappingReads
from generation_infrastructure.monitoring import (
    MonitoringService,
    configuration,
    monitoring_authorized,
)
from generation_infrastructure.monitoring_snapshot import SnapshotUnavailable
from generation_infrastructure.operational import OperationalQueries
from generation_infrastructure.outputs import OutputRepository
from generation_infrastructure.recovery import RecoveryRepository
from generation_infrastructure.security import SecretVault, secret_identifier
from generation_infrastructure.settings import Settings
from generation_infrastructure.storage import make_store
from generation_infrastructure.validation_jobs import ValidationJobs
from generation_infrastructure.wait_estimates import WaitEstimates
from generation_infrastructure.workflow_directory import WorkflowDirectory
from generation_infrastructure.workflow_history import WorkflowHistory
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError
from starlette.exceptions import HTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from control_api.access import AccessEventMiddleware, normalized_ip
from control_api.collections import content_query, job_query
from control_api.compatibility import compatibility_routes
from control_api.gateway import gateway_routes
from control_api.limits import BodyLimitMiddleware, body_limit
from control_api.monitoring import ObservedAPI, RequestLog
from control_api.static import AdminFiles
from control_api.uploads import upload_token

COOKIE = "gen_admin"
logger = logging.getLogger("render_control")


def etag(response: Response, revision: int) -> None:
    response.headers["ETag"] = f'"{revision}"'


def expected_revision(request: Request, revision: int) -> None:
    supplied = request.headers.get("if-match")
    if supplied is None:
        raise DomainError("precondition_required", "修改配置必须提供 If-Match", 428)
    if supplied != f'"{revision}"':
        raise DomainError("config_revision_conflict", "If-Match 与请求版本不一致")


def bearer(request: Request) -> str:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise DomainError("invalid_credentials", "必须提供 Bearer 凭证", 401)
    return token


def admin_token(request: Request) -> str:
    value = request.headers.get("idempotency-key", "")
    if not 16 <= len(value) <= 128 or not value.isascii() or any(ord(c) < 33 or ord(c) > 126 for c in value):
        raise DomainError("idempotency_required", "管理写操作要求 16～128 位 ASCII Idempotency-Key", 400)
    return value


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    sessions = make_sessions(settings)
    auth = AuthRepository(sessions, settings)
    channels = ChannelAdministration(sessions, settings)
    worker_nodes = NodeAdministration(sessions, settings)
    keys = KeyAdministration(sessions, settings)
    queries = AdminQueries(sessions, settings)
    operations = OperationalQueries(sessions, settings)
    alerts = AlertQueries(sessions, settings)
    audits = AuditQueries(sessions, settings)
    admin_jobs = JobAdministration(sessions, settings)
    cancellations = JobCancellations(sessions, settings)
    users = UserAdministration(sessions, settings)
    scope_admin = ScopeAdministration(sessions, settings)
    key_policy_directory = KeyPolicyDirectory(sessions, settings)
    key_generation_directory = KeyGenerationDirectory(sessions, settings)
    model_artifacts = ModelArtifactAdministration(sessions, settings)
    model_files = ModelFileAdministration(sessions, settings)
    spec_isolations = SpecIsolationAdministration(sessions, settings)
    workflows = WorkflowAdministration(sessions, settings)
    workflow_directory = WorkflowDirectory(sessions, settings)
    workflow_history = WorkflowHistory(sessions, settings)
    workflow_imports = WorkflowImportAdministration(sessions, settings)
    workflow_catalogs = WorkflowCatalogAdministration(sessions, settings)
    catalog = CatalogAdministration(sessions, settings)
    model_mapping_reads = ModelMappingReads(sessions, settings)
    workflow_retries = WorkflowSyncRetries(sessions, settings)
    validation_jobs = ValidationJobs(sessions, settings)
    jobs = GenerationService(JobRepository(sessions))
    leases = LeaseRepository(sessions, SecretVault(settings.encryption_key.get_secret_value()), allow_synthetic_catalog=settings.environment == "test")
    commands = CommandRepository(sessions, SecretVault(settings.encryption_key.get_secret_value()))
    store = make_store(settings)
    content = ContentRepository(sessions, store)
    cursors = CursorCodec(settings.encryption_key.get_secret_value())
    outputs = OutputRepository(sessions, store, leases, settings.spool_root)
    recovery = RecoveryRepository(leases)
    access_writer = AccessEventWriter(settings)
    access_queries = AccessEventQueries(sessions, settings.access_log_retention_days)
    monitoring = MonitoringService(settings)
    request_log = RequestLog(monitoring.process)
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            if check_bucket := getattr(store, "check_private_versioned_bucket", None):
                await asyncio.to_thread(check_bucket)
            request_log.start()
            yield
        finally:
            await access_writer.close()
            await asyncio.to_thread(request_log.close)
            await asyncio.to_thread(monitoring.close)
            await asyncio.to_thread(sessions.kw["bind"].dispose)

    app = ObservedAPI(metrics=monitoring.process, request_log=request_log, allow_any_origin=settings.allow_any_origin, title="Render Control", version="0.1.0", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.sessions = sessions
    app.state.settings = settings
    app.state.access_writer = access_writer
    app.state.monitoring = monitoring
    app.state.request_log = request_log
    app.add_middleware(BodyLimitMiddleware)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[__import__("urllib.parse", fromlist=["urlparse"]).urlparse(settings.public_origin).hostname])

    @app.middleware("http")
    async def request_context(request: Request, call_next: Callable[..., Any]) -> Response:
        request.state.request_id = getattr(request.state, "request_id", None) or str(uuid4())
        length = request.headers.get("content-length")
        maximum = body_limit(request.url.path, request.headers.get("content-type", ""))
        if length and (len(length) > 20 or not length.isascii() or not length.isdecimal() or int(length) > maximum):
            request.state.access_error_code = "body_too_large"
            response: Response = JSONResponse({"error": {"code": "body_too_large", "message": "请求体超过限制", "request_id": request.state.request_id}}, status_code=413)
        else:
            response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers.setdefault("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'")
        return response

    @app.exception_handler(DomainError)
    async def domain_error(request: Request, exc: DomainError) -> JSONResponse:
        request.state.access_error_code = exc.code
        return JSONResponse({"error": {"code": exc.code, "message": exc.message, "request_id": request.state.request_id}}, status_code=exc.status, headers={"Retry-After": "2"} if exc.status == 429 else {})

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        request.state.access_error_code = "invalid_request"
        # FastAPI's default error includes the original body (passwords/prompts).
        return JSONResponse({"error": {"code": "invalid_request", "message": "请求字段校验失败", "fields": [{"path": list(e["loc"]), "type": e["type"]} for e in exc.errors()], "request_id": request.state.request_id}}, status_code=422)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        code = "body_too_large" if exc.status_code == 413 else "http_error"
        request.state.access_error_code = code
        return JSONResponse({"error": {"code": code, "message": "请求体超过限制" if exc.status_code == 413 else "请求无法处理", "request_id": request.state.request_id}}, status_code=exc.status_code)

    @app.exception_handler(IntegrityError)
    async def integrity_error(request: Request, exc: IntegrityError) -> JSONResponse:
        request.state.access_error_code = "resource_conflict"
        return JSONResponse({"error": {"code": "resource_conflict", "message": "资源冲突或关联约束不满足", "request_id": request.state.request_id}}, status_code=409)

    def dependency_unavailable(request: Request) -> JSONResponse:
        request.state.access_error_code = "dependency_unavailable"
        return JSONResponse({"error": {"code": "dependency_unavailable", "message": "服务暂时不可用，请稍后核对原请求", "request_id": request.state.request_id}}, status_code=503, headers={"Retry-After": "2"})

    @app.exception_handler(SQLAlchemyTimeoutError)
    async def database_pool_error(request: Request, exc: SQLAlchemyTimeoutError) -> JSONResponse:
        logger.error("database_pool_unavailable request_id=%s", request.state.request_id)
        return dependency_unavailable(request)

    @app.exception_handler(DBAPIError)
    async def database_error(request: Request, exc: DBAPIError) -> JSONResponse:
        record_database_failure("http_request", exc)
        if database_dependency_unavailable(exc):
            return dependency_unavailable(request)
        request.state.access_error_code = "internal_error"
        return JSONResponse({"error": {"code": "internal_error", "message": "服务处理失败", "request_id": request.state.request_id}}, status_code=500)

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception) -> JSONResponse:
        request.state.access_error_code = "internal_error"
        logger.error("request_failed request_id=%s exception=%s", request.state.request_id, type(exc).__name__)
        return JSONResponse({"error": {"code": "internal_error", "message": "服务处理失败", "request_id": request.state.request_id}}, status_code=500)

    def admin(request: Request) -> AdminIdentity:
        return auth.admin(request.cookies.get(COOKIE, ""), write=request.method not in ("GET", "HEAD", "OPTIONS"), origin=request.headers.get("origin"), csrf=request.headers.get("x-csrf-token"))

    def permission(name: str) -> Callable[..., AdminIdentity]:
        def check(identity: Annotated[AdminIdentity, Depends(admin)]) -> AdminIdentity:
            require_permission(identity, name)
            return identity
        return check

    def business(request: Request) -> BusinessIdentity:
        peer = request.client.host if request.client else "127.0.0.1"
        def remember(identity: BusinessIdentity) -> None:
            request.state.access_subject = AccessSubject(identity.tenant_id, identity.project_id, identity.key_id, normalized_ip(peer))
        return auth.business(bearer(request), peer, verified=remember)

    def worker(request: Request, x_worker_node_id: Annotated[str | None, Header(alias="X-Worker-Node-Id", min_length=37, max_length=37, pattern=r"^node_[0-9a-f]{32}$")] = None) -> UUID:
        identity = auth.worker(bearer(request))
        if x_worker_node_id is not None and x_worker_node_id != public_id("node", identity):
            raise DomainError("worker_identity_mismatch", "Worker 节点与认证凭证不匹配", 401)
        return identity

    def gateway(request: Request) -> GatewayIdentity:
        service = auth.gateway(bearer(request), request.client.host if request.client else "127.0.0.1")
        request.state.access_gateway = service
        return service

    @app.get("/health/live")
    def live() -> dict[str, Any]:
        return {"status": "ok", "environment": settings.environment, "storage_backend": settings.storage_backend}

    @app.get("/health/ready")
    def ready() -> dict[str, str]:
        with sessions() as session:
            session.execute(text("SELECT 1"))
        return {"status": "ok"}

    @app.post("/admin/api/v1/session", response_model=SessionView)
    def login(data: Login, request: Request, response: Response) -> dict[str, Any]:
        if not settings.accepts_admin_origin(request.headers.get("origin")):
            raise DomainError("origin_denied", "管理登录来源不匹配", 403)
        result = auth.login(data.username, data.password, request.client.host if request.client else "127.0.0.1", request.state.request_id)
        response.set_cookie(COOKIE, result.pop("token"), max_age=settings.session_seconds, secure=settings.secure_cookies, httponly=True, samesite=settings.cookie_same_site, path="/admin")
        return {**result, "permissions": ROLE_PERMISSIONS[result["user"]["role"]], "scopes": queries.scopes()}

    @app.get("/admin/api/v1/session", response_model=SessionView)
    def session_info(request: Request, identity: Annotated[AdminIdentity, Depends(admin)]) -> dict[str, Any]:
        result = auth.snapshot(request.cookies[COOKIE])
        return {**result, "permissions": ROLE_PERMISSIONS[result["user"]["role"]], "scopes": queries.scopes(), "environment": settings.environment, "storage_backend": settings.storage_backend}

    @app.post("/admin/api/v1/session/activity", response_model=SessionLifetime)
    def session_activity(data: SessionActivity, request: Request, identity: Annotated[AdminIdentity, Depends(admin)]) -> SessionLifetime:
        return auth.activity(request.cookies[COOKIE], origin=request.headers.get("origin"), csrf=request.headers.get("x-csrf-token"))

    @app.delete("/admin/api/v1/session", status_code=204)
    def logout(request: Request, response: Response, identity: Annotated[AdminIdentity, Depends(admin)]) -> None:
        auth.logout(identity, request.state.request_id)
        response.delete_cookie(COOKIE, path="/admin", secure=settings.secure_cookies, httponly=True, samesite=settings.cookie_same_site)

    @app.get("/admin/api/v1/nodes", response_model=NodesView)
    def nodes(identity: Annotated[AdminIdentity, Depends(permission("nodes:read"))]) -> dict[str, Any]:
        return channels.list_nodes()

    @app.get("/admin/api/v1/nodes/{identifier}", response_model=NodeDetail)
    def node_detail(identifier: str, identity: Annotated[AdminIdentity, Depends(permission("nodes:read"))]) -> dict[str, Any]:
        return channels.get_node(parse_id("node", identifier))

    @app.get("/admin/api/v1/worker-nodes", response_model=WorkerNodePage)
    def worker_node_list(identity: Annotated[AdminIdentity, Depends(permission("nodes:read"))], page: int = Query(1, ge=1, le=10000), q: str = Query("", max_length=128), state: Literal["enabled", "disabled", "archived"] | None = None) -> dict[str, Any]:
        return worker_nodes.list_nodes(page, q.strip(), state)

    @app.post("/admin/api/v1/worker-nodes", status_code=201, response_model=WorkerNodeMutation)
    def worker_node_create(data: WorkerNodeCreate, request: Request, response: Response, identity: Annotated[AdminIdentity, Depends(permission("nodes:manage"))]) -> dict[str, Any]:
        response.headers["Cache-Control"] = "no-store"
        result = worker_nodes.create_node(identity, data, admin_token(request), request.state.request_id)
        etag(response, result["node"]["revision"])
        return result

    @app.get("/admin/api/v1/worker-nodes/{identifier}", response_model=WorkerNodeDetail)
    def worker_node_detail(identifier: str, response: Response, identity: Annotated[AdminIdentity, Depends(permission("nodes:read"))]) -> dict[str, Any]:
        result = worker_nodes.get_node(parse_id("node", identifier))
        etag(response, result["revision"])
        return result

    @app.patch("/admin/api/v1/worker-nodes/{identifier}", response_model=WorkerNodeMutation)
    def worker_node_patch(identifier: str, data: WorkerNodePatch, request: Request, response: Response, identity: Annotated[AdminIdentity, Depends(permission("nodes:manage"))]) -> dict[str, Any]:
        token = admin_token(request)
        expected_revision(request, data.expected_revision)
        result = worker_nodes.patch_node(identity, parse_id("node", identifier), data, token, request.state.request_id)
        etag(response, result["node"]["revision"])
        return result

    @app.post("/admin/api/v1/worker-nodes/{identifier}/actions", response_model=WorkerNodeMutation)
    def worker_node_action(identifier: str, data: WorkerNodeAction, request: Request, response: Response, identity: Annotated[AdminIdentity, Depends(permission("nodes:manage"))]) -> dict[str, Any]:
        token = admin_token(request)
        expected_revision(request, data.expected_revision)
        response.headers["Cache-Control"] = "no-store"
        result = worker_nodes.action(identity, parse_id("node", identifier), data, token, request.state.request_id)
        etag(response, result["node"]["revision"])
        return result

    @app.get("/admin/api/v1/channels", response_model=Page[ChannelView])
    def channel_list(identity: Annotated[AdminIdentity, Depends(permission("channels:read"))], query: Annotated[ChannelQuery, Query()]) -> dict[str, Any]:
        return channels.list_channels(query=query)

    @app.get("/admin/api/v1/channels/{identifier}", response_model=ChannelDetailView)
    def channel_detail(identifier: str, response: Response, identity: Annotated[AdminIdentity, Depends(permission("channels:read"))]) -> dict[str, Any]:
        result = channels.get_channel(parse_id("ch", identifier))
        etag(response, result["revision"])
        return result

    @app.get("/admin/api/v1/channels/{identifier}/deletion-check", response_model=ChannelDeletionCheck)
    def channel_deletion_check(identifier: str, response: Response, identity: Annotated[AdminIdentity, Depends(permission("channels:write"))]) -> dict[str, Any]:
        result = channels.deletion_check(parse_id("ch", identifier))
        etag(response, result["revision"])
        return result

    @app.post("/admin/api/v1/channels", status_code=201, response_model=ChannelMutation)
    def channel_create(data: ChannelCreate, request: Request, identity: Annotated[AdminIdentity, Depends(permission("channels:write"))]) -> dict[str, Any]:
        return channels.create_channel(identity, data, admin_token(request), request.state.request_id)

    @app.patch("/admin/api/v1/channels/{identifier}", response_model=ChannelMutation)
    def channel_patch(identifier: str, data: ChannelPatch, request: Request, identity: Annotated[AdminIdentity, Depends(permission("channels:write"))]) -> dict[str, Any]:
        admin_token(request)
        expected_revision(request, data.expected_revision)
        return channels.patch_channel(identity, parse_id("ch", identifier), data, request.state.request_id)

    @app.post("/admin/api/v1/channels/{identifier}/actions", response_model=ChannelView)
    def channel_action(identifier: str, data: ChannelAction, request: Request, identity: Annotated[AdminIdentity, Depends(permission("channels:write"))]) -> dict[str, Any]:
        admin_token(request)
        expected_revision(request, data.expected_revision)
        return channels.channel_action(identity, parse_id("ch", identifier), data, request.state.request_id)

    @app.post("/admin/api/v1/channels/{identifier}/reapply", status_code=202, response_model=ChannelMutation)
    def channel_reapply(identifier: str, data: ExpectedRevision, request: Request, identity: Annotated[AdminIdentity, Depends(permission("channels:write"))]) -> dict[str, Any]:
        token = admin_token(request)
        expected_revision(request, data.expected_revision)
        return channels.reapply_channel(identity, parse_id("ch", identifier), data, token, request.state.request_id)

    @app.delete("/admin/api/v1/channels/{identifier}", response_model=ChannelView)
    def channel_delete(identifier: str, request: Request, identity: Annotated[AdminIdentity, Depends(permission("channels:write"))], revision: int = Query(ge=1)) -> dict[str, Any]:
        admin_token(request)
        expected_revision(request, revision)
        return channels.delete_channel(identity, parse_id("ch", identifier), revision, request.state.request_id)

    @app.post("/admin/api/v1/channels/{identifier}/probe", status_code=202)
    def channel_probe(identifier: str, request: Request, identity: Annotated[AdminIdentity, Depends(permission("channels:write"))]) -> dict[str, Any]:
        return channels.channel_command(identity, parse_id("ch", identifier), "probe_channel", admin_token(request), request.state.request_id)

    @app.post("/admin/api/v1/channels/{identifier}/inventory-scans", status_code=202)
    def channel_scan(identifier: str, request: Request, identity: Annotated[AdminIdentity, Depends(permission("channels:write"))]) -> dict[str, Any]:
        return channels.channel_command(identity, parse_id("ch", identifier), "scan_inventory", admin_token(request), request.state.request_id)

    @app.get("/admin/api/v1/channels/{identifier}/queue", response_model=ChannelQueueView)
    def channel_queue(identifier: str, identity: Annotated[AdminIdentity, Depends(permission("queues:read"))]) -> dict[str, Any]:
        return queries.channel_queue(parse_id("ch", identifier))

    @app.get("/admin/api/v1/channels/{identifier}/inventory", response_model=InventoryView)
    def channel_inventory(identifier: str, identity: Annotated[AdminIdentity, Depends(permission("channels:read"))], model_page: int = Query(1, ge=1, le=20)) -> dict[str, Any]:
        return queries.inventory(parse_id("ch", identifier), model_page)

    @app.get("/admin/api/v1/spec-isolations", response_model=SpecIsolationPage)
    def spec_isolation_list(identity: Annotated[AdminIdentity, Depends(permission("channels:read"))], page: int = Query(1, ge=1, le=10000), state: Literal["all", "isolated", "watching", "clear"] = "all", gpu_id: str | None = None, release_id: str | None = None) -> dict[str, Any]:
        return spec_isolations.list(page, state, parse_id("gpu", gpu_id) if gpu_id else None, parse_id("wfr", release_id) if release_id else None)

    @app.post("/admin/api/v1/spec-isolations", status_code=201, response_model=SpecIsolationView)
    def spec_isolation_create(data: SpecIsolationCreate, request: Request, identity: Annotated[AdminIdentity, Depends(permission("channels:write"))]) -> dict[str, Any]:
        expected_revision(request, data.expected_revision)
        return spec_isolations.create(identity, data, admin_token(request), request.state.request_id)

    @app.post("/admin/api/v1/spec-isolations/{identifier}/actions", response_model=SpecIsolationView)
    def spec_isolation_action(identifier: str, data: SpecIsolationAction, request: Request, identity: Annotated[AdminIdentity, Depends(permission("channels:write"))]) -> dict[str, Any]:
        expected_revision(request, data.expected_revision)
        return spec_isolations.action(identity, parse_id("iso", identifier), data, admin_token(request), request.state.request_id)

    @app.post("/admin/api/v1/model-file-reads", status_code=202)
    def model_file_read(data: ModelFileReadRequest, request: Request, identity: Annotated[AdminIdentity, Depends(permission("models:write"))]) -> dict[str, Any]:
        return model_files.create(identity, data, admin_token(request), request.state.request_id)

    @app.get("/admin/api/v1/model-file-reads/{identifier}", response_model=ModelFileReadView)
    def model_file_detail(identifier: str, identity: Annotated[AdminIdentity, Depends(permission("models:read"))]) -> dict[str, Any]:
        return model_files.detail(identity, parse_id("op", identifier))

    @app.post("/admin/api/v1/model-file-reads/{identifier}/registrations", status_code=201, response_model=ModelArtifactView)
    def model_file_register(identifier: str, data: ModelFileRegistration, request: Request, identity: Annotated[AdminIdentity, Depends(permission("models:write"))]) -> dict[str, Any]:
        return model_files.register(identity, parse_id("op", identifier), data, admin_token(request), request.state.request_id)

    @app.get("/admin/api/v1/api-keys", response_model=KeysView)
    def key_list(identity: Annotated[AdminIdentity, Depends(permission("keys:manage"))], page: int = Query(1, ge=1)) -> dict[str, Any]:
        return keys.list_keys(page)

    @app.get("/admin/api/v1/api-keys/{identifier}")
    def key_detail(identifier: str, response: Response, identity: Annotated[AdminIdentity, Depends(permission("keys:manage"))]) -> dict[str, Any]:
        result = keys.get_key(parse_id("key", identifier))
        etag(response, result["revision"])
        return result

    @app.post("/admin/api/v1/api-keys", status_code=201, response_model=KeyMutation)
    def key_create(data: KeyCreate, request: Request, identity: Annotated[AdminIdentity, Depends(permission("keys:manage"))]) -> dict[str, Any]:
        return keys.create_key(identity, data, admin_token(request), request.state.request_id)

    @app.patch("/admin/api/v1/api-keys/{identifier}", response_model=KeyView)
    def key_patch(identifier: str, data: KeyPatch, request: Request, identity: Annotated[AdminIdentity, Depends(permission("keys:manage"))]) -> dict[str, Any]:
        admin_token(request)
        expected_revision(request, data.expected_revision)
        return keys.patch_key(identity, parse_id("key", identifier), data, request.state.request_id)

    @app.post("/admin/api/v1/api-keys/{identifier}/actions", response_model=KeyMutation)
    def key_action(identifier: str, data: KeyAction, request: Request, identity: Annotated[AdminIdentity, Depends(permission("keys:manage"))]) -> dict[str, Any]:
        expected_revision(request, data.expected_revision)
        return keys.key_action(identity, parse_id("key", identifier), data, admin_token(request), request.state.request_id)

    @app.delete("/admin/api/v1/api-keys/{identifier}")
    def key_delete(identifier: str, request: Request, identity: Annotated[AdminIdentity, Depends(permission("keys:manage"))], revision: int = Query(ge=1)) -> dict[str, Any]:
        admin_token(request)
        expected_revision(request, revision)
        return keys.delete_key(identity, parse_id("key", identifier), revision, request.state.request_id)

    @app.get("/admin/api/v1/api-keys/{identifier}/usage")
    def key_usage(identifier: str, identity: Annotated[AdminIdentity, Depends(permission("keys:usage"))]) -> dict[str, Any]:
        return keys.key_usage(parse_id("key", identifier))

    @app.get("/admin/api/v1/api-keys/{identifier}/access-events", response_model=KeyAccessSnapshot)
    def key_access_events(identifier: str, identity: Annotated[AdminIdentity, Depends(permission("keys:usage"))], result: AccessResult = "all", before: str | None = Query(default=None, min_length=1, max_length=128)) -> dict[str, Any]:
        return {**access_queries.snapshot(parse_id("key", identifier), result, before), "capture_health": access_writer.health()}

    @app.get("/admin/api/v1/queues/snapshot", response_model=QueueView)
    def queue_snapshot(identity: Annotated[AdminIdentity, Depends(permission("queues:read"))], query: Annotated[QueueQuery, Query()]) -> dict[str, Any]:
        return queries.queue_snapshot(query)

    @app.get("/admin/api/v1/queues/statistics", response_model=QueueStatistics)
    def queue_statistics(identity: Annotated[AdminIdentity, Depends(permission("queues:read"))], query: Annotated[QueueStatisticsQuery, Query()]) -> dict[str, Any]:
        return operations.statistics(query)

    @app.get("/admin/api/v1/queues/filter-options", response_model=QueueOptions)
    def queue_filter_options(identity: Annotated[AdminIdentity, Depends(permission("queues:read"))], query: Annotated[QueueOptionQuery, Query()]) -> dict[str, Any]:
        return queries.queue_filter_options(query)

    @app.get("/admin/api/v1/alerts", response_model=AlertSnapshot)
    def alert_snapshot(identity: Annotated[AdminIdentity, Depends(permission("operations:read"))], query: Annotated[AlertQuery, Query()]) -> dict[str, Any]:
        return alerts.snapshot(query=query)

    @app.get("/admin/api/v1/content", response_model=AdminContentPage)
    def admin_content_list(identity: Annotated[AdminIdentity, Depends(permission("content:read"))], query: Annotated[ContentQuery, Query()]) -> dict[str, Any]:
        return content.admin_list(query)

    @app.get("/admin/api/v1/content/filter-options", response_model=QueueOptions)
    def admin_content_options(identity: Annotated[AdminIdentity, Depends(permission("content:read"))], query: Annotated[ContentOptionQuery, Query()]) -> dict[str, Any]:
        return content.admin_filter_options(query)

    @app.patch("/admin/api/v1/content/{identifier}", response_model=ContentView)
    def admin_content_tags(identifier: str, data: ContentTags, request: Request, response: Response, identity: Annotated[AdminIdentity, Depends(permission("content:manage"))]) -> dict[str, Any]:
        expected_revision(request, data.expected_revision)
        asset = identifier.startswith("asset_")
        result = content.admin_tags(identity, parse_id("asset" if asset else "content", identifier), data, admin_token(request), request.state.request_id, asset=asset)
        etag(response, result["metadata_revision"])
        return result

    @app.get("/admin/api/v1/content/{identifier}/preview")
    @app.head("/admin/api/v1/content/{identifier}/preview")
    def admin_content_preview(identifier: str, request: Request, identity: Annotated[AdminIdentity, Depends(permission("content:download"))]) -> Response:
        asset = identifier.startswith("asset_")
        if asset:
            require_permission(identity, "content:sensitive")
        result = content.admin_download(parse_id("asset" if asset else "content", identifier), request.headers.get("range"), asset=asset, head=request.method == "HEAD", preview=True)
        return StreamingResponse(result.data, status_code=result.status, media_type=result.media_type, headers=result.headers)

    @app.get("/admin/api/v1/content/{identifier}", response_model=ContentView)
    def admin_content_metadata(identifier: str, response: Response, identity: Annotated[AdminIdentity, Depends(permission("content:read"))]) -> dict[str, Any]:
        asset = identifier.startswith("asset_")
        result = content.admin_metadata(parse_id("asset" if asset else "content", identifier), asset=asset)
        etag(response, result["metadata_revision"])
        return result

    @app.get("/admin/api/v1/content/{identifier}/download")
    @app.head("/admin/api/v1/content/{identifier}/download")
    def admin_content_download(identifier: str, request: Request, identity: Annotated[AdminIdentity, Depends(permission("content:download"))]) -> Response:
        asset = identifier.startswith("asset_")
        if asset:
            require_permission(identity, "content:sensitive")
        result = content.admin_download(parse_id("asset" if asset else "content", identifier), request.headers.get("range"), asset=asset, head=request.method == "HEAD")
        return StreamingResponse(result.data, status_code=result.status, media_type=result.media_type, headers=result.headers)

    @app.delete("/admin/api/v1/content/{identifier}", status_code=202, response_model=ContentDeletion)
    def admin_content_delete(identifier: str, request: Request, identity: Annotated[AdminIdentity, Depends(permission("content:delete"))]) -> dict[str, Any]:
        asset = identifier.startswith("asset_")
        return content.admin_delete(identity, parse_id("asset" if asset else "content", identifier), admin_token(request), request.state.request_id, asset=asset)

    @app.get("/admin/api/v1/queues/jobs", response_model=AdminJobsPage)
    def queue_jobs(identity: Annotated[AdminIdentity, Depends(permission("jobs:read"))], query: Annotated[QueueQuery, Query()]) -> dict[str, Any]:
        return admin_jobs.list(query)

    @app.post("/admin/api/v1/job-cancellations", status_code=202, response_model=OperationAccepted)
    def cancel_jobs(data: JobCancellationBatch, request: Request, identity: Annotated[AdminIdentity, Depends(permission("jobs:control"))]) -> dict[str, Any]:
        return cancellations.create(identity, data, admin_token(request), request.state.request_id)

    @app.get("/admin/api/v1/job-cancellations/{identifier}", response_model=JobCancellationView)
    def cancellation_batch(identifier: str, identity: Annotated[AdminIdentity, Depends(permission("jobs:read"))]) -> dict[str, Any]:
        return cancellations.get(parse_id("op", identifier))

    @app.get("/admin/api/v1/jobs/{identifier}", response_model=AdminJobDetail, response_model_exclude_none=True)
    def admin_job_detail(identifier: str, request: Request, response: Response, identity: Annotated[AdminIdentity, Depends(permission("jobs:read"))], include_input: bool = False) -> dict[str, Any]:
        if include_input:
            require_permission(identity, "content:sensitive")
        result = admin_jobs.detail(identity, parse_id("job", identifier), request.state.request_id, sensitive=include_input)
        etag(response, result["updated_revision"])
        return result

    @app.post("/admin/api/v1/jobs/{identifier}/cancel", status_code=202, response_model=AdminJobView)
    def admin_job_cancel(identifier: str, request: Request, identity: Annotated[AdminIdentity, Depends(permission("jobs:control"))]) -> dict[str, Any]:
        return admin_jobs.cancel(identity, parse_id("job", identifier), admin_token(request), request.state.request_id)

    @app.get("/admin/api/v1/jobs/{identifier}/wait-estimate", response_model=WaitEstimate)
    def admin_job_wait_estimate(identifier: str, identity: Annotated[AdminIdentity, Depends(permission("jobs:read"))]) -> dict[str, Any]:
        return WaitEstimates(admin_jobs.sessions, settings).get(parse_id("job", identifier))

    @app.post("/admin/api/v1/jobs/{identifier}/retry", status_code=202, response_model=AdminJobView)
    def admin_job_retry(identifier: str, request: Request, identity: Annotated[AdminIdentity, Depends(permission("jobs:control"))]) -> dict[str, Any]:
        return admin_jobs.retry(identity, parse_id("job", identifier), admin_token(request), request.state.request_id)

    @app.patch("/admin/api/v1/jobs/{identifier}/priority", response_model=AdminJobView)
    def admin_job_priority(identifier: str, data: JobPriority, request: Request, identity: Annotated[AdminIdentity, Depends(permission("jobs:control"))]) -> dict[str, Any]:
        expected_revision(request, data.expected_revision)
        return admin_jobs.priority(identity, parse_id("job", identifier), data, admin_token(request), request.state.request_id)

    @app.post("/admin/api/v1/key-policy/options/query", response_model=KeyPolicyOptions)
    def key_policy_options(data: KeyPolicyOptionQuery, identity: Annotated[AdminIdentity, Depends(permission("keys:manage"))]) -> dict[str, Any]:
        return key_policy_directory.query(data)

    @app.post("/admin/api/v1/key-generation-options/query", response_model=KeyGenerationOptions)
    def key_generation_options(data: KeyGenerationQuery, identity: Annotated[AdminIdentity, Depends(permission("keys:manage"))]) -> dict[str, Any]:
        return key_generation_directory.query(data)

    @app.get("/admin/api/v1/tenants", response_model=ScopePage[TenantView])
    def tenant_list(query: Annotated[ScopeQuery, Query()], identity: Annotated[AdminIdentity, Depends(permission("scopes:read"))]) -> dict[str, Any]:
        return scope_admin.tenants(query)

    @app.get("/admin/api/v1/tenants/{identifier}", response_model=TenantView)
    def tenant_detail(identifier: str, identity: Annotated[AdminIdentity, Depends(permission("scopes:read"))]) -> dict[str, Any]:
        return scope_admin.get(parse_id("ten", identifier), project=False)

    @app.post("/admin/api/v1/tenants", response_model=TenantView, status_code=201)
    def tenant_create(data: TenantCreate, request: Request, identity: Annotated[AdminIdentity, Depends(permission("scopes:manage"))]) -> dict[str, Any]:
        return scope_admin.create_tenant(identity, data, admin_token(request), request.state.request_id)

    @app.patch("/admin/api/v1/tenants/{identifier}", response_model=TenantView)
    def tenant_patch(identifier: str, data: ScopePatch, request: Request, identity: Annotated[AdminIdentity, Depends(permission("scopes:manage"))]) -> dict[str, Any]:
        expected_revision(request, data.expected_revision)
        return scope_admin.patch(identity, parse_id("ten", identifier), data, admin_token(request), request.state.request_id, project=False)

    @app.get("/admin/api/v1/projects", response_model=ScopePage[ProjectView])
    def project_list(query: Annotated[ScopeQuery, Query()], identity: Annotated[AdminIdentity, Depends(permission("scopes:read"))]) -> dict[str, Any]:
        return scope_admin.projects(query)

    @app.get("/admin/api/v1/projects/{identifier}", response_model=ProjectView)
    def project_detail(identifier: str, identity: Annotated[AdminIdentity, Depends(permission("scopes:read"))]) -> dict[str, Any]:
        return scope_admin.get(parse_id("prj", identifier), project=True)

    @app.post("/admin/api/v1/projects", response_model=ProjectView, status_code=201)
    def project_create(data: ProjectCreate, request: Request, identity: Annotated[AdminIdentity, Depends(permission("scopes:manage"))]) -> dict[str, Any]:
        return scope_admin.create_project(identity, data, admin_token(request), request.state.request_id)

    @app.patch("/admin/api/v1/projects/{identifier}", response_model=ProjectView)
    def project_patch(identifier: str, data: ScopePatch, request: Request, identity: Annotated[AdminIdentity, Depends(permission("scopes:manage"))]) -> dict[str, Any]:
        expected_revision(request, data.expected_revision)
        return scope_admin.patch(identity, parse_id("prj", identifier), data, admin_token(request), request.state.request_id, project=True)

    @app.get("/admin/api/v1/scopes/options", response_model=Page[ScopeOption])
    def scope_options(query: Annotated[ScopeQuery, Query()], identity: Annotated[AdminIdentity, Depends(permission("scopes:read"))]) -> dict[str, Any]:
        return scope_admin.options(query)

    @app.get("/admin/api/v1/admin-users", response_model=Page[AdminUserView])
    def admin_user_list(identity: Annotated[AdminIdentity, Depends(permission("users:manage"))], page: int = Query(1, ge=1)) -> dict[str, Any]:
        return users.list(page)

    @app.post("/admin/api/v1/admin-users", status_code=201, response_model=AdminUserView)
    def admin_user_create(data: AdminUserCreate, request: Request, identity: Annotated[AdminIdentity, Depends(permission("users:manage"))]) -> dict[str, Any]:
        return users.create(identity, data, admin_token(request), request.state.request_id)

    @app.patch("/admin/api/v1/admin-users/{identifier}", response_model=AdminUserView)
    def admin_user_patch(identifier: str, data: AdminUserPatch, request: Request, identity: Annotated[AdminIdentity, Depends(permission("users:manage"))]) -> dict[str, Any]:
        expected_revision(request, data.expected_revision)
        return users.patch(identity, parse_id("usr", identifier), data, admin_token(request), request.state.request_id)

    @app.get("/admin/api/v1/operations", response_model=Page[OperationView])
    def operation_list(identity: Annotated[AdminIdentity, Depends(permission("operations:read"))], page: int = Query(1, ge=1)) -> dict[str, Any]:
        return queries.operations(page)

    @app.get("/admin/api/v1/operations/{identifier}", response_model=OperationDetail)
    def operation_detail(identifier: str, identity: Annotated[AdminIdentity, Depends(permission("operations:read"))]) -> dict[str, Any]:
        return queries.operation(parse_id("op", identifier))

    @app.get("/admin/api/v1/audit-events", response_model=AuditSnapshot)
    def audit_events(identity: Annotated[AdminIdentity, Depends(permission("audit:read"))], query: Annotated[AuditQuery, Query()]) -> dict[str, Any]:
        return audits.snapshot(query)

    @app.get("/admin/api/v1/audit-retention", response_model=AuditRetentionView)
    def audit_retention(identity: Annotated[AdminIdentity, Depends(permission("audit:read"))]) -> AuditRetentionView:
        return retention_snapshot(sessions, settings.audit_retention_days)

    @app.get("/admin/api/v1/events")
    async def events(request: Request, identity: Annotated[AdminIdentity, Depends(permission("queues:read"))]) -> StreamingResponse:
        async def stream() -> AsyncIterator[str]:
            yield "event: invalidation\ndata: {\"reason\":\"connected\"}\n\n"
            while not await request.is_disconnected():
                await asyncio.sleep(3)
                try:
                    await asyncio.to_thread(auth.admin, request.cookies.get(COOKIE, ""), write=False, origin=None, csrf=None)
                except DomainError:
                    yield "event: session_revoked\ndata: {}\n\n"
                    return
                # Invalidation is deliberately independent of allocated sequence IDs.
                yield "event: invalidation\ndata: {\"reason\":\"refresh\"}\n\n"
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})

    @app.get("/admin/api/v1/channels/{identifier}/workflow-exports", response_model=WorkflowExportStatus)
    def workflow_export_status(identifier: str, identity: Annotated[AdminIdentity, Depends(permission("workflows:write"))]) -> dict[str, Any]:
        return workflow_catalogs.source_status(parse_id("ch", identifier))

    @app.post("/admin/api/v1/workflow-catalogs", status_code=202, response_model=OperationAccepted)
    def workflow_catalog(data: WorkflowCatalogRequest, request: Request, identity: Annotated[AdminIdentity, Depends(permission("workflows:write"))]) -> dict[str, Any]:
        return workflow_catalogs.browse(identity, data, admin_token(request), request.state.request_id)

    @app.get("/admin/api/v1/workflow-catalogs/{identifier}", response_model=WorkflowCatalogView)
    def workflow_catalog_detail(identifier: str, identity: Annotated[AdminIdentity, Depends(permission("workflows:write"))]) -> dict[str, Any]:
        return workflow_catalogs.detail(identity, parse_id("op", identifier))

    @app.post("/admin/api/v1/workflow-pulls", status_code=202, response_model=OperationAccepted)
    def workflow_pull(data: WorkflowPull, request: Request, identity: Annotated[AdminIdentity, Depends(permission("workflows:write"))]) -> dict[str, Any]:
        return workflow_imports.pull(identity, data, admin_token(request), request.state.request_id)

    @app.post("/admin/api/v1/workflow-import-previews", status_code=201, response_model=WorkflowImportPreviewView)
    def workflow_import_preview(data: WorkflowImportPreview, request: Request, identity: Annotated[AdminIdentity, Depends(permission("workflows:write"))]) -> dict[str, Any]:
        return workflow_imports.preview(identity, data, admin_token(request), request.state.request_id)

    @app.get("/admin/api/v1/workflow-import-previews/{identifier}", response_model=WorkflowImportPreviewView)
    def workflow_import_preview_detail(identifier: str, identity: Annotated[AdminIdentity, Depends(permission("workflows:write"))]) -> dict[str, Any]:
        return workflow_imports.get_preview(identity, parse_id("wip", identifier))

    @app.post("/admin/api/v1/workflow-import-previews/{identifier}/commit", response_model=WorkflowImportResult)
    def workflow_import_commit(identifier: str, data: WorkflowImportCommit, request: Request, identity: Annotated[AdminIdentity, Depends(permission("workflows:write"))]) -> dict[str, Any]:
        return workflow_imports.commit(identity, parse_id("wip", identifier), data.candidate_digest, admin_token(request), request.state.request_id)

    @app.get("/admin/api/v1/workflows", response_model=WorkflowDirectoryPage)
    def workflow_list(identity: Annotated[AdminIdentity, Depends(permission("workflows:read"))], query: Annotated[WorkflowDirectoryQuery, Query()]) -> dict[str, Any]:
        return workflow_directory.catalog(query)

    @app.get("/admin/api/v1/workflows/{identifier}/releases", response_model=WorkflowHistoryPage)
    def workflow_history_list(identifier: str, identity: Annotated[AdminIdentity, Depends(permission("workflows:read"))], query: Annotated[WorkflowHistoryQuery, Query()]) -> dict[str, Any]:
        return workflow_history.history(parse_id("wf", identifier), query)

    @app.get("/admin/api/v1/workflows/{identifier}/release-diff", response_model=WorkflowDiff)
    def workflow_release_diff(identifier: str, identity: Annotated[AdminIdentity, Depends(permission("workflows:read"))], query: Annotated[WorkflowDiffQuery, Query()]) -> dict[str, Any]:
        return workflow_history.compare(parse_id("wf", identifier), query)

    @app.get("/admin/api/v1/workflow-sync-options", response_model=WorkflowSyncOptions)
    def workflow_sync_options(identity: Annotated[AdminIdentity, Depends(permission("workflows:publish"))], query: Annotated[WorkflowSyncOptionsQuery, Query()]) -> dict[str, Any]:
        return workflow_directory.options(query)

    @app.post("/admin/api/v1/workflows", status_code=201, response_model=WorkflowView)
    def workflow_create(data: WorkflowCreate, request: Request, identity: Annotated[AdminIdentity, Depends(permission("workflows:write"))]) -> dict[str, Any]:
        return workflows.create_workflow(identity, data, admin_token(request), request.state.request_id)

    @app.get("/admin/api/v1/workflows/{identifier}", response_model=WorkflowDetail)
    def workflow_detail(identifier: str, response: Response, identity: Annotated[AdminIdentity, Depends(permission("workflows:read"))]) -> dict[str, Any]:
        result = workflows.workflow(parse_id("wf", identifier))
        etag(response, result["revision"])
        return result

    @app.patch("/admin/api/v1/workflows/{identifier}", response_model=WorkflowView)
    def workflow_patch(identifier: str, data: WorkflowPatch, request: Request, identity: Annotated[AdminIdentity, Depends(permission("workflows:write"))]) -> dict[str, Any]:
        admin_token(request)
        expected_revision(request, data.expected_revision)
        return workflows.patch_workflow(identity, parse_id("wf", identifier), data, request.state.request_id)

    @app.post("/admin/api/v1/workflows/{identifier}/lifecycle", response_model=WorkflowLifecycleResult)
    def workflow_lifecycle(identifier: str, data: WorkflowLifecycle, request: Request, identity: Annotated[AdminIdentity, Depends(permission("workflows:write"))]) -> dict[str, Any]:
        expected_revision(request, data.expected_revision)
        return workflows.lifecycle(identity, parse_id("wf", identifier), data, admin_token(request), request.state.request_id)

    @app.post("/admin/api/v1/workflows/{identifier}/draft-restorations", response_model=WorkflowDraftRestored)
    def workflow_draft_restore(identifier: str, data: WorkflowDraftRestore, request: Request, identity: Annotated[AdminIdentity, Depends(permission("workflows:write"))]) -> dict[str, Any]:
        expected_revision(request, data.expected_revision)
        return workflows.restore_draft(identity, parse_id("wf", identifier), data, admin_token(request), request.state.request_id)

    @app.post("/admin/api/v1/workflows/{identifier}/releases", status_code=201)
    def workflow_release(identifier: str, data: ExpectedRevision, request: Request, identity: Annotated[AdminIdentity, Depends(permission("workflows:publish"))]) -> dict[str, Any]:
        expected_revision(request, data.expected_revision)
        return workflows.release(identity, parse_id("wf", identifier), data.expected_revision, admin_token(request), request.state.request_id)

    @app.get("/admin/api/v1/workflow-releases/{identifier}", response_model=FrozenReleaseView)
    def workflow_release_detail(identifier: str, identity: Annotated[AdminIdentity, Depends(permission("workflows:read"))]) -> dict[str, Any]:
        return workflows.get_release(parse_id("wfr", identifier))

    @app.post("/admin/api/v1/workflow-releases/{identifier}/validate", status_code=202, response_model=ValidationAccepted)
    def release_validation(identifier: str, data: ReleaseValidation, request: Request, identity: Annotated[AdminIdentity, Depends(permission("workflows:publish"))]) -> dict[str, Any]:
        return validation_jobs.create(identity, parse_id("mdl", data.model_id), data, admin_token(request), request.state.request_id, release_id=parse_id("wfr", identifier), expected_model_revision=data.expected_model_revision, expected_channel_revision=data.expected_channel_revision)

    @app.post("/admin/api/v1/workflow-syncs", status_code=202, response_model=OperationAccepted)
    def workflow_sync(data: WorkflowSync, request: Request, identity: Annotated[AdminIdentity, Depends(permission("workflows:publish"))]) -> dict[str, Any]:
        return catalog.synchronize(identity, data, admin_token(request), request.state.request_id)

    @app.get("/admin/api/v1/workflow-syncs/{identifier}/retry-options", response_model=WorkflowSyncRetryOptions)
    def workflow_retry_options(identifier: str, identity: Annotated[AdminIdentity, Depends(permission("workflows:publish"))]) -> dict[str, Any]:
        return workflow_retries.options(parse_id("op", identifier))

    @app.post("/admin/api/v1/workflow-syncs/{identifier}/retries", status_code=202, response_model=OperationAccepted)
    def workflow_retry(identifier: str, data: WorkflowSyncRetry, request: Request, identity: Annotated[AdminIdentity, Depends(permission("workflows:publish"))]) -> dict[str, Any]:
        return workflow_retries.retry(identity, parse_id("op", identifier), data, admin_token(request), request.state.request_id)

    @app.get("/v1/access-policy")
    def access_policy(identity: Annotated[BusinessIdentity, Depends(business)]) -> dict[str, Any]:
        return identity.policy

    @app.get("/admin/api/v1/public-models", response_model=ModelCatalogPage)
    def model_list(identity: Annotated[AdminIdentity, Depends(permission("models:read"))], query: Annotated[ModelCatalogQuery, Query()]) -> dict[str, Any]:
        return catalog.models(query=query)

    @app.get("/admin/api/v1/model-mapping-options", response_model=ModelMappingOptions, response_model_exclude_none=True)
    def model_mapping_options(identity: Annotated[AdminIdentity, Depends(permission("models:read"))], query: Annotated[ModelMappingQuery, Query()]) -> dict[str, Any]:
        return model_mapping_reads.options(query)

    @app.get("/admin/api/v1/model-artifacts", response_model=Page[ModelArtifactView])
    def model_artifact_list(identity: Annotated[AdminIdentity, Depends(permission("models:read"))], page: int = Query(1, ge=1), include_archived: bool = False) -> dict[str, Any]:
        return model_artifacts.list(page, include_archived)

    @app.post("/admin/api/v1/model-artifacts", status_code=201, response_model=ModelArtifactView)
    def model_artifact_create(data: ModelArtifactCreate, request: Request, identity: Annotated[AdminIdentity, Depends(permission("models:write"))]) -> dict[str, Any]:
        return model_artifacts.create(identity, data, admin_token(request), request.state.request_id)

    @app.patch("/admin/api/v1/model-artifacts/{identifier}", response_model=ModelArtifactView)
    def model_artifact_patch(identifier: str, data: ModelArtifactPatch, request: Request, identity: Annotated[AdminIdentity, Depends(permission("models:write"))]) -> dict[str, Any]:
        expected_revision(request, data.expected_revision)
        return model_artifacts.patch(identity, parse_id("ma", identifier), data, admin_token(request), request.state.request_id)

    @app.delete("/admin/api/v1/model-artifacts/{identifier}", response_model=ModelArtifactDeletion)
    def model_artifact_delete(identifier: str, request: Request, identity: Annotated[AdminIdentity, Depends(permission("models:write"))], revision: int = Query(ge=1)) -> dict[str, Any]:
        expected_revision(request, revision)
        return model_artifacts.delete(identity, parse_id("ma", identifier), revision, admin_token(request), request.state.request_id)

    @app.post("/admin/api/v1/public-models", status_code=201, response_model=ModelView)
    def model_create(data: ModelCreate, request: Request, identity: Annotated[AdminIdentity, Depends(permission("models:write"))]) -> dict[str, Any]:
        return catalog.create_model(identity, data, admin_token(request), request.state.request_id)

    @app.patch("/admin/api/v1/public-models/{identifier}", response_model=ModelView)
    def model_patch(identifier: str, data: ModelPatch, request: Request, identity: Annotated[AdminIdentity, Depends(permission("models:write"))]) -> dict[str, Any]:
        admin_token(request)
        expected_revision(request, data.expected_revision)
        return catalog.patch_model(identity, parse_id("mdl", identifier), data, request.state.request_id)

    @app.post("/admin/api/v1/public-models/{identifier}/actions", response_model=ModelView)
    def model_action(identifier: str, data: ModelAction, request: Request, identity: Annotated[AdminIdentity, Depends(permission("models:publish"))]) -> dict[str, Any]:
        admin_token(request)
        expected_revision(request, data.expected_revision)
        return catalog.model_action(identity, parse_id("mdl", identifier), data.expected_revision, data.action == "publish", request.state.request_id)

    @app.post("/admin/api/v1/public-models/{identifier}/validation-jobs", status_code=202, response_model=ValidationAccepted)
    def validation_job(identifier: str, data: ValidationJob, request: Request, identity: Annotated[AdminIdentity, Depends(permission("workflows:publish"))]) -> dict[str, Any]:
        return validation_jobs.create(identity, parse_id("mdl", identifier), data, admin_token(request), request.state.request_id)

    @app.get("/admin/api/v1/channels/{identifier}/workflow-bindings", response_model=list[BindingView])
    def channel_bindings(identifier: str, identity: Annotated[AdminIdentity, Depends(permission("workflows:read"))]) -> list[dict[str, Any]]:
        return catalog.bindings(parse_id("ch", identifier))

    @app.put("/admin/api/v1/channels/{identifier}/workflow-bindings", status_code=202)
    def bind_workflow(identifier: str, data: BindRelease, request: Request, identity: Annotated[AdminIdentity, Depends(permission("workflows:publish"))]) -> dict[str, Any]:
        expected_revision(request, data.expected_revision)
        return catalog.bind(identity, parse_id("ch", identifier), data, admin_token(request), request.state.request_id)

    @app.post("/admin/api/v1/channels/{identifier}/workflow-bindings/approvals", response_model=RuntimeApprovalView)
    def approve_runtime(identifier: str, data: ApproveRuntime, request: Request, identity: Annotated[AdminIdentity, Depends(permission("workflows:publish"))]) -> dict[str, Any]:
        admin_token(request)
        expected_revision(request, data.expected_revision)
        return catalog.approve_runtime(identity, parse_id("ch", identifier), data, request.state.request_id, admin_token(request))

    @app.get("/v1/models", response_model=PublishedModels)
    def public_models(identity: Annotated[BusinessIdentity, Depends(business)]) -> dict[str, Any]:
        return catalog.public_models(identity)

    @app.get("/v1/models/{model}", response_model=PublishedModel)
    def public_model(model: str, identity: Annotated[BusinessIdentity, Depends(business)]) -> dict[str, Any]:
        return catalog.public_model(identity, model)

    @app.get("/v1/capabilities", response_model=PublishedCapabilities, response_model_exclude_none=True)
    def capabilities(identity: Annotated[BusinessIdentity, Depends(business)]) -> dict[str, Any]:
        return catalog.public_models(identity, capabilities=True)

    @app.post("/v1/assets", status_code=201)
    def upload_asset(request: Request, identity: Annotated[BusinessIdentity, Depends(business)], file: Annotated[UploadFile, File()]) -> dict[str, Any]:
        try:
            return content.upload_asset(identity, file.file, request.state.request_id, upload_token(request))
        finally:
            file.file.close()

    @app.get("/v1/assets/{identifier}")
    def asset_metadata(identifier: str, identity: Annotated[BusinessIdentity, Depends(business)]) -> dict[str, Any]:
        return content.metadata(identity, parse_id("asset", identifier), asset=True)

    @app.delete("/v1/assets/{identifier}", status_code=202, response_model=ContentDeletion)
    def asset_delete(identifier: str, request: Request, identity: Annotated[BusinessIdentity, Depends(business)]) -> dict[str, Any]:
        return content.delete(identity, parse_id("asset", identifier), request.state.request_id, asset=True)

    @app.get("/v1/assets/{identifier}/content")
    @app.head("/v1/assets/{identifier}/content")
    def asset_download(identifier: str, request: Request, identity: Annotated[BusinessIdentity, Depends(business)]) -> Response:
        result = content.download(identity, parse_id("asset", identifier), request.headers.get("range"), asset=True, head=request.method == "HEAD")
        return StreamingResponse(result.data, status_code=result.status, media_type=result.media_type, headers=result.headers)

    @app.get("/v1/content", response_model=CursorPage[ContentSummary])
    def content_list(identity: Annotated[BusinessIdentity, Depends(business)], query: Annotated[CollectionQuery, Depends(content_query)]) -> dict[str, Any]:
        return content.list_content(identity, query, cursors)

    @app.get("/v1/content/{identifier}")
    def content_metadata(identifier: str, identity: Annotated[BusinessIdentity, Depends(business)]) -> dict[str, Any]:
        return content.metadata(identity, parse_id("content", identifier))

    @app.delete("/v1/content/{identifier}", status_code=202, response_model=ContentDeletion)
    def content_delete(identifier: str, request: Request, identity: Annotated[BusinessIdentity, Depends(business)]) -> dict[str, Any]:
        return content.delete(identity, parse_id("content", identifier), request.state.request_id)

    @app.get("/v1/content/{identifier}/download")
    @app.head("/v1/content/{identifier}/download")
    def content_download(identifier: str, request: Request, identity: Annotated[BusinessIdentity, Depends(business)]) -> Response:
        result = content.download(identity, parse_id("content", identifier), request.headers.get("range"), head=request.method == "HEAD")
        return StreamingResponse(result.data, status_code=result.status, media_type=result.media_type, headers=result.headers)

    @app.post("/v1/jobs", status_code=202)
    def job_create(data: JobCreate, request: Request, response: Response, identity: Annotated[BusinessIdentity, Depends(business)], idempotency_key: Annotated[str | None, Header()] = None) -> dict[str, Any]:
        result = jobs.create(identity, data.model_dump(exclude_unset=True), idempotency_key or str(uuid4()), request.state.request_id)
        response.headers["Location"] = "/v1/jobs/" + result["id"]
        response.headers["Retry-After"] = "2"
        return result

    @app.get("/v1/jobs/{identifier}")
    def job_detail(identifier: str, identity: Annotated[BusinessIdentity, Depends(business)]) -> dict[str, Any]:
        return jobs.get(identity, parse_id("job", identifier))

    @app.get("/v1/jobs", response_model=CursorPage[JobSummary], response_model_exclude_unset=True)
    def job_list(identity: Annotated[BusinessIdentity, Depends(business)], query: Annotated[CollectionQuery, Depends(job_query)]) -> dict[str, Any]:
        return JobRepository(sessions).list(identity, query, cursors)

    @app.post("/v1/jobs/{identifier}/cancel", status_code=202)
    def job_cancel(identifier: str, request: Request, identity: Annotated[BusinessIdentity, Depends(business)]) -> dict[str, Any]:
        return jobs.cancel(identity, parse_id("job", identifier), request.state.request_id)

    @app.post("/internal/v1/register")
    def register(data: Register, request: Request, node: Annotated[UUID, Depends(worker)]) -> dict[str, Any]:
        return leases.register(node, UUID(data.boot_id), data.inventory.model_dump(), credential_identifier=secret_identifier("worker", bearer(request)))

    @app.post("/internal/v1/heartbeat")
    def heartbeat(data: Heartbeat, node: Annotated[UUID, Depends(worker)]) -> dict[str, Any]:
        return leases.heartbeat(node, UUID(data.boot_id), [value.model_dump() for value in data.observations], data.capacity.model_dump() if data.capacity else None)

    @app.post("/internal/v1/claims")
    def claim(data: Claim, node: Annotated[UUID, Depends(worker)]) -> dict[str, Any]:
        return {"lease": leases.claim(node, UUID(data.boot_id), data.request_id)}

    @app.get("/internal/v1/attempts/{identifier}", response_model=AttemptStatus)
    def attempt_status(identifier: str, node: Annotated[UUID, Depends(worker)], boot_id: UUID, fencing_token: int = Query(ge=1)) -> dict[str, Any]:
        return leases.attempt_status(node, boot_id, parse_id("att", identifier), fencing_token)

    @app.post("/internal/v1/attempts/{identifier}/renew")
    def renew(identifier: str, data: Renew, node: Annotated[UUID, Depends(worker)]) -> dict[str, Any]:
        return leases.renew(node, UUID(data.boot_id), parse_id("att", identifier), data.fencing_token)

    @app.post("/internal/v1/attempts/{identifier}/events")
    def event(identifier: str, data: WorkerEvent, node: Annotated[UUID, Depends(worker)]) -> dict[str, Any]:
        if data.attempt_id != identifier:
            raise DomainError("attempt_mismatch", "事件执行 ID 与路径不一致", 422)
        return leases.event(node, data)

    @app.post("/internal/v1/commands/claims", response_model=CommandNext)
    def command_claim(data: CommandClaim, node: Annotated[UUID, Depends(worker)]) -> dict[str, Any]:
        return commands.next_command(node, UUID(data.boot_id))

    @app.post("/internal/v1/reconciliations", status_code=202)
    def reconcile(data: Reconciliation, node: Annotated[UUID, Depends(worker)]) -> dict[str, Any]:
        return recovery.reconcile(node, data)

    @app.post("/internal/v1/attempts/{identifier}/reconciliation-context")
    def reconciliation_context(identifier: str, data: CommandClaim, node: Annotated[UUID, Depends(worker)]) -> dict[str, Any]:
        return recovery.context(node, UUID(data.boot_id), parse_id("att", identifier))

    @app.post("/internal/v1/attempts/{identifier}/upload-authorizations")
    def upload_authorizations(identifier: str, data: UploadAuthorization, node: Annotated[UUID, Depends(worker)]) -> dict[str, Any]:
        return outputs.authorize(node, parse_id("att", identifier), data)

    @app.put("/internal/v1/uploads/{identifier}/content")
    async def upload_output(identifier: str, request: Request, node: Annotated[UUID, Depends(worker)], boot_id: str, fencing_token: int = Query(ge=1)) -> dict[str, Any]:
        try:
            boot = UUID(boot_id)
        except ValueError as exc:
            raise DomainError("invalid_boot", "boot_id 无效", 422) from exc
        upload_id = parse_id("upl", identifier)
        spec = await asyncio.to_thread(outputs.upload_spec, node, upload_id, boot, fencing_token)
        temporary = settings.spool_root / (uuid4().hex + ".upload")
        try:
            size = 0
            with temporary.open("xb") as file:
                async with asyncio.timeout(3600):
                    async for chunk in request.stream():
                        size += len(chunk)
                        if size > spec["bytes"]:
                            raise DomainError("output_size_mismatch", "上传字节数超过声明", 413)
                        await asyncio.to_thread(file.write, chunk)
                await asyncio.to_thread(file.flush)
                await asyncio.to_thread(os.fsync, file.fileno())
            return await asyncio.to_thread(outputs.accept, node, upload_id, boot, fencing_token, temporary)
        finally:
            temporary.unlink(missing_ok=True)

    @app.post("/internal/v1/attempts/{identifier}/complete")
    def complete(identifier: str, data: Complete, node: Annotated[UUID, Depends(worker)]) -> dict[str, Any]:
        return outputs.complete(node, parse_id("att", identifier), data)

    @app.get("/internal/v1/attempts/{identifier}/inputs/{asset_id}")
    def worker_input(identifier: str, asset_id: str, node: Annotated[UUID, Depends(worker)], boot_id: str, fencing_token: int = Query(ge=1)) -> Response:
        try:
            boot = UUID(boot_id)
        except ValueError as exc:
            raise DomainError("invalid_boot", "boot_id 无效", 422) from exc
        obj = outputs.input_object(node, parse_id("att", identifier), parse_id("asset", asset_id), boot, fencing_token)
        assert obj.version_id
        return StreamingResponse(store.read(obj.object_key, obj.version_id, 0, obj.bytes - 1), media_type=obj.media_type, headers={"Content-Length": str(obj.bytes), "X-Content-SHA256": obj.sha256})

    @app.post("/internal/v1/commands/{identifier}/receipts")
    def command_receipt(identifier: str, data: CommandReceipt, node: Annotated[UUID, Depends(worker)]) -> dict[str, Any]:
        return commands.receipt(node, parse_id("cmd", identifier), UUID(data.boot_id), data.command_digest, data.result)

    @app.post("/internal/v1/commands/{identifier}/reconciliations", response_model=CommandReconciliationResult)
    def command_reconciliation(identifier: str, data: CommandReconciliation, node: Annotated[UUID, Depends(worker)]) -> dict[str, Any]:
        return commands.reconcile(node, parse_id("cmd", identifier), data)

    def monitoring_identity(request: Request) -> None:
        if not settings.monitoring_token_sha256:
            raise DomainError("not_found", "资源不存在", 404)
        if not monitoring_authorized(settings, request.headers.get("authorization", ""), request.client.host if request.client else None):
            raise DomainError("invalid_credentials", "监控凭证或连接来源无效", 401)

    @app.get("/internal/monitoring/v1/metrics", response_class=Response, responses={200: {"content": {"text/plain": {"schema": {"type": "string"}}}}})
    def monitoring_metrics(identity: Annotated[None, Depends(monitoring_identity)]) -> Response:
        try:
            return Response(monitoring.render(), headers={"Content-Type": "text/plain; version=0.0.4; charset=utf-8", "Cache-Control": "no-store"})
        except SnapshotUnavailable as exc:
            raise DomainError("monitoring_unavailable", "监控快照超过输出预算", 503) from exc

    @app.get("/internal/monitoring/v1/configuration", response_model=MonitoringConfiguration)
    def monitoring_configuration(identity: Annotated[None, Depends(monitoring_identity)]) -> MonitoringConfiguration:
        return configuration(settings)

    app.include_router(compatibility_routes(jobs, content, business, settings.public_origin))
    app.include_router(gateway_routes(GatewayRepository(sessions), GatewayAdministration(sessions, settings), catalog, content, jobs, gateway, permission, admin_token, expected_revision, cursors))
    if (settings.admin_dist / "index.html").is_file():
        app.mount("/admin", AdminFiles(directory=settings.admin_dist, html=True), name="admin")
    app.add_middleware(AccessEventMiddleware, writer=access_writer)
    return app
