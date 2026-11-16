"""Reuse native resource connections without retaining upstream session cookies."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from http.cookiejar import CookieJar, DefaultCookiePolicy
from uuid import UUID

import httpx


class NativeResourcePool:
    """Limit each device's cold frontend burst for the entire response lifetime."""

    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(120, connect=15, pool=30),
            limits=httpx.Limits(max_connections=64, max_keepalive_connections=32),
            cookies=CookieJar(policy=DefaultCookiePolicy(allowed_domains=())),
            trust_env=False,
            follow_redirects=False,
            transport=transport,
        )
        self.devices: dict[UUID, asyncio.Semaphore] = {}

    @asynccontextmanager
    async def open(
        self, device: UUID, method: str, url: str, *, params: str, headers: dict[str, str]
    ) -> AsyncIterator[httpx.Response]:
        gate = self.devices.setdefault(device, asyncio.Semaphore(8))
        async with gate:
            response = await self.client.send(
                self.client.build_request(method, url, params=params, headers=headers), stream=True
            )
            try:
                yield response
            finally:
                await response.aclose()

    async def close(self) -> None:
        await self.client.aclose()
        self.devices.clear()
