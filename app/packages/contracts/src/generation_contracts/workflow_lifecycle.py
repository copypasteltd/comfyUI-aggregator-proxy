from __future__ import annotations

from typing import Self

from pydantic import Field, model_validator

from generation_contracts.model_catalog import WorkflowId
from generation_contracts.validation import StrictDTO, validate


class WorkflowLifecycle(StrictDTO):
    expected_revision: int = Field(ge=1, le=9007199254740990)
    archived: bool

    @model_validator(mode="after")
    def frozen(self) -> Self:
        validate("admin-workflow-lifecycle", self.model_dump())
        return self


class WorkflowLifecycleResult(StrictDTO):
    workflow_id: WorkflowId
    archived: bool
    previous_revision: int = Field(ge=1, le=9007199254740990)
    revision: int = Field(ge=2, le=9007199254740991)

    @model_validator(mode="after")
    def frozen(self) -> Self:
        validate("admin-workflow-lifecycle-result", self.model_dump())
        if self.revision != self.previous_revision + 1:
            raise ValueError("A lifecycle change increases the draft revision once")
        return self
