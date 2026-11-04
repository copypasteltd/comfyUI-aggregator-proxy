from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from email.parser import BytesParser
from email.policy import default
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
            if self.path == "/v1/assets":
                message = BytesParser(policy=default).parsebytes(("Content-Type: " + self.headers["Content-Type"] + "\r\nMIME-Version: 1.0\r\n\r\n").encode() + data)
                parts = list(message.iter_parts())
                state["upload"] = {"kind": parts[0].get_payload(decode=True).decode(), "bytes": parts[1].get_payload(decode=True), "filename": parts[1].get_filename(), "mime": parts[1].get_content_type(), "transfer_encoding": self.headers.get("Transfer-Encoding"), "token": self.headers.get("Idempotency-Key")}
                self.send_response(201)
                self.end_headers()
                self.wfile.write(json.dumps({"id": "asset_" + "c" * 32}).encode())
                return
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
                self.wfile.write(json.dumps({"id": CONTENT, "bytes": len(PAYLOAD), "sha256": hashlib.sha256(PAYLOAD).hexdigest(), "media_type": state.get("media_type", "image/png")}).encode())
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
    assert state["user_agent"] == "media-gen-skill/" + (PATH.parents[1] / "VERSION").read_text("utf-8").strip()
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


def test_model3d_client_uses_image_only_and_declared_parameters() -> None:
    class Fake:
        uploads: list[str] = []

        def describe(self, alias: str) -> dict[str, Any]:
            return {"usage": {"example_request": {"model": alias, "task_type": "image_to_3d", "input": {"source_image": {"asset_id": "example"}}, "parameters": {"quality": "standard"}}, "request_schema": {"properties": {"input": {"properties": {"source_image": {}}}, "parameters": {"properties": {"quality": {"enum": ["standard"]}, "seed": {"minimum": 0, "maximum": 2147483647}}}}}, "references": {"exact_reference_count": 0}, "presets": [{"quality": "standard"}], "defaults": {"quality": "standard"}}}

        def upload(self, path: str) -> dict[str, str]:
            self.uploads.append(path)
            return {"id": "asset_synthetic"}

    args = client.parser().parse_args(["generate", "--model", "mesh", "--source-image", "toy.png", "--seed", "17"])
    fake = Fake()
    request = client.generation_request(fake, args)
    assert request["input"] == {"source_image": {"asset_id": "asset_synthetic"}}
    assert request["parameters"] == {"quality": "standard", "seed": 17}
    args.prompt = "silently ignored text must be rejected"
    with pytest.raises(client.ClientError, match="image only"):
        client.generation_request(fake, args)
    args.prompt = None
    args.source_image = None
    with pytest.raises(client.ClientError, match="requires --source-image"):
        client.generation_request(fake, args)
    args.source_image = "toy.png"
    args.size = "720x1080"
    with pytest.raises(client.ClientError, match="does not expose parameter size"):
        client.generation_request(fake, args)


def test_model3d_download_uses_canonical_extension(server: Any, tmp_path: Path) -> None:
    base, state = server
    state["media_type"] = "model/gltf-binary"
    result = client.wait_job(client.Api(base, "synthetic-key"), JOB, 1, tmp_path)
    target = Path(result["downloads"][0]["path"])
    assert target.suffix == ".glb" and target.read_bytes() == PAYLOAD


def test_single_file_runs_independently_and_downloads_a_verified_glb(server: Any, tmp_path: Path) -> None:
    builder_path = PATH.parents[3] / "scripts/build-media-gen-skill.py"
    builder_spec = importlib.util.spec_from_file_location("skill_builder", builder_path)
    assert builder_spec and builder_spec.loader
    builder = importlib.util.module_from_spec(builder_spec)
    builder_spec.loader.exec_module(builder)
    skill_dir = PATH.parents[1]
    payload = {name: (skill_dir / name).read_bytes() for name in builder.FILES}
    text = builder.render_single_file(payload, payload["VERSION"].decode().strip()).decode("utf-8")
    (tmp_path / "SKILL.md").write_text(text, "utf-8")
    extraction = next(line for line in text.splitlines() if line.startswith('python -c "'))[11:-1]
    subprocess.run([sys.executable, "-I", "-c", extraction], cwd=tmp_path, capture_output=True, check=True, timeout=10)  # noqa: S603 -- extract the checked-in, generated client into an isolated test directory
    extracted = tmp_path / "media_gen.py"
    assert extracted.read_text("utf-8") == PATH.read_text("utf-8")
    base, state = server
    state["media_type"] = "model/gltf-binary"
    result = subprocess.run([sys.executable, "-I", str(extracted), "--base-url", base, "wait", JOB, "--timeout", "1", "--download", "--output-dir", str(tmp_path / "output")], cwd=tmp_path, env=dict(os.environ, MEDIA_GEN_API_KEY="synthetic-key"), capture_output=True, check=True, timeout=10)  # noqa: S603 -- isolated extracted client and owned loopback fixture
    value = json.loads(result.stdout.decode("utf-8"))
    assert Path(value["downloads"][0]["path"]).suffix == ".glb"
    assert Path(value["downloads"][0]["path"]).read_bytes() == PAYLOAD
    assert not state["posts"]


