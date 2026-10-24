from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from generation_contracts.operational import (
    FAILURE_CLASSES,
    OUTCOMES,
    TASK_TYPES,
    WINDOWS,
    QueueStatisticsQuery,
)
from generation_domain.errors import DomainError
from generation_domain.state import TERMINAL
from generation_domain.values import utcnow
from sqlalchemy import BigInteger, Integer, case, cast, func, select, text
from sqlalchemy.exc import OperationalError

from generation_infrastructure.admin_base import AdminBase
from generation_infrastructure.models import Attempt, Job
from generation_infrastructure.queue_queries import begin_queue_read, job_filters

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def milliseconds(value: datetime) -> int:
    delta = value - EPOCH
    return delta.days * 86400000 + delta.seconds * 1000 + delta.microseconds // 1000


def duration(start: datetime | None, end: datetime | None, now: datetime, *, open_span: bool = False) -> dict[str, Any]:
    if start is None or (end is None and not open_span):
        return {"status": "unknown", "reason": "missing_record"}
    finish = end if end is not None else now
    if start < EPOCH or not start <= finish <= now:
        return {"status": "unknown", "reason": "clock_order"}
    result = {"status": "complete" if end is not None else "open", "milliseconds": milliseconds(finish) - milliseconds(start), "started_at_ms": milliseconds(start)}
    if end is not None:
        result["ended_at_ms"] = milliseconds(end)
    return result


def job_timing(job: Job, first_assigned: datetime | None, now: datetime) -> dict[str, Any]:
    terminal = job.state in TERMINAL
    queue_end = first_assigned if first_assigned is not None else job.terminal_at if terminal else None
    if job.state == "admission_pending":
        return {"snapshot_at_ms": milliseconds(now), "elapsed": {"status": "unknown", "reason": "missing_record"}, "queue": {"status": "unknown", "reason": "missing_record"}}
    queue = duration(job.accepted_at, queue_end, now, open_span=not terminal and job.current_attempt_id is None)
    if first_assigned is not None and job.terminal_at is not None and first_assigned > job.terminal_at:
        queue = {"status": "unknown", "reason": "clock_order"}
    return {"snapshot_at_ms": milliseconds(now), "elapsed": duration(job.accepted_at, job.terminal_at, now, open_span=not terminal), "queue": queue}


def attempt_timing(attempt: Attempt, job: Job, now: datetime) -> dict[str, Any]:
    current = attempt.closed_at is None and job.state not in TERMINAL and attempt.id == job.current_attempt_id
    generation_end = attempt.outputs_collected_at or attempt.generation_released_at
    return {
        "elapsed": duration(attempt.created_at, attempt.closed_at, now, open_span=current),
        "preparing": duration(attempt.created_at, attempt.submit_intent_at, now, open_span=current and attempt.submitted_at is None and attempt.generation_released_at is None),
        "submission": duration(attempt.submit_intent_at, attempt.submitted_at, now, open_span=current and generation_end is None),
        "generation_collection": duration(attempt.submitted_at, generation_end, now, open_span=current),
        "upload_verification": duration(attempt.generation_released_at, attempt.closed_at, now, open_span=current),
        "model_loading": {"status": "unknown", "reason": "unmeasured"},
    }


def empty_counts() -> dict[str, int]:
    return dict.fromkeys(("accepted", *OUTCOMES, "completed"), 0)


class OperationalQueries(AdminBase):
    def statistics(self, query: QueueStatisticsQuery) -> dict[str, Any]:
        try:
            with self.sessions.begin() as session:
                begin_queue_read(session)
                session.execute(text("SET LOCAL statement_timeout = '1500ms'"))
                end = datetime.fromtimestamp(int(utcnow().timestamp()), UTC)
                length, width = WINDOWS[query.window]
                start = end - timedelta(seconds=length)
                conditions = job_filters(query)
                if not query.include_validation:
                    conditions.append(Job.is_validation.is_(False))
                if query.task_type is not None:
                    conditions.append(Job.task_type == query.task_type)
                task = case((Job.task_type.in_(TASK_TYPES[:-1]), Job.task_type), else_="other")
                failure = case((Job.error_code == "comfy_oom", "out_of_memory"), (Job.error_code == "comfy_output_invalid", "invalid_output"), (Job.error_code.in_(("preparation_failed", "preparation_transport_failure")), "preparation"), (Job.error_code == "comfy_execution_failed", "execution"), (Job.error_code.is_(None), "unclassified"), else_="other")
                accepted = session.execute(select(task, func.count()).where(*conditions, Job.accepted_at >= start, Job.accepted_at < end).group_by(task)).all()
                bucket_index = cast(func.floor(func.extract("epoch", Job.terminal_at - start) / width), Integer)
                ended_conditions = [*conditions, Job.terminal_at >= start, Job.terminal_at < end, Job.state.in_(OUTCOMES)]
                ended = session.execute(select(bucket_index, task, Job.state, failure, func.count()).where(*ended_conditions).group_by(bucket_index, task, Job.state, failure)).all()
                span = cast(func.floor(func.extract("epoch", Job.terminal_at) * 1000) - func.floor(func.extract("epoch", Job.accepted_at) * 1000), BigInteger)
                samples, p50, p95 = session.execute(select(func.count(), func.percentile_disc(0.5).within_group(span), func.percentile_disc(0.95).within_group(span)).where(*ended_conditions, Job.state == "succeeded", Job.accepted_at >= EPOCH, Job.accepted_at <= Job.terminal_at)).one()
                totals = empty_counts()
                by_task = {name: {"task_type": name, **empty_counts()} for name in TASK_TYPES}
                failures = dict.fromkeys(FAILURE_CLASSES, 0)
                buckets = [{"started_at": int(start.timestamp()) + i * width, "ended_at": int(start.timestamp()) + (i + 1) * width, **dict.fromkeys(OUTCOMES, 0)} for i in range(length // width)]
                for name, count in accepted:
                    totals["accepted"] += count
                    by_task[name]["accepted"] += count
                for index, name, state, category, count in ended:
                    totals[state] += count
                    totals["completed"] += count
                    by_task[name][state] += count
                    by_task[name]["completed"] += count
                    buckets[index][state] += count
                    if state == "failed":
                        failures[category] += count
                denominator = totals["succeeded"] + totals["failed"]
                return {"snapshot_at": int(end.timestamp()), "window": query.window, "window_start": int(start.timestamp()), "window_end": int(end.timestamp()), "bucket_seconds": width, "include_validation": query.include_validation, "task_type": query.task_type, "filters": query.filters(), "totals": totals, "success_rate_bps": totals["succeeded"] * 10000 // denominator if denominator else None, "successful_duration": {"samples": samples, "missing_samples": totals["succeeded"] - samples, "p50_ms": p50, "p95_ms": p95}, "buckets": buckets, "task_types": list(by_task.values()), "failure_classes": [{"category": name, "count": count} for name, count in failures.items()]}
        except OperationalError as exc:
            if getattr(exc.orig, "sqlstate", None) in ("57014", "55P03"):
                raise DomainError("statistics_unavailable", "统计读取暂时不可用，请缩小时间范围或稍后重试", 503) from None
            raise
