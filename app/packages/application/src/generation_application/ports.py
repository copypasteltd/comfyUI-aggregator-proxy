from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from generation_domain.errors import DomainError
from generation_domain.media_types import task_media_kind

_CREATE_SCOPES = {"image": "images:create", "video": "videos:create", "audio": "audio:create", "model": "meshes:create"}


def validate_submission_token(token: str) -> None:
    if not 16 <= len(token) <= 128 or not token.isascii() or any(ord(c) < 33 or ord(c) > 126 for c in token):
        raise DomainError("invalid_idempotency_key", "Idempotency-Key 要求 16～128 位 ASCII 字符", 400)


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
    def create(self, identity: BusinessIdentity, request: dict[str, Any], token: str, request_id: str, *, expected_release_digest: str | None = None) -> dict[str, Any]: ...
    def get(self, identity: BusinessIdentity, job_id: UUID) -> dict[str, Any]: ...
    def lookup(self, identity: BusinessIdentity, request: dict[str, Any], token: str, *, expected_release_digest: str | None = None) -> dict[str, Any]: ...
    def cancel(self, identity: BusinessIdentity, job_id: UUID, request_id: str) -> dict[str, Any]: ...


class GenerationService:
    def __init__(self, repository: GenerationPort) -> None:
        self.repository = repository

    def preflight_inputs(self, identity: BusinessIdentity, request: dict[str, Any]) -> None:
        identity.require("jobs:create")
        identity.require(_CREATE_SCOPES[task_media_kind(request["task_type"])])
        self.repository.preflight_inputs(identity, request)

    def create(self, identity: BusinessIdentity, request: dict[str, Any], token: str, request_id: str, *, expected_release_digest: str | None = None) -> dict[str, Any]:
        identity.require("jobs:create")
        identity.require(_CREATE_SCOPES[task_media_kind(request["task_type"])])
        validate_submission_token(token)
        if expected_release_digest is not None:
            return self.repository.create(identity, request, token, request_id, expected_release_digest=expected_release_digest)
        return self.repository.create(identity, request, token, request_id)

    def get(self, identity: BusinessIdentity, job_id: UUID) -> dict[str, Any]:
        identity.require("jobs:read")
        return self.repository.get(identity, job_id)

    def lookup(self, identity: BusinessIdentity, request: dict[str, Any], token: str, *, expected_release_digest: str | None = None) -> dict[str, Any]:
        identity.require("jobs:read")
        validate_submission_token(token)
        return self.repository.lookup(identity, request, token, expected_release_digest=expected_release_digest)

    def cancel(self, identity: BusinessIdentity, job_id: UUID, request_id: str) -> dict[str, Any]:
        identity.require("jobs:cancel")
        return self.repository.cancel(identity, job_id, request_id)
