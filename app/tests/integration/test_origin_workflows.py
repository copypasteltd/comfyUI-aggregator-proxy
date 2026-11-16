"""Live source Workflows and durable mutation semantics on native PostgreSQL."""
from __future__ import annotations

import hashlib
import json
import socket
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from urllib.parse import parse_qs, quote, unquote, urlsplit
from uuid import UUID, uuid4

import pytest
from generation_infrastructure.models import AuditEvent
from generation_infrastructure.origin_workflows import MAX_BYTES, MAX_FILES
from sqlalchemy import select

from tests.integration.test_origin_access import Handler, native  # noqa: F401 -- shared native fixture

pytestmark = pytest.mark.integration


@pytest.fixture
def workflows(native, monkeypatch):  # noqa: F811 -- imported pytest fixture
    source = native["source"]
    source.files = {
        "workflows/中文目录/完整模型.json": '{"nodes":[],"title":"源站原图"}'.encode(),
        "workflows/broken-model.json": b'{"nodes":[{"type":"MissingInstalledNode"}]}',
        "workflows/中文目录/说明.md": "源站说明".encode(),
        "workflows/@personal/source-name.json": b'{"source":true}',
        "workflows/@source/source-name.json": b'{"source_alias":true}',
    }
    source.file_calls, source.write_calls = [], []
    source.directories = {"workflows/中文目录/空文件夹"}
    source.drop_response, source.fail_catalog, source.bad_catalog = False, False, None
    source.write_started, source.allow_write = Event(), Event()
    source.allow_write.set()
    old_get, old_post = Handler.do_GET, Handler.do_POST

    def get(handler):
        parsed = urlsplit(handler.path)
        if parsed.path == "/api/userdata":
            source.file_calls.append(("GET", handler.path))
            if source.fail_catalog:
                handler.reply({"private_host_path": "must-not-escape"}, status=503)
                return
            rows = [{"path": name[len("workflows/"):], "size": len(raw), "modified": 1700000000123, "created": 1700000000000} for name, raw in sorted(source.files.items())]
            handler.reply(source.bad_catalog if source.bad_catalog is not None else rows)
        elif parsed.path == "/api/v2/userdata":
            handler.reply([{"path": name, "type": "directory"} for name in sorted(source.directories)])
        elif parsed.path.startswith("/api/userdata/"):
            name = unquote(parsed.path.removeprefix("/api/userdata/"))
            source.file_calls.append(("GET", name))
            if name not in source.files:
                handler.reply({}, status=404)
            else:
                handler.reply(source.files[name])
        else:
            old_get(handler)

    def write(handler):
        parsed = urlsplit(handler.path)
        if not parsed.path.startswith("/api/userdata/"):
            old_post(handler)
            return
        route = parsed.path.removeprefix("/api/userdata/")
        name, destination = route.split("/move/", 1) if "/move/" in route else (route, None)
        name, destination = unquote(name), unquote(destination) if destination else None
        raw = handler.rfile.read(int(handler.headers.get("Content-Length", "0")))
        source.write_calls.append((handler.command, name, destination))
        source.write_started.set()
        assert source.allow_write.wait(5)
        query = parse_qs(parsed.query)
        if handler.command == "DELETE":
            if name not in source.files:
                handler.reply({}, status=404)
                return
            source.files.pop(name)
            status, value = 204, b""
        else:
            output = destination or name
            if output in source.files and query.get("overwrite") == ["false"]:
                handler.reply({}, status=409)
                return
            if destination:
                if name not in source.files:
                    handler.reply({}, status=404)
                    return
                raw = source.files.pop(name)
            source.files[output] = raw
            status = 200
            value = {"path": output, "size": len(raw), "modified": 1700000000123, "created": 1700000000000} if query.get("full_info") == ["true"] else output
        if source.drop_response:
            source.drop_response = False
            handler.close_connection = True
            handler.connection.shutdown(socket.SHUT_RDWR)
            handler.connection.close()
            return
        handler.reply(value, status=status)

    monkeypatch.setattr(Handler, "do_GET", get)
    monkeypatch.setattr(Handler, "do_POST", write)
    monkeypatch.setattr(Handler, "do_DELETE", write, raising=False)
    return native


def resource(workflows, name):
    return workflows["prefix"] + "api/userdata/" + quote(name, safe="")


def acquire(workflows):
    access, actor = workflows["access"], workflows["actor"]
    row = access.reserve(actor, workflows["channel"])
    access.activate(UUID(row["id"]), empty_queue=True)


