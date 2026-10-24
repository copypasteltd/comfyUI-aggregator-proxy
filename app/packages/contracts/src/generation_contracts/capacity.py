from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from generation_contracts.validation import StrictDTO

MAX_BYTES = 2**60
CapacityReason = Literal["disk_space", "memory", "spool_limit", "upload_backlog", "observation_unavailable"]


class WorkerCapacity(StrictDTO):
    protocol: Literal["native-capacity-v1"]
    sequence: int = Field(ge=1, le=2**53 - 1)
    observed_ago_ms: int = Field(ge=0, le=60000)
    status: Literal["ready", "blocked", "unavailable"]
    reasons: list[CapacityReason] = Field(max_length=5)
    free_disk_bytes: int | None = Field(ge=0, le=MAX_BYTES)
    available_memory_bytes: int | None = Field(ge=0, le=MAX_BYTES)
    spool_bytes: int | None = Field(ge=0, le=MAX_BYTES)
    reserved_bytes: int = Field(ge=0, le=MAX_BYTES)
    pending_uploads: int = Field(ge=0, le=10000)
    minimum_free_disk_bytes: int = Field(ge=1, le=MAX_BYTES)
    minimum_available_memory_bytes: int = Field(ge=1, le=MAX_BYTES)
    maximum_spool_bytes: int = Field(ge=1, le=MAX_BYTES)
    generation_reserve_bytes: int = Field(ge=1, le=MAX_BYTES)
    maximum_uploads: int = Field(ge=1, le=8)

    def derived_reasons(self, next_reserve: int | None = None) -> list[CapacityReason]:
        if self.free_disk_bytes is None or self.available_memory_bytes is None or self.spool_bytes is None:
            return ["observation_unavailable"]
        reserve = self.generation_reserve_bytes if next_reserve is None else next_reserve
        reasons: list[CapacityReason] = []
        # ComfyUI output and Worker spool can share the same volume.
        if self.free_disk_bytes < self.minimum_free_disk_bytes + 2 * (self.reserved_bytes + reserve):
            reasons.append("disk_space")
        if self.available_memory_bytes < self.minimum_available_memory_bytes:
            reasons.append("memory")
        if self.spool_bytes + self.reserved_bytes + reserve > self.maximum_spool_bytes:
            reasons.append("spool_limit")
        if self.pending_uploads >= self.maximum_uploads:
            reasons.append("upload_backlog")
        return reasons

    @model_validator(mode="after")
    def consistent(self) -> Self:
        reasons = self.derived_reasons()
        expected = "unavailable" if reasons == ["observation_unavailable"] else "blocked" if reasons else "ready"
        if self.generation_reserve_bytes > self.maximum_spool_bytes or self.reasons != reasons or self.status != expected:
            raise ValueError("capacity status/reasons or configured budget are inconsistent")
        return self


def output_reservation(manifest: object, fallback: int) -> int:
    """max_bytes is per output; reserve every expected file, never just one."""
    if isinstance(manifest, dict) and isinstance(output := manifest.get("output"), dict):
        size, count = output.get("max_bytes"), output.get("expected_count")
        if type(size) is int and type(count) is int and 0 < size <= MAX_BYTES and 0 < count <= 1000:
            return min(MAX_BYTES, size * count)
    return fallback
