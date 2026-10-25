from __future__ import annotations

from typing import Literal

from pydantic import Field, SerializerFunctionWrapHandler, model_serializer, model_validator

from generation_contracts.validation import StrictDTO


class RetryInfo(StrictDTO):
    count: int = Field(ge=0, le=2)
    limit: Literal[2]
    next_at: int | None = Field(ge=1, le=9007199254740991)
    last_error: Literal["preparation_transport_failure", "comfy_oom"] | None

    @model_validator(mode="after")
    def coherent(self) -> RetryInfo:
        if self.count == 0 and (self.next_at is not None or self.last_error is not None):
            raise ValueError("No retry has been scheduled")
        if self.count > 0 and self.last_error is None:
            raise ValueError("A scheduled retry requires its fixed reason")
        return self

    @model_serializer(mode="wrap")
    def preserve_required_nulls(self, handler: SerializerFunctionWrapHandler):  # type: ignore[no-untyped-def]
        # A return annotation replaces Pydantic's serialization schema. Keep the
        # declared four-field schema and its required nullable values unchanged.
        result = handler(self)
        result["next_at"] = self.next_at
        result["last_error"] = self.last_error
        return result
