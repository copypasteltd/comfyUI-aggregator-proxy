from __future__ import annotations

import hashlib
import secrets
from contextlib import nullcontext
from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from generation_application.ports import BusinessIdentity
from generation_contracts.collections import CollectionQuery
from generation_domain.errors import DomainError
from generation_domain.media_types import task_media_kind
from generation_domain.policies import enforce_limit, selected
from generation_domain.spec_isolation import GLOBAL_UNFINISHED_JOBS
from generation_domain.state import TERMINAL, transition
from generation_domain.values import fingerprint, parse_id, public_id, request_fingerprint, utcnow
from generation_domain.workflows import (
    audio_specification,
    matching_presets,
    validate_reference_inputs,
)
from sqlalchemy import literal, select, text, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, defer, sessionmaker

from generation_infrastructure.cursors import CursorCodec
from generation_infrastructure.gateway_catalogs import (
    check_admission_model,
    lock_catalog_for_admission,
)
from generation_infrastructure.gateway_intent import PendingGateway, lock_request_fence
from generation_infrastructure.models import (
    ApiKey,
    Artifact,
    Asset,
    Attempt,
    Channel,
    ExternalIdentityBinding,
    GatewayAdmission,
    GpuOccupancy,
    Idempotency,
    Job,
    JobAssetReference,
    ObjectRecord,
    OutboxEvent,
    Project,
    PublicModel,
    Tenant,
    UsageLedger,
    WorkflowRelease,
)
from generation_infrastructure.transactions import lock_buckets, record_change
from generation_infrastructure.workflow_lifecycle import require_active_workflow


def check_input_contract(manifest: dict[str, Any], request: dict[str, Any]) -> None:
    validate_reference_inputs(manifest, request["input"])
    support = set(manifest.get("supported_parameters", []))
    optional = set(request.get("parameters", {})) - {"size", "quality", "seed", "n", "seconds"}
    if not optional <= support or ("negative_prompt" in request["input"] and "negative_prompt" not in support) or ("mask" in request["input"] and "mask" not in support):
        raise DomainError("parameter_not_supported", "工作流没有声明对应参数能力", 422)


