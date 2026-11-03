from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import HTTPException, Request, Response
from fastapi.routing import APIRoute


class AssetUploadRoute(APIRoute):
    """Bound multipart cardinality before FastAPI's generated file binding."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handle(request: Request) -> Response:
            if request.method == "POST" and request.url.path in ("/v1/assets", "/gateway/v1/assets"):
                async with request.form(max_files=1, max_fields=1, max_part_size=1024) as form:
                    names = [name for name, _ in form.multi_items()]
                    if sorted(names) not in (["file"], ["file", "kind"]):
                        raise HTTPException(422, "Only one file and one optional kind are allowed")
                    return await original(request)
            return await original(request)

        return handle
