from __future__ import annotations

from typing import Any
from uuid import UUID

from generation_application.ports import AdminIdentity
from generation_contracts.admin import WorkflowCreate, WorkflowPatch
from generation_contracts.workflow_lifecycle import WorkflowLifecycle, WorkflowLifecycleResult
from generation_contracts.workflow_restoration import WorkflowDraftRestore, WorkflowDraftRestored
from generation_contracts.workflows import WorkflowManifest
from generation_domain.errors import DomainError
from generation_domain.values import fingerprint, parse_id, public_id
from generation_domain.workflows import validate_manifest
from sqlalchemy import Text, cast, func, select, text, update
from sqlalchemy.exc import DBAPIError

from generation_infrastructure.admin_base import (
    AdminBase,
    mutation,
    require_revision,
    scope_ids,
    timestamp,
)
from generation_infrastructure.models import PublicModel, WorkerCommand, Workflow, WorkflowRelease
from generation_infrastructure.transactions import record_change
from generation_infrastructure.workflow_history import MAX_DOCUMENT_BYTES, check_structure


def workflow_projection(workflow: Workflow, *, draft: bool = False) -> dict[str, Any]:
    data: dict[str, Any] = {"id": public_id("wf", workflow.id), "tenant_id": public_id("ten", workflow.tenant_id), "project_id": public_id("prj", workflow.project_id), "name": workflow.name, "task_type": workflow.task_type, "revision": workflow.revision, "archived": workflow.archived, "created_at": timestamp(workflow.created_at)}
    if draft:
        data["draft"] = workflow.draft
    return data


