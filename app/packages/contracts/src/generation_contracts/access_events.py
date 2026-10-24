from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

AccessResult = Literal["all", "success", "denied", "error"]
AccessCompletion = Literal["completed", "disconnected", "failed"]


class AccessCaptureHealth(BaseModel):
    started_at: int
    written: int = Field(ge=0)
    failed: int = Field(ge=0)
    overloaded: int = Field(ge=0)
    anonymous_skipped: int = Field(ge=0)


class AccessEventView(BaseModel):
    id: str
    request_id: str
    started_at: int
    recorded_at: int
    entry: Literal["direct", "gateway"]
    method: str
    route: str
    peer_ip: str | None
    client_ip: str | None
    gateway_id: str | None
    status_code: int | None
    error_code: str | None
    result: Literal["success", "denied", "error"]
    completion: AccessCompletion
    duration_milliseconds: int
    request_bytes: int
    response_bytes: int


class KeyAccessSnapshot(BaseModel):
    key_id: str
    key_name: str
    enabled: bool
    deleted_at: int | None
    snapshot_at: int
    retention_days: int
    data: list[AccessEventView]
    first_cursor: str
    next_cursor: str | None
    capture_health: AccessCaptureHealth
