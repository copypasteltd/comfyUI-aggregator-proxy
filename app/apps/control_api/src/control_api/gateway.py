from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from generation_application.ports import (
    AdminIdentity,
    BusinessIdentity,
    GatewayIdentity,
    GenerationService,
)
from generation_contracts.collections import (
    CollectionQuery,
    CursorPage,
    GatewayContentSummary,
    JobSummary,
)
from generation_contracts.discovery import PublishedCapabilities, PublishedModel, PublishedModels
from generation_contracts.gateway import (
    AdmissionCreate,
    AdmissionView,
    BindingCreate,
    BindingPatch,
    BindingView,
    GatewayCreate,
    GatewayMutation,
    GatewayPatch,
    GatewayRequestIdentity,
    GatewayRequestResolution,
    GatewayView,
    SettlementAck,
)
from generation_contracts.responses import ContentDeletion, Page
from generation_domain.errors import DomainError
from generation_domain.values import parse_id
from generation_infrastructure.access_events import AccessSubject
from generation_infrastructure.admin_catalog import CatalogAdministration
from generation_infrastructure.admin_gateways import GatewayAdministration
from generation_infrastructure.content import ContentRepository
from generation_infrastructure.cursors import CursorCodec
from generation_infrastructure.gateway import GatewayRepository
from generation_infrastructure.jobs import JobRepository

from control_api.access import normalized_ip
from control_api.collections import content_query, job_query
from control_api.uploads import upload_token


