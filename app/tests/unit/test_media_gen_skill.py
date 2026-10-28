from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

PATH = Path(__file__).resolve().parents[2] / "skills/media-gen-skill/scripts/media_gen.py"
spec = importlib.util.spec_from_file_location("media_gen", PATH)
assert spec and spec.loader
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)
JOB = "job_" + "a" * 32
CONTENT = "content_" + "b" * 32
PAYLOAD = b"verified synthetic content"


@pytest.fixture
def server() -> Iterator[tuple[str, dict[str, Any]]]:
    state: dict[str, Any] = {"posts": [], "redirect": False, "corrupt": False, "lost": False}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def do_POST(self) -> None:
            data = self.rfile.read(int(self.headers["Content-Length"]))
            state["posts"].append((self.path, self.headers.get("Idempotency-Key"), json.loads(data)))
            if state["lost"]:
                state["lost"] = False
                self.connection.close()
                return
            self.send_response(202)
            self.end_headers()
            self.wfile.write(json.dumps({"id": JOB, "status": "queued"}).encode())

        def do_GET(self) -> None:
            state["user_agent"] = self.headers.get("User-Agent")
            if state["redirect"]:
                self.send_response(302)
                self.send_header("Location", "http://127.0.0.1:1/steal")
                self.end_headers()
                return
            self.send_response(200)
            self.end_headers()
            if self.path.endswith("/download"):
                self.wfile.write(PAYLOAD + (b"unexpected" if state["corrupt"] else b""))
            elif self.path.startswith("/v1/content/"):
                self.wfile.write(json.dumps({"id": CONTENT, "bytes": len(PAYLOAD), "sha256": hashlib.sha256(PAYLOAD).hexdigest(), "media_type": "image/png"}).encode())
            else:
                self.wfile.write(json.dumps({"id": JOB, "status": state.get("job_status", "succeeded"), "artifacts": [{"id": CONTENT}], "note": "中文元数据"}).encode())

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{http.server_port}", state
    finally:
        http.shutdown()
        http.server_close()
        thread.join(timeout=2)


def test_lost_submission_reuses_exact_identity_and_body_then_only_polls(server: Any, tmp_path: Path) -> None:
    base, state = server
    api, path = client.Api(base, "synthetic-key"), tmp_path / "state.json"
    body = {"model": "example", "task_type": "text_to_image", "input": {"prompt": "A garden"}}
    state["lost"] = True
    with pytest.raises(client.ClientError, match="unknown"):
        client.submit(api, body, path)
    saved = json.loads(path.read_text("utf-8"))
    assert "synthetic-key" not in path.read_text("utf-8") and saved["job_id"] is None
    with pytest.raises(client.ClientError, match="original API Key"):
        client.resume(client.Api(base, "rotated-key"), path)
    result = client.resume(api, path)
    assert result["id"] == JOB
    assert state["posts"][0] == state["posts"][1]
    assert client.resume(api, path)["status"] == "succeeded" and len(state["posts"]) == 2
    saved = json.loads(path.read_text("utf-8"))
    saved["request"]["model"] = "changed"
    path.write_text(json.dumps(saved), encoding="utf-8")
    with pytest.raises(client.ClientError, match="changed"):
        client.resume(api, path)


def test_download_verifies_and_never_overwrites_or_leaves_partial(server: Any, tmp_path: Path) -> None:
    base, state = server
    api = client.Api(base, "synthetic-key")
    target = tmp_path / "image.png"
    state["corrupt"] = True
    with pytest.raises(client.ClientError, match="size"):
        api.download(CONTENT, target)
    assert not target.exists() and not list(tmp_path.glob("*.part"))
    state["corrupt"] = False
    assert api.download(CONTENT, target)["sha256"] == hashlib.sha256(PAYLOAD).hexdigest()
    assert state["user_agent"] == "media-gen-skill/1.0.0"
    assert target.read_bytes() == PAYLOAD
    with pytest.raises(client.ClientError, match="already exists"):
        api.download(CONTENT, target)


def test_wait_uses_native_job_status_and_downloads_completed_artifacts(server: Any, tmp_path: Path) -> None:
    base, state = server
    api = client.Api(base, "synthetic-key")
    result = client.wait_job(api, JOB, 1, tmp_path)
    assert result["status"] == "succeeded"
    assert len(result["downloads"]) == 1
    assert Path(result["downloads"][0]["path"]).read_bytes() == PAYLOAD
    assert not state["posts"]


@pytest.mark.parametrize("status", ["failed", "cancelled", "expired"])
def test_wait_stops_on_native_unsuccessful_terminal_status(server: Any, tmp_path: Path, status: str) -> None:
    base, state = server
    state["job_status"] = status
    with pytest.raises(client.ClientError, match="status " + status):
        client.wait_job(client.Api(base, "synthetic-key"), JOB, 1, tmp_path)
    assert not list(tmp_path.iterdir()) and not state["posts"]


def test_redirect_refused_and_origin_locked(server: Any, tmp_path: Path) -> None:
    base, state = server
    state["redirect"] = True
    with pytest.raises(client.ClientError, match="Redirect"):
        client.Api(base, "synthetic-key").request("GET", "/v1/models")
    with pytest.raises(client.ClientError, match="Base URL"):
        client.Api("https://user:password@example.com", "key")
    with pytest.raises(client.ClientError, match="origin"):
        client.Api(base, "key").request("GET", "https://other.example/v1/models")
    path = tmp_path / "state.json"
    client.private_write(path, {"schema_version": 1, "base_url": "https://other.example"})
    with pytest.raises(client.ClientError, match="another API origin"):
        client.resume(client.Api(base, "key"), path)


def test_audio_presets_fixed_encoding_does_not_require_unexposed_parameters() -> None:
    class Fake:
        def describe(self, alias: str) -> dict[str, Any]:
            return {"usage": {"example_request": {"model": alias, "task_type": "text_to_audio", "input": {"prompt": "score"}, "parameters": {"quality": "standard", "seconds": 10}}, "request_schema": {"properties": {"input": {"properties": {"prompt": {}}}, "parameters": {"properties": {"seconds": {"enum": [10]}, "quality": {"enum": ["standard"]}, "seed": {}}}}}, "references": {"exact_reference_count": 0}, "presets": [{"seconds": 10, "quality": "standard", "audio_format": "flac", "sample_rate": 44100, "channels": 2}], "defaults": {"quality": "standard", "seconds": 10}}}
    args = client.parser().parse_args(["generate", "--model", "music", "--prompt", "score", "--seconds", "10"])
    assert client.generation_request(Fake(), args)["parameters"]["seconds"] == 10
    args.seconds = 11
    with pytest.raises(client.ClientError, match="Unsupported seconds"):
        client.generation_request(Fake(), args)


def test_cli_outputs_utf8_when_windows_redirect_encoding_is_ascii(server: Any) -> None:
    base, _ = server
    result = subprocess.run([sys.executable, str(PATH), "--base-url", base, "models"], env=dict(os.environ, MEDIA_GEN_API_KEY="synthetic-key", PYTHONIOENCODING="ascii"), capture_output=True, timeout=10, check=True)  # noqa: S603 -- checked-in client and an owned loopback fixture.
    assert json.loads(result.stdout.decode("utf-8"))["note"] == "中文元数据"
