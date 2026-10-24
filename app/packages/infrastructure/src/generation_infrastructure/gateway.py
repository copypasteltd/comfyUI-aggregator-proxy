from __future__ import annotations

import hashlib
from collections.abc import Callable
from contextlib import nullcontext
from datetime import timedelta
from decimal import Decimal
from ipaddress import ip_address
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from generation_application.ports import BusinessIdentity, GatewayIdentity
from generation_contracts.gateway import AdmissionCreate, GatewayRequestIdentity
from generation_domain.errors import DomainError
from generation_domain.policies import enforce_limit
from generation_domain.state import transition
from generation_domain.values import public_id, utcnow
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from generation_infrastructure.auth import consume_business_rates, consume_rate
from generation_infrastructure.gateway_intent import (
    PendingGateway,
    fence_matches,
    lock_request_fence,
)
from generation_infrastructure.jobs import JobRepository, terminal_accounting
from generation_infrastructure.models import (
    ApiKey,
    Artifact,
    ExternalIdentityBinding,
    GatewayAdmission,
    GatewayRequestFence,
    GatewayService,
    Job,
    OutboxEvent,
    Project,
    PublicModel,
    QuotaBucket,
    Tenant,
)
from generation_infrastructure.security import check_ip
from generation_infrastructure.transactions import lock_bucket_periods, record_change


def admission_projection(row: GatewayAdmission, job: Job) -> dict[str, Any]:
    return {"id": public_id("adm", row.id), "job_id": public_id("job", job.id), "provider_request_id": row.provider_request_id, "external_task_ref": row.external_task_ref, "status": row.state, "expires_at": int(row.expires_at.timestamp()), "job_status": job.state, "error_code": row.error_code}


class GatewayRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions
        self.jobs = JobRepository(sessions)

    def business(self, gateway: GatewayIdentity, tenant_ref: str, key_ref: str, client_ip: str, *, verified: Callable[[BusinessIdentity, str], None] | None = None) -> BusinessIdentity:
        if not 1 <= len(tenant_ref) <= 128 or not 1 <= len(key_ref) <= 128:
            raise DomainError("gateway_identity_required", "必须提供可信的外部主体引用", 403)
        try:
            peer = str(ip_address(client_ip))
        except ValueError as exc:
            raise DomainError("gateway_client_ip_required", "必须提供原客户 IP", 403) from exc
        with self.sessions.begin() as session:
            binding = session.scalar(select(ExternalIdentityBinding).where(ExternalIdentityBinding.gateway_id == gateway.gateway_id, ExternalIdentityBinding.external_tenant_ref == tenant_ref, ExternalIdentityBinding.external_key_ref == key_ref, ExternalIdentityBinding.enabled.is_(True)))
            if not binding:
                raise DomainError("gateway_identity_unmapped", "外部主体没有显式绑定", 403)
            service = session.get(GatewayService, gateway.gateway_id)
            key = session.get(ApiKey, binding.key_id)
            tenant, project = session.get(Tenant, binding.tenant_id), session.get(Project, binding.project_id)
            if key and key.issuer == "gateway:" + gateway.gateway_id.hex and verified:
                verified(BusinessIdentity(key.tenant_id, key.project_id, key.id, key.policy), peer)
            now = utcnow()
            if not service or not service.enabled or not key or key.issuer != "gateway:" + gateway.gateway_id.hex or not key.enabled or key.deleted_at or (key.expires_at and key.expires_at <= now) or not tenant or not project or not tenant.enabled or not project.enabled:
                raise DomainError("gateway_identity_disabled", "网关业务主体已停用", 403)
            check_ip(key.policy["allowed_cidrs"], peer)
            consume_business_rates(session, tenant, project, key)
            return BusinessIdentity(key.tenant_id, key.project_id, key.id, key.policy)

    def create(self, gateway: GatewayIdentity, identity: BusinessIdentity, data: AdmissionCreate, request_id: str) -> dict[str, Any]:
        identity.require("jobs:create")
        identity.require("audio:create" if data.request.task_type == "text_to_audio" else "videos:create" if data.request.task_type.endswith("video") else "images:create")
        with self.sessions() as session:
            binding = session.scalar(select(ExternalIdentityBinding).where(ExternalIdentityBinding.gateway_id == gateway.gateway_id, ExternalIdentityBinding.key_id == identity.key_id, ExternalIdentityBinding.enabled.is_(True)))
            if not binding:
                raise DomainError("gateway_identity_unmapped", "外部主体没有显式绑定", 403)
            pending = PendingGateway(gateway.gateway_id, binding.id, binding.revision, data.provider_request_id, data.external_task_ref, data.price_version, data.authorization_ref, binding.external_tenant_ref, binding.external_key_ref, data.catalog.model_dump() if data.catalog is not None else None)
        self.jobs.create(identity, data.request.model_dump(exclude_unset=True), hashlib.sha256(data.provider_request_id.encode()).hexdigest(), request_id, pending_gateway=pending)
        with self.sessions() as session:
            row, job = session.execute(select(GatewayAdmission, Job).join(Job, GatewayAdmission.job_id == Job.id).where(GatewayAdmission.gateway_id == gateway.gateway_id, GatewayAdmission.provider_request_id == data.provider_request_id, Job.key_id == identity.key_id)).one()
            return admission_projection(row, job)

    def _resolution(self, session: Session, gateway_id: UUID, identity: GatewayRequestIdentity, fence: GatewayRequestFence | None) -> dict[str, Any]:
        if fence and not fence_matches(fence, identity):
            raise DomainError("gateway_request_conflict", "网关请求身份与原记录不一致", 409)
        row = session.scalar(select(GatewayAdmission).where(GatewayAdmission.gateway_id == gateway_id, GatewayAdmission.provider_request_id == identity.provider_request_id))
        admission = None
        if row:
            # Missing/mismatched fence is an integrity failure, never a proof
            # of absence. The upgrade backfills all historical admissions.
            if not fence or fence.closed_at is not None:
                raise DomainError("gateway_request_state_conflict", "网关请求需要核对", 409)
            job = session.get(Job, row.job_id)
            assert job
            admission = admission_projection(row, job)
        closed = bool(fence and fence.closed_at is not None)
        return {**identity.model_dump(), "gateway_id": public_id("gateway", gateway_id), "status": "admitted" if admission else "closed" if closed else "unknown", "closure_id": public_id("gcl", fence.id) if closed and fence else None, "admission": admission}

    def lookup_request(self, gateway: GatewayIdentity, data: GatewayRequestIdentity) -> dict[str, Any]:
        with self.sessions.begin() as session:
            # An absence observation is intentionally not serialized with an
            # uncommitted create. Only a committed closure proves non-creation.
            fence = session.scalar(select(GatewayRequestFence).where(GatewayRequestFence.gateway_id == gateway.gateway_id, GatewayRequestFence.provider_request_id == data.provider_request_id))
            if not fence and session.scalar(select(GatewayRequestFence.id).where(GatewayRequestFence.gateway_id == gateway.gateway_id, GatewayRequestFence.external_task_ref == data.external_task_ref)):
                raise DomainError("gateway_request_conflict", "网关请求身份与原记录不一致", 409)
            return self._resolution(session, gateway.gateway_id, data, fence)

    def close_request(self, gateway: GatewayIdentity, data: GatewayRequestIdentity, request_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            consume_rate(session, "gateway_recovery:" + str(gateway.gateway_id), 120, 20)
            fence = lock_request_fence(session, gateway.gateway_id, data)
            admission_id = session.scalar(select(GatewayAdmission.id).where(GatewayAdmission.gateway_id == gateway.gateway_id, GatewayAdmission.provider_request_id == data.provider_request_id))
            if admission_id:
                if fence.closed_at is not None:
                    raise DomainError("gateway_request_state_conflict", "网关请求需要核对", 409)
                # This may abort only a pending admission. A confirmed task
                # cannot be cancelled using this service reconciliation realm.
                self._change(gateway.gateway_id, admission_id, "abort", request_id, _session=session)
            elif fence.closed_at is None:
                fence.closed_at = utcnow()
                record_change(session, public_id("gateway", gateway.gateway_id), "gateway.request_closed", fence.id, request_id)
            return self._resolution(session, gateway.gateway_id, data, fence)

    def authorize_job(self, gateway: GatewayIdentity, identity: BusinessIdentity, identifier: UUID) -> None:
        with self.sessions() as session:
            if not session.scalar(select(GatewayAdmission.id).join(Job, GatewayAdmission.job_id == Job.id).where(GatewayAdmission.gateway_id == gateway.gateway_id, GatewayAdmission.job_id == identifier, Job.key_id == identity.key_id, Job.tenant_id == identity.tenant_id, Job.project_id == identity.project_id)):
                raise DomainError("not_found", "任务不存在", 404)

    def authorize_content(self, gateway: GatewayIdentity, identifier: UUID) -> None:
        with self.sessions() as session:
            if not session.scalar(select(Artifact.id).join(GatewayAdmission, Artifact.job_id == GatewayAdmission.job_id).where(Artifact.id == identifier, GatewayAdmission.gateway_id == gateway.gateway_id)):
                raise DomainError("not_found", "内容不存在", 404)

    def confirm(self, gateway: GatewayIdentity, identity: BusinessIdentity, identifier: UUID, request_id: str) -> dict[str, Any]:
        identity.require("jobs:create")
        return self._change(gateway.gateway_id, identifier, "confirm", request_id, identity.key_id)

    def abort(self, gateway: GatewayIdentity, identity: BusinessIdentity, identifier: UUID, request_id: str) -> dict[str, Any]:
        identity.require("jobs:cancel")
        return self._change(gateway.gateway_id, identifier, "abort", request_id, identity.key_id)

    def _change(self, gateway_id: UUID, identifier: UUID, action: str, request_id: str, key_id: UUID | None = None, *, _session: Session | None = None) -> dict[str, Any]:
        now = utcnow()
        day = now.astimezone(ZoneInfo("Asia/Taipei")).date().isoformat()
        with self.sessions.begin() if _session is None else nullcontext(_session) as session:
            row = session.execute(select(GatewayAdmission).where(GatewayAdmission.id == identifier, GatewayAdmission.gateway_id == gateway_id).with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()
            if not row:
                raise DomainError("not_found", "准入记录不存在", 404)
            preview = session.get(Job, row.job_id)
            assert preview
            if key_id and preview.key_id != key_id:
                raise DomainError("not_found", "准入记录不存在", 404)
            if row.state != "pending":
                return admission_projection(row, preview)
            buckets = lock_bucket_periods(session, preview.tenant_id, preview.project_id, preview.key_id, [preview.accepted_period, day])
            session.expunge(preview)
            job = session.execute(select(Job).where(Job.id == row.job_id).with_for_update()).scalar_one()
            if job.state != "admission_pending":
                raise DomainError("admission_state_conflict", "任务与准入状态不一致，需核对")
            original_buckets = {pair: bucket for pair, bucket in buckets.items() if pair[1] in ("active", job.accepted_period)}
            if row.expires_at <= now:
                self._close(session, row, job, original_buckets, "expired", "admission_timeout", "admission_expired", request_id)
            elif action == "abort":
                self._close(session, row, job, original_buckets, "cancelled", "abort_admission", None, request_id)
            elif action == "confirm":
                try:
                    self._check_confirmation(session, row, job, buckets, day)
                except DomainError as exc:
                    self._close(session, row, job, original_buckets, "failed", "reject_admission", exc.code, request_id)
                else:
                    video = job.task_type.endswith("video")
                    audio = job.task_type == "text_to_audio"
                    milliseconds = int(Decimal(str(job.execution["parameters"].get("seconds", 0))) * 1000)
                    for (_kind, period), bucket in buckets.items():
                        if period == day:
                            bucket.daily_jobs += 1
                            if day != job.accepted_period:
                                bucket.daily_image_count += 0 if video or audio else 1
                                bucket.daily_video_milliseconds += milliseconds if video else 0
                                bucket.daily_audio_milliseconds += milliseconds if audio else 0
                        elif period == job.accepted_period and period != "active":
                            bucket.daily_image_count -= 0 if video or audio else 1
                            bucket.daily_video_milliseconds -= milliseconds if video else 0
                            bucket.daily_audio_milliseconds -= milliseconds if audio else 0
                    job.accepted_period = day
                    job.accepted_at = now
                    job.deadline = now + timedelta(seconds=3600 if video or audio else 900)
                    job.state = transition("admission_pending", "queued", "confirm_admission", {"admission_confirmed"})
                    job.revision += 1
                    row.state = "confirmed"
                    record_change(session, public_id("gateway", gateway_id), "gateway.admission_confirmed", job.id, request_id)
            return admission_projection(row, job)

    def _check_confirmation(self, session: Session, row: GatewayAdmission, job: Job, buckets: dict[tuple[str, str], QuotaBucket], day: str) -> None:
        key = session.execute(select(ApiKey).where(ApiKey.id == job.key_id).with_for_update(key_share=True)).scalar_one()
        binding = session.execute(select(ExternalIdentityBinding).where(ExternalIdentityBinding.id == row.binding_id).with_for_update(read=True)).scalar_one()
        service = session.get(GatewayService, row.gateway_id)
        tenant, project = session.get(Tenant, job.tenant_id), session.get(Project, job.project_id)
        if not service or not service.enabled or not binding.enabled or binding.revision != row.binding_revision or key.revision != row.key_revision or not key.enabled or key.deleted_at or (key.expires_at and key.expires_at <= utcnow()) or not tenant or not project or not tenant.enabled or not project.enabled:
            raise DomainError("admission_authorization_changed", "准入身份或策略版本已变化", 403)
        identity = BusinessIdentity(key.tenant_id, key.project_id, key.id, key.policy)
        identity.require("jobs:create")
        identity.require("audio:create" if job.task_type == "text_to_audio" else "videos:create" if job.task_type.endswith("video") else "images:create")
        model = session.scalar(select(PublicModel).where(PublicModel.tenant_id == job.tenant_id, PublicModel.project_id == job.project_id, PublicModel.alias == job.model_alias, PublicModel.published.is_(True)))
        if not model or model.release_id != job.release_id or not set(job.eligible_channels) <= set(model.channel_ids):
            raise DomainError("admission_model_changed", "准入模型版本或路由已变化")
        self.jobs._asset_hashes(session, identity, job.request, require_ready=True)
        video = job.task_type.endswith("video")
        audio = job.task_type == "text_to_audio"
        milliseconds = int(Decimal(str(job.execution["parameters"].get("seconds", 0))) * 1000)
        for kind, limits in [("1_tenant", tenant.limits), ("2_project", project.limits), ("3_key", key.policy["limits"])]:
            enforce_limit(limits, "daily_jobs", buckets[kind, day].daily_jobs, 1)
            if day != job.accepted_period:
                enforce_limit(limits, "daily_image_count", buckets[kind, day].daily_image_count, 0 if video or audio else 1)
                enforce_limit(limits, "daily_video_milliseconds", buckets[kind, day].daily_video_milliseconds, milliseconds if video else 0)
                enforce_limit(limits, "daily_audio_milliseconds", buckets[kind, day].daily_audio_milliseconds, milliseconds if audio else 0)

    def _close(self, session: Session, row: GatewayAdmission, job: Job, buckets: dict[tuple[str, str], QuotaBucket], target: str, trigger: str, error_code: str | None, request_id: str) -> None:
        target = transition(job.state, target, trigger, {"safe_failure", "deadline_reached", "not_running_proven", "cancel_authorized", "no_result_publish", "terminal_commit_once", "resources_accounted"})
        row.state = {"expired": "expired", "cancelled": "aborted", "failed": "rejected"}[target]
        row.error_code = error_code
        job.error_code = error_code
        terminal_accounting(session, job, buckets, target)
        record_change(session, public_id("gateway", row.gateway_id), "gateway.admission_" + row.state, job.id, request_id)

    def expire_pending(self) -> int:
        with self.sessions() as session:
            rows = list(session.execute(select(GatewayAdmission.id, GatewayAdmission.gateway_id).where(GatewayAdmission.state == "pending", GatewayAdmission.expires_at <= utcnow()).order_by(GatewayAdmission.id).limit(100)))
        for identifier, gateway in rows:
            self._change(gateway, identifier, "expire", "runner")
        return len(rows)

    def settlements(self, gateway: GatewayIdentity) -> dict[str, Any]:
        with self.sessions() as session:
            rows = list(session.scalars(select(OutboxEvent).join(GatewayAdmission, GatewayAdmission.job_id == OutboxEvent.aggregate_id).where(OutboxEvent.kind == "gateway.settlement", OutboxEvent.delivered_at.is_(None), GatewayAdmission.gateway_id == gateway.gateway_id).order_by(OutboxEvent.created_at, OutboxEvent.id).limit(100)))
            return {"data": [{"id": public_id("evt", row.id), **{k: v for k, v in row.payload.items() if k != "gateway_id"}} for row in rows], "monetary_ledger": "sub2api"}

    def acknowledge(self, gateway: GatewayIdentity, identifier: UUID, ledger_ref: str, request_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            row = session.execute(select(OutboxEvent).where(OutboxEvent.id == identifier, OutboxEvent.kind == "gateway.settlement").with_for_update()).scalar_one_or_none()
            if not row or row.payload.get("gateway_id") != str(gateway.gateway_id):
                raise DomainError("not_found", "结算事件不存在", 404)
            if row.delivered_at:
                if row.payload.get("ledger_ref") != ledger_ref:
                    raise DomainError("settlement_ack_conflict", "重复结算引用不一致")
            else:
                row.delivered_at = utcnow()
                row.payload = {**row.payload, "ledger_ref": ledger_ref}
                record_change(session, public_id("gateway", gateway.gateway_id), "gateway.settlement_acknowledged", row.aggregate_id, request_id)
            return {"accepted": True, "id": public_id("evt", identifier)}
