from __future__ import annotations

from typing import Annotated, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator

from generation_contracts.collections import TaskType
from generation_contracts.queue_queries import QueueQuery

WINDOWS = {"1h": (3600, 300), "24h": (86400, 3600), "7d": (604800, 21600)}
TASK_TYPES = (*get_args(TaskType), "other")
OUTCOMES = ("succeeded", "failed", "cancelled", "expired")
FailureClass = Literal["out_of_memory", "invalid_output", "preparation", "execution", "other", "unclassified"]
FAILURE_CLASSES = get_args(FailureClass)
NonNegative = Annotated[int, Field(ge=0)]


class QueueStatisticsQuery(QueueQuery):
    window: Literal["1h", "24h", "7d"] = "24h"
    task_type: TaskType | None = None
    include_validation: bool = False

    def filters(self) -> dict[str, str | int]:
        return self.model_dump(exclude={"page", "window", "task_type", "include_validation"}, exclude_none=True)


class OperationalView(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DurationView(OperationalView):
    status: Literal["complete", "open", "unknown"]
    milliseconds: NonNegative | None = None
    started_at_ms: NonNegative | None = None
    ended_at_ms: NonNegative | None = None
    reason: Literal["missing_record", "clock_order", "unmeasured"] | None = None

    @model_validator(mode="after")
    def consistent(self) -> DurationView:
        if self.status == "unknown":
            if self.reason is None or any(value is not None for value in (self.milliseconds, self.started_at_ms, self.ended_at_ms)):
                raise ValueError("Unknown duration must not invent a measurement")
        elif self.reason is not None or self.milliseconds is None or self.started_at_ms is None:
            raise ValueError("Observed duration requires a start and measurement")
        elif self.status == "complete":
            if self.ended_at_ms is None or self.ended_at_ms - self.started_at_ms != self.milliseconds:
                raise ValueError("Complete duration must equal its observation span")
        elif self.ended_at_ms is not None:
            raise ValueError("Open duration cannot have an end")
        return self


class JobTimingView(OperationalView):
    snapshot_at_ms: NonNegative
    elapsed: DurationView
    queue: DurationView


class AttemptTimingView(OperationalView):
    elapsed: DurationView
    preparing: DurationView
    submission: DurationView
    generation_collection: DurationView
    upload_verification: DurationView
    model_loading: DurationView


class OutcomeCounts(OperationalView):
    accepted: NonNegative
    succeeded: NonNegative
    failed: NonNegative
    cancelled: NonNegative
    expired: NonNegative
    completed: NonNegative

    @model_validator(mode="after")
    def total(self) -> OutcomeCounts:
        if self.completed != sum(getattr(self, name) for name in OUTCOMES):
            raise ValueError("Completed total does not match outcomes")
        return self


class StatisticsBucket(OperationalView):
    started_at: NonNegative
    ended_at: NonNegative
    succeeded: NonNegative
    failed: NonNegative
    cancelled: NonNegative
    expired: NonNegative


class TaskStatistics(OutcomeCounts):
    task_type: TaskType | Literal["other"]


class FailureStatistics(OperationalView):
    category: FailureClass
    count: NonNegative


class SuccessfulDuration(OperationalView):
    samples: NonNegative
    missing_samples: NonNegative
    p50_ms: NonNegative | None
    p95_ms: NonNegative | None


class QueueStatistics(OperationalView):
    snapshot_at: NonNegative
    window: Literal["1h", "24h", "7d"]
    window_start: NonNegative
    window_end: NonNegative
    bucket_seconds: NonNegative
    include_validation: bool
    task_type: TaskType | None
    filters: dict[str, str | int]
    totals: OutcomeCounts
    success_rate_bps: Annotated[int, Field(ge=0, le=10000)] | None
    successful_duration: SuccessfulDuration
    buckets: list[StatisticsBucket] = Field(min_length=12, max_length=28)
    task_types: list[TaskStatistics] = Field(min_length=7, max_length=7)
    failure_classes: list[FailureStatistics] = Field(min_length=6, max_length=6)

    @model_validator(mode="after")
    def consistent(self) -> QueueStatistics:
        length, width = WINDOWS[self.window]
        if self.window_end != self.snapshot_at or self.window_end - self.window_start != length or self.bucket_seconds != width or len(self.buckets) != length // width:
            raise ValueError("Statistics window is inconsistent")
        if tuple(item.task_type for item in self.task_types) != TASK_TYPES or tuple(item.category for item in self.failure_classes) != FAILURE_CLASSES:
            raise ValueError("Statistics categories must be bounded and unique")
        for index, bucket in enumerate(self.buckets):
            if (bucket.started_at, bucket.ended_at) != (self.window_start + index * width, self.window_start + (index + 1) * width):
                raise ValueError("Statistics buckets must cover the window exactly")
        for name in OUTCOMES:
            if sum(getattr(item, name) for item in self.buckets) != getattr(self.totals, name):
                raise ValueError("Bucket total does not match outcomes")
        for name in ("accepted", *OUTCOMES, "completed"):
            if sum(getattr(item, name) for item in self.task_types) != getattr(self.totals, name):
                raise ValueError("Task type total does not match outcomes")
        if sum(item.count for item in self.failure_classes) != self.totals.failed:
            raise ValueError("Failure total does not match failed jobs")
        denominator = self.totals.succeeded + self.totals.failed
        expected = self.totals.succeeded * 10000 // denominator if denominator else None
        if self.success_rate_bps != expected:
            raise ValueError("Success rate denominator is inconsistent")
        duration = self.successful_duration
        if duration.samples + duration.missing_samples != self.totals.succeeded:
            raise ValueError("Duration sample count does not match successful jobs")
        if duration.samples == 0:
            if duration.p50_ms is not None or duration.p95_ms is not None:
                raise ValueError("No duration samples must remain unknown")
        elif duration.p50_ms is None or duration.p95_ms is None or duration.p50_ms > duration.p95_ms:
            raise ValueError("Duration percentiles are inconsistent")
        return self
