from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query, Request
from generation_application.ports import AdminIdentity, GatewayIdentity
from generation_contracts.gateway_catalogs import (
    CatalogPublication,
    CatalogReceipt,
    CatalogReceiptResult,
    CatalogReleaseSummary,
    CatalogReleaseView,
    CatalogReplay,
    CatalogState,
)
from generation_contracts.responses import Page
from generation_domain.values import parse_id
from generation_infrastructure.gateway_catalogs import GatewayCatalogAdministration


def catalog_routes(administration: GatewayCatalogAdministration, gateway: Callable[..., GatewayIdentity], permission: Callable[..., Any], token: Callable[[Request], str], revision: Callable[[Request, int], None]) -> APIRouter:
    router = APIRouter()

    @router.get("/admin/api/v1/gateways/{identifier}/catalog", response_model=CatalogState)
    def catalog_state(identifier: str, actor: Annotated[AdminIdentity, Depends(permission("models:read"))]) -> dict[str, Any]:
        return administration.state(parse_id("gateway", identifier))

    @router.get("/admin/api/v1/gateways/{identifier}/catalog/releases", response_model=Page[CatalogReleaseSummary])
    def catalog_history(identifier: str, actor: Annotated[AdminIdentity, Depends(permission("models:read"))], page: int = Query(1, ge=1, le=1000000)) -> dict[str, Any]:
        return administration.history(parse_id("gateway", identifier), page)

    @router.get("/admin/api/v1/gateways/{identifier}/catalog/releases/{number}", response_model=CatalogReleaseView)
    def catalog_release(identifier: str, actor: Annotated[AdminIdentity, Depends(permission("models:read"))], number: int = Path(ge=1, le=2147483647)) -> dict[str, Any]:
        return administration.detail(parse_id("gateway", identifier), number)

    @router.post("/admin/api/v1/gateways/{identifier}/catalog/releases", status_code=201, response_model=CatalogReleaseView)
    def catalog_publish(identifier: str, data: CatalogPublication, request: Request, actor: Annotated[AdminIdentity, Depends(permission("models:publish"))]) -> dict[str, Any]:
        revision(request, data.expected_revision)
        return administration.publish(actor, parse_id("gateway", identifier), data, token(request), request.state.request_id)

    @router.post("/admin/api/v1/gateways/{identifier}/catalog/replay", status_code=201, response_model=CatalogReleaseView)
    def catalog_replay(identifier: str, data: CatalogReplay, request: Request, actor: Annotated[AdminIdentity, Depends(permission("models:publish"))]) -> dict[str, Any]:
        revision(request, data.expected_revision)
        return administration.replay(actor, parse_id("gateway", identifier), data, token(request), request.state.request_id)

    @router.get("/gateway/v1/catalog", response_model=CatalogState)
    def catalog_pull(service: Annotated[GatewayIdentity, Depends(gateway)], instance_id: str = Query(min_length=2, max_length=64, pattern=r"^[a-z0-9][a-z0-9_-]{1,63}$")) -> dict[str, Any]:
        return administration.state(service.gateway_id, instance_id=instance_id)

    @router.post("/gateway/v1/catalog/receipts", response_model=CatalogReceiptResult)
    def catalog_receipt(data: CatalogReceipt, request: Request, service: Annotated[GatewayIdentity, Depends(gateway)]) -> dict[str, Any]:
        return administration.receipt(service, data, request.state.request_id)

    return router
