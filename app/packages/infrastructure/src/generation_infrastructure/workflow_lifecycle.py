from __future__ import annotations

from uuid import UUID

from generation_domain.errors import DomainError
from sqlalchemy import select
from sqlalchemy.orm import Session

from generation_infrastructure.models import Workflow


def require_active_workflow(session: Session, identifier: UUID) -> None:
    """Hold a shared metadata lock through the admitting transaction's commit."""
    archived = session.scalar(select(Workflow.archived).where(Workflow.id == identifier).with_for_update(read=True))
    if archived is None:
        raise DomainError("not_found", "Workflow 不存在", 404)
    if archived:
        raise DomainError("workflow_archived", "Workflow 已归档，请先解除归档", 409)
