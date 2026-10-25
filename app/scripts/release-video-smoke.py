"""Exercise direct video metadata over HTTPS using exact installed modules only."""
from __future__ import annotations

import argparse
import json
import re
import ssl
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from generation_contracts.defaults import default_key_policy, default_limits
from generation_contracts.validation import validate
from generation_domain.values import fingerprint, parse_id, public_id, utcnow
from generation_infrastructure.database import make_sessions
from generation_infrastructure.models import (
    ApiCredential,
    ApiKey,
    Channel,
    Gpu,
    Job,
    Node,
    Project,
    PublicModel,
    Tenant,
    UsageLedger,
    Workflow,
    WorkflowRelease,
)
from generation_infrastructure.security import issue_secret, verifier
from generation_infrastructure.settings import Settings
from sqlalchemy import func, select
from sqlalchemy.engine import make_url


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--ca-file", type=Path, required=True)
    args = parser.parse_args()
    payload = args.payload.resolve(strict=True)
    installed = payload.parent / "venv"
    assert Path(sys.prefix).resolve() == installed
    assert Path(sys.modules[Job.__module__].__file__).resolve().is_relative_to(installed)
    origin = urlsplit(args.origin)
    assert origin.scheme == "https" and origin.hostname == "127.0.0.1" and origin.port and not origin.path and not origin.query and not origin.fragment and not origin.username
    settings = Settings(_env_file=args.config.resolve(strict=True))
    url = make_url(settings.database_url.get_secret_value())
    assert re.fullmatch(r"render_release_[a-f0-9]{12}", args.database)
    assert (url.host, url.port, url.database) == ("127.0.0.1", 55438, args.database)
    sessions = make_sessions(settings)
    policy = default_key_policy()
    for selection in ("models", "workflow_releases", "channels"):
        policy[selection] = {"mode": "all_authorized", "selected_ids": []}
    secrets = [issue_secret("sk-gen"), issue_secret("sk-gen")]
    try:
        # The parent owns this disposable database and removes it in finally.
        # No Worker is assigned this synthetic node; no ComfyUI call can run.
        with sessions.begin() as session:
            tenant = Tenant(name="Synthetic installed video tenant", limits=default_limits())
            session.add(tenant)
            session.flush()
            project = Project(tenant_id=tenant.id, name="Synthetic installed video project", limits=default_limits())
            session.add(project)
            session.flush()
            node = Node(name="Synthetic installed video node", credential_identifier=uuid4().hex, credential_verifier="0" * 64)
            session.add(node)
            session.flush()
            gpu = Gpu(node_id=node.id, hardware_id="SYNTHETIC-INSTALLED-VIDEO", name="Synthetic GPU")
            session.add(gpu)
            session.flush()
            channel = Channel(tenant_id=tenant.id, project_id=project.id, node_id=node.id, gpu_id=gpu.id, name="Synthetic installed video channel", base_url="http://127.0.0.1:9", enabled=True)
            workflow = Workflow(tenant_id=tenant.id, project_id=project.id, name="Synthetic installed video", task_type="text_to_video", draft={})
            session.add_all([channel, workflow])
            session.flush()
            manifest = {"task_type": "text_to_video", "supported_parameters": [], "workflow_api": {"1": {"class_type": "SyntheticOutput", "inputs": {}}}, "presets": [{"seconds": 5.125, "size": "1024x1024", "quality": "standard", "values": {}}]}
            release = WorkflowRelease(tenant_id=tenant.id, project_id=project.id, workflow_id=workflow.id, version=1, digest=fingerprint(manifest), manifest=manifest, validated=True, validation={"test_only": True})
            session.add(release)
            session.flush()
            model = PublicModel(tenant_id=tenant.id, project_id=project.id, alias="synthetic-installed-video", task_type="text_to_video", release_id=release.id, channel_ids=[public_id("ch", channel.id)], defaults={"seconds": 5.125, "size": "1024x1024", "quality": "standard"}, published=True)
            keys = [ApiKey(tenant_id=tenant.id, project_id=project.id, name="Synthetic installed video key", policy=policy) for _ in secrets]
            session.add_all([model, *keys])
            session.flush()
            for key, (identifier, token) in zip(keys, secrets, strict=True):
                session.add(ApiCredential(key_id=key.id, identifier=identifier, verifier=verifier(token, settings.credential_pepper.get_secret_value()), pepper_version=settings.credential_pepper_version))
            key_id, model_id = keys[0].id, model.id
        previous = 0.0

        def pace(_request: httpx.Request) -> None:
            nonlocal previous
            remaining = 3 - (time.monotonic() - previous)
            if remaining > 0:
                time.sleep(remaining)
            previous = time.monotonic()

        with httpx.Client(base_url=args.origin, verify=ssl.create_default_context(cafile=str(args.ca_file.resolve(strict=True))), trust_env=False, timeout=10, follow_redirects=False, event_hooks={"request": [pace]}) as client:
            headers = {"Authorization": "Bearer " + secrets[0][1], "Idempotency-Key": "installed-video-" + uuid4().hex}
            body = {"model": "synthetic-installed-video", "prompt": "SYNTHETIC-INSTALLED-PRIVATE-PROMPT"}
            created = client.post("/v1/videos", json=body, headers=headers)
            assert created.status_code == 202, "installed_video_create"
            original = created.json()
            validate("control-video-response", original)
            assert created.headers["location"] == original["poll_url"] == "/v1/videos/" + original["id"]
            assert original["seconds"] == "5.125" and original["size"] == "1024x1024" and "input" not in original
            with sessions.begin() as session:
                session.get(PublicModel, model_id).defaults = {"seconds": 9, "size": "512x512", "quality": "standard"}
            for response in (client.post("/v1/videos", json=body, headers=headers), client.post("/v1/videos", files=[(name, (None, value)) for name, value in body.items()], headers=headers), client.get(original["poll_url"], headers=headers)):
                assert response.status_code in (200, 202), "installed_video_replay_or_poll"
                value = response.json()
                validate("control-video-response", value)
                assert (value["id"], value["seconds"], value["size"]) == (original["id"], "5.125", "1024x1024")
                assert "input" not in value and "progress" not in value and "error" not in value
            other = {"Authorization": "Bearer " + secrets[1][1]}
            assert client.get(original["poll_url"], headers=other).status_code == 404
            assert client.head(original["poll_url"] + "/content", headers=other).status_code == 404
            with sessions.begin() as session:
                key = session.get(ApiKey, key_id)
                key.policy = {**policy, "scopes": [scope for scope in policy["scopes"] if scope != "jobs:read"]}
                key.revision += 1
            assert client.get(original["poll_url"], headers=headers).status_code == 403
            replay = client.post("/v1/videos", json=body, headers=headers)
            assert replay.status_code == 202 and replay.json()["id"] == original["id"] and replay.json()["seconds"] == "5.125"
            cancelled = client.post("/v1/jobs/" + original["id"] + "/cancel", headers=headers)
            assert cancelled.status_code == 202 and cancelled.json()["status"] == "cancelled"
            with sessions.begin() as session:
                key = session.get(ApiKey, key_id)
                key.policy = policy
                key.revision += 1
            terminal = client.get(original["poll_url"], headers=headers)
            assert terminal.status_code == 200
            validate("control-video-response", terminal.json())
            assert terminal.json()["error"] == {"code": "task_cancelled", "message": "Generation was cancelled"}
            assert client.get(original["poll_url"] + "/content", headers=headers).status_code == 409
            with sessions.begin() as session:
                job_id = parse_id("job", original["id"])
                assert session.scalar(select(func.count()).select_from(Job).where(Job.key_id == key_id)) == 1
                entries = list(session.scalars(select(UsageLedger).where(UsageLedger.job_id == job_id)))
                assert len(entries) == 1 and entries[0].outcome == "cancelled" and entries[0].video_milliseconds == 0
                session.scalar(select(ApiCredential).where(ApiCredential.identifier == secrets[0][0])).revoked_at = utcnow()
            assert client.get(original["poll_url"], headers=headers).status_code == 401
        print(json.dumps({"passed": True, "exact_installed_modules": True, "https_tls_verified": True, "json_multipart_original_key_replay": True, "frozen_seconds": "5.125", "frozen_size": "1024x1024", "current_scope_and_revocation_enforced": True, "cross_key_status_and_head_denied": True, "safe_cancelled_error": True, "new_jobs": 1, "unique_cancelled_usage_entries": 1, "video_billed_milliseconds": 0, "production_comfyui_called": False, "real_gpu": False, "caller_owns_database_cleanup": True, "credentials_printed": False}))
    finally:
        sessions.kw["bind"].dispose()


if __name__ == "__main__":
    main()