class WorkflowAdministration(AdminBase):
    def lifecycle(self, actor: AdminIdentity, identifier: UUID, data: WorkflowLifecycle, token: str, request_id: str) -> dict[str, Any]:
        try:
            with self.sessions.begin() as session:
                session.execute(text("SET LOCAL lock_timeout = '1s'"))
                session.execute(text("SET LOCAL statement_timeout = '5s'"))
                idem = mutation(session, actor, "workflow.lifecycle:" + str(identifier), token, data.model_dump())
                if idem.result is not None:
                    return WorkflowLifecycleResult.model_validate(idem.result).model_dump()
                parent = session.execute(select(Workflow.revision, Workflow.archived).where(Workflow.id == identifier).with_for_update()).mappings().one_or_none()
                if parent is None:
                    raise DomainError("not_found", "Workflow 不存在", 404)
                require_revision(parent["revision"], data.expected_revision)
                if parent["archived"] == data.archived:
                    raise DomainError("workflow_state_conflict", "Workflow 已处于所选状态，请刷新后重试", 409)
                if data.archived:
                    releases = select(WorkflowRelease.id).where(WorkflowRelease.workflow_id == identifier)
                    if session.scalar(select(PublicModel.id).where(PublicModel.release_id.in_(releases), PublicModel.published.is_(True)).limit(1)):
                        raise DomainError("workflow_published", "请先下架该 Workflow 的所有公开模型，再归档", 409)
                    release_ids = select("wfr_" + func.replace(cast(WorkflowRelease.id, Text), "-", "")).where(WorkflowRelease.workflow_id == identifier)
                    if session.scalar(select(WorkerCommand.id).where(WorkerCommand.command == "apply_workflow_release", WorkerCommand.state.not_in(("succeeded", "failed")), WorkerCommand.payload["release_id"].astext.in_(release_ids)).limit(1)):
                        raise DomainError("workflow_sync_active", "该 Workflow 仍有待完成的同步，请先在操作中心核对", 409)
                result = WorkflowLifecycleResult(workflow_id=public_id("wf", identifier), archived=data.archived, previous_revision=data.expected_revision, revision=data.expected_revision + 1).model_dump()
                session.execute(update(Workflow).where(Workflow.id == identifier).values(archived=data.archived, revision=result["revision"]))
                record_change(session, public_id("usr", actor.user_id), "workflow.archived" if data.archived else "workflow.unarchived", identifier, request_id, {"archived": data.archived}, revision_before=data.expected_revision, revision_after=result["revision"])
                idem.result = result
                return result
        except DBAPIError:
            raise DomainError("workflow_lifecycle_unavailable", "Workflow 状态变更结果暂未确认，请使用原请求核对", 503) from None

    def restore_draft(self, actor: AdminIdentity, identifier: UUID, data: WorkflowDraftRestore, token: str, request_id: str) -> dict[str, Any]:
        try:
            with self.sessions.begin() as session:
                session.execute(text("SET LOCAL lock_timeout = '1s'"))
                session.execute(text("SET LOCAL statement_timeout = '5s'"))
                idem = mutation(session, actor, "workflow.draft.restore:" + str(identifier), token, data.model_dump())
                if idem.result:
                    return WorkflowDraftRestored.model_validate(idem.result).model_dump()
                parent = session.execute(select(Workflow.id, Workflow.tenant_id, Workflow.project_id, Workflow.task_type, Workflow.revision, Workflow.archived).where(Workflow.id == identifier).with_for_update()).mappings().one_or_none()
                if parent is None:
                    raise DomainError("not_found", "Workflow 不存在", 404)
                require_revision(parent["revision"], data.expected_revision)
                if parent["archived"]:
                    raise DomainError("workflow_archived", "已归档 Workflow 不能恢复草稿", 409)
                source = session.execute(select(WorkflowRelease.id, WorkflowRelease.digest, WorkflowRelease.validated, func.octet_length(cast(WorkflowRelease.manifest, Text)).label("document_bytes")).where(WorkflowRelease.id == parse_id("wfr", data.source_release_id), WorkflowRelease.workflow_id == identifier, WorkflowRelease.tenant_id == parent["tenant_id"], WorkflowRelease.project_id == parent["project_id"]).with_for_update(read=True)).mappings().one_or_none()
                if source is None:
                    raise DomainError("not_found", "同 Workflow 发布版本不存在", 404)
                if source["digest"] != data.source_digest:
                    raise DomainError("workflow_source_conflict", "来源版本摘要已不匹配，请重新选择", 409)
                if source["document_bytes"] > MAX_DOCUMENT_BYTES:
                    raise DomainError("workflow_restoration_limit", "来源发布包超过恢复草稿的大小或结构限制", 413)
                if not source["validated"]:
                    raise DomainError("workflow_source_invalid", "来源发布版本未通过当前结构与能力校验", 422)
                manifest = session.scalar(select(WorkflowRelease.manifest).where(WorkflowRelease.id == source["id"]))
                if not isinstance(manifest, dict):
                    raise DomainError("workflow_source_invalid", "来源发布版本未通过当前结构与能力校验", 422)
                try:
                    check_structure(manifest)
                except DomainError:
                    raise DomainError("workflow_restoration_limit", "来源发布包超过恢复草稿的大小或结构限制", 413) from None
                try:
                    WorkflowManifest.model_validate(manifest)
                    validate_manifest(manifest, parent["task_type"])
                    if fingerprint(manifest) != source["digest"]:
                        raise ValueError("Source content digest mismatch")
                except (ValueError, KeyError, TypeError, AttributeError, DomainError):
                    raise DomainError("workflow_source_invalid", "来源发布版本未通过当前结构与能力校验", 422) from None
                result = WorkflowDraftRestored(workflow_id=public_id("wf", identifier), source_release_id=data.source_release_id, source_digest=data.source_digest, previous_revision=data.expected_revision, revision=data.expected_revision + 1).model_dump()
                session.execute(update(Workflow).where(Workflow.id == identifier).values(draft=manifest, revision=result["revision"]))
                record_change(session, public_id("usr", actor.user_id), "workflow.draft_restored", identifier, request_id, {"source_release_id": data.source_release_id, "source_digest": data.source_digest}, revision_before=data.expected_revision, revision_after=result["revision"])
                idem.result = result
                return result
        except (ValueError, TypeError, KeyError, AttributeError):
            raise DomainError("workflow_source_invalid", "来源发布版本未通过当前结构与能力校验", 422) from None
        except DBAPIError:
            raise DomainError("workflow_restoration_unavailable", "草稿恢复结果暂未确认，请使用原请求核对", 503) from None

    def workflows(self, page: int = 1) -> dict[str, Any]:
        with self.sessions() as session:
            rows = list(session.scalars(select(Workflow).order_by(Workflow.created_at.desc(), Workflow.id).offset((page - 1) * 50).limit(51)))
            return {"data": [workflow_projection(w) for w in rows[:50]], "page": page, "has_more": len(rows) > 50}

    def workflow(self, identifier: UUID) -> dict[str, Any]:
        with self.sessions() as session:
            row = session.get(Workflow, identifier)
            if not row:
                raise DomainError("not_found", "Workflow 不存在", 404)
            return {**workflow_projection(row, draft=True), "releases": [{"id": public_id("wfr", r.id), "version": r.version, "digest": r.digest, "validated": r.validated, "validation": r.validation, "created_at": timestamp(r.created_at)} for r in session.scalars(select(WorkflowRelease).where(WorkflowRelease.workflow_id == identifier).order_by(WorkflowRelease.version.desc()).limit(100))]}

    def create_workflow(self, actor: AdminIdentity, data: WorkflowCreate, token: str, request_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            idem = mutation(session, actor, "workflow.create", token, data.model_dump())
            if idem.result:
                return idem.result
            tenant, project = scope_ids(session, data.tenant_id, data.project_id)
            row = Workflow(tenant_id=tenant, project_id=project, name=data.name, task_type=data.task_type, draft=data.draft)
            session.add(row)
            session.flush()
            record_change(session, public_id("usr", actor.user_id), "workflow.created", row.id, request_id, revision_before=None, revision_after=row.revision)
            idem.result = workflow_projection(row)
            return idem.result

    def patch_workflow(self, actor: AdminIdentity, identifier: UUID, data: WorkflowPatch, request_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            row = session.execute(select(Workflow).where(Workflow.id == identifier).with_for_update()).scalar_one_or_none()
            if not row:
                raise DomainError("not_found", "Workflow 不存在", 404)
            require_revision(row.revision, data.expected_revision)
            if row.archived:
                raise DomainError("workflow_archived", "Workflow 已归档，请先解除归档", 409)
            if data.name is not None:
                row.name = data.name
            if data.draft is not None:
                row.draft = data.draft
            row.revision += 1
            record_change(session, public_id("usr", actor.user_id), "workflow.updated", row.id, request_id, revision_before=data.expected_revision, revision_after=row.revision)
            return workflow_projection(row, draft=True)

    def release(self, actor: AdminIdentity, identifier: UUID, revision: int, token: str, request_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            idem = mutation(session, actor, "workflow.release:" + str(identifier), token, {"revision": revision})
            if idem.result:
                return idem.result
            workflow = session.execute(select(Workflow).where(Workflow.id == identifier).with_for_update()).scalar_one_or_none()
            if not workflow:
                raise DomainError("not_found", "Workflow 不存在", 404)
            require_revision(workflow.revision, revision)
            if workflow.archived:
                raise DomainError("workflow_archived", "Workflow 已归档，请先解除归档", 409)
            try:
                WorkflowManifest.model_validate(workflow.draft)
                checked = validate_manifest(workflow.draft, workflow.task_type)
            except (ValueError, KeyError, TypeError, AttributeError) as exc:
                raise DomainError("invalid_manifest", "发布包字段结构无效，请核对绑定、输出、依赖与画像", 422) from exc
            # Structural validity never stands in for hardware approval.
            version = (session.scalar(select(func.max(WorkflowRelease.version)).where(WorkflowRelease.workflow_id == identifier)) or 0) + 1
            row = WorkflowRelease(tenant_id=workflow.tenant_id, project_id=workflow.project_id, workflow_id=workflow.id, version=version, digest=fingerprint(workflow.draft), manifest=workflow.draft, validated=True, validation=checked)
            session.add(row)
            session.flush()
            record_change(session, public_id("usr", actor.user_id), "workflow.released", row.id, request_id, {"version": row.version})
            idem.result = {"id": public_id("wfr", row.id), "workflow_id": public_id("wf", identifier), "version": version, "digest": row.digest, "validation": checked}
            return idem.result

    def get_release(self, identifier: UUID) -> dict[str, Any]:
        with self.sessions() as session:
            release = session.get(WorkflowRelease, identifier)
            if not release:
                raise DomainError("not_found", "发布版本不存在", 404)
            return {"id": public_id("wfr", release.id), "workflow_id": public_id("wf", release.workflow_id), "version": release.version, "digest": release.digest, "manifest": release.manifest, "validated": release.validated, "validation": release.validation}
