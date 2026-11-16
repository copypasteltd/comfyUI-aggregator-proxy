from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import threading
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

PATH = Path(__file__).resolve().parents[2] / "skills/media-gen-skill/scripts/comfy_origin.py"
spec = importlib.util.spec_from_file_location("comfy_origin", PATH)
assert spec and spec.loader
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)
SLUG = "rtx-5070"
PROMPT = "1298f867-aa93-493e-b175-9c8054302afb"
KEY = "synthetic-native-api-key"
TOKEN = "synthetic-session-credential"
CSRF = "synthetic-native-csrf"
PAYLOAD = b"synthetic owned native image"
GRAPH = {"1": {"class_type": "EmptyImage", "inputs": {"width": 64, "height": 64, "batch_size": 1, "color": 13825340}}}


@pytest.fixture
def server():
    state: dict[str, Any] = {"requests": [], "submissions": [], "lost": False, "error": None, "workflow": None, "completed": True, "corrupt": False, "redirect": False}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def result(self, value, status=200, headers=None):
            raw = json.dumps(value, ensure_ascii=False).encode() if value is not None else b""
            self.send_response(status)
            self.send_header("Content-Length", str(len(raw)))
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            state["requests"].append(("POST", self.path, dict(self.headers)))
            assert not self.headers.get("Authorization")
            if self.path == "/origin/api/session":
                assert json.loads(raw) == {"api_key": KEY}
                assert not self.headers.get("Cookie")
                self.result({"csrf_token": CSRF, "expires_at": 2000000000}, headers={"Set-Cookie": "comfy_origin_session=" + TOKEN + "; HttpOnly; Path=/origin"})
                return
            assert self.headers.get("Cookie") == "comfy_origin_session=" + TOKEN
            assert self.headers.get("X-Origin-CSRF") == CSRF
            if state["error"]:
                self.result({"error": {"code": state["error"], "message": "private submitted content must not be echoed"}}, status=409)
            elif self.path.endswith("/prompt"):
                state["submissions"].append((raw, self.headers.get("Idempotency-Key")))
                if state["lost"]:
                    state["lost"] = False
                    self.connection.shutdown(2)
                    self.connection.close()
                    return
                self.result({"prompt_id": PROMPT, "number": 1, "node_errors": {}})
            elif "/upload/" in self.path:
                message = BytesParser(policy=default).parsebytes(("Content-Type: " + self.headers["Content-Type"] + "\r\nMIME-Version: 1.0\r\n\r\n").encode() + raw)
                state["upload"] = {part.get_param("name", header="content-disposition"): part.get_payload(decode=True) for part in message.iter_parts()}
                state["upload_transfer_encoding"] = self.headers.get("Transfer-Encoding")
                self.result({"name": "random.png", "subfolder": "origin/key-namespace", "type": "input"})
            elif "/userdata/" in self.path:
                if raw:
                    state["workflow"] = json.loads(raw)
                self.result("workflows/draft.json")
            elif self.path.endswith("/acquire"):
                self.result({"state": "active", "url": "/origin/" + SLUG + "/"})
            elif self.path.endswith("/release"):
                self.result({"state": "closed" if state["completed"] else "closing"})
            else:
                self.result({})

        def do_GET(self):
            state["requests"].append(("GET", self.path, dict(self.headers)))
            assert self.headers.get("Cookie") == "comfy_origin_session=" + TOKEN
            assert not self.headers.get("Authorization")
            if state["redirect"]:
                self.result({}, status=302, headers={"Location": "http://127.0.0.1:1/steal"})
            elif "/view?" in self.path:
                self.send_response(200)
                self.send_header("Content-Length", str(len(PAYLOAD)))
                self.end_headers()
                self.wfile.write(PAYLOAD if not state["corrupt"] else b"X" * len(PAYLOAD))
            elif "/history/" in self.path:
                self.result({PROMPT: {"status": {"completed": state["completed"], "status_str": "success"}, "outputs": {"2": {"images": [{"filename": "result.png", "subfolder": "origin", "type": "output"}]}}}} if state["completed"] else {})
            elif self.path == "/origin/api/devices":
                self.result({"data": [{"slug": SLUG, "online": True}], "note": "中文元数据"})
            elif "/userdata/" in self.path:
                self.result(state["workflow"])
            else:
                self.result({"node": {"input": {"required": {"width": ["INT", {"min": 16}]}}}})

        def do_DELETE(self):
            state["requests"].append(("DELETE", self.path, dict(self.headers)))
            assert self.headers.get("X-Origin-CSRF") == CSRF
            self.result(None, status=204)

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{http.server_port}", state
    finally:
        http.shutdown()
        http.server_close()
        thread.join(timeout=2)


