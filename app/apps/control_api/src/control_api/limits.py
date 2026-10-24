from __future__ import annotations

from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send


def body_limit(path: str, content_type: str = "") -> int:
    if path.startswith("/internal/v1/commands/") and path.endswith(("/receipts", "/reconciliations")):
        return 8 * 1024 * 1024
    if path.startswith("/internal/v1/uploads/") and path.endswith("/content"):
        return 2147483648
    media = path in ("/v1/images/edits", "/v1/images/edits/async", "/v1/videos")
    multipart = path in ("/v1/assets", "/gateway/v1/assets") and content_type.startswith("multipart/form-data")
    return 128 * 1024 * 1024 if media or multipart else 10 * 1024 * 1024 if "/workflows" in path else 1024 * 1024


class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        content_type = next((v.decode("latin-1") for k, v in scope["headers"] if k == b"content-type"), "")
        maximum = body_limit(scope["path"], content_type)
        consumed = 0

        async def limited_receive() -> Message:
            nonlocal consumed
            message = await receive()
            if message["type"] == "http.request":
                consumed += len(message.get("body", b""))
                if consumed > maximum:
                    raise HTTPException(status_code=413, detail="body_too_large")
            return message

        await self.app(scope, limited_receive, send)
