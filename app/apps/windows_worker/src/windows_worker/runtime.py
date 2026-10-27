from __future__ import annotations

import asyncio
import json
import mimetypes
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from generation_contracts.command_recovery import (
    CommandReconciliationResult,
    CommandReconciliationTarget,
)
from generation_domain.errors import DomainError
from generation_domain.media_types import output_mime
from generation_domain.retries import RETRY_POLICY
from generation_domain.values import fingerprint
from generation_domain.workflows import compile_graph

from windows_worker.comfy import ComfyClient
from windows_worker.comfy_json import (
    MAX_HISTORY_BYTES,
    MAX_QUEUE_BYTES,
    MAX_RECEIPT_BYTES,
    async_request_json,
    checked_history,
    checked_queue,
    output_files,
    prompt_identifier,
    uploaded_input,
)
from windows_worker.commands import CommandExecutor
from windows_worker.configuration import LocalChannel, WorkerSettings, validate_startup
from windows_worker.environment import file_digest, safe_path, verify_environment
from windows_worker.failure_causes import (
    COLLECTION_REJECTIONS,
    execution_failure_code,
    oom_retry_evidence,
    preparation_failure_code,
    rejected_media,
    require_prompt_absent,
)
from windows_worker.journal import Journal
from windows_worker.observations import observe_queue
from windows_worker.output_copy import copy_output
from windows_worker.process_lock import NodeProcessLock
from windows_worker.workflow_exports import observe_export


