from __future__ import annotations

from typing import Any
from uuid import UUID

from generation_application.ports import AdminIdentity, BusinessIdentity
from generation_contracts.catalog import (
    ApproveRuntime,
    BindRelease,
    ModelCreate,
    ModelPatch,
    WorkflowSync,
)
from generation_contracts.device_pool import DeviceRouting
from generation_contracts.discovery import PublishedCapability
from generation_contracts.model_catalog import ModelCatalogQuery
from generation_contracts.model_usage import ModelDocumentationPatch
from generation_domain.errors import DomainError
from generation_domain.values import canonical_json, fingerprint, parse_id, public_id, utcnow
from generation_domain.workflows import matching_presets
from jsonschema.exceptions import SchemaError
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from generation_infrastructure.admin_base import (
    AdminBase,
    mutation,
    require_revision,
    scope_ids,
    timestamp,
)
from generation_infrastructure.admin_channels import begin_channel_read, enqueue_command
from generation_infrastructure.device_pools import pool_view, validate_routing
from generation_infrastructure.model_usage import model_usage
from generation_infrastructure.models import (
    Artifact,
    Attempt,
    Channel,
    Job,
    ManagementOperation,
    ObjectRecord,
    PublicModel,
    WorkerCommand,
    Workflow,
    WorkflowBinding,
    WorkflowRelease,
)
from generation_infrastructure.transactions import record_change
from generation_infrastructure.workflow_lifecycle import require_active_workflow


def model_view(row: PublicModel) -> dict[str, Any]:
    return {"id": public_id("mdl", row.id), "tenant_id": public_id("ten", row.tenant_id), "project_id": public_id("prj", row.project_id), "alias": row.alias, "task_type": row.task_type, "release_id": public_id("wfr", row.release_id), "channel_ids": row.channel_ids, "defaults": row.defaults, "revision": row.revision, "published": row.published, "documentation": row.documentation or {}, "routing": row.routing}