def gateway_routes(repository: GatewayRepository, administration: GatewayAdministration, catalog: CatalogAdministration, content: ContentRepository, jobs: GenerationService, gateway: Callable[..., GatewayIdentity], permission: Callable[..., Any], token: Callable[[Request], str], revision: Callable[[Request, int], None], cursors: CursorCodec) -> APIRouter:
    router = APIRouter()

    def delegated(request: Request, service: Annotated[GatewayIdentity, Depends(gateway)]) -> BusinessIdentity:
        def remember(identity: BusinessIdentity, peer: str) -> None:
            request.state.access_subject = AccessSubject(identity.tenant_id, identity.project_id, identity.key_id, normalized_ip(peer), service.gateway_id)
        return repository.business(service, request.headers.get("x-external-tenant-ref", ""), request.headers.get("x-external-key-ref", ""), request.headers.get("x-client-ip", ""), verified=remember)

    @router.get("/admin/api/v1/gateways", response_model=Page[GatewayView])
    def gateway_list(actor: Annotated[AdminIdentity, Depends(permission("keys:manage"))], page: int = Query(1, ge=1)) -> dict[str, Any]:
        return administration.gateways(page)

    @router.post("/admin/api/v1/gateways", status_code=201, response_model=GatewayMutation)
    def gateway_create(data: GatewayCreate, request: Request, actor: Annotated[AdminIdentity, Depends(permission("keys:manage"))]) -> dict[str, Any]:
        return administration.create_gateway(actor, data, token(request), request.state.request_id)

    @router.patch("/admin/api/v1/gateways/{identifier}", response_model=GatewayMutation)
    def gateway_patch(identifier: str, data: GatewayPatch, request: Request, actor: Annotated[AdminIdentity, Depends(permission("keys:manage"))]) -> dict[str, Any]:
        revision(request, data.expected_revision)
        return administration.patch_gateway(actor, parse_id("gateway", identifier), data, token(request), request.state.request_id)

    @router.get("/admin/api/v1/gateway-bindings", response_model=Page[BindingView])
    def bindings(actor: Annotated[AdminIdentity, Depends(permission("keys:manage"))], page: int = Query(1, ge=1)) -> dict[str, Any]:
        return administration.bindings(page)

    @router.post("/admin/api/v1/gateway-bindings", status_code=201, response_model=BindingView)
    def binding_create(data: BindingCreate, request: Request, actor: Annotated[AdminIdentity, Depends(permission("keys:manage"))]) -> dict[str, Any]:
        return administration.create_binding(actor, data, token(request), request.state.request_id)

    @router.patch("/admin/api/v1/gateway-bindings/{identifier}", response_model=BindingView)
    def binding_patch(identifier: str, data: BindingPatch, request: Request, actor: Annotated[AdminIdentity, Depends(permission("keys:manage"))]) -> dict[str, Any]:
        revision(request, data.expected_revision)
        return administration.patch_binding(actor, parse_id("bind", identifier), data, token(request), request.state.request_id)

    @router.post("/gateway/v1/admissions", status_code=202, response_model=AdmissionView)
    def admit(data: AdmissionCreate, request: Request, response: Response, service: Annotated[GatewayIdentity, Depends(gateway)], identity: Annotated[BusinessIdentity, Depends(delegated)]) -> dict[str, Any]:
        result = repository.create(service, identity, data, request.state.request_id)
        response.headers.update({"Location": "/gateway/v1/jobs/" + result["job_id"], "Retry-After": "2"})
        return result

    @router.post("/gateway/v1/admissions/{identifier}/confirm", response_model=AdmissionView)
    def confirm(identifier: str, request: Request, response: Response, service: Annotated[GatewayIdentity, Depends(gateway)], identity: Annotated[BusinessIdentity, Depends(delegated)]) -> dict[str, Any]:
        result = repository.confirm(service, identity, parse_id("adm", identifier), request.state.request_id)
        if result["status"] != "confirmed":
            response.status_code = 409
        return result

    @router.post("/gateway/v1/admissions/{identifier}/abort", response_model=AdmissionView)
    def abort(identifier: str, request: Request, response: Response, service: Annotated[GatewayIdentity, Depends(gateway)], identity: Annotated[BusinessIdentity, Depends(delegated)]) -> dict[str, Any]:
        result = repository.abort(service, identity, parse_id("adm", identifier), request.state.request_id)
        if result["status"] == "confirmed":
            response.status_code = 409
        return result

    @router.post("/gateway/v1/admission-requests/lookup", response_model=GatewayRequestResolution)
    def request_lookup(data: GatewayRequestIdentity, service: Annotated[GatewayIdentity, Depends(gateway)]) -> dict[str, Any]:
        return repository.lookup_request(service, data)

    @router.post("/gateway/v1/admission-requests/close", response_model=GatewayRequestResolution)
    def request_close(data: GatewayRequestIdentity, request: Request, service: Annotated[GatewayIdentity, Depends(gateway)]) -> dict[str, Any]:
        return repository.close_request(service, data, request.state.request_id)

    @router.get("/gateway/v1/jobs", response_model=CursorPage[JobSummary], response_model_exclude_unset=True)
    def job_list(service: Annotated[GatewayIdentity, Depends(gateway)], identity: Annotated[BusinessIdentity, Depends(delegated)], query: Annotated[CollectionQuery, Depends(job_query)]) -> dict[str, Any]:
        return JobRepository(repository.sessions).list(identity, query, cursors, gateway_id=service.gateway_id)

    @router.get("/gateway/v1/jobs/{identifier}")
    def job_get(identifier: str, service: Annotated[GatewayIdentity, Depends(gateway)], identity: Annotated[BusinessIdentity, Depends(delegated)]) -> dict[str, Any]:
        job_id = parse_id("job", identifier)
        repository.authorize_job(service, identity, job_id)
        return jobs.get(identity, job_id)

    @router.post("/gateway/v1/jobs/{identifier}/cancel", status_code=202)
    def cancel(identifier: str, request: Request, service: Annotated[GatewayIdentity, Depends(gateway)], identity: Annotated[BusinessIdentity, Depends(delegated)]) -> dict[str, Any]:
        job_id = parse_id("job", identifier)
        repository.authorize_job(service, identity, job_id)
        return jobs.cancel(identity, job_id, request.state.request_id)

    @router.get("/gateway/v1/access-policy")
    def access_policy(identity: Annotated[BusinessIdentity, Depends(delegated)]) -> dict[str, Any]:
        return identity.policy

    @router.get("/gateway/v1/models", response_model=PublishedModels)
    def models(identity: Annotated[BusinessIdentity, Depends(delegated)]) -> dict[str, Any]:
        return catalog.public_models(identity)

    @router.get("/gateway/v1/models/{model}", response_model=PublishedModel)
    def public_model(model: str, identity: Annotated[BusinessIdentity, Depends(delegated)]) -> dict[str, Any]:
        return catalog.public_model(identity, model)

    @router.get("/gateway/v1/capabilities", response_model=PublishedCapabilities, response_model_exclude_none=True)
    def capabilities(identity: Annotated[BusinessIdentity, Depends(delegated)]) -> dict[str, Any]:
        return catalog.public_models(identity, capabilities=True)

    @router.post("/gateway/v1/assets", status_code=201)
    def upload_asset(request: Request, identity: Annotated[BusinessIdentity, Depends(delegated)], file: Annotated[UploadFile, File()]) -> dict[str, Any]:
        try:
            return content.upload_asset(identity, file.file, request.state.request_id, upload_token(request, required=True))
        finally:
            file.file.close()

    @router.get("/gateway/v1/assets/{identifier}")
    def asset_metadata(identifier: str, identity: Annotated[BusinessIdentity, Depends(delegated)]) -> dict[str, Any]:
        return content.metadata(identity, parse_id("asset", identifier), asset=True)

    @router.delete("/gateway/v1/assets/{identifier}", status_code=202, response_model=ContentDeletion)
    def asset_delete(identifier: str, request: Request, identity: Annotated[BusinessIdentity, Depends(delegated)]) -> dict[str, Any]:
        token(request)
        return content.delete(identity, parse_id("asset", identifier), request.state.request_id, asset=True)

    @router.get("/gateway/v1/assets/{identifier}/content")
    @router.head("/gateway/v1/assets/{identifier}/content")
    def asset_download(identifier: str, request: Request, identity: Annotated[BusinessIdentity, Depends(delegated)]) -> Response:
        download = content.download(identity, parse_id("asset", identifier), request.headers.get("range"), asset=True, head=request.method == "HEAD")
        return StreamingResponse(download.data, status_code=download.status, media_type=download.media_type, headers=download.headers)

    @router.get("/gateway/v1/content", response_model=CursorPage[GatewayContentSummary])
    def content_list(service: Annotated[GatewayIdentity, Depends(gateway)], identity: Annotated[BusinessIdentity, Depends(delegated)], query: Annotated[CollectionQuery, Depends(content_query)]) -> dict[str, Any]:
        return content.list_content(identity, query, cursors, gateway_id=service.gateway_id)

    @router.get("/gateway/v1/content/{identifier}")
    def content_metadata(identifier: str, service: Annotated[GatewayIdentity, Depends(gateway)], identity: Annotated[BusinessIdentity, Depends(delegated)]) -> dict[str, Any]:
        content_id = parse_id("content", identifier)
        repository.authorize_content(service, content_id)
        return content.metadata(identity, content_id)

    @router.delete("/gateway/v1/content/{identifier}", status_code=202, response_model=ContentDeletion)
    def content_delete(identifier: str, request: Request, service: Annotated[GatewayIdentity, Depends(gateway)], identity: Annotated[BusinessIdentity, Depends(delegated)]) -> dict[str, Any]:
        token(request)
        content_id = parse_id("content", identifier)
        repository.authorize_content(service, content_id)
        return content.delete(identity, content_id, request.state.request_id)

    @router.get("/gateway/v1/content/{identifier}/download")
    @router.head("/gateway/v1/content/{identifier}/download")
    def content_download(identifier: str, request: Request, service: Annotated[GatewayIdentity, Depends(gateway)], identity: Annotated[BusinessIdentity, Depends(delegated)]) -> Response:
        content_id = parse_id("content", identifier)
        repository.authorize_content(service, content_id)
        download = content.download(identity, content_id, request.headers.get("range"), head=request.method == "HEAD")
        return StreamingResponse(download.data, status_code=download.status, media_type=download.media_type, headers=download.headers)

    @router.get("/gateway/v1/videos/{identifier}/content")
    @router.head("/gateway/v1/videos/{identifier}/content")
    def video_content(identifier: str, request: Request, service: Annotated[GatewayIdentity, Depends(gateway)], identity: Annotated[BusinessIdentity, Depends(delegated)]) -> Response:
        job_id = parse_id("job", identifier)
        repository.authorize_job(service, identity, job_id)
        job = jobs.get(identity, job_id)
        if not job["task_type"].endswith("video") or job["status"] != "succeeded" or not job.get("artifacts"):
            raise DomainError("video_not_ready", "视频尚未完成或没有产物读取权限")
        download = content.download(identity, parse_id("content", job["artifacts"][0]["id"]), request.headers.get("range"), head=request.method == "HEAD")
        return StreamingResponse(download.data, status_code=download.status, media_type=download.media_type, headers=download.headers)

    @router.get("/gateway/v1/settlements")
    def settlements(service: Annotated[GatewayIdentity, Depends(gateway)]) -> dict[str, Any]:
        return repository.settlements(service)

    @router.post("/gateway/v1/settlements/{identifier}/ack")
    def settlement_ack(identifier: str, data: SettlementAck, request: Request, service: Annotated[GatewayIdentity, Depends(gateway)]) -> dict[str, Any]:
        return repository.acknowledge(service, parse_id("evt", identifier), data.ledger_ref, request.state.request_id)

    return router
