from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SessionActivity(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    activity: Literal["interactive"]


class SessionLifetime(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    absolute_expires_at: int = Field(ge=0, le=4102444800)
    idle_expires_at: int = Field(ge=0, le=4102444800)
    server_time: int = Field(ge=0, le=4102444800)
    idle_timeout_seconds: int = Field(ge=60, le=86400)
