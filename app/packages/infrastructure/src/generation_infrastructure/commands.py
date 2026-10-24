from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from typing import Any
from uuid import UUID

from generation_contracts.command_recovery import CommandReconciliation
from generation_contracts.model_files import ModelInspectionReceipt, ModelInventoryReceipt
from generation_contracts.workflow_catalogs import WorkflowCatalogReceipt
from generation_domain.errors import DomainError
from generation_domain.values import canonical_json, fingerprint, parse_id, public_id, utcnow
from generation_domain.workflow_catalogs import parse_catalog_cursor
from generation_domain.workflow_imports import checked_source_receipt
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from generation_infrastructure.command_recovery import (
    RECOVERY_KEY,
    recovery_proof,
    recovery_reason,
    save_recovery_proof,
)
from generation_infrastructure.model_inventory import require_model_source
from generation_infrastructure.models import (
    Channel,
    Gpu,
    GpuOccupancy,
    ManagementOperation,
    Node,
    WorkerCommand,
    WorkflowBinding,
)
from generation_infrastructure.security import SecretVault
from generation_infrastructure.transactions import record_change


@contextmanager
def command_transaction(sessions: sessionmaker[Session]) -> Iterator[Session]:
    try:
        with sessions.begin() as session:
            session.execute(text("SET LOCAL statement_timeout = '5000ms'"))
            session.execute(text("SET LOCAL lock_timeout = '1000ms'"))
            yield session
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) in ("55P03", "57014", "40P01"):
            raise DomainError("command_busy", "管理指令正在处理其他请求，请稍后核对", 409) from None
        raise DomainError("command_reconciliation_unavailable", "管理指令暂不可用，请稍后核对", 503) from None


def checked_completed_result(command: WorkerCommand, result: dict[str, Any]) -> None:
    """Validate durable evidence against the issued snapshot without applying it."""
    try:
        if RECOVERY_KEY in result or type(result.get("success")) is not bool or len(canonical_json(result).encode("utf-8")) > 8 * 1024**2:
            raise ValueError("invalid completed result")
        if result["success"] is False:
            if not isinstance(result.get("error_code"), str) or not re.fullmatch(r"[a-z0-9_]{1,64}", result["error_code"]):
                raise ValueError("invalid failure evidence")
            return
        payload = command.payload
        if type(result.get("applied_revision")) is not int or result["applied_revision"] != payload["revision"]:
            raise ValueError("revision mismatch")
        if command.command == "probe_channel" and result.get("comfy_reachable") is not True:
            raise ValueError("probe evidence missing")
        if command.command == "apply_workflow_release" and (result.get("environment_verified") is not True or result.get("release_digest") != payload["release_digest"]):
            raise ValueError("workflow evidence mismatch")
        if command.command == "scan_inventory":
            if not isinstance(result.get("inventory"), dict):
                raise ValueError("inventory evidence missing")
            if "model_catalog" in result:
                ModelInventoryReceipt.model_validate(result)
        if command.command == "pull_workflow":
            if payload.get("mode") == "catalog":
                checked_catalog_receipt(result, payload)
            else:
                checked_source_receipt(result)
                if payload.get("expected_source_digest") and payload["expected_source_digest"] != result["source_digest"]:
                    raise ValueError("source changed")
        if command.command == "inspect_model_file":
            proof = ModelInspectionReceipt.model_validate(result).inspection
            if any(proof.model_dump()[name] != payload[name] for name in ("relative_path", "bytes", "modified_at_ns", "kind", "catalog_digest")):
                raise ValueError("inspection evidence mismatch")
        if command.command == "collect_diagnostics" and any(type(result.get(name)) is not int or not 0 <= result[name] <= 1000 for name in ("queue_running_count", "queue_pending_count")):
            raise ValueError("diagnostics evidence mismatch")
    except (ValueError, KeyError, UnicodeError, RecursionError, ValidationError, DomainError):
        raise DomainError("invalid_command_reconciliation", "持久管理指令结果与签发快照不符", 422) from None


