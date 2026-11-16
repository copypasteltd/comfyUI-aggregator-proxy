"""Live source Workflows with an explicit, backwards-compatible private mount.

No upstream I/O occurs inside database transactions. Source file writes have
durable identities; an uncertain write is observed, never blindly resubmitted.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote
from uuid import UUID, uuid4

import httpx
from generation_domain.errors import DomainError
from generation_domain.values import fingerprint, public_id
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from generation_infrastructure.models import AuditEvent, Idempotency, OriginReservation
from generation_infrastructure.origin_access import OriginAccess, OriginActor
from generation_infrastructure.transactions import record_change

PERSONAL = "workflows/@personal"
SOURCE = "workflows/@source"
MAX_BYTES = 4 * 1024 * 1024
MAX_FILES = 10000
WRITE_TYPES = {"json", "md", "csv", "txt"}


def path(value: str, *, folder: bool = False) -> str:
    value = value.rstrip("/") if folder else value
    if (not isinstance(value, str) or len(value) > 2048 or "\\" in value or ":" in value
            or any(ord(c) < 32 for c in value)
            or value and any(part in {"", ".", ".."} for part in value.split("/"))):
        raise DomainError("origin_invalid_path", "工作流路径无效", 400)
    return value


def is_personal(name: str) -> bool:
    return name == PERSONAL or name.startswith(PERSONAL + "/")


def shared_name(name: str) -> str:
    name = path(name)
    escaped = name.startswith(SOURCE + "/")
    if escaped:
        name = "workflows/" + name[len(SOURCE) + 1:]
        if name.split("/")[1] not in {"@personal", "@source"}:
            raise DomainError("origin_invalid_path", "源站别名路径无效", 400)
    if not name.startswith("workflows/") or is_personal(name) and not escaped:
        raise DomainError("origin_invalid_path", "仅允许访问源站工作流目录", 400)
    return name


def public_name(name: str) -> str:
    relative = name.removeprefix("workflows/")
    return SOURCE + "/" + relative if relative.split("/")[0] in {"@personal", "@source"} else name


@dataclass
class WorkflowReply:
    body: bytes = b""
    status: int = 200
    media: str = "application/json"
    headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def json(cls, value: Any, *, status: int = 200) -> WorkflowReply:
        return cls(json.dumps(value, ensure_ascii=False).encode(), status)


class WorkflowHttp:
    def __init__(self, target: dict[str, Any]) -> None:
        self.base = target["base_url"].rstrip("/")
        self.headers = {"Accept-Encoding": "identity"}
        if target["authorization"]:
            self.headers["Authorization"] = target["authorization"]

    def call(self, method: str, route: str, *, params: dict[str, str] | None = None, body: bytes = b"") -> WorkflowReply:
        started = time.monotonic()
        with httpx.Client(timeout=httpx.Timeout(10, connect=3), trust_env=False, follow_redirects=False) as client:
            with client.stream(method, self.base + route, params=params, content=body, headers=self.headers) as response:
                data = bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_BYTES or time.monotonic() - started > 24:
                        raise DomainError("origin_workflow_source_limit", "源站工作流响应超过读取上限", 502)
                return WorkflowReply(bytes(data), response.status_code)


class OriginWorkflows:
    def __init__(self, access: OriginAccess, *, http_factory: Any = WorkflowHttp) -> None:
        self.access, self.http_factory = access, http_factory

    @staticmethod
    def handles(native: str, query: dict[str, str]) -> bool:
        if native in {"userdata", "v2/userdata"}:
            folder = query.get("path" if native == "v2/userdata" else "dir", "").rstrip("/")
            return folder == "" or folder == "workflows" or folder.startswith("workflows/")
        return native.startswith("userdata/workflows/")

    def catalog(self, http: Any) -> dict[str, dict[str, Any]]:
        reply = http.call("GET", "/api/userdata", params={"dir": "workflows", "recurse": "true", "split": "false", "full_info": "true"})
        if reply.status == 404:
            return {}
        self._ok(reply)
        try:
            records = json.loads(reply.body)
            if not isinstance(records, list) or len(records) > MAX_FILES:
                raise ValueError
            result = {}
            for record in records:
                relative = path(record["path"])
                if not relative:
                    raise ValueError
                name = "workflows/" + relative
                values = {key: record[key] for key in ("size", "modified", "created")}
                if any(not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0 for value in values.values()):
                    raise ValueError
                name = public_name(name)
                if name in result:
                    raise ValueError
                result[name] = {"path": name, **values}
            return result
        except (ValueError, TypeError, KeyError, UnicodeDecodeError, DomainError) as error:
            raise DomainError("origin_workflow_source_format", "源站工作流目录格式无法识别", 502) from error

    @staticmethod
    def _ok(reply: WorkflowReply) -> None:
        if reply.status not in {200, 204}:
            codes = {404: ("not_found", "工作流不存在"), 409: ("origin_file_exists", "目标工作流已存在"), 403: ("origin_workflow_source_denied", "源站拒绝工作流操作")}
            code, message = codes.get(reply.status, ("origin_workflow_source_unavailable", "源站工作流暂不可访问"))
            raise DomainError(code, message, reply.status if reply.status in codes else 502)

    @staticmethod
    def _private_files(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {PERSONAL + name[len("workflows"):]: {"path": PERSONAL + name[len("workflows"):], "size": len(value.encode()),
                "modified": data.get("file_times", {}).get(name, 0), "created": data.get("file_times", {}).get(name, 0)}
                for name, value in data.get("files", {}).items() if name.startswith("workflows/")}

    def listing(self, actor: OriginActor, target: dict[str, Any], native: str, query: dict[str, str]) -> WorkflowReply:
        folder = path(query.get("path" if native == "v2/userdata" else "dir", ""), folder=True)
        data = self.access.userdata(actor, target["id"])
        files = self._private_files(data)
        if not is_personal(folder):
            files.update(self.catalog(self.http_factory(target)))
        if not folder:
            for name, value in data.get("files", {}).items():
                if not name.startswith("workflows/"):
                    files[name] = {"path": name, "size": len(value.encode()), "modified": data.get("file_times", {}).get(name, 0), "created": data.get("file_times", {}).get(name, 0)}
        prefix = folder + "/" if folder else ""
        files = {name: value for name, value in files.items() if name.startswith(prefix)}
        if len(files) > MAX_FILES:
            raise DomainError("origin_workflow_source_limit", "工作流目录条目超过读取上限", 502)
        if native == "v2/userdata":
            rows: dict[str, dict[str, Any]] = {}
            # Keep an empty private directory discoverable in the native file tree.
            if PERSONAL.startswith(prefix) and folder != PERSONAL:
                parts = PERSONAL[len(prefix):].split("/")
                for index in range(1, len(parts) + 1):
                    name = prefix + "/".join(parts[:index])
                    rows[name] = {"name": parts[index - 1], "path": name, "type": "directory"}
            for name, info in files.items():
                parts = name[len(prefix):].split("/")
                for index in range(1, len(parts)):
                    directory = prefix + "/".join(parts[:index])
                    rows[directory] = {"name": parts[index - 1], "path": directory, "type": "directory"}
                rows[name] = {**info, "name": parts[-1], "type": "file", "modified": info["modified"] / 1000}
            return WorkflowReply.json(sorted(rows.values(), key=lambda row: (row["type"] != "directory", row["name"].lower())))
        names = sorted(files)
        if query.get("recurse", "false").lower() != "true":
            names = [name for name in names if "/" not in name[len(prefix):]]
        if query.get("full_info", "false").lower() == "true":
            return WorkflowReply.json([{**files[name], "path": name[len(prefix):]} for name in names])
        relative = [name[len(prefix):] for name in names]
        return WorkflowReply.json([[name, *name.split("/")] for name in relative] if query.get("split", "false").lower() == "true" else relative)

    def personal(self, actor: OriginActor, target: dict[str, Any], name: str, method: str, query: dict[str, str], raw: bytes) -> WorkflowReply:
        result = []

        def operate(data: dict[str, Any]) -> dict[str, Any]:
            data = {**data, "files": dict(data.get("files", {})), "file_times": dict(data.get("file_times", {}))}
            files, times = data["files"], data["file_times"]
            internal = "workflows" + name[len(PERSONAL):]
            public = name
            if method in {"GET", "HEAD"}:
                if internal not in files:
                    raise DomainError("not_found", "个人工作流不存在", 404)
                result.append(self._file(files[internal].encode(), name))
            elif method == "DELETE":
                if internal not in files:
                    raise DomainError("not_found", "个人工作流不存在", 404)
                files.pop(internal)
                times.pop(internal, None)
                result.append(WorkflowReply(status=204))
            elif method == "POST":
                if "/move/" in internal:
                    internal, destination = internal.split("/move/", 1)
                    if not is_personal(destination):
                        raise DomainError("origin_workflow_cross_storage_move", "共享与个人目录之间请使用另存为", 409)
                    public = destination
                    destination = "workflows" + destination[len(PERSONAL):]
                    if internal not in files:
                        raise DomainError("not_found", "个人工作流不存在", 404)
                    if destination in files and query.get("overwrite") == "false":
                        raise DomainError("origin_file_exists", "目标工作流已存在", 409)
                    files[destination] = files.pop(internal)
                    times.pop(internal, None)
                    internal = destination
                else:
                    if internal in files and query.get("overwrite") == "false":
                        raise DomainError("origin_file_exists", "目标工作流已存在", 409)
                    files[internal] = raw.decode("utf-8")
                times[internal] = int(time.time() * 1000)
                info = {"path": public, "size": len(files[internal].encode()), "modified": times[internal], "created": times[internal]}
                result.append(WorkflowReply.json(info if query.get("full_info") == "true" else public))
            else:
                raise DomainError("origin_operation_denied", "此个人工作流操作未开放", 403)
            return data

        if method in {"GET", "HEAD"}:
            operate(self.access.userdata(actor, target["id"]))
        else:
            self.access.userdata(actor, target["id"], mutate=operate)
        return result[0]

    @staticmethod
    def _file(raw: bytes, name: str) -> WorkflowReply:
        return WorkflowReply(raw, media="application/json" if name.lower().endswith(".json") else "text/plain; charset=utf-8",
                             headers={"ETag": '"' + hashlib.sha256(raw).hexdigest() + '"', "Content-Disposition": "attachment", "X-Content-Type-Options": "nosniff"})

    @staticmethod
    def _route(name: str, destination: str | None = None) -> str:
        return "/api/userdata/" + quote(name, safe="") + ("/move/" + quote(destination, safe="") if destination else "")

    def _intent(self, actor: OriginActor, target: dict[str, Any], token: str, request: dict[str, Any]) -> tuple[UUID, dict[str, Any] | None, bool]:
        if not 16 <= len(token) <= 128:
            raise DomainError("idempotency_required", "源站工作流修改需要 Idempotency-Key", 400)
        reservation_id = self.access.active(actor, target["id"])
        operation = "origin_workflow_" + target["id"].hex
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.access.sessions.begin() as db:
            reservation = db.get(OriginReservation, reservation_id, with_for_update=True)
            if not reservation or reservation.state != "active":
                raise DomainError("origin_reservation_required", "请先取得设备操作权", 409)
            identifier = uuid4()
            inserted = db.scalar(insert(Idempotency).values(tenant_id=actor.tenant_id, project_id=actor.project_id, key_id=actor.key_id,
                operation=operation, token_digest=digest, request_fingerprint=fingerprint(request), resource_id=identifier)
                .on_conflict_do_nothing(index_elements=[Idempotency.key_id, Idempotency.operation, Idempotency.token_digest]).returning(Idempotency.id))
            row = db.scalar(select(Idempotency).where(Idempotency.key_id == actor.key_id, Idempotency.operation == operation, Idempotency.token_digest == digest).with_for_update())
            if row is None or row.request_fingerprint != fingerprint(request):
                raise DomainError("idempotency_conflict", "同一修改标识对应不同工作流操作", 409)
            assert row.resource_id is not None
            identifier = row.resource_id
            if inserted is None:
                receipt = db.scalar(select(AuditEvent).where(AuditEvent.resource == str(identifier), AuditEvent.action == "origin.workflow_write_completed").order_by(AuditEvent.created_at.desc()).limit(1))
                return identifier, receipt.detail if receipt else None, False
            record_change(db, public_id("key", actor.key_id), "origin.workflow_write_intent", identifier, str(identifier),
                          {"method": request["method"], "request_digest": row.request_fingerprint})
            return identifier, None, True

    def _finish(self, actor: OriginActor, identifier: UUID, reply: WorkflowReply) -> None:
        with self.access.sessions.begin() as db:
            row = db.scalar(select(Idempotency).where(Idempotency.resource_id == identifier).with_for_update())
            if row is None or row.key_id != actor.key_id:
                raise DomainError("origin_workflow_intent_missing", "工作流修改记录缺失", 409)
            if not db.scalar(select(AuditEvent.id).where(AuditEvent.resource == str(identifier), AuditEvent.action == "origin.workflow_write_completed").limit(1)):
                record_change(db, public_id("key", actor.key_id), "origin.workflow_write_completed", identifier, str(identifier),
                              {"status": reply.status, "body": reply.body.decode("utf-8"), "bytes": len(reply.body)})

    def _prepared(self, actor: OriginActor, identifier: UUID, digest: str | None = None) -> str | None:
        with self.access.sessions.begin() as db:
            row = db.scalar(select(Idempotency).where(Idempotency.resource_id == identifier).with_for_update())
            if row is None or row.key_id != actor.key_id:
                raise DomainError("origin_workflow_intent_missing", "工作流修改记录缺失", 409)
            old = db.scalar(select(AuditEvent).where(AuditEvent.resource == str(identifier), AuditEvent.action == "origin.workflow_write_prepared").limit(1))
            if old:
                return old.detail["sha256"]
            if digest:
                record_change(db, public_id("key", actor.key_id), "origin.workflow_write_prepared", identifier, str(identifier), {"sha256": digest})
            return digest

    def request(self, actor: OriginActor, target: dict[str, Any], native: str, method: str, query: dict[str, str], raw: bytes, token: str) -> WorkflowReply:
        if native in {"userdata", "v2/userdata"}:
            if method not in {"GET", "HEAD"}:
                raise DomainError("origin_operation_denied", "目录仅支持读取", 403)
            return self.listing(actor, target, native, query)
        name = path(native.removeprefix("userdata/"))
        if len(raw) > MAX_BYTES:
            raise DomainError("origin_userdata_limit", "工作流文件超过 4 MiB", 413)
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise DomainError("origin_invalid_userdata", "工作流必须为 UTF-8 文本", 422) from error
        if is_personal(name):
            return self.personal(actor, target, name, method, query, raw)
        before, destination = name.split("/move/", 1) if method == "POST" and "/move/" in name else (name, None)
        if destination and is_personal(destination):
            raise DomainError("origin_workflow_cross_storage_move", "共享与个人目录之间请使用另存为", 409)
        source = shared_name(before)
        target_name = shared_name(destination) if destination else None
        http = self.http_factory(target)
        if method in {"GET", "HEAD"}:
            reply = http.call("GET", self._route(source))
            self._ok(reply)
            return self._file(reply.body, name)
        if method not in {"POST", "DELETE"} or any(item.rsplit(".", 1)[-1].lower() not in WRITE_TYPES for item in (source, target_name) if item):
            raise DomainError("origin_operation_denied", "仅允许修改工作流文本文件", 403)
        frozen = {"method": method, "source": source, "destination": target_name, "query": query, "body_sha256": hashlib.sha256(raw).hexdigest(), "origin": hashlib.sha256(target["base_url"].encode()).hexdigest()}
        identifier, receipt, fresh = self._intent(actor, target, token, frozen)
        if receipt is not None:
            reply = WorkflowReply(receipt["body"].encode(), receipt["status"])
            self._ok(reply)
            return reply
        if not fresh:
            # Positive evidence is sufficient for a save/delete. Never send the POST again.
            observed = http.call("GET", self._route(source))
            if method == "DELETE" and observed.status == 404:
                reply = WorkflowReply(status=204)
            elif method == "POST" and not destination and observed.status == 200 and hashlib.sha256(observed.body).hexdigest() == frozen["body_sha256"]:
                catalog = self.catalog(http)
                info = catalog.get(name, {"path": name, "size": len(raw), "modified": 0, "created": 0})
                reply = WorkflowReply.json(info if query.get("full_info") == "true" else name)
            elif destination and observed.status == 404:
                assert target_name is not None
                expected = self._prepared(actor, identifier)
                moved = http.call("GET", self._route(target_name))
                if not expected or moved.status != 200 or hashlib.sha256(moved.body).hexdigest() != expected:
                    raise DomainError("origin_workflow_write_unknown", "原工作流移动结果尚未确认，请刷新核对", 409)
                info = self.catalog(http).get(destination, {"path": destination, "size": len(moved.body), "modified": 0, "created": 0})
                reply = WorkflowReply.json(info if query.get("full_info") == "true" else destination)
            else:
                raise DomainError("origin_workflow_write_unknown", "原工作流修改结果尚未确认，请刷新核对，不要重复提交", 409)
            self._finish(actor, identifier, reply)
            return reply
        self.access.active(actor, target["id"])
        if destination:
            original = http.call("GET", self._route(source))
            if original.status != 200:
                self._finish(actor, identifier, WorkflowReply.json({"error": "source_read_rejected"}, status=original.status))
                self._ok(original)
            self._prepared(actor, identifier, hashlib.sha256(original.body).hexdigest())
        try:
            reply = http.call(method, self._route(source, target_name), params={key: query[key] for key in ("overwrite", "full_info") if key in query}, body=raw)
        except (httpx.HTTPError, TimeoutError, OSError, DomainError) as error:
            raise DomainError("origin_workflow_write_unknown", "源站工作流修改响应丢失，请刷新核对，不要重复提交", 409) from error
        if reply.status not in {200, 204}:
            self._finish(actor, identifier, WorkflowReply.json({"error": "source_write_rejected"}, status=reply.status))
            self._ok(reply)
        if reply.body:
            try:
                value = json.loads(reply.body)
                if isinstance(value, dict) and "path" in value:
                    value["path"] = destination or before
                elif isinstance(value, str):
                    value = destination or before
                else:
                    raise ValueError
                reply = WorkflowReply.json(value, status=reply.status)
            except (ValueError, UnicodeDecodeError) as error:
                raise DomainError("origin_workflow_write_unknown", "工作流修改回执无法确认，请刷新核对", 409) from error
        self._finish(actor, identifier, reply)
        return reply
