from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ChannelStatus = Literal["enabled", "disabled", "draining", "deleted"]
SEARCH_PATTERN = r"^[^\s\x00-\x1f\x7f-\x9f](?:[^\x00-\x1f\x7f-\x9f]*[^\s\x00-\x1f\x7f-\x9f])?$"


class ChannelQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page: int = Field(1, ge=1, le=10000)
    q: str | None = Field(None, min_length=1, max_length=128, pattern=SEARCH_PATTERN)
    channel_id: Annotated[str, Field(pattern=r"^ch_[a-f0-9]{32}$")] | None = None
    tenant_id: Annotated[str, Field(pattern=r"^ten_[a-f0-9]{32}$")] | None = None
    project_id: Annotated[str, Field(pattern=r"^prj_[a-f0-9]{32}$")] | None = None
    node_id: Annotated[str, Field(pattern=r"^node_[a-f0-9]{32}$")] | None = None
    status: ChannelStatus | None = None

    @field_validator("page", mode="before")
    @classmethod
    def integers(cls, value: Any) -> int:
        if type(value) is int:
            return value
        if isinstance(value, str) and re.fullmatch(r"[0-9]{1,10}", value):
            return int(value)
        raise ValueError("Use integer query values")
