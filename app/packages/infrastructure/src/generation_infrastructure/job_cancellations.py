from __future__ import annotations

import time
from typing import Any
from uuid import UUID

from generation_application.ports import AdminIdentity, BusinessIdentity
from generation_contracts.job_cancellations import (
    CancellationDocument,
    CancellationTarget,
    JobCancellationBatch,
)
from generation_domain.errors import DomainError
from generation_domain.values import parse_id, public_id, utcnow
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from generation_infrastructure.admin_base import AdminBase, mutation
from generation_infrastructure.admin_channels import begin_channel_read
from generation_infrastructure.jobs import JobRepository
from generation_infrastructure.models import Job, ManagementOperation
from generation_infrastructure.transactions import record_change

COMMAND = "cancel_jobs"
DOCUMENT = "_job_cancellations"


def budget(session: Session) -> None:
    session.execute(text("SET LOCAL statement_timeout = '2000ms'"))
    session.execute(text("SET LOCAL lock_timeout = '250ms'"))


def document(operation: ManagementOperation) -> CancellationDocument:
    try:
        if operation.command != COMMAND:
            raise ValueError("wrong operation")
        return CancellationDocument.model_validate(operation.summary.get(DOCUMENT))
    except (ValueError, ValidationError):
        raise DomainError("cancellation_batch_unavailable", "批量取消记录需核对", 503) from None


def finish(session: Session, operation: ManagementOperation, data: CancellationDocument) -> None:
    complete = all(target.outcome != "pending" for target in data.targets)
    if complete:
        failures = sum(target.outcome == "failed" for target in data.targets)
        operation.state = "failed" if failures == len(data.targets) else "partial_failed" if failures else "succeeded"
        data.completed_at = int(utcnow().timestamp())
        record_change(session, public_id("usr", operation.actor_id), "job.cancellations_completed", operation.id, data.request_id, {"targets": len(data.targets), "failed": failures}, operation_id=operation.id)
    else:
        operation.state = "running"
    operation.revision += 1
    operation.summary = {**operation.summary, DOCUMENT: data.model_dump(mode="json")}


class JobCancellations(AdminBase):
    def create(self, actor: AdminIdentity, data: JobCancellationBatch, token: str, request_id: str) -> dict[str, Any]:
        try:
            with self.sessions.begin() as session:
                budget(session)
                idem = mutation(session, actor, "job.cancellations", token, data.model_dump(mode="json"))
                if idem.result is not None:
                    return idem.result
                operation = ManagementOperation(actor_id=actor.user_id, command=COMMAND, summary={"target_count": len(data.job_ids), "reason": data.reason, DOCUMENT: CancellationDocument(reason=data.reason, request_id=request_id, targets=[CancellationTarget(job_id=identifier) for identifier in data.job_ids]).model_dump(mode="json")})
                session.add(operation)
                session.flush()
                record_change(session, public_id("usr", actor.user_id), "job.cancellations_accepted", operation.id, request_id, {"target_count": len(data.job_ids), "reason": data.reason}, operation_id=operation.id)
                idem.result = {"operation_id": public_id("op", operation.id), "status": "pending"}
                return idem.result
        except DBAPIError:
            raise DomainError("cancellation_batch_unavailable", "批量取消暂不可用，请用原请求核对", 503) from None

    def get(self, identifier: UUID) -> dict[str, Any]:
        try:
            with self.sessions.begin() as session:
                begin_channel_read(session)
                operation = session.get(ManagementOperation, identifier)
                if operation is None or operation.command != COMMAND:
                    raise DomainError("not_found", "批量取消操作不存在", 404)
                data = document(operation)
                ids = [parse_id("job", target.job_id) for target in data.targets]
                statuses = {identifier: state for identifier, state in session.execute(select(Job.id, Job.state).where(Job.id.in_(ids)))}
                return {"operation_id": public_id("op", operation.id), "status": operation.state, "reason": data.reason, "created_at": int(operation.created_at.timestamp()), "completed_at": data.completed_at, "snapshot_at": int(utcnow().timestamp()), "processing_complete": data.completed_at is not None, "targets": [{**target.model_dump(), "current_status": statuses.get(parse_id("job", target.job_id))} for target in data.targets]}
        except DBAPIError:
            raise DomainError("cancellation_batch_unavailable", "批量取消结果暂不可读，请刷新", 503) from None