class CatalogAdministration(AdminBase):
    def models(self, page: int = 1, *, query: ModelCatalogQuery | None = None) -> dict[str, Any]:
        chosen = query or ModelCatalogQuery(page=page)
        try:
            with self.sessions.begin() as session:
                begin_channel_read(session)
                statement = select(PublicModel)
                if chosen.q:
                    statement = statement.where(PublicModel.alias.icontains(chosen.q, autoescape=True))
                if chosen.project_id:
                    statement = statement.where(PublicModel.project_id == parse_id("prj", chosen.project_id))
                if chosen.task_type:
                    statement = statement.where(PublicModel.task_type == chosen.task_type)
                if chosen.status:
                    statement = statement.where(PublicModel.published.is_(chosen.status == "published"))
                if chosen.model_id:
                    statement = statement.where(PublicModel.id == parse_id("mdl", chosen.model_id))
                total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
                rows = list(session.scalars(statement.order_by(PublicModel.created_at.desc(), PublicModel.id).offset((chosen.page - 1) * 50).limit(51)))
                return {"snapshot_at": int(utcnow().timestamp()), "total": total, "data": [model_view(row) for row in rows[:50]], "page": chosen.page, "has_more": len(rows) > 50}
        except DBAPIError:
            raise DomainError("model_catalog_unavailable", "模型目录暂不可用，请稍后重试", 503) from None

    def _validate(self, session: Any, tenant: UUID, project: UUID, release_id: str, channel_ids: list[str], defaults: dict[str, Any]) -> WorkflowRelease:
        release = session.get(WorkflowRelease, parse_id("wfr", release_id))
        if not release or release.tenant_id != tenant or release.project_id != project or not release.validated:
            raise DomainError("release_not_available", "请选择同项目的冻结 Workflow 发布版本", 422)
        require_active_workflow(session, release.workflow_id)
        if len(set(channel_ids)) != len(channel_ids):
            raise DomainError("duplicate_channel", "渠道不能重复", 422)
        for identifier in channel_ids:
            channel = session.get(Channel, parse_id("ch", identifier))
            if not channel or channel.deleted_at or channel.tenant_id != tenant or channel.project_id != project:
                raise DomainError("channel_not_available", "请选择同项目的有效渠道", 422)
        if release.manifest["task_type"] == "image_to_3d" and set(defaults) - {"quality", "n"}:
            raise DomainError("invalid_defaults", "3D 默认值只允许冻结质量和 n=1", 422)
        if set(defaults) - {"size", "quality", "seconds", "n"} or defaults.get("n", 1) != 1:
            raise DomainError("invalid_defaults", "模型默认值仅允许受控 size、quality、seconds、n=1", 422)
        if len(matching_presets(release.manifest, defaults)) != 1:
            raise DomainError("unsupported_specification", "模型默认值须对应唯一冻结预设", 422)
        return release

    def create_model(self, actor: AdminIdentity, data: ModelCreate, token: str, request_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            idem = mutation(session, actor, "model.create", token, data.model_dump())
            if idem.result:
                return idem.result
            tenant, project = scope_ids(session, data.tenant_id, data.project_id)
            release = self._validate(session, tenant, project, data.release_id, data.channel_ids, data.defaults)
            row = PublicModel(tenant_id=tenant, project_id=project, alias=data.alias, task_type=release.manifest["task_type"], release_id=release.id, channel_ids=data.channel_ids, defaults=data.defaults, documentation=data.documentation.model_dump())
            session.add(row)
            session.flush()
            row.routing = validate_routing(session, row, data.routing)
            record_change(session, public_id("usr", actor.user_id), "model.created", row.id, request_id, revision_before=None, revision_after=row.revision)
            idem.result = model_view(row)
            return idem.result

    def patch_documentation(self, actor: AdminIdentity, identifier: UUID, data: ModelDocumentationPatch, token: str, request_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            idem = mutation(session, actor, "model.documentation:" + str(identifier), token, data.model_dump())
            if idem.result is not None:
                return idem.result
            row = session.execute(select(PublicModel).where(PublicModel.id == identifier).with_for_update()).scalar_one_or_none()
            if row is None:
                raise DomainError("not_found", "模型不存在", 404)
            require_revision(row.revision, data.expected_revision)
            row.documentation = data.documentation.model_dump()
            row.revision += 1
            record_change(session, public_id("usr", actor.user_id), "model.documentation_updated", row.id, request_id, revision_before=data.expected_revision, revision_after=row.revision)
            idem.result = model_view(row)
            return idem.result

    def usage(self, identifier: UUID) -> dict[str, Any]:
        with self.sessions() as session:
            row = session.get(PublicModel, identifier)
            if row is None:
                raise DomainError("not_found", "模型不存在", 404)
            release = session.get(WorkflowRelease, row.release_id)
            if release is None or fingerprint(release.manifest) != release.digest:
                raise DomainError("release_not_available", "冻结发布版本不可用", 409)
            try:
                return model_usage(row.alias, row.task_type, release.manifest, row.defaults, row.documentation or {})
            except (DomainError, KeyError, TypeError, ValueError, JsonSchemaValidationError, SchemaError):
                raise DomainError("usage_not_available", "发布版本尚无完整可用的调用说明", 409) from None

    def patch_model(self, actor: AdminIdentity, identifier: UUID, data: ModelPatch, request_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            row = session.execute(select(PublicModel).where(PublicModel.id == identifier).with_for_update()).scalar_one_or_none()
            if not row:
                raise DomainError("not_found", "模型不存在", 404)
            require_revision(row.revision, data.expected_revision)
            release = self._validate(session, row.tenant_id, row.project_id, data.release_id, data.channel_ids, data.defaults)
            row.release_id, row.task_type, row.channel_ids, row.defaults = release.id, release.manifest["task_type"], data.channel_ids, data.defaults
            if data.routing is not None:
                row.routing = validate_routing(session, row, data.routing)
            else:
                row.routing = validate_routing(session, row, DeviceRouting.model_validate(row.routing))
            row.published = False
            row.revision += 1
            record_change(session, public_id("usr", actor.user_id), "model.updated", row.id, request_id, revision_before=data.expected_revision, revision_after=row.revision)
            return model_view(row)

    def device_pool(self, identifier: UUID) -> dict[str, Any]:
        with self.sessions.begin() as session:
            begin_channel_read(session)
            row = session.get(PublicModel, identifier)
            if row is None:
                raise DomainError("not_found", "模型不存在", 404)
            return pool_view(session, row)

    def model_action(self, actor: AdminIdentity, identifier: UUID, revision: int, publish: bool, request_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            row = session.execute(select(PublicModel).where(PublicModel.id == identifier).with_for_update()).scalar_one_or_none()
            if not row:
                raise DomainError("not_found", "模型不存在", 404)
            require_revision(row.revision, revision)
            release = session.get(WorkflowRelease, row.release_id)
            assert release
            if publish:
                require_active_workflow(session, release.workflow_id)
                for channel_identifier in row.channel_ids:
                    channel = session.execute(select(Channel).where(Channel.id == parse_id("ch", channel_identifier)).with_for_update(read=True)).scalar_one()
                    binding = session.scalar(select(WorkflowBinding).where(WorkflowBinding.channel_id == channel.id, WorkflowBinding.release_id == row.release_id))
                    if channel.deleted_at or channel.desired_revision != channel.verified_revision or channel.desired_revision != channel.applied_revision or not binding or binding.verified_digest != release.digest:
                        raise DomainError("channel_not_verified", "所有映射渠道须先应用并验证当前 Workflow")
                    if not binding.runtime_approval:
                        raise DomainError("runtime_evidence_required", "缺少该发布版本在目标 GPU 的成功验证产物，不能对外发布")
            row.published = publish
            row.revision += 1
            record_change(session, public_id("usr", actor.user_id), "model.published" if publish else "model.unpublished", row.id, request_id, revision_before=revision, revision_after=row.revision)
            return model_view(row)

    def approve_runtime(self, actor: AdminIdentity, channel_id: UUID, data: ApproveRuntime, request_id: str, token: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            idem = mutation(session, actor, "workflow.runtime_approve", token, {"channel_id": str(channel_id), **data.model_dump()})
            if idem.result is not None:
                return idem.result
            channel = session.execute(select(Channel).where(Channel.id == channel_id).with_for_update()).scalar_one_or_none()
            if not channel or channel.deleted_at:
                raise DomainError("not_found", "渠道不存在", 404)
            require_revision(channel.revision, data.expected_revision)
            release_id = parse_id("wfr", data.release_id)
            binding = session.scalar(select(WorkflowBinding).where(WorkflowBinding.channel_id == channel_id, WorkflowBinding.release_id == release_id).with_for_update())
            release = session.get(WorkflowRelease, release_id)
            evidence = session.scalar(select(Artifact).where(Artifact.id == parse_id("content", data.evidence_id)).with_for_update())
            obj = session.scalar(select(ObjectRecord).where(ObjectRecord.id == evidence.object_id).with_for_update()) if evidence else None
            job = session.get(Job, evidence.job_id) if evidence else None
            attempt = session.get(Attempt, job.current_attempt_id) if job else None
            if not release or not binding or binding.verified_digest != release.digest or not evidence or evidence.deleted_at or not obj or not obj.verified_at or obj.deleted_at or obj.delete_requested_at or obj.expires_at <= utcnow() or (evidence.tenant_id, evidence.project_id) != (channel.tenant_id, channel.project_id) or not job or job.state != "succeeded" or not job.is_validation or job.release_id != release_id or not attempt or attempt.gpu_id != channel.gpu_id or attempt.channel_id != channel.id or attempt.channel_snapshot.get("revision") != channel.desired_revision:
                raise DomainError("runtime_evidence_required", "批准必须引用该发布版本在目标 GPU 已成功交付的验证任务产物", 422)
            require_active_workflow(session, release.workflow_id)
            from generation_infrastructure.models import Gpu
            gpu = session.get(Gpu, channel.gpu_id)
            profile = next((p for p in release.manifest["runtime_profiles"] if gpu and p["gpu_hardware_id"] == gpu.hardware_id), None)
            if not profile or profile["id"] != data.profile_id:
                raise DomainError("profile_mismatch", "运行画像与目标物理 GPU 不匹配", 422)
            binding.runtime_approval = {"profile_id": data.profile_id, "evidence_id": data.evidence_id, "actor_id": public_id("usr", actor.user_id)}
            channel.revision += 1
            record_change(session, public_id("usr", actor.user_id), "workflow.runtime_approved", channel.id, request_id, {"release_id": data.release_id}, revision_before=data.expected_revision, revision_after=channel.revision)
            idem.result = {"approved": True, "revision": channel.revision}
            return idem.result

    def _prepare_binding(self, session: Session, channel: Channel, release: WorkflowRelease) -> dict[str, Any]:
        require_active_workflow(session, release.workflow_id)
        binding = session.scalar(select(WorkflowBinding).where(WorkflowBinding.channel_id == channel.id, WorkflowBinding.release_id == release.id))
        if not binding:
            binding = WorkflowBinding(tenant_id=channel.tenant_id, project_id=channel.project_id, channel_id=channel.id, release_id=release.id, desired_digest=release.digest)
            session.add(binding)
        binding.verified_digest = None
        binding.runtime_approval = None
        channel.draining = True
        channel.revision += 1
        return {"revision": channel.desired_revision, "release_id": public_id("wfr", release.id), "release_digest": release.digest, "manifest": release.manifest}

    def synchronize(self, actor: AdminIdentity, data: WorkflowSync, token: str, request_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            idem = mutation(session, actor, "workflow.synchronize", token, data.model_dump(mode="json"))
            if idem.result is not None:
                return idem.result
            release = session.get(WorkflowRelease, parse_id("wfr", data.release_id))
            if not release or not release.validated or release.digest != data.release_digest:
                raise DomainError("release_not_available", "发布版本或摘要不匹配", 422)
            if len(canonical_json(release.manifest).encode()) * len(data.targets) > 16 * 1024 * 1024:
                raise DomainError("sync_batch_too_large", "本批冻结指令超过 16 MiB，请减少目标数量", 422)
            expected = {parse_id("ch", target.channel_id): target.expected_revision for target in data.targets}
            channels = list(session.scalars(select(Channel).where(Channel.id.in_(expected)).order_by(Channel.id).with_for_update()))
            if len(channels) != len(expected) or any(channel.deleted_at or (channel.tenant_id, channel.project_id) != (release.tenant_id, release.project_id) for channel in channels):
                raise DomainError("not_found", "目标渠道必须全部属于发布版本的同一项目", 404)
            for channel in channels:
                require_revision(channel.revision, expected[channel.id])
            operation = ManagementOperation(actor_id=actor.user_id, command="apply_workflow_release", summary={"release_id": data.release_id, "target_count": len(channels)})
            session.add(operation)
            session.flush()
            for channel in channels:
                payload = self._prepare_binding(session, channel, release)
                session.add(WorkerCommand(operation_id=operation.id, node_id=channel.node_id, channel_id=channel.id, command="apply_workflow_release", payload=payload))
                record_change(session, public_id("usr", actor.user_id), "workflow.channel_bound", channel.id, request_id, {"release_id": data.release_id}, revision_before=expected[channel.id], revision_after=channel.revision, operation_id=operation.id)
            record_change(session, public_id("usr", actor.user_id), "workflow.sync_requested", operation.id, request_id, {"release_id": data.release_id, "target_count": len(channels)}, operation_id=operation.id)
            idem.result = {"operation_id": public_id("op", operation.id), "status": "pending"}
            return idem.result

    def bindings(self, channel_id: UUID) -> list[dict[str, Any]]:
        with self.sessions() as session:
            return [{"release_id": public_id("wfr", row.release_id), "desired_digest": row.desired_digest, "applied_digest": row.applied_digest, "verified_digest": row.verified_digest, "observed_at": timestamp(row.observed_at)} for row in session.scalars(select(WorkflowBinding).where(WorkflowBinding.channel_id == channel_id).order_by(WorkflowBinding.created_at))]

    def bind(self, actor: AdminIdentity, channel_id: UUID, data: BindRelease, token: str, request_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            idem = mutation(session, actor, "channel.bind:" + str(channel_id), token, data.model_dump())
            if idem.result:
                return idem.result
            channel = session.execute(select(Channel).where(Channel.id == channel_id).with_for_update()).scalar_one_or_none()
            release = session.get(WorkflowRelease, parse_id("wfr", data.release_id))
            if not channel or channel.deleted_at or not release or not release.validated or (channel.tenant_id, channel.project_id) != (release.tenant_id, release.project_id):
                raise DomainError("not_found", "渠道或同项目发布版本不存在", 404)
            require_revision(channel.revision, data.expected_revision)
            approval = None
            if data.verify_only:
                existing = session.scalar(select(WorkflowBinding).where(WorkflowBinding.channel_id == channel.id, WorkflowBinding.release_id == release.id).with_for_update())
                if not existing or existing.desired_digest != release.digest or existing.applied_digest != release.digest or not existing.runtime_approval or channel.desired_revision != channel.applied_revision:
                    raise DomainError("workflow_reverification_required", "仅允许重新核验相同已应用且取得运行批准的发布；新绑定或配置变更必须重新验证", 422)
                approval = dict(existing.runtime_approval)
            payload = self._prepare_binding(session, channel, release)
            if approval is not None:
                # A boot change invalidates environment observation, not historical
                # runtime approval. Dispatch remains fenced by verified_digest until
                # this immutable package passes a fresh environment command.
                assert existing is not None
                existing.runtime_approval = approval
            idem.result = enqueue_command(session, actor, channel, "apply_workflow_release", payload, request_id)
            record_change(session, public_id("usr", actor.user_id), "workflow.channel_bound", channel.id, request_id, {"release_id": data.release_id}, revision_before=data.expected_revision, revision_after=channel.revision, operation_id=parse_id("op", idem.result["operation_id"]))
            return idem.result

    def public_models(self, identity: BusinessIdentity, capabilities: bool = False, *, model: str | None = None) -> dict[str, Any]:
        identity.require("capabilities:read" if capabilities else "models:read")
        with self.sessions() as session:
            query = select(PublicModel, WorkflowRelease).join(WorkflowRelease, PublicModel.release_id == WorkflowRelease.id).join(Workflow, Workflow.id == WorkflowRelease.workflow_id).where(Workflow.archived.is_(False), PublicModel.tenant_id == identity.tenant_id, PublicModel.project_id == identity.project_id, PublicModel.published.is_(True), WorkflowRelease.validated.is_(True), WorkflowRelease.manifest["task_type"].astext == PublicModel.task_type)
            if model is not None:
                query = query.where(PublicModel.alias == model)
            selection = identity.policy["models"]
            if selection["mode"] != "all_authorized":
                query = query.where(PublicModel.alias.in_(selection["selected_ids"]))
            selection = identity.policy["workflow_releases"]
            if selection["mode"] != "all_authorized":
                query = query.where(PublicModel.release_id.in_([parse_id("wfr", identifier) for identifier in selection["selected_ids"]]))
            selection = identity.policy["channels"]
            if selection["mode"] != "all_authorized":
                query = query.where(or_(False, *[PublicModel.channel_ids.contains([identifier]) for identifier in selection["selected_ids"]]))
            rows = list(session.execute(query.order_by(PublicModel.alias).limit(4097)))
            if len(rows) > 4096:
                raise DomainError("catalog_too_large", "模型目录超过发现接口限额", 503)
            result = []
            response_bytes = 0
            for row, release in rows:
                item = {"id": row.alias, "object": "model", "created": int(row.created_at.timestamp()), "owned_by": "render-control"}
                manifest = release.manifest
                if fingerprint(manifest) != release.digest:
                    continue
                if {"bindings", "presets", "supported_parameters"} <= manifest.keys():
                    try:
                        item["usage"] = model_usage(row.alias, row.task_type, manifest, row.defaults, row.documentation or {})
                    except (DomainError, KeyError, TypeError, ValueError, JsonSchemaValidationError, SchemaError):
                        continue
                if capabilities:
                    try:
                        manifest = release.manifest
                        if fingerprint(manifest) != release.digest:
                            continue
                        item.update({"task_type": row.task_type, "asynchronous": True, "release": {"version": release.version, "digest": release.digest}, "supported_parameters": manifest["supported_parameters"], "presets": [{k: v for k, v in preset.items() if k in ("quality", "size", "seconds", "audio_format", "sample_rate", "channels")} for preset in manifest["presets"]], "output": {"media_types": manifest["output"]["mime_types"], "expected_count": manifest["output"]["expected_count"], "max_bytes": manifest["output"]["max_bytes"]}})
                        item = PublishedCapability.model_validate(item).model_dump(exclude_none=True)
                    except (ValidationError, KeyError, TypeError):
                        continue
                response_bytes += len(canonical_json(item).encode("utf-8"))
                if response_bytes > 8 * 1024**2:
                    raise DomainError("catalog_too_large", "模型目录说明超过响应限额，请缩小 Key 的授权模型范围", 503)
                result.append(item)
            return {"object": "list", "data": result}

    def public_model(self, identity: BusinessIdentity, model: str) -> dict[str, Any]:
        items = self.public_models(identity, model=model)["data"]
        if not items:
            raise DomainError("not_found", "模型不存在或未授权", 404)
        return dict(items[0])