class Execution:
    def __init__(self, worker: Worker, claim: dict[str, Any]) -> None:
        self.worker, self.claim = worker, claim
        self.aid = claim["attempt_id"]
        self.valid_until = time.monotonic() + max(0, claim["lease_expires_at"] - claim["server_time"])
        self.cancelled, self.released = False, False
        self.attention_required = False
        self.auth = {"protocol_version": "0.4.0", "boot_id": worker.boot, "fencing_token": claim["fencing_token"]}

    def current(self) -> None:
        if time.monotonic() >= self.valid_until:
            raise DomainError("lease_uncertain", "租约无法确认，保留 journal 和 GPU 占用以待核对")

    async def renew(self) -> None:
        while True:
            await asyncio.sleep(15)
            started = time.monotonic()
            try:
                result = await self.worker.post(f"/internal/v1/attempts/{self.aid}/renew", self.auth)
                self.valid_until = started + 60
                self.cancelled = result["cancel_requested"]
            except (httpx.HTTPError, DomainError):
                if time.monotonic() >= self.valid_until:
                    return

    async def flush(self) -> None:
        while True:
            pending = [e for e in self.worker.journal.pending() if e["attempt_id"] == self.aid]
            if not pending:
                return
            self.current()
            try:
                await self.worker.post(f"/internal/v1/attempts/{self.aid}/events", pending[0])
                self.worker.journal.acknowledge(pending[0]["event_id"])
            except httpx.TransportError:
                await asyncio.sleep(2)

    async def emit(self, kind: str, payload: dict[str, Any]) -> None:
        self.worker.journal.enqueue(self.aid, {"protocol_version": "0.4.0", "node_id": self.worker.node_id, "boot_id": self.worker.boot, "job_id": self.claim["job_id"], "attempt_id": self.aid, "fencing_token": self.claim["fencing_token"], "observed_at": int(time.time()), "type": kind, "payload": payload})
        await self.flush()

    async def inputs(self, comfy: httpx.AsyncClient) -> dict[str, str]:
        execution = self.claim["execution"]
        items = [(name, value["asset_id"]) for name, value in execution["input"].items() if name in ("source_image", "mask")]
        items.extend((f"reference_{i}", value["asset_id"]) for i, value in enumerate(execution["input"].get("references", [])))
        result = {}
        for slot, identifier in items:
            self.current()
            target = self.worker.settings.state_root / "inputs" / (uuid4().hex + ".image")
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                async with self.worker.http.stream("GET", f"/internal/v1/attempts/{self.aid}/inputs/{identifier}", params={"boot_id": self.worker.boot, "fencing_token": self.claim["fencing_token"]}) as response:
                    response.raise_for_status()
                    size = 0
                    with target.open("xb") as file:
                        async for chunk in response.aiter_bytes(1024 * 1024):
                            size += len(chunk)
                            if size > 20 * 1024 * 1024:
                                raise DomainError("input_too_large", "输入超过限制")
                            file.write(chunk)
                digest, _ = await self.worker.blocking(file_digest, target, 20 * 1024 * 1024)
                if digest != execution["asset_hashes"][identifier]:
                    raise DomainError("input_digest_mismatch", "输入素材完整性校验失败")
                with target.open("rb") as file:
                    value = await async_request_json(comfy, "POST", "/upload/image", maximum=MAX_RECEIPT_BYTES, files={"image": (f"rc-{uuid4().hex}.png", file, "application/octet-stream")}, data={"type": "input", "overwrite": "false"})
                result[slot] = uploaded_input(value)
            finally:
                target.unlink(missing_ok=True)
        return result

    async def collect(self, history: dict[str, Any], local: LocalChannel) -> tuple[list[Path], list[dict[str, Any]]]:
        adapter = self.claim["workflow"]["output"]
        files = output_files(history, adapter["final_node_ids"], adapter["history_field"])
        if len(files) != adapter["expected_count"]:
            raise DomainError("output_count_mismatch", "最终节点没有提供预期数量的产物")
        outputs, specs = [], []
        for i, item in enumerate(files):
            if item.get("type") not in (("output",) if adapter["adapter"] == "audio-files-v1" else ("output", "temp")):
                raise DomainError("invalid_output_path", "不支持的 ComfyUI 输出目录")
            directory = local.comfy_root / item["type"]
            # ComfyUI returns native Windows separators in its relative folder.
            # Filename and the normalized path still pass the same containment checks.
            folder = item.get("subfolder", "").replace("\\", "/")
            relative = "/".join(part for part in (folder, item["filename"]) if part)
            source = safe_path(directory, relative)
            before = source.stat()
            await asyncio.sleep(1)
            after = source.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise DomainError("output_not_stable", "ComfyUI 输出文件仍在变化")
            maximum = min(adapter["max_bytes"], 32 * 1024 * 1024 if adapter["adapter"] == "save-image-v1" else 2147483648)
            target = self.worker.settings.state_root / "outputs" / self.aid / f"{i}{source.suffix}"
            digest, size = await self.worker.blocking(copy_output, source, target, maximum, self.worker.settings.state_root)
            media = output_mime(target.suffix) or mimetypes.guess_type(target.name)[0]
            if media not in adapter["mime_types"]:
                raise DomainError("output_media_mismatch", "输出格式未在发布包登记")
            outputs.append(target)
            specs.append({"position": i, "bytes": size, "sha256": digest, "media_type": media})
        return outputs, specs

    async def require_stopped(self, comfy: httpx.AsyncClient, prompt: str) -> dict[str, Any]:
        queue = checked_queue(await async_request_json(comfy, "GET", "/queue", maximum=MAX_QUEUE_BYTES))
        require_prompt_absent(queue, prompt)
        return queue

    async def fail(self, code: str, proof: dict[str, Any] | None = None) -> None:
        payload = {"error_code": code, "execution_stop_confirmed": True, "evidence_digest": fingerprint(proof if proof is not None else {"attempt_id": self.aid, "error_code": code, "stopped": True})}
        if proof is not None:
            payload["retry_evidence"] = proof
        await self.emit("execution_failed", payload)
        self.worker.journal.finish(self.aid, retention_seconds=self.worker.settings.output_retention_seconds)

    async def run(self) -> None:
        renew = asyncio.create_task(self.renew())
        try:
            self.worker.journal.accept_claim(self.claim)
            if fingerprint(self.claim["execution"]) != self.claim["execution_digest"] or fingerprint(self.claim["workflow"]) != self.claim["workflow_digest"]:
                raise DomainError("claim_digest_mismatch", "冻结执行或工作流摘要无效")
            if self.claim.get("resume_state") == "cancel_requested" and self.claim.get("generation_released"):
                await self.emit("cancel_confirmed", {"execution_stop_confirmed": True, "evidence_digest": fingerprint({"generation_released": self.aid})})
                self.worker.journal.finish(self.aid)
                return
            if self.claim.get("resume_state") == "uploading":
                package = self.worker.journal.outputs(self.aid)
                if not package:
                    raise DomainError("output_package_missing", "缺少可恢复的本地产物清单")
                files = [safe_path(self.worker.settings.state_root, relative) for relative in package["files"]]
                for file, spec in zip(files, package["specs"], strict=True):
                    digest, size = await self.worker.blocking(file_digest, file, spec["bytes"])
                    if digest != spec["sha256"] or size != spec["bytes"]:
                        raise DomainError("local_output_mismatch", "恢复产物摘要不一致")
                self.released = True
                await self.upload(files, package["specs"])
                return
            local = self.worker.settings.channel(self.claim["channel"]["base_url"])
            if not self.claim.get("resume_state"):
                await self.emit("preparing", {"execution_digest": self.claim["execution_digest"]})
                await self.worker.blocking(verify_environment, local.comfy_root, self.claim["workflow"], self.worker.settings.git_executable)
            profile = next((p for p in self.claim["workflow"]["runtime_profiles"] if p["gpu_hardware_id"] == local.hardware_id), None)
            if not profile:
                raise DomainError("profile_not_available", "发布包没有此物理 GPU 的执行画像")
            headers = {"Authorization": self.claim["channel"]["authorization"]} if self.claim["channel"].get("authorization") else {}
            async with httpx.AsyncClient(base_url=local.base_url, headers=headers, timeout=httpx.Timeout(20, connect=5), trust_env=False, follow_redirects=False) as comfy:
                if self.claim.get("resume_state"):
                    prompt = prompt_identifier(self.claim["prompt_id"])
                    self.cancelled = self.claim["resume_state"] == "cancel_requested"
                else:
                    inputs = await self.inputs(comfy)
                    # This claim is already reserved in the durable journal; do not
                    # add another next-job reserve. Recovered submissions bypass it.
                    capacity = await self.worker.capacity()
                    if capacity.derived_reasons(0):
                        raise DomainError("worker_capacity_blocked", "本地容量不足，尚未提交生成")
                    graph = compile_graph(self.claim["workflow"], self.claim["execution"], inputs, f"render-control/{self.aid}/result")
                    if self.cancelled:
                        await self.emit("cancel_confirmed", {"execution_stop_confirmed": True, "evidence_digest": fingerprint({"never_submitted": self.aid})})
                        self.worker.journal.finish(self.aid)
                        return
                    association = self.worker.journal.intent(self.aid, self.claim["workflow_digest"])
                    await self.emit("submit_intent", {"association_id": association, "workflow_digest": self.claim["workflow_digest"], "journal_committed": True})
                    self.current()
                    self.worker.journal.begin_post(self.aid, association)
                    # Exactly one POST after durable posting; every ambiguous outcome is quarantined.
                    value = await async_request_json(comfy, "POST", "/prompt", maximum=MAX_RECEIPT_BYTES, json={"prompt": graph, "client_id": association, "extra_data": {"render_control": {"association_id": association, "attempt_id": self.aid}}})
                    prompt = prompt_identifier(value.get("prompt_id"))
                    self.worker.journal.submitted(self.aid, prompt)
                    await self.emit("submitted", {"prompt_id": prompt})
                timeout = time.monotonic() + profile["execution_timeout_seconds"]
                while True:
                    self.current()
                    history = checked_history(await async_request_json(comfy, "GET", "/history/" + prompt, maximum=MAX_HISTORY_BYTES)).get(prompt)
                    if history:
                        status = history.get("status", {})
                        if status.get("status_str") == "error":
                            queue = await self.require_stopped(comfy, prompt)
                            row = self.worker.journal.get(self.aid)
                            proof = oom_retry_evidence(history, queue, prompt, row["association_id"]) if self.claim.get("retry_policy") == RETRY_POLICY and row and row.get("association_id") else None
                            await self.fail(execution_failure_code(history, prompt), proof)
                            return
                        if status.get("completed") is True and status.get("status_str") == "success":
                            await self.require_stopped(comfy, prompt)
                            break
                    if time.monotonic() >= timeout:
                        raise DomainError("execution_timeout_unknown", "执行超时，需要核对实际 ComfyUI 状态")
                    await asyncio.sleep(2)
                if self.cancelled:
                    await self.emit("cancel_confirmed", {"execution_stop_confirmed": True, "evidence_digest": fingerprint(history)})
                    self.worker.journal.finish(self.aid)
                    return
                try:
                    files, specs = await self.collect(history, local)
                except DomainError as exc:
                    if exc.code not in COLLECTION_REJECTIONS:
                        raise
                    await self.require_stopped(comfy, prompt)
                    await self.fail("comfy_output_invalid")
                    return
                self.worker.journal.save_outputs(self.aid, {"files": [f.relative_to(self.worker.settings.state_root).as_posix() for f in files], "specs": specs})
                await self.emit("outputs_collected", {"manifest_digest": fingerprint(specs), "output_count": len(specs), "files_stable": True})
                await self.emit("gpu_released", {"gpu_id": self.claim["channel"]["gpu_id"], "compute_stopped": True, "gpu_encoding_stopped": True, "evidence_digest": fingerprint({"prompt_id": prompt, "manifest": specs})})
                self.released = True
                self.worker.journal.stage(self.aid, "uploading")
                await self.upload(files, specs)
        except (httpx.HTTPError, DomainError, OSError, KeyError, ValueError, TypeError, subprocess.SubprocessError) as exc:
            row = self.worker.journal.get(self.aid)
            if row and row["stage"] == "accepted":
                try:
                    await self.emit("execution_failed", {"error_code": preparation_failure_code(exc), "execution_stop_confirmed": True, "evidence_digest": fingerprint({"never_submitted": self.aid})})
                    self.worker.journal.finish(self.aid)
                except (httpx.HTTPError, DomainError):
                    pass
            else:
                self.attention_required = True
                self.worker.quarantined = True
            if (row := self.worker.journal.get(self.aid)) and not row["cloud_ack"]:
                self.attention_required = True
                self.worker.quarantined = True
            print(json.dumps({"event": "attempt_attention", "attempt_id": self.aid, "code": exc.code if isinstance(exc, DomainError) else "transport_or_runtime_failure"}), flush=True)
        finally:
            renew.cancel()
            await asyncio.gather(renew, return_exceptions=True)

    async def upload(self, files: list[Path], specs: list[dict[str, Any]]) -> None:
        plan = await self.worker.post(f"/internal/v1/attempts/{self.aid}/upload-authorizations", {**self.auth, "outputs": specs})
        for file, upload in zip(files, plan["uploads"], strict=True):
            self.current()
            if upload["uploaded"]:
                continue
            if not upload["url"].startswith("/internal/v1/uploads/") or ".." in upload["url"]:
                raise DomainError("invalid_upload_target", "上传目标不在受控接口内")
            with file.open("rb") as incoming:
                async def chunks() -> Any:
                    while chunk := incoming.read(1024 * 1024):
                        yield chunk
                response = await self.worker.http.put(upload["url"], params={"boot_id": self.worker.boot, "fencing_token": self.claim["fencing_token"]}, content=chunks(), headers={"Content-Length": str(file.stat().st_size)})
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    if not self.released or not rejected_media(exc):
                        raise
                    await self.fail("comfy_output_invalid")
                    return
        await self.worker.post(f"/internal/v1/attempts/{self.aid}/complete", {**self.auth, "upload_ids": [u["id"] for u in plan["uploads"]], "manifest_digest": plan["manifest_digest"]})
        self.worker.journal.finish(self.aid, retention_seconds=self.worker.settings.output_retention_seconds)


