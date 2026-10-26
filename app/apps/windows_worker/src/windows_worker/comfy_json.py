"""Bounded ComfyUI metadata; never buffer a response before enforcing its limit."""
from __future__ import annotations

import asyncio
import json
import math
import re
import time
import zlib
from typing import Any

import httpx
from generation_domain.errors import DomainError
from generation_domain.workflows import relative_file

MAX_RECEIPT_BYTES = 64 * 1024
MAX_SYSTEM_BYTES = 256 * 1024
MAX_QUEUE_BYTES = 2 * 1024 * 1024
MAX_HISTORY_BYTES = 8 * 1024 * 1024
MAX_INVENTORY_BYTES = 16 * 1024 * 1024
METADATA_SECONDS = 30
METADATA_TIMEOUT = httpx.Timeout(connect=5, read=5, write=20, pool=5)


def invalid_response() -> DomainError:
    return DomainError("invalid_comfy_response", "ComfyUI 元数据格式无效")


class _Body:
    def __init__(self, response: httpx.Response, maximum: int, started: float) -> None:
        self.maximum, self.started = maximum, started
        self.data = bytearray()
        self.wire_bytes = 0
        encoding = response.headers.get("content-encoding", "identity").strip().lower()
        if encoding not in ("identity", "gzip"):
            raise invalid_response()
        self.decoder = zlib.decompressobj(16 + zlib.MAX_WBITS) if encoding == "gzip" else None
        length = response.headers.get("content-length")
        if length is not None:
            if not re.fullmatch(r"[0-9]{1,20}", length):
                raise invalid_response()
            if int(length) > maximum:
                raise DomainError("response_too_large", "ComfyUI 元数据超过读取限额")
        self.check_time()

    def check_time(self) -> None:
        if time.monotonic() - self.started >= METADATA_SECONDS:
            raise httpx.ReadTimeout("ComfyUI metadata time budget exceeded")

    def feed(self, raw: bytes) -> None:
        self.check_time()
        self.wire_bytes += len(raw)
        if self.wire_bytes > self.maximum:
            raise DomainError("response_too_large", "ComfyUI 元数据超过读取限额")
        remaining = self.maximum - len(self.data)
        try:
            # max_length bounds allocation inside zlib, including compression bombs.
            decoded = self.decoder.decompress(raw, remaining + 1) if self.decoder else raw
        except zlib.error as exc:
            raise invalid_response() from exc
        if len(decoded) > remaining:
            raise DomainError("response_too_large", "ComfyUI 元数据超过读取限额")
        if self.decoder and self.decoder.unused_data:
            raise invalid_response()
        self.data.extend(decoded)

    def finish(self) -> dict[str, Any]:
        self.check_time()
        if self.decoder and not self.decoder.eof:
            raise invalid_response()
        try:
            value = json.loads(self.data.decode("utf-8"), parse_constant=_invalid_number, parse_float=_finite_float)
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise invalid_response() from exc
        if not isinstance(value, dict):
            raise invalid_response()
        _check_structure(value)
        self.check_time()
        return value


def _invalid_number(value: str) -> None:
    raise ValueError("Non-finite JSON number")


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Non-finite JSON number")
    return number


def _check_structure(value: dict[str, Any]) -> None:
    # Iterator stack bounds auxiliary memory for wide JSON and ignores braces in strings.
    levels = [iter(value.values())]
    count = 0
    while levels:
        try:
            child = next(levels[-1])
        except StopIteration:
            levels.pop()
            continue
        count += 1
        if count > 500000:
            raise invalid_response()
        if isinstance(child, (dict, list)):
            if len(levels) >= 64:
                raise invalid_response()
            levels.append(iter(child.values() if isinstance(child, dict) else child))


def request_json(client: httpx.Client, method: str, path: str, *, maximum: int, **kwargs: Any) -> dict[str, Any]:
    started = time.monotonic()
    with client.stream(method, path, headers={"Accept-Encoding": "gzip, identity"}, timeout=METADATA_TIMEOUT, follow_redirects=False, **kwargs) as response:
        response.raise_for_status()
        body = _Body(response, maximum, started)
        for chunk in response.iter_raw():
            body.feed(chunk)
        return body.finish()


async def async_request_json(client: httpx.AsyncClient, method: str, path: str, *, maximum: int, **kwargs: Any) -> dict[str, Any]:
    started = time.monotonic()
    try:
        async with asyncio.timeout(METADATA_SECONDS):
            async with client.stream(method, path, headers={"Accept-Encoding": "gzip, identity"}, timeout=METADATA_TIMEOUT, follow_redirects=False, **kwargs) as response:
                response.raise_for_status()
                body = _Body(response, maximum, started)
                async for chunk in response.aiter_raw():
                    body.feed(chunk)
                return body.finish()
    except TimeoutError as exc:
        raise httpx.ReadTimeout("ComfyUI metadata time budget exceeded") from exc


def prompt_identifier(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise invalid_response()
    return value


def checked_queue(value: dict[str, Any]) -> dict[str, Any]:
    for name in ("queue_running", "queue_pending"):
        if not isinstance(value.get(name), list) or len(value[name]) > 1000:
            raise invalid_response()
        if any(not isinstance(entry, list) for entry in value[name]):
            raise invalid_response()
    return value


def checked_history(value: dict[str, Any]) -> dict[str, Any]:
    if len(value) > 1000 or any(not isinstance(entry, dict) or ("status" in entry and not isinstance(entry["status"], dict)) or ("outputs" in entry and not isinstance(entry["outputs"], dict)) for entry in value.values()):
        raise invalid_response()
    return value


def output_files(history: dict[str, Any], node_ids: list[str], field: str) -> list[dict[str, Any]]:
    outputs = history.get("outputs")
    if not isinstance(outputs, dict):
        raise invalid_response()
    files = []
    for identifier in node_ids:
        node = outputs.get(identifier, {})
        if not isinstance(node, dict) or not isinstance(entries := node.get(field, []), list):
            raise invalid_response()
        for entry in entries:
            if (not isinstance(entry, dict)
                    or not isinstance(entry.get("filename"), str) or not entry["filename"]
                    or not isinstance(entry.get("type"), str) or not entry["type"]
                    or not isinstance(entry.get("subfolder", ""), str)):
                raise invalid_response()
            files.append(entry)
    return files


def uploaded_input(value: dict[str, Any]) -> str:
    name, folder = value.get("name"), value.get("subfolder", "")
    if value.get("type") != "input" or not isinstance(name, str) or not 1 <= len(name) <= 255 or "/" in name or "\\" in name or not isinstance(folder, str) or len(folder) > 1024:
        raise invalid_response()
    if any(ord(character) < 32 for character in name + folder):
        raise invalid_response()
    return relative_file(folder + "/" + name if folder else name)
