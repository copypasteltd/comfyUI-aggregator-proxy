from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal
from typing import Any
from uuid import UUID

from generation_application.ports import AdminIdentity, GatewayIdentity
from generation_contracts.gateway_catalogs import CatalogPublication, CatalogReceipt, CatalogReplay
from generation_contracts.validation import validate
from generation_domain.errors import DomainError
from generation_domain.values import canonical_json, parse_id, public_id, utcnow
from sqlalchemy import select
from sqlalchemy.orm import Session

from generation_infrastructure.admin_base import AdminBase, mutation, require_revision, timestamp
from generation_infrastructure.auth import consume_rate
from generation_infrastructure.models import (
    Channel,
    GatewayCatalog,
    GatewayCatalogReceipt,
    GatewayCatalogRelease,
    GatewayService,
    PublicModel,
    WorkflowBinding,
    WorkflowRelease,
)
from generation_infrastructure.transactions import record_change
from generation_infrastructure.workflow_lifecycle import require_active_workflow


def release_view(row: GatewayCatalogRelease, *, detail: bool = True) -> dict[str, Any]:
    result = {"gateway_id": public_id("gateway", row.gateway_id), "revision": row.revision, "instance_id": row.instance_id, "digest": row.digest, "action": row.action, "source_revision": row.source_revision, "created_at": timestamp(row.created_at)}
    if detail:
        result["configuration_json"] = row.configuration_json
    return result


def receipt_view(row: GatewayCatalogReceipt) -> dict[str, Any]:
    return {"instance_id": row.instance_id, "revision": row.revision, "digest": row.digest, "status": row.status, "error_code": row.error_code, "observed_at": timestamp(row.observed_at)}


def model_snapshot(model: PublicModel, release: WorkflowRelease) -> dict[str, Any]:
    return {"model_id": public_id("mdl", model.id), "revision": model.revision, "tenant_id": public_id("ten", model.tenant_id), "project_id": public_id("prj", model.project_id), "alias": model.alias, "task_type": model.task_type, "release_id": public_id("wfr", release.id), "release_digest": release.digest, "channel_ids": sorted(model.channel_ids), "defaults": model.defaults}


def snapshots(session: Session, identifiers: set[UUID], *, lock: bool) -> dict[str, dict[str, Any]]:
    query = select(PublicModel).where(PublicModel.id.in_(identifiers)).order_by(PublicModel.id)
    models = list(session.scalars(query.with_for_update(read=True) if lock else query))
    if len(models) != len(identifiers) or any(not model.published for model in models):
        raise DomainError("catalog_binding_changed", "目录引用的公开模型不存在或未发布", 409)
    releases = {row.id: row for row in session.scalars(select(WorkflowRelease).where(WorkflowRelease.id.in_({model.release_id for model in models})))}
    for workflow_id in sorted({row.workflow_id for row in releases.values()}):
        require_active_workflow(session, workflow_id)
    channel_ids = {parse_id("ch", value) for model in models for value in model.channel_ids}
    query_channels = select(Channel).where(Channel.id.in_(channel_ids)).order_by(Channel.id)
    channels = {row.id: row for row in session.scalars(query_channels.with_for_update(read=True) if lock else query_channels)}
    bindings = {(row.channel_id, row.release_id): row for row in session.scalars(select(WorkflowBinding).where(WorkflowBinding.channel_id.in_(channel_ids), WorkflowBinding.release_id.in_(releases)))}
    result: dict[str, dict[str, Any]] = {}
    for model in models:
        release = releases.get(model.release_id)
        if not release or not release.validated or release.manifest.get("task_type") != model.task_type or not model.channel_ids:
            raise DomainError("catalog_binding_changed", "目录模型的 Workflow 发布版本不可用", 409)
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,99}", model.alias):
            raise DomainError("catalog_model_unsupported", "网关模型别名须为不超过 100 字符的受控标识", 422)
        available = False
        for identifier in model.channel_ids:
            channel = channels.get(parse_id("ch", identifier))
            binding = bindings.get((channel.id, model.release_id)) if channel else None
            if not channel or channel.deleted_at or (channel.tenant_id, channel.project_id) != (model.tenant_id, model.project_id) or channel.desired_revision != channel.applied_revision or channel.desired_revision != channel.verified_revision or not binding or binding.verified_digest != release.digest or not binding.runtime_approval:
                raise DomainError("catalog_binding_changed", "目录引用渠道尚未应用、验证或取得运行批准", 409)
            available = available or channel.enabled and not channel.draining
        if not available:
            raise DomainError("catalog_channel_unavailable", "目录模型至少需要一个已启用且未排空的合格渠道", 409)
        result[public_id("mdl", model.id)] = model_snapshot(model, release)
    return result


