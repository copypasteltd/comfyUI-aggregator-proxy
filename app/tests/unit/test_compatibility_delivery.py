import asyncio
import base64
import hashlib
from collections.abc import Iterator
from threading import Event

import pytest
from control_api.compatibility import encode_download
from control_api.compatibility_inputs import finished_io, strict_object
from generation_domain.errors import DomainError
from generation_infrastructure.content import AuthorizedDownload


def test_inline_delivery_is_bounded_verified_and_closes_iterator() -> None:
    sample = b"synthetic media chunks"
    closed = []

    def chunks() -> Iterator[bytes]:
        try:
            yield sample[:3]
            yield sample[3:]
        finally:
            closed.append(True)

    headers = {"Content-Length": str(len(sample)), "ETag": '"' + hashlib.sha256(sample).hexdigest() + '"'}
    result = encode_download(AuthorizedDownload(chunks(), 200, headers, "image/png"))
    assert base64.b64decode(result) == sample and closed == [True]


@pytest.mark.parametrize("mode", ["unbounded", "oversize", "overrun", "truncated", "hash", "type", "range"])
def test_inline_delivery_rejects_invalid_media(mode: str) -> None:
    sample = b"synthetic-private-image"
    headers = {"Content-Length": str(len(sample)), "ETag": '"' + hashlib.sha256(sample).hexdigest() + '"'}
    if mode == "unbounded":
        headers.pop("Content-Length")
    if mode == "oversize":
        headers["Content-Length"] = str(32 * 1024 * 1024 + 1)
    if mode == "overrun":
        headers["Content-Length"] = str(len(sample) - 1)
    if mode == "truncated":
        headers["Content-Length"] = str(len(sample) + 1)
    if mode == "hash":
        headers["ETag"] = '"' + "0" * 64 + '"'
    download = AuthorizedDownload(iter([sample]), 206 if mode == "range" else 200, headers, "video/mp4" if mode == "type" else "image/png")
    with pytest.raises(DomainError) as error:
        encode_download(download)
    assert error.value.code == "invalid_image_delivery" and "synthetic-private" not in str(error.value)


@pytest.mark.parametrize("raw", ['{"seed":1,"seed":2}', '{"seed":1.00000000000000001}', '{"seed":1e999}', '{"negative_prompt":"\\ud800"}'])
def test_compatibility_json_rejects_ambiguity_and_lossy_numbers(raw: str) -> None:
    with pytest.raises(ValueError):
        strict_object(raw)


async def test_cancelled_upload_waits_for_source_user_before_releasing_resources() -> None:
    entered, release, stopped = Event(), Event(), Event()

    def upload() -> None:
        entered.set()
        assert release.wait(3)
        stopped.set()

    task = asyncio.create_task(finished_io(upload))
    assert await asyncio.to_thread(entered.wait, 3)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done() and not stopped.is_set()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stopped.is_set()
