"""Synthetic media/Comfy protocol fixtures for the isolated native rehearsal only."""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from uuid import uuid4

from generation_contracts.defaults import default_key_policy, default_limits
from generation_domain.values import fingerprint, public_id, utcnow
from generation_infrastructure.models import (
    ApiKey,
    Channel,
    ExternalIdentityBinding,
    GatewayService,
    Gpu,
    Node,
    Project,
    PublicModel,
    Tenant,
    Workflow,
    WorkflowBinding,
    WorkflowRelease,
)
from generation_infrastructure.security import issue_secret, verifier
from PIL import Image
from sqlalchemy.orm import Session, sessionmaker

TASKS = ("text_to_image", "image_to_image", "references_to_image", "text_to_video", "references_to_video", "text_to_audio")
HARDWARE = "SYNTHETIC-PROTOCOL-GPU"


def native(command: list[str], *, cwd: Path) -> bytes:
    result = subprocess.run(command, cwd=cwd, capture_output=True, timeout=60, creationflags=subprocess.CREATE_NO_WINDOW)  # noqa: S603 -- bounded native fixture commands, no shell
    if result.returncode:
        raise RuntimeError("fixture_native_command_failed")
    return result.stdout


def media_root(root: Path) -> str:
    """Create a local Git fixture, not a Comfy installation or model download."""
    root.mkdir()
    (root / "output").mkdir()
    (root / "temp").mkdir()
    (root / "README.txt").write_text("Synthetic Comfy protocol fixture; no GPU execution.\n", encoding="utf-8")
    git, ffmpeg = shutil.which("git"), shutil.which("ffmpeg")
    if not git or not ffmpeg:
        raise RuntimeError("native_git_and_ffmpeg_required")
    native([git, "init", "--quiet", "--initial-branch=protocol"], cwd=root)
    native([git, "add", "README.txt"], cwd=root)
    native([git, "-c", "user.name=Protocol Fixture", "-c", "user.email=fixture@invalid.test", "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "bounded protocol fixture"], cwd=root)
    commit = native([git, "rev-parse", "HEAD"], cwd=root).decode("ascii").strip()
    Image.new("RGB", (1024, 1024), (35, 80, 120)).save(root / "output/result.png")
    native([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-f", "lavfi", "-i", "color=c=0x235078:s=1024x1024:r=8:d=5", "-an", "-c:v", "libx264", "-threads", "1", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(root / "output/result.mp4")], cwd=root)
    with wave.open(str(root / "output/result.wav"), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(b"\x10\x00\xf0\xff" * 6000)
    return commit


def defaults(task: str) -> dict[str, Any]:
    if task == "text_to_audio":
        return {"quality": "standard", "seconds": 2}
    return {"size": "1024x1024", "quality": "standard", **({"seconds": 5} if task.endswith("video") else {})}


def manifest(task: str, commit: str) -> dict[str, Any]:
    sources = ["prompt", "seed"]
    if task == "image_to_image":
        sources.append("source_image")
    if task.startswith("references_"):
        sources.extend(["reference_0", "reference_1"])
    video = task.endswith("video")
    audio = task == "text_to_audio"
    if video or audio:
        sources.append("seconds")
    result = {
        "format_version": "1.1" if audio else "1.0", "task_type": task,
        "workflow_api": {"1": {"class_type": "SyntheticProtocolInput", "inputs": {source: "" for source in sources}}, "2": {"class_type": "SyntheticProtocolAudio" if audio else "SyntheticProtocolVideo" if video else "SyntheticProtocolImage", "inputs": {"media": ["1", 0], "filename_prefix": ""}}},
        "bindings": [{"source": source, "node_id": "1", "input_name": source} for source in sources] + [{"source": "output_prefix", "node_id": "2", "input_name": "filename_prefix"}],
        "output": {"adapter": "audio-files-v1" if audio else "video-files-v1" if video else "save-image-v1", "final_node_ids": ["2"], "expected_count": 1, "history_field": "audio" if audio else "videos" if video else "images", "max_bytes": 33554432, "mime_types": ["audio/wav" if audio else "video/mp4" if video else "image/png"]},
        "presets": [{**defaults(task), "values": {}, **({"audio_format": "wav", "sample_rate": 24000, "channels": 1} if audio else {})}],
        "dependency_lock": {"comfy_commit": commit, "models": [], "custom_nodes": []},
        "runtime_profiles": [{"id": "protocol-only", "gpu_hardware_id": HARDWARE, "approved": False, "evidence_id": None, "execution_timeout_seconds": 30}],
        "supported_parameters": [],
    }
    from generation_contracts.workflows import WorkflowManifest
    from generation_domain.workflows import validate_manifest
    WorkflowManifest.model_validate(result)
    validate_manifest(result, task)
    return result


class ComfyProtocol:
    def __init__(self, root: Path, port: int) -> None:
        self.root = root
        self.receipts: dict[str, dict[str, Any]] = {}
        self.uploads = 0
        self.submissions = 0
        self.fail_next_uploads = 0
        self.preparation_failures = 0
        self.lock = threading.Lock()
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args: object) -> None:
                pass  # No prompt, graph, headers or media bodies in test output.

            def reply(self, value: dict[str, Any], status: int = 200) -> None:
                data = json.dumps(value).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:
                path = self.path.split("?", 1)[0]
                with fixture.lock:
                    if path == "/queue":
                        self.reply({"queue_running": [], "queue_pending": []})
                    elif path.startswith("/history/"):
                        key = path.removeprefix("/history/")
                        self.reply({key: fixture.receipts[key]} if key in fixture.receipts else {})
                    elif path == "/history":
                        self.reply(dict(fixture.receipts))
                    else:
                        self.reply({"error": "unsupported_protocol_fixture_path"}, 404)

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 22 * 1024 * 1024:
                    self.reply({"error": "bounded_body_required"}, 413)
                    self.close_connection = True
                    return
                data = self.rfile.read(length)
                with fixture.lock:
                    if self.path == "/upload/image":
                        if fixture.fail_next_uploads:
                            fixture.fail_next_uploads -= 1
                            fixture.preparation_failures += 1
                            self.reply({"error": "synthetic_temporary_upload_failure"}, 503)
                            return
                        fixture.uploads += 1
                        self.reply({"name": f"fixture-{fixture.uploads}.png", "subfolder": "", "type": "input"})
                    elif self.path == "/prompt":
                        value = json.loads(data)
                        graph = value["prompt"]
                        if set(graph) != {"1", "2"} or graph["2"]["class_type"] not in ("SyntheticProtocolVideo", "SyntheticProtocolImage", "SyntheticProtocolAudio"):
                            self.reply({"error": "synthetic_graph_required"}, 400)
                            return
                        identifier = str(uuid4())
                        video = graph["2"]["class_type"] == "SyntheticProtocolVideo"
                        audio = graph["2"]["class_type"] == "SyntheticProtocolAudio"
                        fixture.receipts[identifier] = {"prompt": [0, identifier, graph, value.get("extra_data", {}), ["2"]], "outputs": {"2": {"audio" if audio else "videos" if video else "images": [{"filename": "result.wav" if audio else "result.mp4" if video else "result.png", "subfolder": "", "type": "output"}]}}, "status": {"completed": True, "status_str": "success", "messages": []}}
                        fixture.submissions += 1
                        self.reply({"prompt_id": identifier, "number": fixture.submissions, "node_errors": {}})
                    else:
                        self.reply({"error": "unsupported_protocol_fixture_path"}, 404)

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def seed_control(factory: sessionmaker[Session], pepper: str, comfy_origin: str, commit: str, upstream: dict[str, Any]) -> dict[str, Any]:
    worker_id, worker_token = issue_secret("worker")
    gateway_id, gateway_token = issue_secret("gateway")
    with factory.begin() as session:
        limits = {**default_limits(), "daily_audio_milliseconds": {"mode": "unlimited"}}
        tenant = Tenant(name="Native protocol tenant", limits=limits)
        session.add(tenant)
        session.flush()
        project = Project(tenant_id=tenant.id, name="Native protocol project", limits=limits)
        node = Node(name="Native protocol worker", credential_identifier=worker_id, credential_verifier=verifier(worker_token, pepper))
        gateway = GatewayService(name="Native Sub2API protocol", issuer="native-protocol", allowed_cidrs=["127.0.0.1/32"], credential_identifier=gateway_id, credential_verifier=verifier(gateway_token, pepper))
        session.add_all([project, node, gateway])
        session.flush()
        policy = default_key_policy()
        policy["scopes"].append("audio:create")
        policy["spec_limits"]["audio_max_milliseconds"] = 2000
        policy["limits"]["daily_audio_milliseconds"] = {"mode": "unlimited"}
        for selection in ("models", "workflow_releases", "channels"):
            policy[selection] = {"mode": "all_authorized", "selected_ids": []}
        keys = [ApiKey(tenant_id=tenant.id, project_id=project.id, name=f"Native protocol key {i}", policy=policy, issuer="gateway:" + gateway.id.hex, external_ref=f"native-protocol:key:{key}") for i, key in enumerate(upstream["keys"])]
        gpu = Gpu(node_id=node.id, hardware_id=HARDWARE, name="Synthetic protocol GPU")
        session.add_all([*keys, gpu])
        session.flush()
        for key, external_key in zip(keys, upstream["keys"], strict=True):
            session.add(ExternalIdentityBinding(tenant_id=tenant.id, project_id=project.id, gateway_id=gateway.id, key_id=key.id, external_tenant_ref=f"native-protocol:user:{upstream['user']}", external_key_ref=f"native-protocol:key:{external_key}"))
        channel = Channel(tenant_id=tenant.id, project_id=project.id, node_id=node.id, gpu_id=gpu.id, name="Synthetic protocol channel", base_url=comfy_origin, enabled=True, desired_revision=1)
        session.add(channel)
        session.flush()
        for task in TASKS:
            workflow = Workflow(tenant_id=tenant.id, project_id=project.id, name=f"Protocol {task}", task_type=task, draft={})
            session.add(workflow)
            session.flush()
            value = manifest(task, commit)
            release = WorkflowRelease(tenant_id=tenant.id, project_id=project.id, workflow_id=workflow.id, version=1, digest=fingerprint(value), manifest=value, validated=True, validation={"test_only": True})
            session.add(release)
            session.flush()
            session.add(WorkflowBinding(tenant_id=tenant.id, project_id=project.id, channel_id=channel.id, release_id=release.id, desired_digest=release.digest, applied_digest=release.digest, verified_digest=release.digest, observed_at=utcnow()))
            session.add(PublicModel(tenant_id=tenant.id, project_id=project.id, alias="protocol-" + task.replace("_", "-"), task_type=task, release_id=release.id, channel_ids=[public_id("ch", channel.id)], defaults=defaults(task), published=True))
        return {"node_uuid": node.id, "channel_uuid": channel.id, "key_uuids": [key.id for key in keys], "worker_credential": {"node_id": public_id("node", node.id), "token": worker_token}, "gateway_id": public_id("gateway", gateway.id), "gateway_token": gateway_token}