def test_live_complete_catalog_keeps_source_names_and_mounts_legacy_private_files(workflows):
    c, prefix, access = workflows["client"], workflows["prefix"], workflows["access"]
    access.userdata(workflows["actor"], workflows["channel"], mutate=lambda data: {**data, "files": {"workflows/legacy.json": "{}"}})
    listed = c.get(prefix + "api/userdata?dir=workflows&recurse=true").json()
    assert set(listed) == {"中文目录/完整模型.json", "中文目录/说明.md", "broken-model.json", "@source/@personal/source-name.json", "@source/@source/source-name.json", "@personal/legacy.json"}
    v2 = c.get(prefix + "api/v2/userdata?path=workflows").json()
    assert len([row for row in v2 if row["type"] == "file"]) == 6
    assert any(row["path"] == "workflows/@personal" and row["type"] == "directory" for row in v2)
    assert c.get(resource(workflows, "workflows/@personal/legacy.json")).json() == {}
    # Core model availability is deliberately irrelevant to catalog visibility.
    assert c.get(resource(workflows, "workflows/broken-model.json")).json()["nodes"][0]["type"] == "MissingInstalledNode"
    assert c.get(resource(workflows, "workflows/@source/@personal/source-name.json")).json() == {"source": True}


def test_nested_split_metadata_utf8_and_head_match_source_bytes(workflows):
    c, prefix = workflows["client"], workflows["prefix"]
    query = "dir=workflows/中文目录&recurse=true"
    assert c.get(prefix + "userdata?" + query + "&split=true").json() == [["完整模型.json", "完整模型.json"], ["说明.md", "说明.md"]]
    metadata = c.get(prefix + "userdata?" + query + "&full_info=true").json()
    assert metadata[0]["modified"] == 1700000000123 and metadata[0]["path"] == "完整模型.json"
    raw = workflows["source"].files["workflows/中文目录/完整模型.json"]
    response = c.get(resource(workflows, "workflows/中文目录/完整模型.json"))
    assert response.content == raw
    assert response.headers["etag"] == '"' + hashlib.sha256(raw).hexdigest() + '"'
    head = c.head(resource(workflows, "workflows/中文目录/完整模型.json"))
    assert not head.content and int(head.headers["content-length"]) == len(raw)
    assert c.get(resource(workflows, "workflows/中文目录/说明.md")).headers["content-type"].startswith("text/plain")


def test_source_changes_are_live_and_root_does_not_expose_source_settings(workflows):
    c, prefix, source = workflows["client"], workflows["prefix"], workflows["source"]
    assert len(c.get(prefix + "userdata?dir=workflows&recurse=true").json()) == 5
    source.files["workflows/new-on-device.json"] = b"{}"
    assert "new-on-device.json" in c.get(prefix + "userdata?dir=workflows&recurse=true").json()
    root = c.get(prefix + "v2/userdata?path=").json()
    assert any(row["path"] == "workflows" and row["type"] == "directory" for row in root)
    assert c.get(prefix + "userdata/comfy.settings.json").status_code == 404


def test_empty_source_directories_remain_visible_and_refresh_live(workflows):
    c, prefix, source = workflows["client"], workflows["prefix"], workflows["source"]
    rows = c.get(prefix + "v2/userdata?path=workflows/中文目录").json()
    assert any(row["path"] == "workflows/中文目录/空文件夹" and row["type"] == "directory" for row in rows)
    source.directories.add("workflows/new-empty-folder")
    rows = c.get(prefix + "v2/userdata?path=workflows").json()
    assert any(row["path"] == "workflows/new-empty-folder" for row in rows)


def test_shared_writes_require_csrf_active_owner_and_idempotency(workflows):
    c = workflows["client"]
    name = resource(workflows, "workflows/owned.json")
    headers = {"Idempotency-Key": uuid4().hex}
    assert c.post(name, content=b"{}", headers=headers).status_code == 409
    acquire(workflows)
    assert c.post(name, content=b"{}").status_code == 400
    assert c.post(name, content=b"{}", headers={**headers, "X-Origin-CSRF": "wrong"}).status_code == 403
    assert not workflows["source"].write_calls


def test_shared_save_move_delete_replay_updates_actual_source_and_audits(workflows, pg):
    c, source = workflows["client"], workflows["source"]
    acquire(workflows)
    name, destination = "workflows/自建/测试.json", "workflows/自建/已移动.json"
    url = resource(workflows, name)
    headers = {"Idempotency-Key": uuid4().hex}
    raw = b'{"nodes":[],"synthetic_private_prompt":"not-in-audit"}'
    first = c.post(url + "?full_info=true", content=raw, headers=headers)
    assert first.status_code == 200 and first.json()["path"] == name
    assert source.files[name] == raw
    assert c.post(url + "?full_info=true", content=raw, headers=headers).json() == first.json()
    assert len(source.write_calls) == 1
    assert c.post(url + "?full_info=true", content=b"{}", headers=headers).status_code == 409
    move = c.post(url + "/move/" + quote(destination, safe=""), headers={"Idempotency-Key": uuid4().hex})
    assert move.status_code == 200 and move.json() == destination
    assert name not in source.files and source.files[destination] == raw
    delete_headers = {"Idempotency-Key": uuid4().hex}
    assert c.delete(resource(workflows, destination), headers=delete_headers).status_code == 204
    assert c.delete(resource(workflows, destination), headers=delete_headers).status_code == 204
    assert destination not in source.files and len(source.write_calls) == 3
    with pg() as db:
        audits = list(db.scalars(select(AuditEvent).where(AuditEvent.action.like("origin.workflow_write_%"))))
        assert sum(row.action == "origin.workflow_write_intent" for row in audits) == 3
        assert sum(row.action == "origin.workflow_write_completed" for row in audits) == 3
        assert "not-in-audit" not in json.dumps([row.detail for row in audits])


