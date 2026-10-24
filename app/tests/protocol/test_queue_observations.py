from __future__ import annotations

import asyncio

import httpx
import pytest
from windows_worker.observations import MAX_QUEUE_BYTES, observe_queue


@pytest.mark.parametrize("response, expected", [
    (httpx.Response(200, json={"queue_running": [[1]], "queue_pending": [[], []]}), {"reachable": True, "queue_running": 1, "queue_pending": 2}),
    (httpx.Response(200, json={}), {"reachable": False, "error_code": "invalid_response"}),
    (httpx.Response(401), {"reachable": False, "error_code": "unreachable"}),
    (httpx.Response(200, content=b"x" * (MAX_QUEUE_BYTES + 1)), {"reachable": False, "error_code": "response_too_large"}),
])
def test_queue_observation_preserves_unknown(response: httpx.Response, expected: dict[str, object]) -> None:
    async def run() -> None:
        def network(request: httpx.Request) -> httpx.Response:
            assert request.method == "GET" and request.url.path == "/queue"
            return httpx.Response(response.status_code, headers=response.headers, stream=httpx.ByteStream(response.content))
        async with httpx.AsyncClient(base_url="http://127.0.0.1:8188", transport=httpx.MockTransport(network)) as client:
            assert await observe_queue(client) == expected
    asyncio.run(run())