def job_projection(job: Job, *, sensitive: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {"id": public_id("job", job.id), "object": "generation.job", "model": job.model_alias, "task_type": job.task_type, "status": job.state, "created_at": int(job.created_at.timestamp()), "updated_revision": job.revision, "error": {"code": job.error_code} if job.error_code else None}
    result["retry"] = {"count": job.retry_count or 0, "limit": 2, "next_at": int(job.retry_after.timestamp()) if job.retry_after else None, "last_error": job.retry_error_code}
    if job.terminal_at:
        result["completed_at"] = int(job.terminal_at.timestamp())
    if sensitive:
        result["input"] = job.request["input"]
    return result


def owned_job(session: Session, identity: BusinessIdentity, identifier: UUID, *, lock: bool = False) -> Job:
    query = select(Job).where(Job.id == identifier, Job.tenant_id == identity.tenant_id, Job.project_id == identity.project_id, Job.key_id == identity.key_id)
    job = session.execute(query.with_for_update() if lock else query).scalar_one_or_none()
    if job is None:
        raise DomainError("not_found", "任务不存在", 404)
    return job


def terminal_accounting(session: Session, job: Job, buckets: dict[Any, Any], target: str, *, audio_milliseconds: int | None = None) -> None:
    video = job.task_type.endswith("video")
    audio = job.task_type == "text_to_audio"
    milliseconds = int(Decimal(str(job.execution["parameters"].get("seconds", 0))) * 1000)
    actual_audio = audio_milliseconds if audio and target == "succeeded" else 0
    if audio and target == "succeeded" and (type(actual_audio) is not int or not 0 < actual_audio <= milliseconds):
        raise DomainError("audio_usage_not_verified", "音频成功结算必须提供已验证且未超过预留的实际时长", 409)
    assert actual_audio is not None
    for (_kind, period), bucket in buckets.items():
        if period == "active":
            bucket.unfinished_jobs -= 1
        elif target != "succeeded":
            bucket.daily_image_count -= 0 if video or audio else 1
            bucket.daily_video_milliseconds -= milliseconds if video else 0
        if period != "active" and audio:
            bucket.daily_audio_milliseconds -= milliseconds - actual_audio
    job.terminal_at = utcnow()
    job.retry_after = None
    job.revision += 1
    job.state = target
    input_objects = select(Asset.object_id).join(JobAssetReference, JobAssetReference.asset_id == Asset.id).where(JobAssetReference.job_id == job.id)
    for obj in session.scalars(select(ObjectRecord).where(ObjectRecord.id.in_(input_objects)).order_by(ObjectRecord.id).with_for_update()):
        obj.expires_at = max(obj.expires_at, job.terminal_at + timedelta(hours=24))
    session.add(UsageLedger(job_id=job.id, key_id=job.key_id, image_count=1 if target == "succeeded" and not video and not audio else 0, video_milliseconds=milliseconds if target == "succeeded" and video else 0, audio_milliseconds=actual_audio, outcome=target))
    admission = session.scalar(select(GatewayAdmission).where(GatewayAdmission.job_id == job.id))
    if admission:
        session.add(OutboxEvent(kind="gateway.settlement", aggregate_id=job.id, payload={"gateway_id": str(admission.gateway_id), "job_id": public_id("job", job.id), "external_task_ref": admission.external_task_ref, "authorization_ref": admission.authorization_ref, "price_version": admission.price_version, "outcome": target, "image_count": 1 if target == "succeeded" and not video and not audio else 0, "video_milliseconds": milliseconds if target == "succeeded" and video else 0, **({"audio_milliseconds": actual_audio} if audio else {})}))


class JobRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def preflight_inputs(self, identity: BusinessIdentity, request: dict[str, Any]) -> None:
        # Read-only input check before compatibility uploads; create() repeats it
        # inside the authoritative admission transaction after checking assets.
        with self.sessions() as session:
            model = session.scalar(select(PublicModel).where(PublicModel.tenant_id == identity.tenant_id, PublicModel.project_id == identity.project_id, PublicModel.alias == request["model"], PublicModel.published.is_(True)))
            if model is None or model.task_type != request["task_type"]:
                raise DomainError("model_not_available", "模型未发布或不支持该任务", 422)
            release = session.get(WorkflowRelease, model.release_id)
            if not selected(identity.policy["models"], model.alias) or not release or not release.validated or not selected(identity.policy["workflow_releases"], public_id("wfr", release.id)):
                raise DomainError("workflow_denied", "模型或工作流未授权", 403)
            require_active_workflow(session, release.workflow_id)
            check_input_contract(release.manifest, request)

    def create(self, identity: BusinessIdentity, request: dict[str, Any], token: str, request_id: str, *, validation_channel: UUID | None = None, pending_gateway: PendingGateway | None = None, _session: Session | None = None, retry_of: UUID | None = None, audit_actor: str | None = None, validation_model: tuple[UUID, UUID, int] | None = None, validation_revision: int | None = None) -> dict[str, Any]:
        now = utcnow()
        period = now.astimezone(ZoneInfo("Asia/Taipei")).date().isoformat()
        with self.sessions.begin() if _session is None else nullcontext(_session) as session:
            if pending_gateway:
                fence = lock_request_fence(session, pending_gateway.gateway_id, pending_gateway.identity)
                if fence.closed_at is not None:
                    raise DomainError("gateway_request_closed", "网关请求已经持久关闭", 409)
            asset_hashes = self._asset_hashes(session, identity, request, require_ready=False)
            digest = request_fingerprint(request, asset_hashes)
            token_digest = hashlib.sha256(token.encode()).hexdigest()
            session.execute(insert(Idempotency).values(tenant_id=identity.tenant_id, project_id=identity.project_id, key_id=identity.key_id, operation="create_job", token_digest=token_digest, request_fingerprint=digest).on_conflict_do_nothing())
            idem = session.execute(select(Idempotency).where(Idempotency.key_id == identity.key_id, Idempotency.operation == "create_job", Idempotency.token_digest == token_digest).with_for_update()).scalar_one()
            if idem.request_fingerprint != digest:
                raise DomainError("idempotency_conflict", "同一幂等键对应不同请求")
            if idem.resource_id is not None:
                if pending_gateway:
                    previous = session.scalar(select(GatewayAdmission).where(GatewayAdmission.job_id == idem.resource_id))
                    if not previous or previous.intent_digest != pending_gateway.digest:
                        raise DomainError("idempotency_conflict", "网关准入意图与原请求不一致")
                return job_projection(owned_job(session, identity, idem.resource_id), sensitive="content:sensitive" in identity.policy["scopes"])
            buckets = lock_buckets(session, identity.tenant_id, identity.project_id, identity.key_id, period)
            # Idempotency inserts already hold an FK KEY SHARE on this parent.
            # NO KEY UPDATE serializes policy edits without a lock upgrade cycle.
            key = session.execute(select(ApiKey).where(ApiKey.id == identity.key_id).with_for_update(key_share=True)).scalar_one_or_none()
            tenant = session.get(Tenant, identity.tenant_id)
            project = session.get(Project, identity.project_id)
            if not key or not tenant or not project or not key.enabled or key.deleted_at or not tenant.enabled or not project.enabled or (key.expires_at and key.expires_at <= now):
                raise DomainError("key_disabled", "访问已停用", 403)
            policy = key.policy
            binding: ExternalIdentityBinding | None = None
            if pending_gateway:
                binding = session.execute(select(ExternalIdentityBinding).where(ExternalIdentityBinding.id == pending_gateway.binding_id).with_for_update(read=True)).scalar_one_or_none()
                if not binding or not binding.enabled or binding.key_id != key.id or binding.gateway_id != pending_gateway.gateway_id or binding.revision != pending_gateway.binding_revision or key.issuer != "gateway:" + pending_gateway.gateway_id.hex:
                    raise DomainError("gateway_binding_changed", "网关主体绑定已变化或未授权", 403)
            if validation_channel is not None and key.issuer != "internal_validation":
                raise DomainError("validation_identity_required", "验证任务必须由控制面内部主体创建", 403)
            if retry_of is not None:
                original = owned_job(session, identity, retry_of)
                active_attempt = session.scalar(select(Attempt.id).where(Attempt.job_id == original.id, (Attempt.closed_at.is_(None) | Attempt.generation_released_at.is_(None))).limit(1))
                occupied = session.scalar(select(GpuOccupancy.gpu_id).where(GpuOccupancy.job_id == original.id).limit(1))
                if original.state not in ("failed", "cancelled", "expired") or active_attempt or occupied:
                    raise DomainError("retry_not_safe", "仅可重试已确认停止且无资源占用的失败、取消或过期任务", 409)
                if key.issuer != "local" or original.is_validation:
                    raise DomainError("retry_requires_origin", "网关任务须重新预授权；验证任务须使用 Workflow 验证入口", 409)
            BusinessIdentity(key.tenant_id, key.project_id, key.id, policy).require("jobs:create")
            kind = task_media_kind(request["task_type"])
            BusinessIdentity(key.tenant_id, key.project_id, key.id, policy).require({"image": "images:create", "video": "videos:create", "audio": "audio:create"}[kind])
            self._asset_hashes(session, identity, request, require_ready=True)
            if not selected(policy["models"], request["model"]):
                raise DomainError("model_denied", "模型未授权", 403)
            gateway_configuration = lock_catalog_for_admission(session, pending_gateway.gateway_id, pending_gateway.catalog_stamp) if pending_gateway else None
            model_query = select(PublicModel).where(PublicModel.tenant_id == identity.tenant_id, PublicModel.project_id == identity.project_id, PublicModel.alias == request["model"])
            if validation_channel is None:
                model_query = model_query.where(PublicModel.published.is_(True))
            if validation_channel is not None or pending_gateway is not None:
                model_query = model_query.with_for_update(read=True)
            model = session.execute(model_query).scalar_one_or_none()
            if model is None or model.task_type != request["task_type"]:
                raise DomainError("model_not_available", "模型未发布或不支持该任务", 422)
            if validation_model is not None and (model.id, model.release_id, model.revision) != validation_model:
                raise DomainError("validation_target_changed", "验证模型或发布版本已变化，请刷新后重试", 409)
            release = session.get(WorkflowRelease, model.release_id)
            if not release or not release.validated or not selected(policy["workflow_releases"], public_id("wfr", release.id)):
                raise DomainError("workflow_denied", "工作流不可用或未授权", 403)
            if gateway_configuration is not None and pending_gateway is not None:
                check_admission_model(session, gateway_configuration, model, release, pending_gateway.price_version)
            require_active_workflow(session, release.workflow_id)
            check_input_contract(release.manifest, request)
            routes = list(session.scalars(select(Channel).where(Channel.tenant_id == identity.tenant_id, Channel.project_id == identity.project_id, Channel.deleted_at.is_(None), Channel.enabled.is_(True), Channel.draining.is_(False)).order_by(Channel.id).with_for_update(read=True)))
            eligible = [public_id("ch", c.id) for c in routes if public_id("ch", c.id) in model.channel_ids and selected(policy["channels"], public_id("ch", c.id))]
            if validation_channel is not None:
                target = next((channel for channel in routes if channel.id == validation_channel), None)
                if not target or validation_revision is not None and target.revision != validation_revision:
                    raise DomainError("validation_target_changed", "验证渠道版本或启用状态已变化", 409)
                eligible = [identifier for identifier in eligible if identifier == public_id("ch", validation_channel)]
            if not eligible:
                raise DomainError("no_authorized_channel", "没有授权渠道", 422)
            parameters = {**model.defaults, **request.get("parameters", {})}
            parameters.setdefault("seed", secrets.randbelow(2147483648))
            if kind != "audio":
                parameters.setdefault("size", "1024x1024")
            parameters.setdefault("quality", "standard")
            parameters.setdefault("n", 1)
            if release.manifest.get("presets") and len(matching_presets(release.manifest, parameters)) != 1:
                raise DomainError("unsupported_specification", "规格必须对应唯一冻结预设", 422)
            video = request["task_type"].endswith("video")
            audio = kind == "audio"
            width, height = (0, 0) if audio else (int(x) for x in parameters["size"].split("x"))
            duration = int(Decimal(str(parameters.get("seconds", 0))) * 1000)
            if (video or audio) and duration <= 0:
                raise DomainError("duration_required", "视频/音频模型必须配置有效时长", 422)
            spec = policy["spec_limits"]
            if (not audio and width * height > spec["video_max_pixels" if video else "image_max_pixels"]) or duration > (spec.get("audio_max_milliseconds", 0) if audio else spec["video_max_milliseconds"]) or len(request["input"].get("references", [])) > spec["max_references"] or parameters["quality"] not in spec["allowed_qualities"]:
                raise DomainError("spec_denied", "生成规格超过授权限制", 403)
            enforce_limit({"unfinished_jobs": {"mode": "bounded", "value": GLOBAL_UNFINISHED_JOBS}}, "unfinished_jobs", buckets["0_global", "active"].unfinished_jobs, 1)
            for kind, limits in [("1_tenant", tenant.limits), ("2_project", project.limits), ("3_key", policy["limits"])]:
                for name, addition in [("daily_jobs", 1), ("daily_image_count", 0 if video or audio else 1), ("daily_video_milliseconds", duration if video else 0), ("daily_audio_milliseconds", duration if audio else 0)]:
                    enforce_limit(limits, name, getattr(buckets[kind, period], name), addition)
                enforce_limit(limits, "unfinished_jobs", buckets[kind, "active"].unfinished_jobs, 1)
            execution = {"release_id": public_id("wfr", release.id), "release_digest": release.digest, "parameters": parameters, "input": request["input"], "asset_hashes": asset_hashes}
            if audio:
                execution["audio_spec"] = audio_specification(release.manifest, parameters)
            job = Job(tenant_id=identity.tenant_id, project_id=identity.project_id, key_id=identity.key_id, model_alias=model.alias, task_type=model.task_type, state="admission_pending" if pending_gateway else "queued", release_id=release.id, request=request, execution=execution, request_fingerprint=digest, execution_fingerprint=fingerprint(execution), eligible_channels=eligible, deadline=now + timedelta(seconds=3600 if video or audio else 900), accepted_period=period)
            job.is_validation = validation_channel is not None
            job.retry_of = retry_of
            job.accepted_at = None if pending_gateway else now
            session.add(job)
            session.flush()
            idem.resource_id = job.id
            for identifier in asset_hashes:
                session.add(JobAssetReference(tenant_id=job.tenant_id, project_id=job.project_id, key_id=job.key_id, job_id=job.id, asset_id=parse_id("asset", identifier)))
            if pending_gateway:
                assert binding
                stamp = pending_gateway.catalog_stamp or {}
                session.add(GatewayAdmission(tenant_id=job.tenant_id, project_id=job.project_id, key_id=job.key_id, gateway_id=pending_gateway.gateway_id, binding_id=pending_gateway.binding_id, binding_revision=binding.revision, job_id=job.id, provider_request_id=pending_gateway.provider_request_id, external_task_ref=pending_gateway.external_task_ref, intent_digest=pending_gateway.digest, price_version=pending_gateway.price_version, authorization_ref=pending_gateway.authorization_ref, key_revision=key.revision, expires_at=now + timedelta(seconds=120), catalog_revision=stamp.get("revision"), catalog_digest=stamp.get("digest"), catalog_instance_id=stamp.get("instance_id")))
            for (_kind, day), bucket in buckets.items():
                if day == "active":
                    bucket.unfinished_jobs += 1
                else:
                    bucket.daily_jobs += 0 if pending_gateway else 1
                    bucket.daily_image_count += 0 if video or audio else 1
                    bucket.daily_video_milliseconds += duration if video else 0
                    bucket.daily_audio_milliseconds += duration if audio else 0
            record_change(session, audit_actor or public_id("key", key.id), "job.accepted", job.id, request_id, {"retry_of": public_id("job", retry_of)} if retry_of else None)
            return job_projection(job, sensitive="content:sensitive" in policy["scopes"])

    def _asset_hashes(self, session: Session, identity: BusinessIdentity, request: dict[str, Any], *, require_ready: bool) -> dict[str, str]:
        items = request["input"]
        identifiers = [v["asset_id"] for k, v in items.items() if k in ("source_image", "mask")]
        identifiers += [v["asset_id"] for v in items.get("references", [])]
        result = {}
        for identifier in identifiers:
            query = select(Asset, ObjectRecord).join(ObjectRecord, Asset.object_id == ObjectRecord.id).where(Asset.id == parse_id("asset", identifier), Asset.tenant_id == identity.tenant_id, Asset.project_id == identity.project_id, Asset.key_id == identity.key_id)
            if require_ready:
                query = query.where(Asset.deleted_at.is_(None), ObjectRecord.deleted_at.is_(None), ObjectRecord.delete_requested_at.is_(None), ObjectRecord.verified_at.is_not(None), ObjectRecord.expires_at > utcnow()).with_for_update()
            asset = session.execute(query).one_or_none()
            if not asset:
                raise DomainError("asset_not_available", "输入素材不存在或未授权", 404)
            result[identifier] = asset[1].sha256
        return result

    def get(self, identity: BusinessIdentity, job_id: UUID) -> dict[str, Any]:
        with self.sessions() as session:
            # Read status, revision and publication from one committed snapshot.
            session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            job = owned_job(session, identity, job_id)
            result = job_projection(job, sensitive="content:sensitive" in identity.policy["scopes"])
            if "artifacts:read" in identity.policy["scopes"]:
                result["artifacts"] = [{"id": public_id("content", a.id), "position": a.position} for a in session.scalars(select(Artifact).where(Artifact.job_id == job.id, Artifact.deleted_at.is_(None)).order_by(Artifact.position))]
            return result

    def list(self, identity: BusinessIdentity, query: CollectionQuery, codec: CursorCodec, *, gateway_id: UUID | None = None) -> dict[str, Any]:
        identity.require("jobs:read")
        scope = codec.scope(identity, "jobs", {**query.model_dump(exclude={"cursor"}), "gateway": str(gateway_id) if gateway_id else None})
        position = codec.read(query.cursor, scope)
        with self.sessions() as session:
            statement = select(Job).options(defer(Job.request), defer(Job.execution)).where(Job.tenant_id == identity.tenant_id, Job.project_id == identity.project_id, Job.key_id == identity.key_id, Job.created_at <= position.through)
            if gateway_id is not None:
                statement = statement.where(Job.id.in_(select(GatewayAdmission.job_id).where(GatewayAdmission.gateway_id == gateway_id)))
            if query.model is not None:
                statement = statement.where(Job.model_alias == query.model)
            if query.status is not None:
                statement = statement.where(Job.state == query.status)
            if position.after_time is not None:
                statement = statement.where(tuple_(Job.created_at, Job.id) < tuple_(literal(position.after_time), literal(position.after_id)))
            rows = list(session.scalars(statement.order_by(Job.created_at.desc(), Job.id.desc()).limit(query.limit + 1)))
            selected_rows = rows[:query.limit]
            more = len(rows) > query.limit
            return {"object": "list", "data": [job_projection(row) for row in selected_rows], "has_more": more, "next_cursor": codec.next(position, scope, selected_rows[-1].created_at, selected_rows[-1].id) if more else None}

    def cancel(self, identity: BusinessIdentity, job_id: UUID, request_id: str, *, _session: Session | None = None, audit_actor: str | None = None) -> dict[str, Any]:
        with self.sessions.begin() if _session is None else nullcontext(_session) as session:
            admission = session.execute(select(GatewayAdmission).where(GatewayAdmission.job_id == job_id, GatewayAdmission.tenant_id == identity.tenant_id, GatewayAdmission.project_id == identity.project_id, GatewayAdmission.key_id == identity.key_id).with_for_update()).scalar_one_or_none()
            preview = owned_job(session, identity, job_id)
            period = preview.accepted_period
            session.expunge(preview)
            buckets = lock_buckets(session, identity.tenant_id, identity.project_id, identity.key_id, period)
            job = owned_job(session, identity, job_id, lock=True)
            if job.state in TERMINAL or job.state == "cancel_requested":
                return job_projection(job)
            job.cancellation_requested = True
            if job.state in ("queued", "admission_pending", "retry_wait") and job.current_attempt_id is None:
                trigger = "abort_admission" if job.state == "admission_pending" else "cancel_before_claim"
                if admission and admission.state == "pending":
                    admission.state = "aborted"
                target = transition(job.state, "cancelled", trigger, {"cancel_authorized", "not_running_proven", "no_result_publish", "terminal_commit_once", "resources_accounted"})
                terminal_accounting(session, job, buckets, target)
            else:
                job.state = transition(job.state, "cancel_requested", "request_cancel", {"cancel_authorized"})
                job.revision += 1
            record_change(session, audit_actor or public_id("key", identity.key_id), "job.cancel_requested", job.id, request_id)
            return job_projection(job)