def check_configuration(session: Session, configuration: dict[str, Any], *, lock: bool, refresh: bool = False) -> dict[str, dict[str, Any]]:
    bindings = [binding for rule in configuration["models"] for binding in rule["model_bindings"]]
    current = snapshots(session, {parse_id("mdl", binding["model_id"]) for binding in bindings}, lock=lock)
    for binding in bindings:
        actual = dict(current[binding["model_id"]])
        expected = dict(binding)
        if refresh:
            actual.pop("revision")
            expected.pop("revision")
        if actual != expected:
            raise DomainError("catalog_binding_changed", "目录模型版本或冻结内容已变化，请重新发布", 409)
    return current


def lock_catalog_for_admission(session: Session, gateway_id: UUID, stamp: dict[str, Any] | None) -> dict[str, Any] | None:
    service = session.scalar(select(GatewayService).where(GatewayService.id == gateway_id).with_for_update(read=True))
    if not service or not service.enabled:
        raise DomainError("gateway_disabled", "网关访问已停用", 403)
    catalog = session.scalar(select(GatewayCatalog).where(GatewayCatalog.gateway_id == gateway_id).with_for_update(read=True))
    if not catalog or catalog.confirmed_revision is None:
        if stamp is not None:
            raise DomainError("catalog_not_confirmed", "网关目录尚未确认启用", 409)
        return None
    release = session.scalar(select(GatewayCatalogRelease).where(GatewayCatalogRelease.gateway_id == gateway_id, GatewayCatalogRelease.revision == catalog.confirmed_revision))
    assert release
    if stamp != {"instance_id": catalog.instance_id, "revision": release.revision, "digest": release.digest}:
        raise DomainError("catalog_revision_conflict", "新请求必须使用已确认的网关目录版本", 409)
    return json.loads(release.configuration_json)


def check_admission_model(session: Session, configuration: dict[str, Any], model: PublicModel, release: WorkflowRelease, price_version: str) -> None:
    rule = next((item for item in configuration["models"] if item["model"] == model.alias), None)
    current = snapshots(session, {model.id}, lock=True)[public_id("mdl", model.id)]
    binding = next((item for item in rule["model_bindings"] if item["model_id"] == current["model_id"]), None) if rule else None
    if not rule or rule["task_type"] != model.task_type or rule["price_version"] != price_version or binding != current:
        raise DomainError("catalog_binding_changed", "模型、主体范围、规格或价格与已确认目录不一致", 409)


