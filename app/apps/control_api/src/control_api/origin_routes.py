"""Authenticated native ComfyUI HTTP/WebSocket facade, scoped to registered devices."""

import asyncio
import copy
import hashlib
import json
import re
import time
from collections.abc import AsyncIterator, Callable
from pathlib import PurePosixPath
from typing import Annotated, Any
from urllib.parse import quote, unquote, urlencode, urlsplit
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect
from generation_application.ports import AdminIdentity
from generation_contracts.origin import OriginGrantPatch, OriginGrantView, OriginLogin
from generation_domain.errors import DomainError
from generation_domain.values import parse_id
from generation_infrastructure.origin_access import OriginAccess, OriginActor
from generation_infrastructure.origin_runtime import (
    OriginReconciler,
    bounded_json,
    output_files,
    queue_entries,
)
from starlette.datastructures import UploadFile
from starlette.responses import (
    FileResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

COOKIE = "comfy_origin_session"
SKIP_HEADERS = {
    "connection",
    "transfer-encoding",
    "content-encoding",
    "content-length",
    "set-cookie",
    "keep-alive",
    "upgrade",
    "server",
    "access-control-allow-origin",
    "access-control-allow-credentials",
    "access-control-allow-methods",
    "access-control-allow-headers",
    "access-control-expose-headers",
}
STATIC = ("assets/", "scripts/", "extensions/", "web/", "templates/", "docs/", "locales/", "i18n/")
READS = {
    "object_info",
    "embeddings",
    "models",
    "features",
    "workflow_templates",
    "system_stats",
    "extensions",
    "global_subgraphs",
    "node_replace",
    "experiment",
    "translations",
    "default_workflow",
}


def safe_path(value: str) -> str:
    decoded = value
    for _ in range(4):
        new = unquote(decoded)
        if new == decoded:
            break
        decoded = new
    if (
        len(value) > 2048
        or "\\" in decoded
        or ":" in decoded
        or any(ord(c) < 32 for c in decoded)
        or decoded.startswith("/")
        or any(part in {".", ".."} for part in decoded.split("/"))
    ):
        raise DomainError("not_found", "路径不可访问", 404)
    return decoded


def file_parameters(request: Request) -> tuple[str, str, str]:
    if any(
        len(request.query_params.getlist(name)) > 1 for name in ("filename", "subfolder", "type")
    ):
        raise DomainError("not_found", "文件不存在", 404)
    name, folder, kind = (
        request.query_params.get("filename", ""),
        request.query_params.get("subfolder", ""),
        request.query_params.get("type", "output"),
    )
    if not name or not output_files({"filename": name, "subfolder": folder, "type": kind}):
        raise DomainError("not_found", "文件不存在", 404)
    return name, folder, kind


def upstream_headers(target: dict[str, Any], request: Request) -> dict[str, str]:
    result = {"Accept-Encoding": "identity"}
    for name in ("range", "if-range", "if-none-match", "if-modified-since", "accept"):
        if name in request.headers:
            result[name] = request.headers[name]
    if target["authorization"]:
        result["Authorization"] = target["authorization"]
    return result


def proxied_location(value: str, base_url: str, prefix: str) -> str:
    parsed, upstream = urlsplit(value), urlsplit(base_url)
    if parsed.netloc and (parsed.scheme, parsed.netloc) != (upstream.scheme, upstream.netloc):
        raise DomainError("origin_redirect_denied", "设备返回了范围外的跳转", 502)
    path = safe_path(parsed.path.lstrip("/"))
    return prefix + path + ("?" + parsed.query if parsed.query else "")


async def json_call(target: dict[str, Any], path: str) -> Any:
    return await asyncio.to_thread(bounded_json, target["base_url"], path, target["authorization"])


def origin_routes(
    access: OriginAccess,
    permission: Callable[..., Any],
    admin_token: Callable[..., str],
    expected_revision: Callable[..., None],
) -> APIRouter:
    router = APIRouter()
    socket_counts: dict[UUID, int] = {}
    reconciler = OriginReconciler(access.sessions, access.settings)

    def actor(request: Request, *, write: bool = False) -> OriginActor:
        return access.authenticate(
            request.cookies.get(COOKIE, ""),
            request.client.host if request.client else "127.0.0.1",
            csrf=request.headers.get("x-origin-csrf"),
            write=write,
        )

    @router.get("/admin/api/v1/api-keys/{identifier}/origin", response_model=OriginGrantView)
    def grant(
        identifier: str, identity: Annotated[AdminIdentity, Depends(permission("keys:manage"))]
    ) -> dict[str, Any]:
        return access.grant_view(parse_id("key", identifier))

    @router.patch("/admin/api/v1/api-keys/{identifier}/origin")
    def patch(
        identifier: str,
        data: OriginGrantPatch,
        request: Request,
        identity: Annotated[AdminIdentity, Depends(permission("keys:manage"))],
    ) -> dict[str, Any]:
        expected_revision(request, data.expected_revision)
        return access.patch_grant(
            identity,
            parse_id("key", identifier),
            data,
            admin_token(request),
            request.state.request_id,
        )

    @router.get("/orgin", include_in_schema=False)
    @router.get("/orgin/", include_in_schema=False)
    @router.get("/origin", include_in_schema=False)
    def alias() -> Response:
        return RedirectResponse("/origin/", status_code=307)

    @router.get("/list", include_in_schema=False)
    def list_alias() -> Response:
        return RedirectResponse("/origin/list", status_code=307)

    @router.get("/origin/", include_in_schema=False)
    @router.get("/origin/list", include_in_schema=False)
    def page() -> Response:
        path = access.settings.admin_dist / "origin.html"
        if not path.is_file():
            raise DomainError("origin_ui_unavailable", "原生面板入口资源尚未部署", 503)
        return FileResponse(
            path,
            headers={
                "Cache-Control": "no-store",
                "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'",
                "Referrer-Policy": "no-referrer",
            },
        )

    @router.post("/origin/api/session")
    def login(data: OriginLogin, request: Request) -> Response:
        result = access.login(data.api_key, request.client.host if request.client else "127.0.0.1")
        token = result.pop("token")
        response = JSONResponse(result, headers={"Cache-Control": "no-store"})
        response.set_cookie(
            COOKIE,
            token,
            httponly=True,
            secure=access.settings.secure_cookies,
            samesite=access.settings.cookie_same_site,
            path="/origin",
            max_age=access.settings.session_seconds,
        )
        return response

    @router.get("/origin/api/session")
    def session(request: Request) -> dict[str, Any]:
        identity = actor(request)
        return {
            "csrf_token": access.csrf(request.cookies[COOKIE]),
            "expires_at": int(identity.expires_at.timestamp()),
        }

    @router.delete("/origin/api/session", status_code=204)
    def logout(request: Request) -> Response:
        access.logout(actor(request, write=True))
        response = Response(status_code=204)
        response.delete_cookie(COOKIE, path="/origin")
        return response

    @router.get("/origin/api/devices")
    async def devices(request: Request) -> dict[str, Any]:
        identity = await asyncio.to_thread(actor, request)
        result = await asyncio.to_thread(access.devices, identity)

        async def observe(row: dict[str, Any]) -> None:
            try:
                target = await asyncio.to_thread(access.target, identity, row["slug"])
                queue = await json_call(target, "/queue")
                queue_entries(queue)
                row.update(
                    online=True,
                    queue_running=len(queue["queue_running"]),
                    queue_pending=len(queue["queue_pending"]),
                )
            except (DomainError, httpx.HTTPError, ValueError):
                row.update(online=False, queue_running=None, queue_pending=None)

        # One response has a 12s total observation budget, including offline devices.
        for row in result["data"]:
            row.update(online=False, queue_running=None, queue_pending=None)
        try:
            async with asyncio.timeout(12):
                for offset in range(0, len(result["data"]), 4):
                    await asyncio.gather(
                        *(observe(row) for row in result["data"][offset : offset + 4])
                    )
        except TimeoutError:
            result["observation_incomplete"] = True
        return result

    @router.post("/origin/api/devices/{slug}/acquire")
    async def acquire(slug: str, request: Request) -> dict[str, Any]:
        identity = await asyncio.to_thread(actor, request, write=True)
        target = await asyncio.to_thread(access.target, identity, slug)
        hold = await asyncio.to_thread(access.reserve, identity, target["id"])
        if not hold["reused"]:
            try:
                empty = not queue_entries(await json_call(target, "/queue"))
                await asyncio.to_thread(access.activate, UUID(hold["id"]), empty_queue=empty)
                if not empty:
                    raise DomainError(
                        "origin_external_queue_busy", "设备存在原生任务，等待完成后再申请", 409
                    )
            except (httpx.HTTPError, ValueError) as exc:
                await asyncio.to_thread(access.closing, identity, target["id"])
                raise DomainError(
                    "origin_source_unavailable", "设备暂不可访问，占用会在安全核对后归还", 502
                ) from exc
        return {"url": "/origin/" + slug + "/", "state": "active"}

    @router.post("/origin/api/devices/{slug}/release")
    async def release(slug: str, request: Request) -> dict[str, Any]:
        identity = await asyncio.to_thread(actor, request, write=True)
        target = await asyncio.to_thread(access.target, identity, slug)
        identifiers = await asyncio.to_thread(access.closing, identity, target["id"])
        results = [
            await asyncio.to_thread(reconciler.reconcile, identifier) for identifier in identifiers
        ]
        return {
            "state": "closed" if all(row["state"] == "closed" for row in results) else "closing",
            "message": "任务停止及队列核对完成后归还设备，不会强制终止生成。",
        }

    def client_id(identity: OriginActor, target: dict[str, Any], client: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", client):
            raise DomainError("origin_invalid_client", "客户端标识无效", 422)
        return hashlib.sha256(
            (str(identity.key_id) + str(target["id"]) + client).encode()
        ).hexdigest()

    @router.websocket("/origin/{slug}/ws")
    @router.websocket("/origin/{slug}/api/ws")
    async def socket(websocket: WebSocket, slug: str) -> None:
        token, peer = (
            websocket.cookies.get(COOKIE, ""),
            websocket.client.host if websocket.client else "127.0.0.1",
        )
        try:
            identity = await asyncio.to_thread(
                access.authenticate,
                token,
                peer,
                csrf=websocket.query_params.get("origin_csrf"),
                write=True,
            )
            target = await asyncio.to_thread(access.target, identity, slug)
            public_client = websocket.query_params.get("clientId") or uuid4().hex
            upstream_client = client_id(identity, target, public_client)
        except DomainError:
            await websocket.close(code=4403)
            return
        if socket_counts.get(identity.key_id, 0) >= 4 or sum(socket_counts.values()) >= 32:
            await websocket.close(code=4429)
            return
        socket_counts[identity.key_id] = socket_counts.get(identity.key_id, 0) + 1
        try:
            await relay_socket(
                websocket, slug, identity, target, token, peer, public_client, upstream_client
            )
        finally:
            socket_counts[identity.key_id] -= 1
            if not socket_counts[identity.key_id]:
                socket_counts.pop(identity.key_id)

    async def relay_socket(
        websocket: WebSocket,
        slug: str,
        identity: OriginActor,
        target: dict[str, Any],
        token: str,
        peer: str,
        public_client: str,
        upstream_client: str,
    ) -> None:
        url = (
            target["base_url"]
            .replace("https://", "wss://", 1)
            .replace("http://", "ws://", 1)
            .rstrip("/")
            + "/ws?"
            + urlencode({"clientId": upstream_client})
        )
        headers = {"Authorization": target["authorization"]} if target["authorization"] else None
        try:
            async with connect(
                url,
                additional_headers=headers,
                origin=target["base_url"],
                open_timeout=5,
                close_timeout=2,
                ping_interval=20,
                max_size=8 * 1024 * 1024,
                max_queue=1,
                proxy=None,
            ) as upstream:
                await websocket.accept()
                owned = await asyncio.to_thread(access.prompt_ids, identity, target["id"])
                current_owned = False

                async def receive() -> None:
                    nonlocal current_owned, owned
                    async for message in upstream:
                        if isinstance(message, bytes):
                            if current_owned:
                                await websocket.send_bytes(message)
                            continue
                        value = json.loads(message)
                        kind, data = value.get("type"), value.get("data", {})
                        if kind == "status":
                            data = {"status": data.get("status", {}), "sid": public_client}
                        elif kind == "feature_flags":
                            pass
                        elif kind in {
                            "execution_start",
                            "execution_cached",
                            "executing",
                            "executed",
                            "progress",
                            "progress_state",
                            "progress_text",
                            "execution_error",
                            "execution_interrupted",
                            "execution_success",
                        }:
                            prompt_id = data.get("prompt_id") if isinstance(data, dict) else None
                            if prompt_id:
                                if prompt_id not in owned:
                                    owned = await asyncio.to_thread(
                                        access.prompt_ids, identity, target["id"]
                                    )
                                current_owned = prompt_id in owned
                            if not current_owned:
                                continue
                            if kind == "executed" and isinstance(data, dict):
                                for filename, subfolder, file_type in output_files(
                                    data.get("output", {})
                                ):
                                    await asyncio.to_thread(
                                        access.remember_file,
                                        identity.key_id,
                                        target["id"],
                                        filename,
                                        subfolder,
                                        file_type,
                                    )
                        else:
                            continue
                        await websocket.send_json({"type": kind, "data": data})

                async def send() -> None:
                    while True:
                        message = await websocket.receive()
                        if message["type"] == "websocket.disconnect":
                            return
                        if message.get("text") is not None:
                            value = json.loads(message["text"])
                            if value.get("type") == "feature_flags":
                                await upstream.send(json.dumps(value))

                async def guard() -> None:
                    while True:
                        await asyncio.sleep(5)
                        await asyncio.to_thread(access.authenticate, token, peer)
                        await asyncio.to_thread(access.target, identity, slug)

                tasks = [asyncio.create_task(action()) for action in (receive, send, guard)]
                try:
                    done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    for task in done:
                        task.result()
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
        except (
            DomainError,
            httpx.HTTPError,
            OSError,
            ValueError,
            ConnectionClosed,
            WebSocketDisconnect,
        ):
            if websocket.application_state.value == 1:
                await websocket.close(code=4403)

    @router.api_route(
        "/origin/{slug}/{path:path}",
        methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"],
        include_in_schema=False,
    )
    async def proxy(slug: str, path: str, request: Request) -> Response:
        write = request.method not in {"GET", "HEAD"}
        identity = await asyncio.to_thread(actor, request, write=write)
        target = await asyncio.to_thread(access.target, identity, slug)
        path = safe_path(path)
        native = path.removeprefix("api/")
        prefix = "/origin/" + slug + "/"
        headers = upstream_headers(target, request)
        if path == "__origin_bootstrap.js" and not write:
            config = {
                "prefix": prefix,
                "csrf": access.csrf(request.cookies[COOKIE]),
                "namespace": str(identity.key_id) + ":" + str(target["id"]),
            }
            source = (access.settings.admin_dist / "origin-bootstrap.js").read_text(
                encoding="utf-8"
            )
            return Response(
                "window.__originConfig=" + json.dumps(config) + ";\n" + source,
                media_type="text/javascript",
                headers={"Cache-Control": "no-store"},
            )
        if native in {"settings", "users"} or native.startswith(
            ("settings/", "userdata", "v2/userdata")
        ):
            return await user_data(identity, target, native, request)
        if native in {"user.css"}:
            return Response("", media_type="text/css")
        if native == "prompt" and request.method == "POST":
            body = await request.json()
            if (
                not isinstance(body, dict)
                or not isinstance(body.get("prompt"), dict)
                or len(body["prompt"]) > 3000
                or not isinstance(body.get("extra_data", {}), dict)
            ):
                raise DomainError("origin_invalid_prompt", "请提交有效的 ComfyUI API Workflow", 422)
            native_client_id = client_id(identity, target, str(body.get("client_id") or "origin"))
            identifier, association, replay = await asyncio.to_thread(
                access.prompt_intent,
                identity,
                target["id"],
                body,
                request.headers.get("idempotency-key", ""),
            )
            if replay is not None:
                return JSONResponse(replay, status_code=400 if "error" in replay else 200)
            wire = copy.deepcopy(body)
            wire["client_id"] = native_client_id
            wire.pop("prompt_id", None)
            wire["extra_data"] = {
                "extra_pnginfo": body.get("extra_data", {}).get("extra_pnginfo", {}),
                "origin_control": {"association_id": association},
            }
            # Queue priority and source-side secret overrides are not delegated.
            wire.pop("number", None)
            wire.pop("front", None)
            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(30, connect=4), trust_env=False, follow_redirects=False
                ) as client:
                    upstream = await client.post(
                        target["base_url"].rstrip("/") + "/prompt", headers=headers, json=wire
                    )
                    if len(upstream.content) > 1024 * 1024:
                        raise ValueError("Oversized prompt receipt")
                    receipt = upstream.json()
                    rejected = (
                        upstream.status_code == 400
                        and isinstance(receipt, dict)
                        and "error" in receipt
                    )
                    await asyncio.to_thread(
                        access.prompt_receipt,
                        identifier,
                        receipt if isinstance(receipt, dict) else None,
                        rejected=rejected,
                    )
                    if upstream.status_code != 200 and not rejected:
                        raise ValueError("Unconfirmed upstream acceptance")
                    return JSONResponse(receipt, status_code=400 if rejected else 200)
            except (httpx.HTTPError, ValueError) as exc:
                await asyncio.to_thread(access.prompt_receipt, identifier, None)
                raise DomainError(
                    "origin_submission_unknown",
                    "提交结果未知，系统正在核对原任务，请勿重复运行",
                    409,
                ) from exc
        if native.startswith("upload/") and request.method == "POST":
            await asyncio.to_thread(access.active, identity, target["id"])
            return await upload(identity, target, request, headers)
        if native == "view" and not write:
            name, folder, kind = file_parameters(request)
            if not await asyncio.to_thread(
                access.owns_file, identity, target["id"], name, folder, kind
            ):
                try:
                    hold = await asyncio.to_thread(access.active, identity, target["id"])
                    await asyncio.to_thread(reconciler.reconcile, hold)
                except DomainError:
                    pass
                if not await asyncio.to_thread(
                    access.owns_file, identity, target["id"], name, folder, kind
                ):
                    raise DomainError("not_found", "文件不存在或不属于当前 Key", 404)
        elif native == "history" or native.startswith("history/"):
            owned = await asyncio.to_thread(access.prompt_ids, identity, target["id"])
            if request.method != "GET":
                raise DomainError(
                    "origin_operation_denied", "原生执行记录由平台保留，请导出所需流程", 403
                )
            prompt_id = native.removeprefix("history/") if native.startswith("history/") else None
            if prompt_id and prompt_id not in owned:
                raise DomainError("not_found", "任务不存在", 404)
            history = await json_call(
                target, "/history/" + prompt_id if prompt_id else "/history?max_items=128"
            )
            return JSONResponse({pid: row for pid, row in history.items() if pid in owned})
        elif native in {"queue", "prompt"} and request.method == "GET":
            queue = await json_call(target, "/queue")
            queue_entries(queue)
            owned = await asyncio.to_thread(access.prompt_ids, identity, target["id"])
            return JSONResponse(
                {
                    key: [entry for entry in queue[key] if entry[1] in owned]
                    for key in ("queue_running", "queue_pending")
                }
                if native == "queue"
                else {
                    "exec_info": {
                        "queue_remaining": sum(entry[1] in owned for entry in queue_entries(queue))
                    }
                }
            )
        elif native in {"queue", "interrupt"} and request.method == "POST":
            await asyncio.to_thread(access.active, identity, target["id"])
            queue = await json_call(target, "/queue")
            queue_entries(queue)
            owned = await asyncio.to_thread(access.prompt_ids, identity, target["id"])
            if native == "interrupt":
                if any(entry[1] not in owned for entry in queue["queue_running"]):
                    raise DomainError("origin_operation_denied", "其他来源的任务不能被中断", 403)
                body = {}
            else:
                requested = await request.json()
                ids = [entry[1] for entry in queue["queue_pending"] if entry[1] in owned]
                body = {
                    "delete": ids
                    if requested.get("clear")
                    else [pid for pid in requested.get("delete", []) if pid in ids]
                }
            async with httpx.AsyncClient(
                timeout=10, trust_env=False, follow_redirects=False
            ) as client:
                upstream = await client.post(
                    target["base_url"].rstrip("/") + "/" + native, headers=headers, json=body
                )
            return Response(
                upstream.content, status_code=upstream.status_code, media_type="application/json"
            )
        elif (native == "jobs" or native.startswith("jobs/")) and request.method == "GET":
            owned = await asyncio.to_thread(access.prompt_ids, identity, target["id"])
            if native.startswith("jobs/"):
                pid = native[5:]
                if pid not in owned:
                    raise DomainError("not_found", "任务不存在", 404)
                return JSONResponse(await json_call(target, "/api/jobs/" + quote(pid, safe="")))
            params = {
                key: value
                for key, value in request.query_params.items()
                if key in {"status", "workflow_id", "sort_by", "sort_order"}
            }
            params.update(limit="2000", offset="0")
            value = await json_call(target, "/api/jobs?" + urlencode(params))
            if not isinstance(value, dict) or not isinstance(value.get("jobs"), list):
                raise DomainError("origin_source_format", "设备任务格式暂不支持", 502)
            jobs = [
                row
                for row in value["jobs"]
                if isinstance(row, dict) and str(row.get("id", row.get("prompt_id", ""))) in owned
            ]
            try:
                limit = min(100, max(1, int(request.query_params.get("limit", "100"))))
                offset = min(2000, max(0, int(request.query_params.get("offset", "0"))))
            except ValueError as exc:
                raise DomainError("origin_invalid_pagination", "分页参数无效", 422) from exc
            return JSONResponse(
                {
                    "jobs": jobs[offset : offset + limit],
                    "pagination": {
                        "offset": offset,
                        "limit": limit,
                        "total": len(jobs),
                        "has_more": offset + limit < len(jobs),
                    },
                }
            )
        elif write or (
            path
            and not path.startswith(STATIC)
            and native.split("/", 1)[0] not in READS
            and path
            not in {
                "favicon.ico",
                "materialdesignicons.min.css",
                "manifest.json",
                "service-worker.js",
            }
            and native != "view"
        ):
            raise DomainError(
                "origin_operation_denied", "此原生接口未开放；设备维护请使用 Admin", 403
            )
        return await stream(target, path, request, headers, prefix, identity)

    async def user_data(
        identity: OriginActor, target: dict[str, Any], native: str, request: Request
    ) -> Response:
        write = request.method not in {"GET", "HEAD"}
        raw = await request.body() if write else b""
        if len(raw) > 4 * 1024 * 1024:
            raise DomainError("origin_userdata_limit", "单个工作流文件超过 4 MiB", 413)
        query = request.query_params
        now = int(time.time() * 1000)
        result: list[Response] = []

        def info(name: str, data: dict[str, Any], *, relative: str = "") -> dict[str, Any]:
            return {
                "path": name.removeprefix(relative),
                "size": len(data["files"][name].encode()),
                "modified": data.get("file_times", {}).get(name, 0),
                "created": data.get("file_times", {}).get(name, 0),
            }

        def operate(data: dict[str, Any]) -> dict[str, Any]:
            data = copy.deepcopy(data)
            files = data.setdefault("files", {})
            times = data.setdefault("file_times", {})
            response: Response = JSONResponse({})
            if native == "users":
                if write:
                    raise DomainError("origin_operation_denied", "身份由 API Key 管理", 403)
                response = JSONResponse({"storage": "server", "migrated": True})
            elif native == "settings" or native.startswith("settings/"):
                settings = data.setdefault("settings", {})
                if not write:
                    response = JSONResponse(
                        settings.get(native[9:]) if native.startswith("settings/") else settings
                    )
                elif request.method == "POST":
                    body = json.loads(raw)
                    if native.startswith("settings/"):
                        settings[native[9:]] = body
                    elif isinstance(body, dict):
                        settings.update(body)
                    else:
                        raise DomainError("origin_invalid_settings", "设置格式不正确", 422)
                else:
                    raise DomainError("origin_operation_denied", "此设置操作未开放", 403)
            elif native in {"userdata", "v2/userdata"} and not write:
                folder = safe_path(
                    query.get("path" if native == "v2/userdata" else "dir", "")
                ).rstrip("/")
                prefix = folder + "/" if folder else ""
                names = sorted(name for name in files if name.startswith(prefix))
                if native == "v2/userdata":
                    listing: dict[str, dict[str, Any]] = {}
                    for name in names:
                        parts = name[len(prefix) :].split("/")
                        for index in range(1, len(parts)):
                            directory = prefix + "/".join(parts[:index])
                            listing[directory] = {
                                "name": parts[index - 1],
                                "path": directory,
                                "type": "directory",
                            }
                        listing[name] = {
                            **info(name, data),
                            "name": parts[-1],
                            "type": "file",
                            "modified": times.get(name, 0) / 1000,
                        }
                    response = JSONResponse(
                        sorted(
                            listing.values(),
                            key=lambda row: (row["type"] != "directory", row["name"].lower()),
                        )
                    )
                else:
                    if query.get("recurse", "false").lower() != "true":
                        names = [name for name in names if "/" not in name[len(prefix) :]]
                    if query.get("full_info", "false").lower() == "true":
                        response = JSONResponse(
                            [info(name, data, relative=prefix) for name in names]
                        )
                    else:
                        relative = [name[len(prefix) :] for name in names]
                        response = JSONResponse(
                            [[name, *name.split("/")] for name in relative]
                            if query.get("split", "false").lower() == "true"
                            else relative
                        )
            elif native.startswith("userdata/"):
                name = safe_path(native[9:])
                if not name:
                    raise DomainError("not_found", "个人文件不存在", 404)
                if not write:
                    if name == "user.css":
                        response = Response("", media_type="text/css")
                    elif name not in files:
                        raise DomainError("not_found", "个人文件不存在", 404)
                    else:
                        response = Response(
                            files[name],
                            media_type="application/json",
                            headers={
                                "Content-Disposition": "attachment",
                                "X-Content-Type-Options": "nosniff",
                            },
                        )
                elif request.method == "DELETE":
                    if name not in files:
                        raise DomainError("not_found", "个人文件不存在", 404)
                    files.pop(name)
                    times.pop(name, None)
                    response = Response(status_code=204)
                elif request.method == "POST":
                    if "/move/" in name:
                        before, name = name.split("/move/", 1)
                        if before not in files:
                            raise DomainError("not_found", "个人文件不存在", 404)
                        if name in files and query.get("overwrite") == "false":
                            raise DomainError("origin_file_exists", "目标文件已存在", 409)
                        files[name] = files.pop(before)
                        times.pop(before, None)
                    else:
                        if name in files and query.get("overwrite") == "false":
                            raise DomainError("origin_file_exists", "目标文件已存在", 409)
                        files[name] = raw.decode("utf-8")
                    times[name] = now
                    response = JSONResponse(
                        info(name, data)
                        if query.get("full_info", "false").lower() == "true"
                        else name
                    )
                else:
                    raise DomainError("origin_operation_denied", "此个人文件操作未开放", 403)
            else:
                raise DomainError("origin_operation_denied", "此个人存储操作未开放", 403)
            result.append(response)
            return data

        try:
            if write:
                await asyncio.to_thread(access.userdata, identity, target["id"], mutate=operate)
            else:
                operate(await asyncio.to_thread(access.userdata, identity, target["id"]))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DomainError(
                "origin_invalid_userdata", "个人文件必须为有效 UTF-8 文本", 422
            ) from exc
        return result[0]

    async def upload(
        identity: OriginActor, target: dict[str, Any], request: Request, headers: dict[str, str]
    ) -> Response:
        async with request.form(max_files=1, max_fields=10) as form:
            file = form.get("image")
            if not isinstance(file, UploadFile):
                raise DomainError("origin_upload_invalid", "请选择上传文件", 422)
            suffix = PurePosixPath(file.filename or "input.png").suffix.lower()
            if suffix not in {
                ".png",
                ".jpg",
                ".jpeg",
                ".webp",
                ".gif",
                ".bmp",
                ".tif",
                ".tiff",
                ".mp4",
                ".webm",
                ".mov",
                ".mkv",
                ".wav",
                ".mp3",
                ".flac",
                ".ogg",
                ".m4a",
                ".glb",
                ".obj",
                ".ply",
            }:
                raise DomainError("origin_upload_invalid", "此文件类型未开放", 422)
            name, folder, boundary = (
                uuid4().hex + suffix,
                "origin/" + identity.key_id.hex,
                uuid4().hex,
            )

            async def content() -> AsyncIterator[bytes]:
                for key, value in (
                    ("subfolder", folder),
                    ("type", "input"),
                    ("overwrite", "false"),
                ):
                    yield f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
                yield f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{name}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode()
                total = 0
                while chunk := await file.read(65536):
                    total += len(chunk)
                    if total > 127 * 1024 * 1024:
                        raise DomainError("origin_upload_limit", "单次上传最多 127 MiB", 413)
                    yield chunk
                yield f"\r\n--{boundary}--\r\n".encode()

            headers["Content-Type"] = "multipart/form-data; boundary=" + boundary
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(120, connect=4), trust_env=False, follow_redirects=False
            ) as client:
                upstream = await client.post(
                    target["base_url"].rstrip("/") + "/upload/image",
                    headers=headers,
                    content=content(),
                )
                if upstream.status_code != 200 or len(upstream.content) > 65536:
                    raise DomainError("origin_upload_failed", "设备未确认上传，请重试", 502)
                receipt = upstream.json()
            for filename, subfolder, kind in output_files(
                {
                    "filename": receipt.get("name"),
                    "subfolder": receipt.get("subfolder", ""),
                    "type": receipt.get("type", "input"),
                }
            ):
                await asyncio.to_thread(
                    access.remember_file, identity.key_id, target["id"], filename, subfolder, kind
                )
            return JSONResponse(receipt)

    async def stream(
        target: dict[str, Any],
        path: str,
        request: Request,
        headers: dict[str, str],
        prefix: str,
        identity: OriginActor,
    ) -> Response:
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(120, connect=4), trust_env=False, follow_redirects=False
        )
        try:
            upstream = await client.send(
                client.build_request(
                    request.method,
                    target["base_url"].rstrip("/") + "/" + quote(path, safe="/-_.~"),
                    params=str(request.query_params),
                    headers=headers,
                ),
                stream=True,
            )
        except httpx.HTTPError as exc:
            await client.aclose()
            raise DomainError("origin_source_unavailable", "设备暂不可访问", 502) from exc
        response_headers = {
            name: value
            for name, value in upstream.headers.items()
            if name.lower() not in SKIP_HEADERS
        }
        response_headers.update(
            {"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer"}
        )
        if path == "":
            response_headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' data: blob:; connect-src 'self'; worker-src 'self' blob:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
        if "location" in response_headers:
            try:
                response_headers["location"] = proxied_location(
                    response_headers["location"], target["base_url"], prefix
                )
            except DomainError:
                await upstream.aclose()
                await client.aclose()
                raise
        rewrite = (
            path == ""
            or path.removeprefix("api/") in {"extensions", "workflow_templates", "jobs"}
            or path.endswith((".js", ".css"))
        )
        if rewrite:
            try:
                data = bytearray()
                async for chunk in upstream.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > 32 * 1024 * 1024:
                        raise DomainError("origin_source_limit", "前端资源超过读取上限", 502)
                content = data.decode("utf-8")
                if path == "":
                    bootstrap = f'<base href="{prefix}"><script src="{prefix}__origin_bootstrap.js"></script>'
                    content = re.sub(
                        r"""((?:src|href)=["'])/(?!/)""", lambda match: match[1] + prefix, content
                    )
                    content = content.replace("<head>", "<head>" + bootstrap, 1)
                elif path.removeprefix("api/") == "extensions":
                    content = json.dumps(
                        [prefix + name.lstrip("/") for name in json.loads(content)]
                    )
                elif path.removeprefix("api/") == "jobs":
                    value = json.loads(content)
                    owned = await asyncio.to_thread(access.prompt_ids, identity, target["id"])
                    if isinstance(value, dict):
                        for key in ("jobs", "data", "items"):
                            if isinstance(value.get(key), list):
                                value[key] = [
                                    row
                                    for row in value[key]
                                    if str(row.get("id", row.get("prompt_id", ""))) in owned
                                ]
                        for key in ("total", "total_count"):
                            if key in value:
                                value[key] = len(
                                    value.get("jobs")
                                    or value.get("data")
                                    or value.get("items")
                                    or []
                                )
                    content = json.dumps(value)
                elif path.endswith((".js", ".css")):
                    content = re.sub(
                        r"""(["'(])/(assets|scripts|extensions)/""",
                        lambda match: match[1] + prefix + match[2] + "/",
                        content,
                    )
                response_headers.pop("etag", None)
                return Response(content, status_code=upstream.status_code, headers=response_headers)
            finally:
                await upstream.aclose()
                await client.aclose()

        async def body() -> AsyncIterator[bytes]:
            try:
                async for chunk in upstream.aiter_bytes(65536):
                    yield chunk
            finally:
                await upstream.aclose()
                await client.aclose()

        if "content-length" in upstream.headers:
            response_headers["content-length"] = upstream.headers["content-length"]
        return StreamingResponse(body(), status_code=upstream.status_code, headers=response_headers)

    return router
