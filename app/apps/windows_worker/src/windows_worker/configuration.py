from __future__ import annotations

import ipaddress
import json
import re
import ssl
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from generation_domain.errors import DomainError
from pydantic import BaseModel, ConfigDict, Field, model_validator


def strict_json(value: bytes) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = item
        return result

    def constant(value: str) -> Any:
        raise ValueError("non-finite JSON number")

    return json.loads(value.decode("utf-8-sig"), object_pairs_hook=pairs, parse_constant=constant)


def bounded_file(path: Path, maximum: int) -> bytes:
    if not stat.S_ISREG(path.lstat().st_mode) or bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400):
        raise ValueError("ordinary file required")
    with path.open("rb") as stream:
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError("file budget exceeded")
    return data


def canonical_origin(value: str) -> str:
    origin = urlparse(value)
    if origin.scheme != "https" or not origin.hostname or origin.path not in ("", "/") or origin.username is not None or origin.password is not None or origin.query or origin.fragment or any(ord(c) <= 32 or ord(c) == 127 for c in value):
        raise ValueError("control_origin must be a plain HTTPS origin")
    port = origin.port
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("invalid control port")
    try:
        address = ipaddress.ip_address(origin.hostname)
    except ValueError:
        host = origin.hostname.encode("idna").decode("ascii").lower()
    else:
        host = "[" + address.compressed + "]" if address.version == 6 else address.compressed
    return "https://" + host + (":" + str(port) if port not in (None, 443) else "")


@dataclass(frozen=True)
class WorkerCredential:
    node_id: str
    token: str = field(repr=False)


def read_credential(path: Path) -> WorkerCredential:
    try:
        value = strict_json(bounded_file(path, 4096))
        if not isinstance(value, dict) or set(value) != {"node_id", "token"}:
            raise ValueError("invalid credential fields")
        node, token = value["node_id"], value["token"]
        if not isinstance(node, str) or not re.fullmatch(r"node_[0-9a-f]{32}", node) or not isinstance(token, str) or not re.fullmatch(r"worker_[0-9a-f]{32}\.[A-Za-z0-9_-]{43,128}", token):
            raise ValueError("invalid worker identity")
        return WorkerCredential(node, token)
    except (OSError, ValueError, TypeError, RecursionError):
        raise DomainError("invalid_worker_credential", "Worker 凭证文件无效") from None


def trust_context(settings: WorkerSettings) -> ssl.SSLContext:
    try:
        return ssl.create_default_context(cafile=str(settings.ca_file) if settings.ca_file else None)
    except (OSError, ValueError):
        raise DomainError("invalid_worker_trust", "Worker TLS 信任配置无效") from None


def load_settings(path: Path) -> WorkerSettings:
    try:
        return WorkerSettings.model_validate(strict_json(bounded_file(path, 1024 * 1024)))
    except (OSError, ValueError, TypeError, RecursionError):
        raise DomainError("invalid_worker_configuration", "Worker 配置文件无效") from None


def validate_startup(settings: WorkerSettings) -> tuple[WorkerCredential, ssl.SSLContext]:
    return read_credential(settings.credential_file), trust_context(settings)


class LocalGpu(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hardware_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=128)
    memory_bytes: int | None = Field(default=None, gt=0)


class LocalChannel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: str
    hardware_id: str
    comfy_root: Path
    workflow_export_root: Path | None = None


class WorkerSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    control_origin: str
    credential_file: Path
    state_root: Path
    ca_file: Path | None = None
    gpus: list[LocalGpu] = Field(max_length=32)
    channels: list[LocalChannel] = Field(max_length=100)
    maximum_uploads: int = Field(default=2, strict=True, ge=1, le=8)
    minimum_free_disk_bytes: int = Field(default=5 * 1024**3, strict=True, ge=1, le=2**60)
    minimum_available_memory_bytes: int = Field(default=1024**3, strict=True, ge=1, le=2**60)
    maximum_spool_bytes: int = Field(default=20 * 1024**3, strict=True, ge=1, le=2**60)
    generation_reserve_bytes: int = Field(default=2 * 1024**3, strict=True, ge=1, le=2**60)
    output_retention_seconds: int = Field(default=3600, strict=True, ge=3600, le=30 * 86400)
    model_hash_maximum_bytes: int = Field(default=64 * 1024**3, strict=True, ge=1, le=1024**4)
    model_hash_timeout_seconds: int = Field(default=120, strict=True, ge=1, le=240)

    @model_validator(mode="after")
    def validate_endpoints(self) -> WorkerSettings:
        if self.generation_reserve_bytes > self.maximum_spool_bytes:
            raise ValueError("maximum_spool_bytes must hold one generation reserve")
        self.control_origin = canonical_origin(self.control_origin)
        ids = [gpu.hardware_id for gpu in self.gpus]
        if len(ids) != len(set(ids)) or len({c.base_url for c in self.channels}) != len(self.channels):
            raise ValueError("duplicate GPU or channel URL")
        for channel in self.channels:
            parsed = urlparse(channel.base_url)
            if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/") or channel.hardware_id not in ids:
                raise ValueError("invalid local ComfyUI channel")
            channel.base_url = channel.base_url.rstrip("/")
            channel.comfy_root = channel.comfy_root.resolve(strict=True)
            if channel.workflow_export_root is not None:
                from windows_worker.workflow_exports import configured_export_root
                channel.workflow_export_root = configured_export_root(channel.workflow_export_root)
        self.state_root = self.state_root.resolve()
        return self

    def channel(self, base_url: str) -> LocalChannel:
        from generation_domain.errors import DomainError
        for channel in self.channels:
            if channel.base_url == base_url.rstrip("/"):
                return channel
        raise DomainError("channel_not_allowlisted", "渠道地址未在本地 Worker 配置白名单登记")
