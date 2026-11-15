from __future__ import annotations

from pydantic import BaseModel, Field

from generation_contracts.validation import StrictDTO


class OriginGrantPatch(StrictDTO):
    expected_revision: int = Field(ge=1)
    enabled: bool
    channel_ids: list[str] = Field(max_length=100)


class OriginChannelOption(BaseModel):
    id: str
    name: str
    enabled: bool


class OriginGrantView(BaseModel):
    key_id: str
    enabled: bool
    channel_ids: list[str]
    revision: int
    options: list[OriginChannelOption]
    trust_notice: str


class OriginLogin(StrictDTO):
    api_key: str = Field(min_length=40, max_length=256)
