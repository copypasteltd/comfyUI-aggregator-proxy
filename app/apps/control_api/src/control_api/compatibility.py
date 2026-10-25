import asyncio
import base64
import hashlib
import re
import time
from collections.abc import Callable
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse
from generation_application.ports import BusinessIdentity, GenerationService
from generation_contracts.compatibility import ImageGeneration
from generation_contracts.validation import JobCreate
from generation_domain.errors import DomainError
from generation_domain.state import TERMINAL
from generation_domain.values import parse_id
from generation_infrastructure.content import AuthorizedDownload, ContentRepository

from control_api.compatibility_inputs import media_request, media_request_schema, strict_object


def encode_download(download: AuthorizedDownload) -> str:
    try:
        length = int(download.headers.get("Content-Length", "0"))
    except ValueError:
        length = 0
    etag = download.headers.get("ETag", "")
    data = bytearray()
    try:
        if download.status != 200 or download.media_type not in {"image/png", "image/jpeg", "image/webp"} or not 1 <= length <= 32 * 1024 * 1024 or not re.fullmatch(r'"[a-f0-9]{64}"', etag):
            raise DomainError("invalid_image_delivery", "图片内联交付元数据无效或超过 32 MiB", 502)
        for chunk in download.data:
            if len(data) + len(chunk) > length:
                raise DomainError("invalid_image_delivery", "图片内联交付长度与元数据不符", 502)
            data.extend(chunk)
        if len(data) != length or hashlib.sha256(data).hexdigest() != etag[1:-1]:
            raise DomainError("invalid_image_delivery", "图片内联交付完整性校验失败", 502)
        return base64.b64encode(data).decode("ascii")
    finally:
        close = getattr(download.data, "close", None)
        if close:
            close()


def normalized(factory: Callable[[], JobCreate]) -> JobCreate:
    try:
        return factory()
    except (ValueError, TypeError) as exc:
        raise DomainError("invalid_parameters", "参数不符合模型公开输入契约", 422) from exc


