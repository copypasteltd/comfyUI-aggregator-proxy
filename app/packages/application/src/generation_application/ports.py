from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from generation_domain.errors import DomainError


@dataclass(frozen=True)
class BusinessIdentity:
    tenant_id: UUID
    project_id: UUID
    key_id: UUID
    policy: dict[str, Any]

    def require(self, permission: str) -> None:
        if permission not in self.policy["scopes"]:
            raise DomainError("scope_denied", "缺少接口权限：" + permission, 403)


@dataclass(frozen=True)
class AdminIdentity:
    user_id: UUID
    username: str
    role: str
    session_id: UUID


@dataclass(frozen=True)
class GatewayIdentity:
    gateway_id: UUID
    issuer: str


class GenerationPort(Protocol):
    def preflight_inputs(self, identity: BusinessIdentity, request: dict[str, Any]) -> None: ...
    def create(self, identity: BusinessIdentity, request: dict[str, Any], token: str, request_id: str) -> dict[str, Any]: ...
    def get(self, identity: BusinessIdentity, job_id: UUID) -> dict[str, Any]: ...
    def cancel(self, identity: BusinessIdentity, job_id: UUID, request_id: str) -> dict[str, Any]: ...


class GenerationService:
    def __init__(self, repository: GenerationPort) -> None:
        self.repository = repository

    def preflight_inputs(self, identity: BusinessIdentity, request: dict[str, Any]) -> None:
        identity.require("jobs:create")
        identity.require("audio:create" if request["task_type"] == "text_to_audio" else "videos:create" if request["task_type"].endswith("video") else "images:create")
        self.repository.preflight_inputs(identity, request)

    def create(self, identity: BusinessIdentity, request: dict[str, Any], token: str, request_id: str) -> dict[str, Any]:
        identity.require("jobs:create")
        identity.require("audio:create" if request["task_type"] == "text_to_audio" else "videos:create" if request["task_type"].endswith("video") else "images:create")
        if not 16 <= len(token) <= 128 or not token.isascii() or any(ord(c) < 33 or ord(c) > 126 for c in token):
            raise DomainError("invalid_idempotency_key", "Idempotency-Key 要求 16～128 位 ASCII 字符", 400)
        return self.repository.create(identity, request, token, request_id)

    def get(self, identity: BusinessIdentity, job_id: UUID) -> dict[str, Any]:
        identity.require("jobs:read")
        return self.repository.get(identity, job_id)

    def cancel(self, identity: BusinessIdentity, job_id: UUID, request_id: str) -> dict[str, Any]:
        identity.require("jobs:cancel")
        return self.repository.cancel(identity, job_id, request_id)
