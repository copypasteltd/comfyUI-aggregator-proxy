"""Create/inspect/clean a uniquely owned synthetic catalog in the native dev DB."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

from generation_contracts.defaults import default_key_policy, default_limits
from generation_domain.values import fingerprint, public_id, utcnow
from generation_infrastructure.content import ContentRepository
from generation_infrastructure.models import (
    AdminMutation,
    ApiKey,
    Artifact,
    Asset,
    AuditEvent,
    Job,
    JobAssetReference,
    ObjectRecord,
    OutboxEvent,
    Project,
    PublicModel,
    Tenant,
    Workflow,
    WorkflowRelease,
)
from generation_infrastructure.settings import Settings
from generation_infrastructure.storage import LocalDevelopmentStore
from PIL import Image
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / ".local/content-browser-fixture.json"


def environment() -> tuple[Settings, sessionmaker[Session]]:
    settings = Settings()
    url = make_url(settings.database_url.get_secret_value())
    if (url.host, url.port, url.database, settings.storage_backend) != ("127.0.0.1", 55438, "render_control", "local_development"):
        raise RuntimeError("Fixture requires the project-native development database and local store")
    return settings, sessionmaker(create_engine(url, pool_pre_ping=True), expire_on_commit=False)


def save(value: dict) -> None:
    MANIFEST.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def cleanup(value: dict) -> dict:
    run = value["run_id"]
    if len(run) != 32 or any(c not in "0123456789abcdef" for c in run):
        raise RuntimeError("Invalid owned fixture identity")
    settings, sessions = environment()
    owner = UUID(value["tenant_uuid"])
    resources = [UUID(identifier) for identifier in value.get("resource_uuids", [])]
    with sessions.begin() as session:
        tenant = session.get(Tenant, owner)
        if tenant and tenant.name != "48 浏览器合成租户 " + run:
            raise RuntimeError("Fixture owner no longer matches")
        if tenant:
            session.execute(delete(AdminMutation).where(AdminMutation.operation.in_([operation + ":" + str(identifier) for operation in ("content.tags", "content.delete") for identifier in resources])))
            session.execute(delete(AuditEvent).where(AuditEvent.resource.in_([str(identifier) for identifier in resources])))
            session.execute(delete(OutboxEvent).where(OutboxEvent.aggregate_id.in_(resources)))
            for entity in (JobAssetReference, Artifact, Asset, Job, ObjectRecord, PublicModel, WorkflowRelease, Workflow, ApiKey, Project):
                session.execute(delete(entity).where(entity.tenant_id == owner))
            session.delete(tenant)
    store = LocalDevelopmentStore(settings.local_storage_root)
    for obj in value.get("objects", []):
        if not obj["key"].startswith("synthetic-content-browser/" + run + "/"):
            raise RuntimeError("Object outside fixture ownership")
        store.delete(obj["key"], obj["version"])
    directory = (ROOT / ".local/content-browser" / run).resolve()
    if not directory.is_relative_to((ROOT / ".local/content-browser").resolve()):
        raise RuntimeError("Unsafe fixture directory")
    if directory.exists():
        shutil.rmtree(directory)
    with sessions() as session:
        if session.get(Tenant, owner):
            raise RuntimeError("Fixture tenant cleanup failed")
    value["cleaned"] = True
    save(value)
    return {"cleaned": True, "synthetic_tenant_removed": True, "private_media_removed": True}


def seed() -> dict:
    if MANIFEST.exists() and not json.loads(MANIFEST.read_text(encoding="utf-8")).get("cleaned"):
        raise RuntimeError("Clean the previous fixture before creating another")
    settings, sessions = environment()
    run, tenant_id = uuid4().hex, uuid4()
    value: dict = {"run_id": run, "tenant_uuid": str(tenant_id), "resource_uuids": [], "objects": [], "cleaned": False}
    save(value)
    directory = ROOT / ".local/content-browser" / run
    directory.mkdir(parents=True)
    now = utcnow()
    try:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("A native ffmpeg executable is required for the synthetic preview video")
        video_path = directory / "synthetic.webm"
        subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=teal:s=96x64:r=8", "-t", "0.5", "-c:v", "libvpx-vp9", "-an", str(video_path)], check=True, timeout=30, capture_output=True)  # noqa: S603 -- discovered native executable and fixed synthetic generator
        png = BytesIO()
        Image.new("RGB", (24, 24), "teal").save(png, format="PNG")
        store = LocalDevelopmentStore(settings.local_storage_root)
        with sessions.begin() as session:
            session.add(Tenant(id=tenant_id, name="48 浏览器合成租户 " + run, limits=default_limits()))
            session.flush()
            session.add(project := Project(tenant_id=tenant_id, name="48 浏览器项目 " + run[:8], limits=default_limits()))
            session.flush()
            keys = [ApiKey(tenant_id=tenant_id, project_id=project.id, name=f"48 浏览器密钥 {run[:8]} {index:02}", policy=default_key_policy()) for index in range(53)]
            session.add_all(keys)
            session.add(workflow := Workflow(tenant_id=tenant_id, project_id=project.id, name="48 浏览器 Workflow " + run[:8], task_type="text_to_image", draft={}))
            session.flush()
            manifest = {"task_type": "text_to_image", "workflow_api": {"1": {"class_type": "SyntheticOutput", "inputs": {}}}, "supported_parameters": []}
            session.add(release := WorkflowRelease(tenant_id=tenant_id, project_id=project.id, workflow_id=workflow.id, version=1, digest=fingerprint(manifest), manifest=manifest, validated=True, validation={"test_only": True}))
            session.flush()
            alias = "content-browser-model-" + run[:8]
            session.add(PublicModel(tenant_id=tenant_id, project_id=project.id, alias=alias, task_type="text_to_image", release_id=release.id, channel_ids=[], defaults={}, published=False))
            session.add(job := Job(tenant_id=tenant_id, project_id=project.id, key_id=keys[0].id, release_id=release.id, model_alias=alias, task_type="text_to_image", state="succeeded", request={"input": {"prompt": "Synthetic content browser fixture"}}, execution={}, request_fingerprint="a" * 64, execution_fingerprint="b" * 64, eligible_channels=[], accepted_at=now, deadline=now + timedelta(days=1), accepted_period=now.date().isoformat()))
            session.flush()
            ids = {}
            for index in range(57):
                is_video, is_input = index == 1, index == 56
                media_type = "video/webm" if is_video else "image/svg+xml" if index == 55 else "image/png"
                source = BytesIO(video_path.read_bytes() if is_video else png.getvalue())
                stored = store.put(f"synthetic-content-browser/{run}/{index}", source, media_type, 1024 * 1024)
                value["objects"].append({"key": stored.key, "version": stored.version})
                save(value)
                obj = ObjectRecord(tenant_id=tenant_id, project_id=project.id, object_key=stored.key, version_id=stored.version, bytes=stored.bytes, sha256=stored.sha256, media_type=media_type, verified_at=None if index == 54 else now, expires_at=now + timedelta(days=1) if index != 53 else now - timedelta(seconds=1), metadata_json={"width": 96 if is_video else 24, "height": 64 if is_video else 24})
                session.add(obj)
                session.flush()
                entity = Asset if is_input else Artifact
                fields = {} if is_input else {"job_id": job.id, "position": index}
                row = entity(tenant_id=tenant_id, project_id=project.id, key_id=keys[0].id, object_id=obj.id, created_at=now - timedelta(seconds=index), tags=["48 初始标签"] if index == 0 else ["48 分页"], **fields)
                session.add(row)
                session.flush()
                value["resource_uuids"].append(str(row.id))
                if index in (0, 1, 53, 54, 55, 56):
                    ids[{0: "image", 1: "video", 53: "expired", 54: "unverified", 55: "unsupported", 56: "input"}[index]] = public_id("asset" if is_input else "content", row.id)
                if is_input:
                    session.add(JobAssetReference(tenant_id=tenant_id, project_id=project.id, key_id=keys[0].id, job_id=job.id, asset_id=row.id))
            value.update({"ids": ids, "tenant": public_id("ten", tenant_id), "project": public_id("prj", project.id), "key": public_id("key", keys[0].id), "workflow": public_id("wf", workflow.id), "release": public_id("wfr", release.id), "job": public_id("job", job.id), "model": alias, "labels": {"tenant": "48 浏览器合成租户 " + run, "project": project.name, "key": keys[0].name, "workflow": workflow.name, "release": workflow.name + " · v1", "model": alias}})
        save(value)
        return {"seeded": True, "outputs": 56, "inputs": 1, "keys": 53, "real_generated_synthetic_video": True}
    except BaseException:
        cleanup(value)
        raise


def inspect(value: dict) -> dict:
    settings, sessions = environment()
    repository = ContentRepository(sessions, LocalDevelopmentStore(settings.local_storage_root))
    image = UUID(value["ids"]["image"].removeprefix("content_"))
    with sessions() as session:
        audits = session.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "content.tags_changed", AuditEvent.resource == str(image)))
    return {"metadata": repository.admin_metadata(image), "tag_audits": audits}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("seed", "inspect", "cleanup"))
    action = parser.parse_args().action
    value = {} if action == "seed" else json.loads(MANIFEST.read_text(encoding="utf-8"))
    print(json.dumps(seed() if action == "seed" else inspect(value) if action == "inspect" else cleanup(value), ensure_ascii=False))