def compatibility_routes(jobs: GenerationService, content: ContentRepository, business: Callable[..., BusinessIdentity], public_origin: str) -> APIRouter:
    router = APIRouter()
    synchronous = asyncio.BoundedSemaphore(8)
    media_capacity = asyncio.BoundedSemaphore(2)

    def submit(data: JobCreate, request: Request, response: Response, identity: BusinessIdentity) -> dict[str, Any]:
        result = jobs.create(identity, data.model_dump(exclude_unset=True), request.headers.get("idempotency-key") or str(uuid4()), request.state.request_id)
        response.headers.update({"Location": "/v1/jobs/" + result["id"], "Retry-After": "2"})
        return result

    async def image(data: JobCreate, request: Request, response: Response, identity: BusinessIdentity, response_format: str, asynchronous: bool) -> dict[str, Any]:
        if asynchronous:
            return await asyncio.to_thread(submit, data, request, response, identity)
        identity.require("jobs:read")
        identity.require("artifacts:read")
        identity.require("content:read")
        identity.require("content:download")
        if synchronous.locked():
            raise DomainError("sync_capacity_reached", "同步等待容量已满，请使用异步图片入口", 503)
        async with synchronous:
            result = await asyncio.to_thread(submit, data, request, response, identity)
            deadline = time.monotonic() + 60
            while result["status"] not in TERMINAL:
                if time.monotonic() >= deadline:
                    raise DomainError("sync_wait_timeout", "同步等待超时；原任务继续执行，可查询 /v1/jobs/" + result["id"], 504)
                await asyncio.sleep(1)
                result = await asyncio.to_thread(jobs.get, identity, parse_id("job", result["id"]))
            if result["status"] != "succeeded":
                raise DomainError("generation_failed", "图片任务未成功，任务 ID：" + result["id"], 502)
            items = []
            for artifact in result.get("artifacts", []):
                if response_format == "b64_json":
                    download = await asyncio.to_thread(content.download, identity, parse_id("content", artifact["id"]), None)
                    items.append({"b64_json": await asyncio.to_thread(encode_download, download)})
                else:
                    items.append({"url": public_origin + "/v1/content/" + artifact["id"] + "/download"})
            if "Retry-After" in response.headers:
                del response.headers["Retry-After"]
            return {"created": result["created_at"], "data": items}

    @router.post("/v1/images/generations")
    async def generate(data: ImageGeneration, request: Request, response: Response, identity: Annotated[BusinessIdentity, Depends(business)]) -> dict[str, Any]:
        raw = await request.body()
        return await image(normalized(lambda: ImageGeneration.model_validate(strict_object(raw)).job()), request, response, identity, data.response_format, False)

    @router.post("/v1/images/generations/async", status_code=202)
    async def generate_async(data: ImageGeneration, request: Request, response: Response, identity: Annotated[BusinessIdentity, Depends(business)]) -> dict[str, Any]:
        raw = await request.body()
        return await image(normalized(lambda: ImageGeneration.model_validate(strict_object(raw)).job()), request, response, identity, data.response_format, True)

    async def parse_media(request: Request, identity: BusinessIdentity, *, video: bool = False) -> tuple[JobCreate, str]:
        tokens = request.headers.getlist("idempotency-key")
        if len(tokens) > 1 or tokens and (not 16 <= len(tokens[0]) <= 128 or any(not 33 <= ord(character) <= 126 for character in tokens[0])):
            raise DomainError("invalid_idempotency_key", "幂等键格式无效", 400)
        if media_capacity.locked():
            raise DomainError("upload_capacity_reached", "素材输入处理容量已满，请稍后重试", 429)
        async with media_capacity:
            try:
                return await media_request(request, identity, jobs, content, public_origin, video=video)
            except (ValueError, TypeError, RecursionError) as exc:
                raise DomainError("invalid_parameters", "兼容输入不符合公开契约", 422) from exc

    @router.post("/v1/images/edits", openapi_extra=media_request_schema(False))
    async def edit(request: Request, response: Response, identity: Annotated[BusinessIdentity, Depends(business)]) -> dict[str, Any]:
        for scope in ("jobs:read", "artifacts:read", "content:read", "content:download"):
            identity.require(scope)
        try:
            data, response_format = await parse_media(request, identity)
        except (ValueError, TypeError) as exc:
            raise DomainError("invalid_parameters", "图片编辑参数不符合公开契约", 422) from exc
        return await image(data, request, response, identity, response_format, False)

    @router.post("/v1/images/edits/async", status_code=202, openapi_extra=media_request_schema(False))
    async def edit_async(request: Request, response: Response, identity: Annotated[BusinessIdentity, Depends(business)]) -> dict[str, Any]:
        try:
            data, response_format = await parse_media(request, identity)
        except (ValueError, TypeError) as exc:
            raise DomainError("invalid_parameters", "图片编辑参数不符合公开契约", 422) from exc
        return await image(data, request, response, identity, response_format, True)

    @router.get("/v1/images/tasks/{identifier}")
    def image_task(identifier: str, identity: Annotated[BusinessIdentity, Depends(business)]) -> dict[str, Any]:
        result = jobs.get(identity, parse_id("job", identifier))
        if not result["task_type"].endswith("image"):
            raise DomainError("not_found", "图片任务不存在", 404)
        state = "completed" if result["status"] == "succeeded" else "failed" if result["status"] in TERMINAL else "processing"
        return {**result, "status": state, "job_status": result["status"]}

    def video_view(result: dict[str, Any]) -> dict[str, Any]:
        if not result["task_type"].endswith("video"):
            raise DomainError("not_found", "视频任务不存在", 404)
        state = "completed" if result["status"] == "succeeded" else "failed" if result["status"] in TERMINAL else "queued" if result["status"] in ("queued", "admission_pending", "leased") else "in_progress"
        view = {**result, "object": "video", "status": state, "job_status": result["status"], "poll_url": "/v1/videos/" + result["id"]}
        view.pop("error", None)
        errors = {"failed": ("generation_failed", "Generation did not complete"), "cancelled": ("task_cancelled", "Generation was cancelled"), "expired": ("task_expired", "Generation expired")}
        if result["status"] in errors:
            code, message = errors[result["status"]]
            view["error"] = {"code": code, "message": message}
        return view

    @router.post("/v1/videos", status_code=202, openapi_extra=media_request_schema(True))
    async def video_create(request: Request, response: Response, identity: Annotated[BusinessIdentity, Depends(business)]) -> dict[str, Any]:
        data, _ = await parse_media(request, identity, video=True)
        result = video_view(await asyncio.to_thread(submit, data, request, response, identity))
        response.headers["Location"] = result["poll_url"]
        return result

    @router.get("/v1/videos/{identifier}")
    def video_get(identifier: str, identity: Annotated[BusinessIdentity, Depends(business)]) -> dict[str, Any]:
        return video_view(jobs.get(identity, parse_id("job", identifier)))

    @router.get("/v1/videos/{identifier}/content")
    @router.head("/v1/videos/{identifier}/content")
    def video_content(identifier: str, request: Request, identity: Annotated[BusinessIdentity, Depends(business)]) -> Response:
        result = video_view(jobs.get(identity, parse_id("job", identifier)))
        if result["status"] != "completed" or not result.get("artifacts"):
            raise DomainError("video_not_ready", "视频尚未完成或没有产物读取权限")
        download = content.download(identity, parse_id("content", result["artifacts"][0]["id"]), request.headers.get("range"), head=request.method == "HEAD")
        return StreamingResponse(download.data, status_code=download.status, media_type=download.media_type, headers=download.headers)

    return router