def failed_attempt(sessions: sessionmaker[Session], identifier: UUID, job_id: str) -> None:
    """Only after the cancellation transaction rolled back; never overwrite a commit."""
    try:
        with sessions.begin() as session:
            budget(session)
            operation = session.scalar(select(ManagementOperation).where(ManagementOperation.id == identifier, ManagementOperation.command == COMMAND, ManagementOperation.state.in_(("pending", "running"))).with_for_update(skip_locked=True))
            if operation is None:
                return
            data = document(operation)
            target = next((item for item in data.targets if item.job_id == job_id), None)
            if target is None or target.outcome != "pending":
                return
            target.transient_attempts += 1
            if target.transient_attempts == 5:
                target.outcome, target.error_code = "failed", "database_busy"
                target.processed_at = int(utcnow().timestamp())
                record_change(session, public_id("usr", operation.actor_id), "job.cancellation_processed", parse_id("job", job_id), data.request_id, {"outcome": "failed", "error_code": target.error_code, "reason": data.reason}, operation_id=operation.id)
            finish(session, operation, data)
    except (DBAPIError, DomainError):
        return


def advance_job_cancellations(sessions: sessionmaker[Session], *, limit: int = 20) -> int:
    if type(limit) is not int or not 1 <= limit <= 20:
        raise ValueError("Cancellation runner limit must be 1..20")
    started, processed = time.monotonic(), 0
    for _ in range(limit):
        if time.monotonic() - started >= 5:
            break
        operation_id: UUID | None = None
        job_id: str | None = None
        try:
            with sessions.begin() as session:
                budget(session)
                operation = session.scalar(select(ManagementOperation).where(ManagementOperation.command == COMMAND, ManagementOperation.state.in_(("pending", "running"))).order_by(ManagementOperation.created_at, ManagementOperation.id).limit(1).with_for_update(skip_locked=True))
                if operation is None:
                    break
                operation_id = operation.id
                try:
                    data = document(operation)
                except DomainError:
                    operation.state = "failed"
                    operation.revision += 1
                    record_change(session, "runner", "job.cancellations_invalid", operation.id, public_id("op", operation.id), operation_id=operation.id)
                    continue
                target = next((item for item in data.targets if item.outcome == "pending"), None)
                if target is None:
                    finish(session, operation, data)
                    continue
                job_id = target.job_id
                identifier = parse_id("job", job_id)
                scope = session.execute(select(Job.tenant_id, Job.project_id, Job.key_id).where(Job.id == identifier)).one_or_none()
                if scope is None:
                    target.outcome, target.error_code = "failed", "not_found"
                else:
                    identity = BusinessIdentity(scope.tenant_id, scope.project_id, scope.key_id, {})
                    try:
                        with session.begin_nested():
                            result = JobRepository(sessions).cancel(identity, identifier, data.request_id, _session=session, audit_actor=public_id("usr", operation.actor_id))
                        target.recorded_status = result["status"]
                        target.outcome = result["status"] if result["status"] in ("cancelled", "cancel_requested") else "unchanged"
                    except DomainError:
                        target.outcome, target.error_code = "failed", "cancellation_rejected"
                target.processed_at = int(utcnow().timestamp())
                record_change(session, public_id("usr", operation.actor_id), "job.cancellation_processed", identifier, data.request_id, {"outcome": target.outcome, "error_code": target.error_code, "reason": data.reason}, operation_id=operation.id)
                finish(session, operation, data)
            processed += 1
        except DBAPIError:
            if operation_id is not None and job_id is not None:
                failed_attempt(sessions, operation_id, job_id)
            break
    return processed
