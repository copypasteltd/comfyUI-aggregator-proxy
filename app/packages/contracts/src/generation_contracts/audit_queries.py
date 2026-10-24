from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

RESOURCE = r"^(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[a-z][a-z0-9]{0,15}_[0-9a-f]{32})$"


class AuditQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page: int = Field(1, ge=1, le=10000)
    actor: str | None = Field(None, min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_.:-]{0,127}$")
    action: str | None = Field(None, min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_.]{0,127}$")
    resource: str | None = Field(None, max_length=49, pattern=RESOURCE)
    request_id: str | None = Field(None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]{1,128}$")
    created_from: int | None = Field(None, ge=0, le=4102444800)
    created_before: int | None = Field(None, ge=0, le=4102444800)

    @field_validator("actor", "action", "resource", "request_id")
    @classmethod
    def no_controls(cls, value: str | None) -> str | None:
        if value is not None and any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("Control characters are not allowed")
        return value

    @field_validator("page", "created_from", "created_before", mode="before")
    @classmethod
    def integers(cls, value: Any) -> Any:
        if value is None or type(value) is int:
            return value
        if isinstance(value, str) and re.fullmatch(r"[0-9]{1,10}", value):
            return int(value)
        raise ValueError("Use integer query values")

    @model_validator(mode="after")
    def time_order(self) -> AuditQuery:
        if self.created_from is not None and self.created_before is not None and self.created_from >= self.created_before:
            raise ValueError("created_from must precede created_before")
        return self

    def resource_uuid(self) -> str | None:
        return str(UUID(self.resource.rsplit("_", 1)[-1])) if self.resource else None

    def filters(self) -> dict[str, str | int]:
        return self.model_dump(exclude={"page"}, exclude_none=True)