def checked_catalog_receipt(result: dict[str, Any], payload: dict[str, Any]) -> None:
    try:
        if len(canonical_json(result).encode("utf-8")) > 64 * 1024 or result.get("success") is not True or type(result.get("applied_revision")) is not int:
            raise ValueError("catalog receipt size or scalar type")
        receipt = WorkflowCatalogReceipt.model_validate(result)
        page = receipt.catalog
        if page.relative_directory != payload["relative_directory"]:
            raise ValueError("catalog directory mismatch")
        cursor = parse_catalog_cursor(payload.get("cursor"), page.relative_directory)
        if cursor and (cursor["digest"] != page.directory_digest or any(item.name <= cursor["after"] for item in page.entries)):
            raise ValueError("catalog page mismatch")
    except (ValueError, UnicodeError, RecursionError, ValidationError, DomainError) as exc:
        raise DomainError("invalid_workflow_catalog_receipt", "Workflow 目录回执无效", 422) from exc


class CommandRepository:
    def __init__(self, sessions: sessionmaker[Session], vault: SecretVault) -> None:
        self.sessions, self.vault = sessions, vault

    def claim(self, node_id: UUID, boot_id: UUID) -> dict[str, Any] | None:
        value = self.next_command(node_id, boot_id)
        if value["reconciliation"] is not None:
            raise DomainError("command_expired", "上次管理操作需核对，暂停该节点的新管理操作")
        return value["command"]

    def next_command(self, node_id: UUID, boot_id: UUID) -> dict[str, Any]:
        with command_transaction(self.sessions) as session:
            # Serialize claims before reading running rows. Receipts hold Node
            # SHARE, so their locked command cannot be skipped for a new claim.
            node = session.execute(select(Node).where(Node.id == node_id).with_for_update()).scalar_one()
            if not node.enabled or node.current_boot_id != boot_id:
                raise DomainError("stale_boot", "Worker 启动标识已失效")
            command = session.scalar(select(WorkerCommand).where(WorkerCommand.node_id == node_id, WorkerCommand.state == "running").order_by(WorkerCommand.created_at, WorkerCommand.id).with_for_update().limit(1))
            if command:
                if reason := recovery_reason(command, boot_id, utcnow()):
                    return {"command": None, "reconciliation": {"command_id": public_id("cmd", command.id), "command_digest": fingerprint({"type": command.command, "payload": command.payload}), "reason": reason}}
                return {"command": self._projection(session, command, node_id, boot_id), "reconciliation": None}
            command = session.scalar(select(WorkerCommand).where(WorkerCommand.node_id == node_id, WorkerCommand.state == "pending").order_by(WorkerCommand.created_at, WorkerCommand.id).with_for_update(skip_locked=True).limit(1))
            if not command:
                return {"command": None, "reconciliation": None}
            channel = session.get(Channel, command.channel_id)
            assert channel
            changes_runtime = command.command in ("apply_channel_config", "apply_workflow_release")
            gpu = session.execute(select(Gpu).where(Gpu.id == channel.gpu_id).with_for_update()).scalar_one()
            if changes_runtime:
                if gpu.management_hold is not None or session.get(GpuOccupancy, channel.gpu_id) is not None:
                    return {"command": None, "reconciliation": None}
            if command.payload.get("revision") != channel.desired_revision:
                command.state = "failed"
                command.receipt = {"error_code": "superseded_revision"}
                self._operation(session, command)
                return {"command": None, "reconciliation": None}
            if command.command == "pull_workflow" and command.payload.get("mode") == "catalog" and command.payload.get("export_boot_id") != str(boot_id):
                command.state = "failed"
                command.receipt = {"error_code": "superseded_boot"}
                self._operation(session, command)
                return {"command": None, "reconciliation": None}
            if command.command == "inspect_model_file":
                try:
                    if command.payload.get("catalog_boot_id") != str(boot_id):
                        raise DomainError("model_source_stale", "设备启动标识已变化", 409)
                    require_model_source(channel, node, command.payload["catalog_digest"], utcnow())
                except DomainError:
                    command.state = "failed"
                    command.receipt = {"success": False, "error_code": "model_source_stale"}
                    self._operation(session, command)
                    return {"command": None, "reconciliation": None}
            payload = {**command.payload, "base_url": channel.base_url, "channel_id": public_id("ch", channel.id), "gpu_id": public_id("gpu", channel.gpu_id)}
            command.payload = payload
            command.authorization_snapshot = channel.encrypted_auth
            command.state = "running"
            command.boot_id = boot_id
            command.lease_expires_at = utcnow() + timedelta(seconds=300)
            if changes_runtime:
                gpu.management_hold = command.id
            operation = session.scalar(select(ManagementOperation).where(ManagementOperation.id == command.operation_id).with_for_update())
            assert operation
            operation.state = "running"
            operation.revision += 1
            return {"command": self._projection(session, command, node_id, boot_id), "reconciliation": None}

    def _projection(self, session: Session, command: WorkerCommand, node_id: UUID, boot_id: UUID) -> dict[str, Any]:
        channel = session.get(Channel, command.channel_id)
        assert channel and command.lease_expires_at
        return {"command_id": public_id("cmd", command.id), "operation_id": public_id("op", command.operation_id), "protocol_version": "0.4.0", "node_id": public_id("node", node_id), "boot_id": str(boot_id), "type": command.command, "payload": command.payload, "command_digest": fingerprint({"type": command.command, "payload": command.payload}), "deadline": int(command.lease_expires_at.timestamp()), "requires_drain": command.command in ("apply_channel_config", "apply_workflow_release"), "authorization": self.vault.decrypt(command.authorization_snapshot) if command.authorization_snapshot else None}

    def receipt(self, node_id: UUID, command_id: UUID, boot_id: UUID, digest: str, result: dict[str, Any]) -> dict[str, Any]:
        if RECOVERY_KEY in result:
            raise DomainError("invalid_command_receipt", "回执不能声明服务端恢复证明", 422)
        with command_transaction(self.sessions) as session:
            node = session.execute(select(Node).where(Node.id == node_id).with_for_update(read=True)).scalar_one()
            if node.current_boot_id != boot_id:
                raise DomainError("stale_boot", "Worker 启动标识已失效")
            command = session.execute(select(WorkerCommand).where(WorkerCommand.id == command_id, WorkerCommand.node_id == node_id).with_for_update()).scalar_one_or_none()
            if not command:
                raise DomainError("not_found", "管理指令不存在", 404)
            channel_preview = session.get(Channel, command.channel_id)
            assert channel_preview
            gpu = session.execute(select(Gpu).where(Gpu.id == channel_preview.gpu_id).with_for_update()).scalar_one()
            session.expunge(channel_preview)
            channel = session.execute(select(Channel).where(Channel.id == command.channel_id).with_for_update()).scalar_one()
            if not node or node.current_boot_id != boot_id or command.boot_id != boot_id:
                raise DomainError("stale_boot", "管理指令的启动标识已失效")
            payload = command.payload
            if fingerprint({"type": command.command, "payload": payload}) != digest:
                raise DomainError("command_digest_mismatch", "管理指令摘要或目标版本已变化")
            if command.receipt:
                operation = session.get(ManagementOperation, command.operation_id)
                assert operation
                saved_proof = recovery_proof(operation, command)
                if command.receipt != result and (not saved_proof or saved_proof.get("result_digest") != fingerprint(result)):
                    raise DomainError("receipt_conflict", "重复回执内容不同")
                return {"accepted": True, "status": command.state}
            if command.state != "running" or not command.lease_expires_at or command.lease_expires_at <= utcnow():
                raise DomainError("command_expired", "管理指令租约已过期，需核对后恢复")
            if command.command in ("pull_workflow", "inspect_model_file") and result.get("success") is not True:
                if set(result) != {"success", "error_code"} or result.get("success") is not False or not isinstance(result.get("error_code"), str) or not re.fullmatch(r"[a-z0-9_]{1,64}", result["error_code"]):
                    raise DomainError("invalid_workflow_receipt" if command.command == "pull_workflow" else "invalid_model_inspection", "文件读取失败回执格式无效", 422)
            if result.get("success") is True:
                if result.get("applied_revision") != payload["revision"]:
                    raise DomainError("receipt_revision_mismatch", "回执配置版本不一致")
                if command.command == "apply_channel_config":
                    channel.applied_revision = payload["revision"]
                    channel.revision += 1
                elif command.command == "probe_channel":
                    if result.get("comfy_reachable") is not True or channel.applied_revision != payload["revision"]:
                        raise DomainError("probe_not_verified", "探测没有验证已应用的配置")
                    channel.verified_revision = payload["revision"]
                    channel.revision += 1
                elif command.command == "scan_inventory":
                    observation = {**channel.observation}
                    observation.pop("model_catalog", None)
                    if "model_catalog" in result:
                        try:
                            if len(canonical_json(result).encode("utf-8")) > 4 * 1024**2 or type(result.get("applied_revision")) is not int:
                                raise ValueError("inventory size or revision type")
                            checked = ModelInventoryReceipt.model_validate(result)
                        except (ValueError, DomainError, ValidationError, RecursionError) as exc:
                            raise DomainError("invalid_model_inventory", "模型目录回执无效", 422) from exc
                        if channel.desired_revision != payload["revision"] or channel.applied_revision != payload["revision"]:
                            raise DomainError("model_source_stale", "渠道配置已变化", 409)
                        observation["model_catalog"] = {"boot_id": str(boot_id), "revision": payload["revision"], "observed_at": int(utcnow().timestamp()), "catalog": checked.model_catalog.model_dump()}
                    channel.observation = {**observation, "inventory": result.get("inventory", {}), "inventory_observed_at": int(utcnow().timestamp()), "inventory_boot_id": str(boot_id), "inventory_revision": payload["revision"]}
                elif command.command == "inspect_model_file":
                    try:
                        if len(canonical_json(result).encode("utf-8")) > 8192 or type(result.get("applied_revision")) is not int:
                            raise ValueError("inspection size or revision type")
                        proof = ModelInspectionReceipt.model_validate(result).inspection
                        if any(proof.model_dump()[name] != payload[name] for name in ("relative_path", "bytes", "modified_at_ns", "kind", "catalog_digest")):
                            raise ValueError("inspection metadata mismatch")
                    except (ValueError, DomainError, ValidationError, RecursionError) as exc:
                        raise DomainError("invalid_model_inspection", "模型检查回执无效", 422) from exc
                    # A valid read is an observation of its issued snapshot.
                    # Finish it even if that snapshot expired or was superseded
                    # during hashing; registration independently locks and checks
                    # the current source. Do not strand the management queue.
                    if payload["catalog_boot_id"] != str(boot_id):
                        raise DomainError("stale_boot", "设备启动标识已变化", 409)
                elif command.command == "pull_workflow":
                    if payload.get("mode") == "catalog":
                        checked_catalog_receipt(result, payload)
                    else:
                        checked_source_receipt(result)
                        if payload.get("expected_source_digest") and result["source_digest"] != payload["expected_source_digest"]:
                            raise DomainError("workflow_export_changed", "读取内容与所选目录条目不一致，请刷新目录", 409)
                elif command.command == "apply_workflow_release":
                    binding = session.scalar(select(WorkflowBinding).where(WorkflowBinding.channel_id == channel.id, WorkflowBinding.release_id == parse_id("wfr", payload["release_id"])).with_for_update())
                    if not binding or result.get("release_digest") != payload["release_digest"] or not result.get("environment_verified") or channel.desired_revision != payload["revision"]:
                        raise DomainError("workflow_not_verified", "Workflow 环境验证结果无效或配置已变化")
                    binding.applied_digest = payload["release_digest"]
                    binding.verified_digest = payload["release_digest"]
                    binding.observed_at = utcnow()
                command.state = "succeeded"
            else:
                command.state = "failed"
            command.receipt = result
            if gpu.management_hold == command.id:
                gpu.management_hold = None
            channel.observed_at = utcnow()
            self._operation(session, command)
            record_change(session, public_id("node", node_id), "command.receipt", command.id, public_id("cmd", command.id))
            return {"accepted": True, "status": command.state}

    def reconcile(self, node_id: UUID, command_id: UUID, data: CommandReconciliation) -> dict[str, Any]:
        boot_id = UUID(data.boot_id)
        with command_transaction(self.sessions) as session:
            node = session.execute(select(Node).where(Node.id == node_id).with_for_update(read=True)).scalar_one_or_none()
            if node is None or not node.enabled or node.current_boot_id != boot_id:
                raise DomainError("stale_boot", "Worker 启动标识已失效", 409)
            command = session.scalar(select(WorkerCommand).where(WorkerCommand.id == command_id, WorkerCommand.node_id == node_id).with_for_update())
            if command is None:
                raise DomainError("not_found", "管理指令不存在", 404)
            if fingerprint({"type": command.command, "payload": command.payload}) != data.command_digest:
                raise DomainError("command_digest_mismatch", "管理指令摘要不匹配", 409)

            def response(resolution: str) -> dict[str, Any]:
                return {"protocol": "command-reconciliation-v1", "command_id": public_id("cmd", command.id), "acknowledged": resolution != "evidence_required", "resolution": resolution, "command_status": command.state, "server_time": int(utcnow().timestamp())}

            operation = session.get(ManagementOperation, command.operation_id)
            assert operation
            proof = recovery_proof(operation, command)
            if command.state in ("succeeded", "failed"):
                if data.result is not None and (proof.get("result_digest") if proof else fingerprint(command.receipt)) != fingerprint(data.result):
                    raise DomainError("receipt_conflict", "持久结果与云端已确认结果不同", 409)
                return response("result_requires_revalidation" if proof else "already_completed")
            if command.state != "running":
                raise DomainError("command_not_running", "指令尚未领取或状态需要人工核对", 409)
            if data.observation == "unknown":
                return response("evidence_required")
            if command.boot_id is None:
                raise DomainError("command_not_issued", "缺少指令签发事实，继续保留待核对状态", 409)
            if recovery_reason(command, boot_id, utcnow()) is None:
                raise DomainError("command_still_leased", "当前有效租约请使用普通回执", 409)
            assert data.result is not None
            checked_completed_result(command, data.result)
            channel_preview = session.get(Channel, command.channel_id)
            assert channel_preview
            gpu = session.scalar(select(Gpu).where(Gpu.id == channel_preview.gpu_id).with_for_update())
            assert gpu
            session.expunge(channel_preview)
            session.execute(select(Channel).where(Channel.id == command.channel_id).with_for_update()).scalar_one()
            command.state = "failed"
            command.receipt = {"success": False, "error_code": "command_revalidation_required"}
            if gpu.management_hold == command.id:
                gpu.management_hold = None
            operation = self._operation(session, command)
            at = int(utcnow().timestamp())
            save_recovery_proof(operation, command, {"protocol": "command-reconciliation-v1", "command_digest": data.command_digest, "result_digest": fingerprint(data.result), "previous_boot_id": str(command.boot_id), "reconciled_boot_id": data.boot_id, "reconciled_at": at})
            record_change(session, public_id("node", node_id), "command.reconciled", command.id, public_id("cmd", command.id), {"operation_id": public_id("op", command.operation_id), "result_digest": fingerprint(data.result), "resolution": "result_requires_revalidation"})
            return response("result_requires_revalidation")

    def _operation(self, session: Session, command: WorkerCommand) -> ManagementOperation:
        if command.state in ("succeeded", "failed") and command.completed_at is None:
            command.completed_at = utcnow()
        operation = session.scalar(select(ManagementOperation).where(ManagementOperation.id == command.operation_id).with_for_update().execution_options(populate_existing=True))
        assert operation
        session.flush()
        rows = list(session.scalars(select(WorkerCommand.state).where(WorkerCommand.operation_id == command.operation_id)))
        operation.state = "running" if any(s in ("pending", "running") for s in rows) else "succeeded" if all(s == "succeeded" for s in rows) else "failed" if all(s == "failed" for s in rows) else "partial_failed"
        operation.revision += 1
        return operation
