"""Native PostgreSQL ownership/auth and a bounded synthetic ComfyUI HTTP protocol.

Synthetic output proves the facade protocol, not real GPU/model execution.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from uuid import UUID, uuid4

import pytest
from control_api.main import create_app
from control_api.origin_routes import COOKIE, proxied_location, safe_path
from fastapi.testclient import TestClient
from generation_contracts.origin import OriginGrantPatch
from generation_domain.errors import DomainError
from generation_domain.values import public_id, utcnow
from generation_infrastructure.jobs import JobRepository
from generation_infrastructure.leases import LeaseRepository
from generation_infrastructure.models import (
    ApiCredential,
    ApiKey,
    Channel,
    Gpu,
    OriginGrant,
    OriginPrompt,
    OriginReservation,
    OriginSession,
)
from generation_infrastructure.origin_access import OriginAccess
from generation_infrastructure.origin_runtime import OriginReconciler, output_files
from generation_infrastructure.security import SecretVault, issue_secret, verifier
from generation_infrastructure.settings import Settings
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from starlette.websockets import WebSocketDisconnect
from websockets.exceptions import ConnectionClosed
from websockets.sync.server import serve

from tests.integration.test_admin_controls import admin_context

pytestmark = pytest.mark.integration


class Source(ThreadingHTTPServer):
    daemon_threads = True
    prompts: list[dict[str, Any]]
    history: dict[str, Any]
    queue: dict[str, list[Any]]
    uploads: list[dict[str, Any]]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_: Any) -> None:
        pass

    def reply(self, value: Any, *, status: int = 200, media: str = "application/json") -> None:
        data = value if isinstance(value, bytes) else json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", media)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Set-Cookie", "upstream-private=must-not-escape")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        source: Any = self.server
        if self.path == "/queue":
            self.reply(source.queue)
        elif self.path.startswith("/history/"):
            pid = self.path.split("/")[-1]
            self.reply({pid: source.history[pid]} if pid in source.history else {})
        elif self.path.startswith("/history"):
            self.reply(source.history)
        elif self.path == "/":
            self.reply(
                b'<html><head><script src="/assets/main.js"></script></head><body>synthetic native UI</body></html>',
                media="text/html",
            )
        elif self.path.startswith("/view"):
            self.reply(
                b"synthetic-file", status=206 if "Range" in self.headers else 200, media="image/png"
            )
        elif self.path == "/object_info":
            self.reply({"EmptyImage": {"input": {}}})
        elif self.path == "/api/i18n":
            self.reply({"en": {"synthetic": "Native translation"}})
        elif self.path == "/extensions":
            self.reply(["/extensions/synthetic/tool.js"])
        elif self.path.startswith("/api/jobs"):
            self.reply(
                {
                    "jobs": [{"id": pid} for pid in source.history],
                    "pagination": {
                        "total": len(source.history),
                        "has_more": False,
                        "offset": 0,
                        "limit": 100,
                    },
                }
            )
        else:
            self.reply({}, status=404)

    def do_POST(self) -> None:
        source: Any = self.server
        if self.path in {"/upload/image", "/upload/mask"}:
            data = b""
            if self.headers.get("Transfer-Encoding") == "chunked":
                while size := int(self.rfile.readline().strip(), 16):
                    data += self.rfile.read(size)
                    assert self.rfile.read(2) == b"\r\n"
                self.rfile.readline()
            else:
                data = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            message = BytesParser(policy=policy.default).parsebytes(
                ("Content-Type: " + self.headers["Content-Type"] + "\r\n\r\n").encode() + data
            )
            fields = {}
            filename = ""
            for part in message.iter_parts():
                name = part.get_param("name", header="content-disposition")
                if name == "image":
                    filename = part.get_filename()
                else:
                    fields[name] = part.get_content()
            source.uploads.append({"path": self.path, "filename": filename, "fields": fields})
            self.reply({"name": filename, "subfolder": fields["subfolder"], "type": fields["type"]})
            return
        data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        assert self.path == "/prompt"
        source.prompts.append(data)
        pid = str(uuid4())
        source.history[pid] = {
            "prompt": [0, pid, data["prompt"], data["extra_data"]],
            "status": {"completed": True, "status_str": "success"},
            "outputs": {
                "2": {
                    "images": [
                        {"filename": "synthetic.png", "subfolder": "origin", "type": "output"}
                    ]
                }
            },
        }
        self.reply({"prompt_id": pid, "number": len(source.prompts), "node_errors": {}})


@pytest.fixture
def native(
    pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings
) -> Iterator[dict[str, Any]]:
    source = Source(("127.0.0.1", 0), Handler)
    source.prompts, source.history, source.uploads = [], {}, []
    source.queue = {"queue_running": [], "queue_pending": []}
    thread = threading.Thread(target=source.serve_forever, daemon=True)
    thread.start()
    key_id = catalog["identity"].key_id
    credential_identifier, secret = issue_secret("sk-gen")
    with pg.begin() as db:
        db.add(
            ApiCredential(
                key_id=key_id,
                identifier=credential_identifier,
                verifier=verifier(secret, test_settings.credential_pepper.get_secret_value()),
                pepper_version=test_settings.credential_pepper_version,
            )
        )
        db.add(
            OriginGrant(
                key_id=key_id,
                enabled=True,
                channel_ids=[public_id("ch", catalog["channel_ids"][0])],
            )
        )
        db.get(
            Channel, catalog["channel_ids"][0]
        ).base_url = f"http://127.0.0.1:{source.server_port}"
    access = OriginAccess(pg, test_settings)
    settings = test_settings.model_copy(update={"secure_cookies": False})
    try:
        with TestClient(
            create_app(settings), base_url=settings.public_origin, client=("127.0.0.1", 50146)
        ) as client:
            login = client.post("/origin/api/session", json={"api_key": secret})
            assert login.status_code == 200, login.text
            client.headers["X-Origin-CSRF"] = login.json()["csrf_token"]
            token = client.cookies.get(COOKIE)
            actor = access.authenticate(token, "127.0.0.1")
            slug = access.devices(actor)["data"][0]["slug"]
            yield {
                "source": source,
                "access": access,
                "actor": actor,
                "client": client,
                "token": token,
                "secret": secret,
                "channel": catalog["channel_ids"][0],
                "slug": slug,
                "prefix": "/origin/" + slug + "/",
            }
    finally:
        source.shutdown()
        source.server_close()
        thread.join(timeout=3)
        assert not thread.is_alive()


def test_native_cookie_auth_grants_csrf_and_revocation(
    native: dict[str, Any], pg: sessionmaker[Session]
) -> None:
    c, access, actor = native["client"], native["access"], native["actor"]
    assert c.get("/origin/api/devices").json()["data"][0]["online"] is True
    assert (
        c.post(
            f"/origin/api/devices/{native['slug']}/acquire", headers={"X-Origin-CSRF": "wrong"}
        ).status_code
        == 403
    )
    assert c.get(native["prefix"] + "object_info").status_code == 200
    assert "token" not in c.get("/origin/api/session").json()
    with pg.begin() as db:
        session = db.get(OriginSession, actor.session_id)
        assert native["token"] not in session.verifier
        credential = db.get(ApiCredential, session.credential_id)
        credential.revoked_at = utcnow()
    assert c.get(native["prefix"] + "object_info").status_code == 401
    with pytest.raises(DomainError):
        access.login(native["secret"], "127.0.0.1")


@pytest.mark.parametrize("condition", ["key", "grant", "idle", "ip"])
def test_native_live_policy_checks(
    native: dict[str, Any], pg: sessionmaker[Session], condition: str
) -> None:
    with pg.begin() as db:
        if condition == "key":
            db.get(ApiKey, native["actor"].key_id).enabled = False
        elif condition == "grant":
            db.get(OriginGrant, native["actor"].key_id).enabled = False
        elif condition == "idle":
            db.get(OriginSession, native["actor"].session_id).last_activity_at = (
                utcnow() - timedelta(days=1)
            )
        else:
            key = db.get(ApiKey, native["actor"].key_id)
            key.policy = {
                **key.policy,
                "allowed_cidrs": {"mode": "allowlist", "values": ["203.0.113.0/24"]},
            }
    assert native["client"].get("/origin/api/devices").status_code in {401, 403}


def test_native_grant_revision_idempotency_and_existing_key_not_auto_granted(
    pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings
) -> None:
    actor, _, _ = admin_context(pg, test_settings)
    access = OriginAccess(pg, test_settings)
    key_id = catalog["identity"].key_id
    assert access.grant_view(key_id)["enabled"] is False
    data = OriginGrantPatch(
        expected_revision=1, enabled=True, channel_ids=[public_id("ch", catalog["channel_ids"][0])]
    )
    token = str(uuid4())
    first = access.patch_grant(actor, key_id, data, token, "test")
    assert first["revision"] == 2
    assert access.patch_grant(actor, key_id, data, token, "test") == first
    with pytest.raises(DomainError):
        access.patch_grant(actor, key_id, data, str(uuid4()), "stale")
    with pytest.raises(DomainError):
        access.patch_grant(
            actor,
            key_id,
            OriginGrantPatch(
                expected_revision=2, enabled=True, channel_ids=[public_id("ch", uuid4())]
            ),
            str(uuid4()),
            "foreign",
        )


def test_native_persistent_hold_excludes_api_and_sibling_channels(
    native: dict[str, Any], pg: sessionmaker[Session], catalog: dict[str, Any]
) -> None:
    access, actor = native["access"], native["actor"]
    hold = access.reserve(actor, native["channel"])
    access.activate(UUID(hold["id"]), empty_queue=True)
    with pg() as db:
        assert db.get(Gpu, catalog["gpu_id"]).management_hold == UUID(hold["id"])
    jobs = JobRepository(pg)
    jobs.create(
        catalog["identity"],
        {
            "model": "synthetic-image",
            "task_type": "text_to_image",
            "input": {"prompt": "synthetic"},
        },
        str(uuid4()),
        "test",
    )
    leases = LeaseRepository(
        pg,
        SecretVault(access.settings.encryption_key.get_secret_value()),
        allow_synthetic_catalog=True,
    )
    assert leases.claim(catalog["node_id"], catalog["boot_id"]) is None
    # New repository object simulates process replacement; ownership is in PG.
    assert OriginAccess(pg, access.settings).reserve(actor, native["channel"])["reused"]
    access.closing(actor, native["channel"])
    assert OriginReconciler(pg, access.settings).reconcile(UUID(hold["id"]))["state"] == "closed"
    assert leases.claim(catalog["node_id"], catalog["boot_id"]) is not None


def test_native_one_owner_under_concurrent_acquisition(
    native: dict[str, Any], pg: sessionmaker[Session]
) -> None:
    def acquire(_: int) -> str:
        try:
            return native["access"].reserve(native["actor"], native["channel"])["id"]
        except DomainError:
            return "busy"

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(acquire, range(6)))
    assert sum(value != "busy" for value in results) == 1
    with pg() as db:
        assert len(list(db.scalars(select(OriginReservation)))) == 1


def test_native_source_observation_does_not_invalidate_same_config_hold(
    native: dict[str, Any], pg: sessionmaker[Session]
) -> None:
    """Worker bootstrap/probe revisions do not change the registered HTTP source."""
    with pg.begin() as db:
        channel = db.get(Channel, native["channel"])
        assert channel
        channel.verified_revision = None
    c = native["client"]
    acquired = c.post(f"/origin/api/devices/{native['slug']}/acquire")
    assert acquired.status_code == 200
    with pg.begin() as db:
        channel = db.get(Channel, native["channel"])
        assert channel
        channel.verified_revision = channel.desired_revision
        channel.revision += 1
    assert native["access"].active(native["actor"], native["channel"])
    assert c.get(native["prefix"] + "object_info").status_code == 200


def test_native_unknown_submit_never_requeues_or_releases(
    native: dict[str, Any], pg: sessionmaker[Session]
) -> None:
    access, actor = native["access"], native["actor"]
    hold = access.reserve(actor, native["channel"])
    access.activate(UUID(hold["id"]), empty_queue=True)
    body = {"prompt": {"1": {"class_type": "Synthetic", "inputs": {}}}}
    identifier, association, _ = access.prompt_intent(
        actor, native["channel"], body, "synthetic-idempotency-001"
    )
    access.prompt_receipt(identifier, None)
    access.closing(actor, native["channel"])
    runner = OriginReconciler(pg, access.settings)
    assert runner.reconcile(UUID(hold["id"]))["state"] == "closing"
    assert not native["source"].prompts
    with pg() as db:
        assert db.get(Gpu, db.get(Channel, native["channel"]).gpu_id).management_hold == UUID(
            hold["id"]
        )
    # Positive history association recovers the exact original, never a new run.
    pid = str(uuid4())
    native["source"].history[pid] = {
        "prompt": [0, pid, {}, {"origin_control": {"association_id": association}}],
        "status": {"completed": True},
        "outputs": {},
    }
    assert runner.reconcile(UUID(hold["id"]))["state"] == "closed"
    with pg() as db:
        assert db.get(OriginPrompt, identifier).prompt_id == pid


def test_native_prompt_replay_filter_files_range_and_static_paths(
    native: dict[str, Any], pg: sessionmaker[Session]
) -> None:
    c, prefix = native["client"], native["prefix"]
    assert c.post(f"/origin/api/devices/{native['slug']}/acquire").status_code == 200
    html = c.get(prefix)
    assert f'<base href="{prefix}">' in html.text and "__origin_bootstrap.js" in html.text
    assert "set-cookie" not in html.headers
    assert c.get(prefix + "extensions").json() == [prefix + "extensions/synthetic/tool.js"]
    assert c.get(prefix + "__origin_bootstrap.js").status_code == 200
    assert c.get(prefix + "api/i18n").json()["en"]["synthetic"] == "Native translation"
    graph = {"prompt": {"1": {"class_type": "Synthetic", "inputs": {}}}, "extra_data": {}}
    headers = {"Idempotency-Key": "synthetic-http-prompt-01"}
    first = c.post(prefix + "prompt", json=graph, headers=headers)
    assert first.status_code == 200, first.text
    assert c.post(prefix + "prompt", json=graph, headers=headers).json() == first.json()
    assert len(native["source"].prompts) == 1
    native["source"].history["foreign"] = {"secret": "another-key-never-visible"}
    history = c.get(prefix + "history")
    assert "foreign" not in history.text and len(history.json()) == 1
    assert c.get(prefix + "history/foreign").status_code == 404
    assert c.get(prefix + "view?filename=foreign.png&type=output").status_code == 404
    file = c.get(
        prefix + "view?filename=synthetic.png&subfolder=origin&type=output",
        headers={"Range": "bytes=0-3"},
    )
    assert file.status_code == 206 and file.content == b"synthetic-file"
    assert c.get(prefix + "internal/logs").status_code == 403
    assert c.post(prefix + "free", json={}).status_code == 403
    assert c.get(prefix + "view?filename=../private.png").status_code == 404


def test_native_mask_upload_requires_owned_reference_and_preserves_mask_protocol(
    native: dict[str, Any],
) -> None:
    c, prefix = native["client"], native["prefix"]
    assert c.post(f"/origin/api/devices/{native['slug']}/acquire").status_code == 200
    upload = c.post(
        prefix + "upload/image", files={"image": ("source.png", b"synthetic-png", "image/png")}
    )
    assert upload.status_code == 200
    receipt = upload.json()
    reference = {
        "filename": receipt["name"],
        "subfolder": receipt["subfolder"],
        "type": receipt["type"],
    }
    foreign = {**reference, "filename": "foreign.png"}
    assert (
        c.post(
            prefix + "upload/mask",
            data={"original_ref": json.dumps(foreign)},
            files={"image": ("mask.png", b"synthetic-mask", "image/png")},
        ).status_code
        == 404
    )
    assert len(native["source"].uploads) == 1
    masked = c.post(
        prefix + "upload/mask",
        data={"original_ref": json.dumps(reference)},
        files={"image": ("mask.png", b"synthetic-mask", "image/png")},
    )
    assert masked.status_code == 200
    actual = native["source"].uploads[-1]
    assert actual["path"] == "/upload/mask"
    assert json.loads(actual["fields"]["original_ref"]) == reference
    assert actual["fields"]["type"] == "input"
    assert actual["filename"] != receipt["name"]
    assert (
        c.get(
            prefix + "view",
            params={
                "filename": masked.json()["name"],
                "subfolder": masked.json()["subfolder"],
                "type": "input",
            },
        ).status_code
        == 200
    )
    assert (
        c.post(
            prefix + "upload/unsupported", files={"image": ("x.png", b"x", "image/png")}
        ).status_code
        == 403
    )


def test_native_isolated_userdata_workflow_save_rename_and_listing(native: dict[str, Any]) -> None:
    c, prefix = native["client"], native["prefix"]
    saved = c.post(prefix + "api/userdata/workflows/a.json?full_info=true", content=b'{"nodes":[]}')
    assert saved.status_code == 200 and saved.json()["path"] == "workflows/a.json"
    assert c.get(prefix + "userdata?dir=workflows&recurse=true").json() == ["a.json"]
    assert c.get(prefix + "v2/userdata?path=workflows").json()[0]["type"] == "file"
    assert (
        c.post(prefix + "userdata/workflows/a.json/move/workflows/b.json").json()
        == "workflows/b.json"
    )
    assert c.get(prefix + "userdata/workflows/b.json").json() == {"nodes": []}
    assert (
        c.post(prefix + "userdata/workflows/b.json?overwrite=false", content=b"{}").status_code
        == 409
    )
    assert c.delete(prefix + "userdata/workflows/b.json").status_code == 204
    assert c.get(prefix + "userdata?dir=workflows").json() == []


def test_native_same_project_key_cannot_read_other_files_or_userdata(
    native: dict[str, Any], pg: sessionmaker[Session]
) -> None:
    actor, access = native["actor"], native["access"]
    access.remember_file(actor.key_id, native["channel"], "private.png", "", "output")
    native["client"].post(native["prefix"] + "userdata/workflows/private.json", content=b"{}")
    identifier, secret = issue_secret("sk-gen")
    with pg.begin() as db:
        current = db.get(ApiKey, actor.key_id)
        assert current
        key = ApiKey(
            tenant_id=current.tenant_id,
            project_id=current.project_id,
            name="Synthetic separate owner",
            policy=current.policy,
        )
        db.add(key)
        db.flush()
        db.add(
            ApiCredential(
                key_id=key.id,
                identifier=identifier,
                verifier=verifier(secret, access.settings.credential_pepper.get_secret_value()),
                pepper_version=access.settings.credential_pepper_version,
            )
        )
        db.add(
            OriginGrant(
                key_id=key.id, enabled=True, channel_ids=[public_id("ch", native["channel"])]
            )
        )
    token = access.login(secret, "127.0.0.1")["token"]
    headers = {"Cookie": COOKIE + "=" + token}
    assert (
        native["client"].get(native["prefix"] + "userdata?dir=workflows", headers=headers).json()
        == []
    )
    assert (
        native["client"]
        .get(native["prefix"] + "view?filename=private.png", headers=headers)
        .status_code
        == 404
    )
    assert native["client"].get(native["prefix"] + "history", headers=headers).json() == {}


def test_native_websocket_json_binary_filter_and_invalid_csrf(
    native: dict[str, Any], pg: sessionmaker[Session]
) -> None:
    """A real native socket verifies Upgrade, filtering and client-ID rewriting."""
    pid, foreign = str(uuid4()), str(uuid4())
    observed: list[str] = []

    def relay(socket: Any) -> None:
        observed.append(socket.request.path)
        socket.send(
            json.dumps(
                {
                    "type": "status",
                    "data": {
                        "sid": "private-upstream-id",
                        "status": {"exec_info": {"queue_remaining": 1}},
                    },
                }
            )
        )
        socket.send(json.dumps({"type": "executing", "data": {"prompt_id": foreign, "node": "1"}}))
        socket.send(b"foreign-preview")
        socket.send(json.dumps({"type": "executing", "data": {"prompt_id": pid, "node": "1"}}))
        socket.send(b"own-preview")
        socket.send(json.dumps({"type": "execution_success", "data": {"prompt_id": pid}}))
        try:
            for _ in socket:
                pass
        except ConnectionClosed:
            return

    server = serve(relay, "127.0.0.1", 0, open_timeout=3, close_timeout=1)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pg.begin() as db:
            db.get(
                Channel, native["channel"]
            ).base_url = f"http://127.0.0.1:{server.socket.getsockname()[1]}"
        access = native["access"]
        hold = access.reserve(native["actor"], native["channel"])
        access.activate(UUID(hold["id"]), empty_queue=True)
        identifier, _, _ = access.prompt_intent(
            native["actor"], native["channel"], {"prompt": {}}, "socket-synthetic-intent-01"
        )
        access.prompt_receipt(identifier, {"prompt_id": pid})
        c = native["client"]
        with c.websocket_connect(
            str(c.base_url).rstrip("/").replace("https:", "wss:").replace("http:", "ws:")
            + native["prefix"]
            + "ws?clientId=browser-client&origin_csrf="
            + c.headers["X-Origin-CSRF"]
        ) as socket:
            assert socket.receive_json()["data"]["sid"] == "browser-client"
            assert socket.receive_json()["data"]["prompt_id"] == pid
            assert socket.receive_bytes() == b"own-preview"
            assert socket.receive_json()["type"] == "execution_success"
        assert len(observed) == 1 and "browser-client" not in observed[0]
        with pytest.raises(WebSocketDisconnect) as denied:
            with c.websocket_connect(
                str(c.base_url).rstrip("/").replace("https:", "wss:").replace("http:", "ws:")
                + native["prefix"]
                + "ws?origin_csrf=invalid"
            ):
                pass
        assert getattr(denied.value, "code", None) == 4403
    finally:
        server.shutdown()
        thread.join(timeout=3)
        assert not thread.is_alive()


@pytest.mark.parametrize(
    "path",
    ["../private", "%252e%252e/private", "/absolute", "a\\b", "http:example", "a/../b", "a\x00"],
)
def test_origin_path_rejects_escape(path: str) -> None:
    with pytest.raises(DomainError):
        safe_path(path)


def test_origin_output_descriptor_and_redirect_boundaries() -> None:
    assert not output_files({"filename": "a.glb", "subfolder": "C:/private", "type": "output"})
    assert not output_files({"filename": "a.glb", "subfolder": "../private", "type": "output"})
    assert output_files(
        {"3d": [{"filename": "a.glb", "subfolder": "exports", "type": "output"}]}
    ) == [("a.glb", "exports", "output")]
    assert (
        proxied_location("/assets/main.js", "http://127.0.0.1:8188", "/origin/x/")
        == "/origin/x/assets/main.js"
    )
    with pytest.raises(DomainError):
        proxied_location("http://other.invalid/", "http://127.0.0.1:8188", "/origin/x/")