def logged_in(server, tmp_path):
    base, state = server
    api = client.Origin(base, tmp_path / "private/session.json")
    result = api.login(KEY)
    assert "csrf_token" not in result and TOKEN not in json.dumps(result)
    return api, state


def test_native_session_is_private_origin_bound_and_no_bearer_is_forwarded(server, tmp_path):
    api, state = logged_in(server, tmp_path)
    stored = api.session_file.read_text("utf-8")
    assert TOKEN in stored and KEY not in stored
    assert client.Origin(api.base, api.session_file).request("GET", "/origin/api/devices")["data"][0]["slug"] == SLUG
    with pytest.raises(client.OriginError, match="another origin"):
        client.Origin("http://127.0.0.1:1", api.session_file)
    with pytest.raises(client.OriginError, match="/origin/"):
        api.request("GET", "https://other.example/steal")
    api.logout()
    assert not api.session_file.exists()
    assert state["requests"][-1][0:2] == ("DELETE", "/origin/api/session")


def test_unknown_submission_keeps_exact_intent_then_known_id_only_reads(server, tmp_path):
    api, state = logged_in(server, tmp_path)
    path = tmp_path / "intent.json"
    state["lost"] = True
    with pytest.raises(client.OriginError, match="unknown"):
        client.submit(api, SLUG, GRAPH, path)
    saved = json.loads(path.read_text())
    assert saved["prompt_id"] is None and KEY not in path.read_text() and TOKEN not in path.read_text()
    assert client.resume(api, path)["prompt_id"] == PROMPT
    assert state["submissions"][0] == state["submissions"][1]
    assert client.resume(api, path)[PROMPT]["status"]["completed"]
    assert len(state["submissions"]) == 2
    with pytest.raises(FileExistsError):
        client.submit(api, SLUG, GRAPH, path)
    assert len(state["submissions"]) == 2
    saved["request"]["prompt"]["1"]["inputs"]["width"] = 128
    path.write_text(json.dumps(saved))
    with pytest.raises(client.OriginError, match="graph changed"):
        client.resume(api, path)


def test_unknown_prompt_cannot_move_to_a_different_credential(server, tmp_path):
    api, state = logged_in(server, tmp_path)
    path = tmp_path / "intent.json"
    state["lost"] = True
    with pytest.raises(client.OriginError):
        client.submit(api, SLUG, GRAPH, path)
    api.session["key_fingerprint"] = "different"
    with pytest.raises(client.OriginError, match="original API Key"):
        client.resume(api, path)
    assert len(state["submissions"]) == 1


def test_server_unknown_error_never_creates_another_local_intent_or_echoes_data(server, tmp_path):
    api, state = logged_in(server, tmp_path)
    state["error"] = "origin_submission_unknown"
    path = tmp_path / "intent.json"
    with pytest.raises(client.OriginError, match="origin_submission_unknown") as caught:
        client.submit(api, SLUG, GRAPH, path)
    assert "private submitted" not in str(caught.value)
    assert read_intent(path)["prompt_id"] is None
    assert not state["submissions"]


def read_intent(path):
    return json.loads(path.read_text())


def test_native_wait_tracks_history_heartbeats_and_downloads_owned_descriptors(server, tmp_path):
    api, state = logged_in(server, tmp_path)
    path = tmp_path / "intent.json"
    client.submit(api, SLUG, GRAPH, path)
    result = client.wait(api, path, 2, tmp_path / "outputs")
    assert result["status"] == "completed" and len(result["downloads"]) == 1
    output = result["downloads"][0]
    assert Path(output["path"]).read_bytes() == PAYLOAD
    assert output["content_length_verified"] and not output["source_checksum_verified"]
    assert any(row[1] == "/origin/api/devices" for row in state["requests"])
    assert len(state["submissions"]) == 1