class GatewayCatalogAdministration(AdminBase):
    def options(self, page: int, q: str) -> dict[str, Any]:
        with self.sessions() as session:
            query = select(GatewayService).order_by(GatewayService.name, GatewayService.id)
            if q:
                query = query.where(GatewayService.name.icontains(q, autoescape=True))
            rows = list(session.scalars(query.offset((page - 1) * 50).limit(51)))
            return {"data": [{"id": public_id("gateway", row.id), "name": row.name, "enabled": row.enabled} for row in rows[:50]], "page": page, "has_more": len(rows) > 50}

    def _service(self, session: Session, gateway_id: UUID, *, write: bool = False) -> GatewayService:
        query = select(GatewayService).where(GatewayService.id == gateway_id)
        row = session.scalar(query.with_for_update(key_share=True) if write else query)
        if not row:
            raise DomainError("not_found", "网关不存在", 404)
        if write and not row.enabled:
            raise DomainError("gateway_disabled", "网关访问已停用", 409)
        return row

    def _release(self, session: Session, gateway_id: UUID, number: int) -> GatewayCatalogRelease:
        row = session.scalar(select(GatewayCatalogRelease).where(GatewayCatalogRelease.gateway_id == gateway_id, GatewayCatalogRelease.revision == number))
        if not row:
            raise DomainError("not_found", "网关目录版本不存在", 404)
        return row

    def _state(self, session: Session, gateway_id: UUID) -> dict[str, Any]:
        root = session.get(GatewayCatalog, gateway_id)
        result: dict[str, Any] = {"gateway_id": public_id("gateway", gateway_id), "revision": 0, "instance_id": None, "managed": False, "status": "legacy", "desired_stale": False, "confirmed_stale": False, "desired": None, "confirmed": None, "last_receipt": None}
        if not root:
            return result
        desired = self._release(session, gateway_id, root.revision)
        confirmed = self._release(session, gateway_id, root.confirmed_revision) if root.confirmed_revision is not None else None
        receipt = session.scalar(select(GatewayCatalogReceipt).where(GatewayCatalogReceipt.gateway_id == gateway_id, GatewayCatalogReceipt.revision == root.revision))
        def stale(row: GatewayCatalogRelease | None) -> bool:
            if row is None:
                return False
            try:
                check_configuration(session, json.loads(row.configuration_json), lock=False)
            except DomainError:
                return True
            return False
        desired_stale = stale(desired)
        confirmed_stale = desired_stale if desired is confirmed else stale(confirmed)
        status = "stale" if desired_stale else "confirmed" if root.revision == root.confirmed_revision else "rejected" if receipt and receipt.status == "rejected" else "pending"
        result.update(revision=root.revision, instance_id=root.instance_id, managed=root.confirmed_revision is not None, status=status, desired_stale=desired_stale, confirmed_stale=confirmed_stale, desired=release_view(desired), confirmed=release_view(confirmed) if confirmed else None, last_receipt=receipt_view(receipt) if receipt else None)
        return result

    def state(self, gateway_id: UUID, *, instance_id: str | None = None) -> dict[str, Any]:
        with self.sessions.begin() as session:
            self._service(session, gateway_id)
            if instance_id is not None:
                consume_rate(session, "gateway_catalog_pull:" + str(gateway_id), 120, 20)
            result = self._state(session, gateway_id)
            if instance_id is not None and result["instance_id"] not in (None, instance_id):
                raise DomainError("catalog_instance_conflict", "目录属于另一 Provider 实例", 409)
            return result

    def history(self, gateway_id: UUID, page: int) -> dict[str, Any]:
        with self.sessions() as session:
            self._service(session, gateway_id)
            rows = list(session.scalars(select(GatewayCatalogRelease).where(GatewayCatalogRelease.gateway_id == gateway_id).order_by(GatewayCatalogRelease.revision.desc()).offset((page - 1) * 20).limit(21)))
            return {"data": [release_view(row, detail=False) for row in rows[:20]], "page": page, "has_more": len(rows) > 20}

    def detail(self, gateway_id: UUID, number: int) -> dict[str, Any]:
        with self.sessions() as session:
            self._service(session, gateway_id)
            return release_view(self._release(session, gateway_id, number))

    def _save(self, session: Session, actor: AdminIdentity, root: GatewayCatalog | None, gateway_id: UUID, configuration: dict[str, Any], action: str, source_revision: int | None, request_id: str) -> dict[str, Any]:
        number = 1 if root is None else root.revision + 1
        if number > 2147483647:
            raise DomainError("catalog_revision_exhausted", "目录修订号已达到上限", 409)
        configuration.update(format_version="gateway-catalog-v1", gateway_id=public_id("gateway", gateway_id), revision=number)
        try:
            validate("gateway-catalog-configuration", configuration)
        except ValueError as exc:
            raise DomainError("catalog_configuration_unsupported", "模型配置超出网关目录契约，不能发布", 422) from exc
        payload = canonical_json(configuration)
        if len(payload.encode("utf-8")) > 512 * 1024:
            raise DomainError("catalog_too_large", "网关目录配置不能超过 512 KiB", 422)
        row = GatewayCatalogRelease(gateway_id=gateway_id, revision=number, instance_id=configuration["instance_id"], digest=hashlib.sha256(payload.encode("utf-8")).hexdigest(), configuration_json=payload, action=action, source_revision=source_revision, actor_id=actor.user_id)
        session.add(row)
        if root is None:
            session.add(GatewayCatalog(gateway_id=gateway_id, revision=number, instance_id=configuration["instance_id"]))
        else:
            root.revision = number
        session.flush()
        record_change(session, public_id("usr", actor.user_id), "gateway.catalog_" + action, gateway_id, request_id, {"digest": row.digest, "model_count": len(configuration["models"]), "source_revision": source_revision}, revision_before=number - 1, revision_after=number)
        return release_view(row)

    def publish(self, actor: AdminIdentity, gateway_id: UUID, data: CatalogPublication, token: str, request_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            idem = mutation(session, actor, "gateway.catalog_publish", token, {"gateway_id": str(gateway_id), **data.model_dump()})
            if idem.result is not None:
                return idem.result
            self._service(session, gateway_id, write=True)
            root = session.get(GatewayCatalog, gateway_id)
            require_revision(root.revision if root else 0, data.expected_revision)
            if root and root.instance_id != data.instance_id:
                raise DomainError("catalog_instance_conflict", "已有目录的 Provider 实例标识不可更改", 409)
            refs = [binding for rule in data.models for binding in rule.model_bindings]
            identifiers = [parse_id("mdl", ref.model_id) for ref in refs]
            if len(refs) > 512 or len(set(identifiers)) != len(identifiers) or sum(len(rule.group_ids) for rule in data.models) > 4096:
                raise DomainError("catalog_binding_budget", "目录须使用唯一模型，最多 512 个绑定和 4096 个分组引用", 422)
            current = snapshots(session, set(identifiers), lock=True)
            models: list[dict[str, Any]] = []
            aliases: set[str] = set()
            for rule in data.models:
                bound = [current[binding.model_id] for binding in rule.model_bindings]
                for binding, snapshot in zip(rule.model_bindings, bound, strict=True):
                    require_revision(snapshot["revision"], binding.expected_revision)
                first = bound[0]
                if first["alias"] in aliases or any((item["alias"], item["task_type"], item["defaults"]) != (first["alias"], first["task_type"], first["defaults"]) for item in bound):
                    raise DomainError("catalog_rule_conflict", "目录别名须唯一，同一规则的别名、能力和默认参数须一致", 422)
                aliases.add(first["alias"])
                models.append({"model": first["alias"], "task_type": first["task_type"], "usage_account_id": rule.usage_account_id, "group_ids": sorted(rule.group_ids, key=int), "price_version": rule.price_version, "unit_price": format(Decimal(rule.unit_price), ".8f"), "defaults": first["defaults"], "model_bindings": sorted(bound, key=lambda item: item["model_id"])})
            configuration = {"instance_id": data.instance_id, "models": sorted(models, key=lambda item: item["model"])}
            idem.result = self._save(session, actor, root, gateway_id, configuration, "publish", None, request_id)
            return idem.result

    def replay(self, actor: AdminIdentity, gateway_id: UUID, data: CatalogReplay, token: str, request_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            idem = mutation(session, actor, "gateway.catalog_replay", token, {"gateway_id": str(gateway_id), **data.model_dump()})
            if idem.result is not None:
                return idem.result
            self._service(session, gateway_id, write=True)
            root = session.get(GatewayCatalog, gateway_id)
            if root is None:
                raise DomainError("not_found", "尚未发布网关目录", 404)
            require_revision(root.revision, data.expected_revision)
            if data.action == "retry" and data.source_revision != root.revision:
                raise DomainError("catalog_retry_conflict", "重试只能引用当前期望版本", 409)
            configuration = json.loads(self._release(session, gateway_id, data.source_revision).configuration_json)
            current = check_configuration(session, configuration, lock=True, refresh=True)
            for rule in configuration["models"]:
                rule["model_bindings"] = [current[binding["model_id"]] for binding in rule["model_bindings"]]
            idem.result = self._save(session, actor, root, gateway_id, configuration, data.action, data.source_revision, request_id)
            return idem.result

    def receipt(self, gateway: GatewayIdentity, data: CatalogReceipt, request_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            self._service(session, gateway.gateway_id, write=True)
            consume_rate(session, "gateway_catalog_receipt:" + str(gateway.gateway_id), 120, 20)
            root = session.get(GatewayCatalog, gateway.gateway_id)
            release = self._release(session, gateway.gateway_id, data.revision)
            if not root or data.instance_id != root.instance_id or release.instance_id != data.instance_id or release.digest != data.digest:
                raise DomainError("catalog_receipt_conflict", "目录回执的实例、修订号或摘要不匹配", 409)
            row = session.scalar(select(GatewayCatalogReceipt).where(GatewayCatalogReceipt.gateway_id == gateway.gateway_id, GatewayCatalogReceipt.revision == data.revision))
            changed = row is None or (row.status, row.error_code) != (data.status, data.error_code)
            if row and row.status == "applied" and changed:
                raise DomainError("catalog_receipt_conflict", "已应用的目录回执不可降级或改写", 409)
            if changed:
                if data.status == "applied" and data.revision == root.revision:
                    check_configuration(session, json.loads(release.configuration_json), lock=True)
                    root.confirmed_revision = data.revision
                if row is None:
                    row = GatewayCatalogReceipt(gateway_id=gateway.gateway_id, **data.model_dump(), observed_at=utcnow())
                    session.add(row)
                else:
                    row.status, row.error_code, row.observed_at = data.status, data.error_code, utcnow()
                record_change(session, public_id("gateway", gateway.gateway_id), "gateway.catalog_" + data.status, gateway.gateway_id, request_id, {"catalog_revision": data.revision, "digest": data.digest, "error_code": data.error_code})
                session.flush()
            assert row
            return {"receipt": receipt_view(row), "activated": row.status == "applied" and root.confirmed_revision == row.revision, "state": self._state(session, gateway.gateway_id)}
