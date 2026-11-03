from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from generation_contracts.collections import JobSummary
from generation_contracts.validation import StrictDTO


class LookupArtifact(StrictDTO):
    id: str = Field(pattern=r"^content_[a-f0-9]{32}$")
    position: int = Field(ge=0)


class LookupJob(JobSummary):
    artifacts: list[LookupArtifact] | None = None
    size: str | None = Field(default=None, pattern=r"^[1-9][0-9]{1,4}x[1-9][0-9]{1,4}$")
    seconds: str | None = None


class SubmissionLookup(StrictDTO):
    object: Literal["generation.submission_lookup"]
    outcome: Literal["found", "not_observed"]
    job: LookupJob | None

    @model_validator(mode="after")
    def matches_outcome(self) -> SubmissionLookup:
        if (self.outcome == "found") != (self.job is not None):
            raise ValueError("Submission lookup outcome must match job presence")
        return self