def test_wait_timeout_does_not_cancel_logout_release_or_submit(server, tmp_path):
    api, state = logged_in(server, tmp_path)
    path = tmp_path / "intent.json"
    client.submit(api, SLUG, GRAPH, path)
    state["completed"] = False
    before = len(state["requests"])
    with pytest.raises(client.OriginError, match="task and reservation remain"):
        client.wait(api, path, 0)
    assert all(row[0] == "GET" for row in state["requests"][before:])
    assert api.session_file.exists() and read_intent(path)["prompt_id"] == PROMPT


def test_streamed_mask_uses_actual_image_field_and_owned_original_protocol(server, tmp_path, monkeypatch):
    api, state = logged_in(server, tmp_path)
    file = tmp_path / "mask.png"
    file.write_bytes(PAYLOAD)
    monkeypatch.setattr(Path, "read_bytes", lambda p: pytest.fail("Upload must stream"))
    original = {"filename": "original.png", "subfolder": "origin/key", "type": "input"}
    result = api.upload(SLUG, file, original)
    assert state["requests"][-1][1].endswith("/upload/mask")
    assert state["upload"]["image"] == PAYLOAD
    assert json.loads(state["upload"]["original_ref"]) == original
    assert not state["upload_transfer_encoding"]
    assert result["descriptor"]["type"] == "input"


def test_download_supplied_hash_is_verified_and_mismatch_removes_only_own_partial(server, tmp_path):
    api, state = logged_in(server, tmp_path)
    file = {"filename": "image.png", "subfolder": "origin", "type": "output"}
    target = tmp_path / "result.png"
    state["corrupt"] = True
    digest = hashlib.sha256(PAYLOAD).hexdigest()
    with pytest.raises(client.OriginError, match="verification"):
        api.download(SLUG, file, target, digest)
    assert not target.exists() and not list(tmp_path.glob("*.part"))
    state["corrupt"] = False
    assert api.download(SLUG, file, target, digest)["source_checksum_verified"]
    with pytest.raises(client.OriginError, match="already exists"):
        api.download(SLUG, file, target)


@pytest.mark.parametrize("path", ["../api/session", "userdata/../secret", "%252e%252e/api/session", "https://other.example/prompt", "//other.example/prompt", "object_info/..\\secret"])
def test_direct_paths_cannot_escape_the_selected_device(path):
    with pytest.raises(client.OriginError):
        client.native_path(SLUG, path)


def test_ui_graph_is_not_silently_miscompiled_and_file_descriptors_are_safe(server, tmp_path):
    api, state = logged_in(server, tmp_path)
    with pytest.raises(client.OriginError, match="UI"):
        client.submit(api, SLUG, {"nodes": [], "links": []}, tmp_path / "no-intent.json")
    assert not (tmp_path / "no-intent.json").exists() and not state["submissions"]
    assert client.output_files({"images": [{"filename": "../secret", "type": "output"}, {"filename": "same.glb", "type": "output"}], "mesh": {"filename": "same.glb", "type": "output"}}) == [{"filename": "same.glb", "subfolder": "", "type": "output"}]


def test_redirect_refused_and_native_cli_reuses_private_session_without_key(server, tmp_path):
    api, state = logged_in(server, tmp_path)
    environment = {k: v for k, v in os.environ.items() if k != "MEDIA_GEN_API_KEY"}
    result = subprocess.run([sys.executable, "-I", str(PATH), "--base-url", api.base, "--session-file", str(api.session_file), "devices"], capture_output=True, timeout=10, check=True, env=environment)  # noqa: S603 -- owned loopback fixture and checked-in client
    assert json.loads(result.stdout.decode("utf-8"))["note"] == "中文元数据"
    assert TOKEN.encode() not in result.stdout + result.stderr and KEY.encode() not in result.stdout + result.stderr
    state["redirect"] = True
    with pytest.raises(client.OriginError, match="Redirect"):
        api.request("GET", "/origin/api/devices")