def test_typed_audio_upload_streams_multipart_with_explicit_kind_and_stable_token(server: Any, audio_wav: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base, state = server
    expected = audio_wav.read_bytes()

    def forbid_full_read(path: Path) -> bytes:
        raise AssertionError("The upload client must stream files")

    monkeypatch.setattr(Path, "read_bytes", forbid_full_read)
    result = client.Api(base, "synthetic-key").upload(audio_wav, kind="audio", token="audio-retry-token")
    assert result["id"].startswith("asset_")
    assert state["upload"] == {"kind": "audio", "bytes": expected, "filename": "input.wav", "mime": "audio/wav", "transfer_encoding": None, "token": "audio-retry-token"}
    with pytest.raises(client.ClientError, match="signature"):
        client.Api(base, "synthetic-key").upload(audio_wav)


def test_named_inputs_keep_video_audio_and_transcripts_in_their_semantic_slots(tmp_path: Path) -> None:
    usage = {"example_request": {"model": "typed", "task_type": "references_to_video", "input": {"media": [{"slot": "source_video", "asset_id": "placeholder"}], "texts": {"speaker_1_transcript": "placeholder"}}, "parameters": {"quality": "standard", "seconds": 5}}, "request_schema": {"properties": {"input": {"properties": {"media": {}, "texts": {}, "context": {}}}, "parameters": {"properties": {"quality": {"enum": ["standard"]}, "seconds": {"enum": [5]}}}}}, "references": {"exact_reference_count": 0}, "presets": [{"quality": "standard", "seconds": 5}], "context": {"required": True, "scope": "same_api_key"}, "input_slots": [{"name": "source_video", "kind": "video", "required": True, "max_bytes": 100}, {"name": "speaker_1", "kind": "audio", "required": True, "max_bytes": 100}], "text_slots": [{"name": "speaker_1_transcript", "required": True, "max_length": 100}]}

    class Fake:
        uploads: list[tuple[str, str]] = []

        def describe(self, alias: str) -> dict[str, Any]:
            return {"usage": usage}

        def upload(self, path: str, *, kind: str) -> dict[str, str]:
            self.uploads.append((path, kind))
            return {"id": "asset_" + kind + "_synthetic"}

    video, audio, transcript = tmp_path / "video.mp4", tmp_path / "voice.wav", tmp_path / "voice.txt"
    video.write_bytes(b"synthetic typed file")
    audio.write_bytes(b"synthetic typed file")
    transcript.write_text("These exact words were spoken.", "utf-8")
    args = client.parser().parse_args(["generate", "--model", "typed", "--media", "source_video=" + str(video), "--media", "speaker_1=" + str(audio), "--text-file", "speaker_1_transcript=" + str(transcript), "--parent-job", JOB])
    fake = Fake()
    body = client.generation_request(fake, args)
    assert body["input"] == {"media": [{"slot": "source_video", "asset_id": "asset_video_synthetic"}, {"slot": "speaker_1", "asset_id": "asset_audio_synthetic"}], "texts": {"speaker_1_transcript": "These exact words were spoken."}, "context": {"job_id": JOB}}
    assert fake.uploads == [(str(video), "video"), (str(audio), "audio")]
    assert usage["example_request"]["input"]["media"][0]["asset_id"] == "placeholder"
    args.parent_job = None
    with pytest.raises(client.ClientError, match="requires --parent-job"):
        client.generation_request(fake, args)
    assert len(fake.uploads) == 2
    args.parent_job = JOB
    args.text = ["speaker_1_transcript=duplicate"]
    with pytest.raises(client.ClientError, match="both inline"):
        client.generation_request(fake, args)


@pytest.mark.parametrize("values", [["not-a-slot=value"], ["source_video=a", "source_video=b"], ["speaker_1="]])
def test_invalid_named_input_arguments_are_rejected(values: list[str]) -> None:
    with pytest.raises(client.ClientError, match="NAME=VALUE"):
        client.assignments(values)