class Worker:
    def __init__(self, settings: WorkerSettings, *, adopt_legacy_journal: bool = False) -> None:
        self.settings = settings
        credential, verify = validate_startup(settings)
        self.node_id = credential.node_id
        self.process_lock = NodeProcessLock(settings.control_origin, self.node_id, allow_http=settings.allow_http)
        self.boot = str(uuid4())
        try:
            self.journal = Journal(settings.state_root)
            self.journal.bind_identity(settings.control_origin, self.node_id, adopt_legacy=adopt_legacy_journal)
            self.commands = CommandExecutor(settings.state_root / "config", settings)
            self.http = httpx.AsyncClient(base_url=settings.control_origin, headers={"Authorization": "Bearer " + credential.token, "X-Worker-Node-Id": self.node_id}, verify=verify, timeout=httpx.Timeout(30, connect=5), trust_env=False, follow_redirects=False)
        except BaseException:
            try:
                if hasattr(self, "journal"):
                    self.journal.close()
            finally:
                self.process_lock.close()
            raise
        self._closed = False
        self.active: dict[str, tuple[Execution, asyncio.Task[None]]] = {}
        self.quarantined = bool(self.journal.recovery())
        self.untracked_recovery: set[str] = set()
        self.observation_channels: list[dict[str, Any]] = []
        self.observations: dict[str, tuple[float, dict[str, Any]]] = {}
        self.capacity_sequence = 0
        self.capacity_lock = asyncio.Lock()
        self._blocking: set[asyncio.Task[Any]] = set()

    async def blocking[T](self, operation: Callable[..., T], *args: Any) -> T:
        if self._closed:
            raise DomainError("worker_stopped", "Worker 已停止接受本地操作")
        task = asyncio.create_task(asyncio.to_thread(operation, *args))
        self._blocking.add(task)

        def completed(finished: asyncio.Task[T]) -> None:
            self._blocking.discard(finished)
            if not finished.cancelled():
                finished.exception()

        task.add_done_callback(completed)
        # Cancelling the coroutine cannot cancel an already running OS thread.
        # Keep that operation tracked until it really stops, before releasing locks.
        return await asyncio.shield(task)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            pending = asyncio.gather(*list(self._blocking), return_exceptions=True)
            cancelled = False
            while not pending.done():
                try:
                    await asyncio.shield(pending)
                except asyncio.CancelledError:
                    cancelled = True
            if cancelled:
                raise asyncio.CancelledError
        finally:
            try:
                await self.http.aclose()
            finally:
                try:
                    self.journal.close()
                finally:
                    self.process_lock.close()

    async def capacity(self) -> Any:
        from windows_worker.capacity import observe_capacity
        async with self.capacity_lock:
            self.capacity_sequence += 1
            reserved, uploads = self.journal.capacity_usage(self.settings.generation_reserve_bytes)
            return await self.blocking(observe_capacity, self.settings, self.capacity_sequence, reserved, uploads)

    async def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        response = await self.http.post(path, json=body)
        response.raise_for_status()
        return response.json()

    async def heartbeat(self) -> None:
        while True:
            try:
                now = time.monotonic()
                values = [{**value, "observed_ago_ms": min(60000, int((now - at) * 1000))} for at, value in self.observations.values()]
                capacity = await self.capacity()
                result = await self.post("/internal/v1/heartbeat", {"protocol_version": "0.4.0", "boot_id": self.boot, "observations": values, "capacity": capacity.model_dump()})
                self.observation_channels = result.get("channels", [])
            except httpx.HTTPError:
                pass
            await asyncio.sleep(5)

    async def observe(self) -> None:
        semaphore = asyncio.Semaphore(4)
        async def one(channel: dict[str, Any]) -> None:
            async with semaphore:
                at = time.monotonic()
                export = {"protocol": "local-directory-v1", "status": "unavailable"}
                try:
                    local = self.settings.channel(channel["base_url"])
                    export = await self.blocking(observe_export, local.workflow_export_root)
                    headers = {"Authorization": channel["authorization"]} if channel.get("authorization") else {}
                    async with httpx.AsyncClient(base_url=channel["base_url"], headers=headers, timeout=httpx.Timeout(3, connect=2), follow_redirects=False, trust_env=False) as client:
                        observation = await observe_queue(client)
                except DomainError:
                    observation = {"reachable": False, "error_code": "unreachable"}
                self.observations[channel["id"]] = (at, {"channel_id": channel["id"], "revision": channel["revision"], "observation": observation, "workflow_export": export})
        while True:
            channels = list(self.observation_channels)
            active = {c["id"] for c in channels}
            self.observations = {identifier: value for identifier, value in self.observations.items() if identifier in active}
            await asyncio.gather(*(one(channel) for channel in channels))
            await asyncio.sleep(5)

    async def recover(self) -> None:
        remaining = bool(self.untracked_recovery)
        for row in self.journal.recovery():
            if row["id"] in self.active:
                continue
            previous = json.loads(row["claim"])
            try:
                context = await self.post(f"/internal/v1/attempts/{row['id']}/reconciliation-context", {"boot_id": self.boot})
                previous["channel"] = context["channel"]
            except (httpx.HTTPError, KeyError):
                remaining = True
                continue
            package = self.journal.outputs(row["id"])
            observation: dict[str, Any] = {"observation": "unknown", "source": "unknown", "prompt_id": row["prompt_id"]}
            if row["stage"] == "uploading" and package:
                observation = {"observation": "local_outputs", "source": "local_outputs", "prompt_id": row["prompt_id"], "manifest_digest": fingerprint(package["specs"])}
            elif row["association_id"]:
                try:
                    self.settings.channel(previous["channel"]["base_url"])
                    def observe(previous: dict[str, Any] = previous, row: dict[str, Any] = row) -> dict[str, Any]:
                        client = ComfyClient(previous["channel"]["base_url"], previous["channel"].get("authorization"))
                        try:
                            return client.reconcile(row["association_id"], row["prompt_id"])
                        finally:
                            client.close()
                    observation = await self.blocking(observe)
                    observation.setdefault("source", "unknown")
                    observation.setdefault("prompt_id", row["prompt_id"])
                except (DomainError, OSError):
                    pass
            try:
                result = await self.post("/internal/v1/reconciliations", {"boot_id": self.boot, "attempt_id": row["id"], "previous_boot_id": previous["boot_id"], "previous_fencing_token": previous["fencing_token"], "association_id": row["association_id"], "prompt_id": observation.get("prompt_id"), "observation": observation["observation"], "source": observation["source"], "evidence_digest": fingerprint(observation), "manifest_digest": observation.get("manifest_digest")})
                if result["status"] == "closed":
                    self.journal.finish(row["id"], retention_seconds=self.settings.output_retention_seconds)
                elif result["status"] == "resumed":
                    claim = result["lease"]
                    self.journal.rebase(claim)
                    execution = Execution(self, claim)
                    self.active[row["id"]] = (execution, asyncio.create_task(execution.run()))
                else:
                    remaining = True
            except httpx.HTTPError:
                remaining = True
        # A resumed task can fail while another journal row is being reconciled.
        # Do not clear its quarantine from the earlier scan's local result.
        for pending in self.journal.recovery():
            active = self.active.get(pending["id"])
            if active is None or active[1].done() or active[0].attention_required:
                remaining = True
        self.quarantined = remaining

    async def reconcile_command(self, identifier: str, digest: str, result: dict[str, Any] | None) -> None:
        value = await self.post(f"/internal/v1/commands/{identifier}/reconciliations", {"protocol": "command-reconciliation-v1", "boot_id": self.boot, "command_digest": digest, "observation": "completed" if result is not None else "unknown", "result": result})
        proof = CommandReconciliationResult.model_validate(value)
        if proof.command_id != identifier:
            raise ValueError("command acknowledgement identity mismatch")
        if proof.acknowledged:
            self.journal.command_ack(identifier)

    async def deliver_command(self, identifier: str, digest: str, result: dict[str, Any] | None) -> None:
        if result is None:
            await self.reconcile_command(identifier, digest, None)
            return
        try:
            value = await self.post(f"/internal/v1/commands/{identifier}/receipts", {"boot_id": self.boot, "command_digest": digest, "result": result})
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 409:
                raise
            body = exc.response.json()
            if not isinstance(body, dict) or not isinstance(body.get("error"), dict) or body["error"].get("code") not in ("command_expired", "stale_boot"):
                raise
            await self.reconcile_command(identifier, digest, result)
            return
        if value.get("accepted") is not True or value.get("status") not in ("succeeded", "failed"):
            raise ValueError("command receipt was not acknowledged")
        self.journal.command_ack(identifier)

    async def management_step(self) -> None:
        if row := self.journal.pending_command(getattr(self, "command_cursor", "")):
            self.command_cursor = row["id"]
            await self.deliver_command(row["id"], row["digest"], row["result"])
            return
        value = await self.post("/internal/v1/commands/claims", {"boot_id": self.boot})
        if hint := value.get("reconciliation"):
            target = CommandReconciliationTarget.model_validate(hint)
            await self.reconcile_command(target.command_id, target.command_digest, None)
            return
        if command := value.get("command"):
            identifier, digest = command["command_id"], command["command_digest"]
            existing = self.journal.command_record(identifier)
            if existing is not None:
                if existing["digest"] != digest:
                    raise DomainError("command_conflict", "同一管理指令摘要发生变化")
                await self.deliver_command(identifier, digest, existing["result"])
                return
            # Commit intent before execution; an interrupted intent is unknown.
            self.journal.command_intent(identifier, digest)
            try:
                result = await self.blocking(self.commands.execute, command)
            except (DomainError, httpx.HTTPError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
                result = {"success": False, "error_code": exc.code if isinstance(exc, DomainError) else "command_failed"}
            self.journal.command_result(identifier, result)
            await self.deliver_command(identifier, digest, result)

    async def management(self) -> None:
        while True:
            try:
                await self.management_step()
            except (httpx.HTTPError, DomainError, ValueError, KeyError, TypeError):
                pass
            await asyncio.sleep(2)

    async def run(self) -> None:
        background: list[asyncio.Task[None]] = []
        try:
            registration = await self.post("/internal/v1/register", {"protocol_version": "0.4.0", "boot_id": self.boot, "inventory": {"gpus": [gpu.model_dump() for gpu in self.settings.gpus], "features": ["audio-output-v1"]}})
            if registration.get("node_id") != self.node_id or registration.get("boot_id") != self.boot:
                raise DomainError("worker_registration_identity_mismatch", "Worker 注册回执身份不匹配")
            self.quarantined |= bool(registration.get("reconciliation_required"))
            self.untracked_recovery = set(registration.get("reconciliation_required", [])) - {row["id"] for row in self.journal.recovery()}
            background = [asyncio.create_task(self.heartbeat()), asyncio.create_task(self.management()), asyncio.create_task(self.observe())]
            last_recovery = 0.0
            last_cleanup = 0.0
            while True:
                for identifier, (_, task) in list(self.active.items()):
                    if task.done():
                        await task
                        del self.active[identifier]
                if self.quarantined and time.monotonic() - last_recovery >= 30:
                    await self.recover()
                    last_recovery = time.monotonic()
                if time.monotonic() - last_cleanup >= 60:
                    self.journal.cleanup_outputs()
                    last_cleanup = time.monotonic()
                capacity = await self.capacity()
                if not self.quarantined and (capacity.status == "ready" or self.journal.has_pending_claim(self.boot)) and len(self.active) < len(self.settings.gpus) + self.settings.maximum_uploads:
                    try:
                        value = await self.post("/internal/v1/claims", {"protocol_version": "0.4.0", "boot_id": self.boot, "request_id": self.journal.claim_request(self.boot)})
                        if claim := value.get("lease"):
                            if claim.get("closed"):
                                # A lost claim reply can be replayed after the
                                # attempt ended. It carries no execution payload.
                                pass
                            elif claim.get("reconciliation_required"):
                                self.quarantined = True
                            else:
                                self.journal.accept_claim(claim)
                                execution = Execution(self, claim)
                                self.active[execution.aid] = (execution, asyncio.create_task(execution.run()))
                        self.journal.clear_claim(self.boot)
                    except httpx.HTTPError:
                        pass
                await asyncio.sleep(2)
        finally:
            tasks = background + [task for _, task in self.active.values()]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self.close()
