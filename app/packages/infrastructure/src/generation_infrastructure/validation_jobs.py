from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import UUID

from generation_application.admin_access import require_permission
from generation_application.ports import AdminIdentity, BusinessIdentity
from generation_contracts.catalog import ValidationJob
from generation_contracts.defaults import default_key_policy
from generation_domain.errors import DomainError
from generation_domain.values import parse_id, public_id, utcnow
from sqlalchemy import select, text

from generation_infrastructure.admin_base import AdminBase, mutation
from generation_infrastructure.jobs import JobRepository
from generation_infrastructure.models import (
    ApiKey,
    Asset,
    Channel,
    ObjectRecord,
    PublicModel,
    WorkflowRelease,
)
from generation_infrastructure.transactions import record_change
from generation_infrastructure.workflow_lifecycle import require_active_workflow


class ValidationJobs(AdminBase):
    def create(self, actor: AdminIdentity, model_id: UUID, data: ValidationJob, token: str, request_id: str, *, release_id: UUID | None = None, expected_model_revision: int | None = None, expected_channel_revision: int | None = None) -> dict[str, Any]:
        request = data.request.model_dump(exclude_unset=True)
        guard = {"model_id": str(model_id), "channel_id": data.channel_id, "request": request, "release_id": str(release_id) if release_id else None, "expected_model_revision": expected_model_revision, "expected_channel_revision": expected_channel_revision}
        with self.sessions.begin() as session:
            idem = mutation(session, actor, "workflow.validation", token, guard)
            if idem.result is not None:
                return idem.result
            identity, target, revision = self._prepare(actor, model_id, data.channel_id, request, release_id, expected_model_revision, expected_channel_revision)
            result = JobRepository(self.sessions).create(identity, request, "validation:" + str(idem.id), request_id, validation_channel=parse_id("ch", data.channel_id), _session=session, audit_actor=public_id("usr", actor.user_id), validation_model=target, validation_revision=revision)
            result.pop("input", None)
            record_change(session, public_id("usr", actor.user_id), "workflow.validation_requested", parse_id("job", result["id"]), request_id, {"channel_id": data.channel_id, "release_id": public_id("wfr", target[1])})
            idem.result = result
            return result

    def _prepare(self, actor: AdminIdentity, model_id: UUID, channel_id: str, request: dict[str, Any], release_id: UUID | None, model_revision: int | None, channel_revision: int | None) -> tuple[BusinessIdentity, tuple[UUID, UUID, int], int]:
        inputs = request["input"]
        identifiers = {item["asset_id"] for item in inputs.get("references", [])}
        identifiers.update(inputs[name]["asset_id"] for name in ("source_image", "mask") if name in inputs)
        if identifiers:
            require_permission(actor, "content:sensitive")
        # Reusable aliases precede admission locks. Failed admission leaves no job or quota.
        with self.sessions.begin() as session:
            model = session.get(PublicModel, model_id)
            channel = session.get(Channel, parse_id("ch", channel_id))
            if model is not None:
                workflow_id = session.scalar(select(WorkflowRelease.workflow_id).where(WorkflowRelease.id == model.release_id))
                if workflow_id is not None:
                    require_active_workflow(session, workflow_id)
            if not model or not channel or channel.deleted_at or model.alias != request["model"] or channel_id not in model.channel_ids or (model.tenant_id, model.project_id) != (channel.tenant_id, channel.project_id):
                raise DomainError("not_found", "验证模型或同项目渠道不存在", 404)
            if release_id is not None and model.release_id != release_id or model_revision is not None and model.revision != model_revision or channel_revision is not None and channel.revision != channel_revision:
                raise DomainError("validation_target_changed", "模型、发布版本或渠道已变化，请刷新后重试", 409)
            target, revision = (model.id, model.release_id, model.revision), channel.revision
            session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 624087030))"), {"scope": str(model.project_id)})
            key = session.scalar(select(ApiKey).where(ApiKey.tenant_id == model.tenant_id, ApiKey.project_id == model.project_id, ApiKey.issuer == "internal_validation"))
            if not key:
                policy = default_key_policy()
                for selection in ("models", "workflow_releases", "channels"):
                    policy[selection] = {"mode": "all_authorized", "selected_ids": []}
                key = ApiKey(tenant_id=model.tenant_id, project_id=model.project_id, name="内部 Workflow 验证", issuer="internal_validation", policy=policy)
                session.add(key)
                session.flush()
            if model.task_type == "text_to_audio":
                policy = deepcopy(key.policy)
                if "audio:create" not in policy["scopes"]:
                    policy["scopes"].append("audio:create")
                policy["spec_limits"]["audio_max_milliseconds"] = 600000
                policy["limits"]["daily_audio_milliseconds"] = {"mode": "unlimited", "value": None}
                if policy != key.policy:
                    key.policy = policy
                    key.revision += 1
            identity = BusinessIdentity(key.tenant_id, key.project_id, key.id, key.policy)
            assets = list(session.scalars(select(Asset).where(Asset.id.in_([parse_id("asset", identifier) for identifier in identifiers]), Asset.tenant_id == key.tenant_id, Asset.project_id == key.project_id).order_by(Asset.id).with_for_update()))
            if len(assets) != len(identifiers) or any(asset.deleted_at for asset in assets):
                raise DomainError("input_not_available", "验证素材须属于同项目且未删除", 422)
            objects = {obj.id: obj for obj in session.scalars(select(ObjectRecord).where(ObjectRecord.id.in_([asset.object_id for asset in assets])).order_by(ObjectRecord.id).with_for_update())}
            replacements: dict[str, str] = {}
            for asset in assets:
                obj = objects.get(asset.object_id)
                if not obj or obj.deleted_at or obj.delete_requested_at or not obj.verified_at or obj.expires_at <= utcnow():
                    raise DomainError("input_not_available", "验证素材须已验证且未过期", 422)
                copied = session.scalar(select(Asset).where(Asset.object_id == obj.id, Asset.key_id == key.id, Asset.deleted_at.is_(None)))
                if not copied:
                    copied = Asset(tenant_id=key.tenant_id, project_id=key.project_id, key_id=key.id, object_id=obj.id)
                    session.add(copied)
                    session.flush()
                replacements[public_id("asset", asset.id)] = public_id("asset", copied.id)
            for reference in inputs.get("references", []):
                reference["asset_id"] = replacements[reference["asset_id"]]
            for name in ("source_image", "mask"):
                if name in inputs:
                    inputs[name]["asset_id"] = replacements[inputs[name]["asset_id"]]
            return identity, target, revision
