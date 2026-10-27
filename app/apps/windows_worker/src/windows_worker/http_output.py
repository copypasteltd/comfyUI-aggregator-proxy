from __future__ import annotations

import asyncio
import hashlib
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
from generation_domain.errors import DomainError
from generation_domain.workflows import relative_file

from windows_worker.output_copy import BLOCK_BYTES, _mismatch, _plain, _signature, _target_directory


def output_locator(item: dict[str, Any]) -> dict[str, str]:
    filename, folder, kind = item.get("filename"), item.get("subfolder", ""), item.get("type")
    if not isinstance(filename, str) or not isinstance(folder, str) or kind not in ("output", "temp") or any(c in filename for c in ("/", "\\")):
        raise DomainError("invalid_output_path", "ComfyUI HTTP 输出定位无效")
    folder = folder.replace("\\", "/")
    relative_file((folder + "/" if folder else "") + filename)
    return {"filename": filename, "subfolder": folder, "type": kind}


def declared_size(response: httpx.Response, maximum: int) -> int:
    length = response.headers.get("content-length", "")
    if not length.isascii() or not length.isdigit() or len(length) > 12 or response.headers.get("content-encoding", "identity") != "identity":
        raise DomainError("invalid_output_response", "HTTP 输出必须提供原始文件的完整长度")
    size = int(length)
    if not 0 < size <= maximum:
        raise DomainError("output_too_large", "输出超过发布包大小限制或为空")
    return size


async def download_output(
    client: httpx.AsyncClient,
    item: dict[str, Any],
    target: Path,
    maximum: int,
    root: Path,
    verify: Callable[[Path, int], Awaitable[tuple[str, int]]],
) -> tuple[str, int]:
    """Resume an exact HTTP source prefix, without truncation or extra media copies."""
    params = output_locator(item)
    _target_directory(root, target)
    try:
        existing = target.lstat()
    except FileNotFoundError:
        existing = None
    if existing is not None and not _plain(existing):
        raise _mismatch()
    async with asyncio.timeout(600):
        async with client.stream("GET", "/view", params=params, headers={"Accept-Encoding": "identity"}) as response:
            response.raise_for_status()
            if response.status_code != 200:
                raise DomainError("invalid_output_response", "HTTP 输出必须返回完整原文件")
            expected = declared_size(response, maximum)
            if existing is not None and existing.st_size > expected:
                raise _mismatch()
            with target.open("r+b" if existing is not None else "x+b") as outgoing:
                original = os.fstat(outgoing.fileno())
                if not _plain(original) or existing is not None and _signature(original) != _signature(existing):
                    raise _mismatch()
                digest, size, previous = hashlib.sha256(), 0, original.st_size
                async for chunk in response.aiter_bytes(BLOCK_BYTES):
                    size += len(chunk)
                    if size > expected:
                        raise DomainError("output_not_stable", "HTTP 输出长度变化")
                    prefix = min(previous, len(chunk))
                    if prefix:
                        if outgoing.read(prefix) != chunk[:prefix]:
                            raise _mismatch()
                        previous -= prefix
                    if prefix < len(chunk):
                        outgoing.write(chunk[prefix:])
                    digest.update(chunk)
                if size != expected or previous:
                    raise DomainError("output_not_stable", "HTTP 输出不完整")
                outgoing.flush()
                os.fsync(outgoing.fileno())
                confirmed = await verify(target, maximum)
                current = target.lstat()
                if not _plain(current) or (current.st_dev, current.st_ino) != (original.st_dev, original.st_ino) or confirmed != (digest.hexdigest(), size):
                    raise _mismatch()
                head = await client.head("/view", params=params, headers={"Accept-Encoding": "identity"})
                head.raise_for_status()
                if head.status_code != 200 or declared_size(head, maximum) != size or any(response.headers.get(name) != head.headers.get(name) for name in ("etag", "last-modified")):
                    raise DomainError("output_not_stable", "HTTP 原文件在下载期间变化")
                return confirmed