def test_personal_workflow_cli_save_is_scoped_and_does_not_execute(server, tmp_path):
    api, state = logged_in(server, tmp_path)
    file = tmp_path / "ui.json"
    file.write_text(json.dumps({"nodes": [], "links": []}))
    assert client.main(["--base-url", api.base, "--session-file", str(api.session_file), "save-workflow", SLUG, "draft.json", str(file)]) == 0
    assert state["requests"][-1][1] == "/origin/rtx-5070/userdata/workflows/%40personal/draft.json?overwrite=false"
    assert state["workflow"] == {"nodes": [], "links": []} and not state["submissions"]
    assert client.main(["--base-url", api.base, "--session-file", str(api.session_file), "delete-workflow", SLUG, "draft.json"]) == 0


def test_shared_workflow_cli_requires_explicit_intent_and_keeps_original_identity(server, tmp_path):
    api, state = logged_in(server, tmp_path)
    file, intent = tmp_path / "ui.json", tmp_path / "source-write.json"
    file.write_text(json.dumps({"nodes": [], "links": []}))
    args = ["--base-url", api.base, "--session-file", str(api.session_file), "save-workflow", SLUG, "中文/draft.json", str(file), "--shared"]
    with pytest.raises(client.OriginError, match="operation-file"):
        client.main(args)
    state["error"] = "origin_workflow_write_unknown"
    with pytest.raises(client.OriginError, match="unknown"):
        client.main([*args, "--operation-file", str(intent)])
    saved = client.read_json(intent)
    assert not saved["completed"] and KEY not in intent.read_text(encoding="utf-8") and TOKEN not in intent.read_text(encoding="utf-8")
    first = state["requests"][-1]
    state["error"] = None
    client.resume_workflow(api, intent)
    second = state["requests"][-1]
    assert first[1] == second[1] and "/%40personal/" not in second[1]
    assert first[2]["Idempotency-Key"] == second[2]["Idempotency-Key"]
    count = len(state["requests"])
    client.resume_workflow(api, intent)
    assert len(state["requests"]) == count and not state["submissions"]
    with pytest.raises(FileExistsError):
        client.workflow_write(api, SLUG, "draft.json", "save", intent, {})


def test_shared_workflow_move_and_delete_are_explicit_source_operations(server, tmp_path):
    api, state = logged_in(server, tmp_path)
    client.workflow_write(api, SLUG, "中文/a.json", "delete", tmp_path / "delete.json")
    assert state["requests"][-1][0] == "DELETE" and state["requests"][-1][2]["Idempotency-Key"]
    client.workflow_write(api, SLUG, "中文/a.json", "move", tmp_path / "move.json", destination="中文/b.json")
    move = state["requests"][-1]
    assert move[0] == "POST" and "/move/workflows%2F" in move[1] and "%E4%B8%AD" in move[1]
    with pytest.raises(client.OriginError, match="reserved"):
        client.workflow_path(SLUG, "@personal/a.json", shared=True)


def test_compiled_single_file_extracts_both_clients_and_runs_native_without_repository(server, tmp_path):
    builder_spec = importlib.util.spec_from_file_location("skill_builder_native", PATH.parents[3] / "scripts/build-media-gen-skill.py")
    assert builder_spec and builder_spec.loader
    builder = importlib.util.module_from_spec(builder_spec)
    builder_spec.loader.exec_module(builder)
    skill = PATH.parents[1]
    payload = {name: (skill / name).read_bytes() for name in builder.FILES}
    rendered = builder.render_single_file(payload, payload["VERSION"].decode().strip()).decode()
    (tmp_path / "SKILL.md").write_text(rendered, "utf-8")
    extraction = next(line for line in rendered.splitlines() if line.startswith('python -c "'))[11:-1]
    subprocess.run([sys.executable, "-I", "-c", extraction], cwd=tmp_path, capture_output=True, timeout=10, check=True)  # noqa: S603 -- extract reviewed client sources in isolated owned folder
    assert (tmp_path / "comfy_origin.py").read_text() == PATH.read_text()
    api, _ = logged_in(server, tmp_path)
    result = subprocess.run([sys.executable, "-I", str(tmp_path / "comfy_origin.py"), "--base-url", api.base, "--session-file", str(api.session_file), "devices"], capture_output=True, timeout=10, check=True)  # noqa: S603 -- extracted client uses only owned loopback fixture
    assert json.loads(result.stdout.decode("utf-8"))["data"][0]["slug"] == SLUG
