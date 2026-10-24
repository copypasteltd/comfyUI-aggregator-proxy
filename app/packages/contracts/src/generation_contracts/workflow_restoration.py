from __future__ import annotations

from typing import Self

from pydantic import Field, model_validator

from generation_contracts.model_catalog import ReleaseId, WorkflowId
from generation_contracts.validation import StrictDTO, validate


class WorkflowDraftRestore(StrictDTO):
    expected_revision: int = Field(ge=1, le=9007199254740990)
    source_release_id: ReleaseId
    source_digest: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def frozen(self) -> Self:
        validate("admin-workflow-draft-restore", self.model_dump())
        return self


class WorkflowDraftRestored(StrictDTO):
    workflow_id: WorkflowId
    source_release_id: ReleaseId
    source_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    previous_revision: int = Field(ge=1, le=9007199254740990)
    revision: int = Field(ge=2, le=9007199254740991)

    @model_validator(mode="after")
    def frozen(self) -> Self:
        validate("admin-workflow-draft-restored", self.model_dump())
        if self.revision != self.previous_revision + 1:
            raise ValueError("A restoration increases the draft revision once")
        return self
