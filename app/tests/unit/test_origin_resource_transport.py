"""Cold editor bursts, upstream-cookie isolation and mounted module imports."""

import asyncio
from collections import Counter
from uuid import uuid4

import httpx
from control_api.origin_routes import resource_paths
from control_api.origin_transport import NativeResourcePool


def test_native_resource_burst_holds_device_slot_until_body_closed() -> None:
    async def run() -> None:
        active: Counter[str] = Counter()
        peak: Counter[str] = Counter()
        closed: Counter[str] = Counter()

        class Body(httpx.AsyncByteStream):
            def __init__(self, host: str) -> None:
                self.host = host

            async def __aiter__(self):  # type: ignore[no-untyped-def]
                await asyncio.sleep(0.005)
                yield b"native-resource"

            async def aclose(self) -> None:
                active[self.host] -= 1
                closed[self.host] += 1

        async def handle(request: httpx.Request) -> httpx.Response:
            assert "cookie" not in request.headers
            host = request.url.host
            active[host] += 1
            peak[host] = max(peak[host], active[host])
            return httpx.Response(
                200, stream=Body(host), headers={"Set-Cookie": "source-private=must-not-cross-keys"}
            )

        pool = NativeResourcePool(transport=httpx.MockTransport(handle))
        devices = {"first.test": uuid4(), "second.test": uuid4()}

        async def read(host: str) -> None:
            async with pool.open(
                devices[host], "GET", "http://" + host + "/scripts/app.js", params="", headers={}
            ) as response:
                assert await response.aread() == b"native-resource"

        try:
            await asyncio.gather(*(read(host) for host in devices for _ in range(24)))
            # A later request must not receive a cookie accepted from another Key.
            await read("first.test")
            assert peak == {"first.test": 8, "second.test": 8}
            assert active == {"first.test": 0, "second.test": 0}
            assert closed == {"first.test": 25, "second.test": 24}
            assert not list(pool.client.cookies.jar)
        finally:
            await pool.close()
        assert pool.client.is_closed and not pool.devices

    asyncio.run(run())


def test_native_resource_failure_and_cancellation_release_slots() -> None:
    async def run() -> None:
        device, attempts = uuid4(), 0

        class Body(httpx.AsyncByteStream):
            async def __aiter__(self):  # type: ignore[no-untyped-def]
                yield b"recovered"

        async def handle(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts <= 8:
                raise httpx.ConnectError("synthetic unavailable source", request=request)
            return httpx.Response(200, stream=Body())

        pool = NativeResourcePool(transport=httpx.MockTransport(handle))

        async def read() -> bytes:
            async with pool.open(device, "GET", "http://source.test/a", params="", headers={}) as r:
                return await r.aread()

        try:
            result = await asyncio.gather(*(read() for _ in range(8)), return_exceptions=True)
            assert all(isinstance(value, httpx.ConnectError) for value in result)
            assert await asyncio.wait_for(read(), 1) == b"recovered"
            entered = asyncio.Event()
            held: list[httpx.Response] = []

            async def hold() -> None:
                async with pool.open(
                    device, "GET", "http://source.test/a", params="", headers={}
                ) as r:
                    held.append(r)
                    entered.set()
                    await asyncio.Event().wait()

            task = asyncio.create_task(hold())
            await entered.wait()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            assert held[0].is_closed
            assert await asyncio.wait_for(read(), 1) == b"recovered"
        finally:
            await pool.close()

    asyncio.run(run())


def test_native_relative_modules_resolve_at_source_root_before_mounting() -> None:
    prefix = "/origin/rtx4090/"
    source = """import { app } from '../../../scripts/app.js';
import('../../../scripts/api.js?variant=1');
export { x } from '../shared.js';
import './style.css';
const text = '../../../scripts/unchanged.js';
import { external } from 'https://external.test/app.js';
const absolute = '/assets/main.js';"""
    result = resource_paths(source, "extensions/plugin/ui.mjs", prefix)
    assert "from '/origin/rtx4090/scripts/app.js'" in result
    assert "import('/origin/rtx4090/scripts/api.js?variant=1')" in result
    assert "from '/origin/rtx4090/extensions/shared.js'" in result
    assert "import '/origin/rtx4090/extensions/plugin/style.css'" in result
    assert "const text = '../../../scripts/unchanged.js'" in result
    assert "from 'https://external.test/app.js'" in result
    assert "'/origin/rtx4090/assets/main.js'" in result