@pytest.mark.parametrize("operation", ["save", "move", "delete"])
def test_lost_source_write_response_is_observed_without_repeating_mutation(workflows, operation):
    c, source = workflows["client"], workflows["source"]
    acquire(workflows)
    name, destination = "workflows/reconcile.json", "workflows/reconciled.json"
    source.files[name] = b'{"synthetic":"move-delete"}'
    source.drop_response = True
    headers = {"Idempotency-Key": uuid4().hex}
    url = resource(workflows, name)
    def send():
        if operation == "save":
            return c.post(url, content=b'{"nodes":[]}', headers=headers)
        if operation == "move":
            return c.post(url + "/move/" + quote(destination, safe=""), headers=headers)
        return c.delete(url, headers=headers)
    assert send().status_code == 409
    assert send().status_code == (204 if operation == "delete" else 200)
    assert len(source.write_calls) == 1


def test_known_overwrite_rejection_is_replayed_as_rejection(workflows):
    c, source = workflows["client"], workflows["source"]
    acquire(workflows)
    name = "workflows/same.json"
    source.files[name] = b"{}"
    headers = {"Idempotency-Key": uuid4().hex}
    for _ in range(2):
        assert c.post(resource(workflows, name) + "?overwrite=false", content=b"{}", headers=headers).status_code == 409
    assert len(source.write_calls) == 1


def test_concurrent_duplicate_does_not_send_a_second_source_write(workflows):
    c, source = workflows["client"], workflows["source"]
    acquire(workflows)
    source.allow_write.clear()
    headers = {"Idempotency-Key": uuid4().hex}
    url = resource(workflows, "workflows/concurrent.json")
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(c.post, url, content=b'{"nodes":[]}', headers=headers)
        try:
            assert source.write_started.wait(3)
            assert c.post(url, content=b'{"nodes":[]}', headers=headers).status_code == 409
        finally:
            source.allow_write.set()
        assert first.result(timeout=5).status_code == 200
    assert len(source.write_calls) == 1


@pytest.mark.parametrize("bad", ["workflows/../secret.json", "workflows/../../comfy.settings.json", "workflows/C:/secret.json", "workflows/evil\\secret.json"])
def test_directory_traversal_never_reaches_source(workflows, bad):
    c, prefix, source = workflows["client"], workflows["prefix"], workflows["source"]
    assert c.get(prefix + "v2/userdata", params={"path": bad}).status_code == 400
    assert not source.file_calls


def test_unreachable_or_malformed_source_is_not_an_empty_catalog(workflows):
    c, prefix, source = workflows["client"], workflows["prefix"], workflows["source"]
    source.fail_catalog = True
    response = c.get(prefix + "userdata?dir=workflows&recurse=true")
    assert response.status_code == 502 and "must-not-escape" not in response.text
    source.fail_catalog = False
    source.bad_catalog = [{"path": "../secrets", "size": 1, "modified": 1, "created": 1}]
    assert c.get(prefix + "userdata?dir=workflows").status_code == 502
    source.bad_catalog = [{}] * (MAX_FILES + 1)
    assert c.get(prefix + "userdata?dir=workflows").status_code == 502


def test_private_storage_keeps_working_during_source_outage(workflows):
    c, prefix, source = workflows["client"], workflows["prefix"], workflows["source"]
    source.fail_catalog = True
    name = "workflows/@personal/solo.json"
    assert c.post(resource(workflows, name), content=b'{"nodes":[]}').status_code == 200
    assert c.get(resource(workflows, name)).json() == {"nodes": []}
    assert c.get(prefix + "userdata?dir=workflows/@personal&recurse=true").json() == ["solo.json"]
    assert not source.write_calls


def test_cross_mount_move_and_oversize_or_executable_writes_are_rejected(workflows):
    c, source = workflows["client"], workflows["source"]
    acquire(workflows)
    headers = {"Idempotency-Key": uuid4().hex}
    assert c.post(resource(workflows, "workflows/code.py"), content=b"print(1)", headers=headers).status_code == 403
    assert c.post(resource(workflows, "workflows/large.json"), content=b"x" * (MAX_BYTES + 1), headers=headers).status_code == 413
    assert c.post(resource(workflows, "workflows/broken-model.json") + "/move/" + quote("workflows/@personal/private.json", safe=""), headers=headers).status_code == 409
    assert not source.write_calls
