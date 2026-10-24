"""Record the real Admin against a disposable, explicitly synthetic native database."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import secrets
import ssl
import subprocess
import sys
import traceback
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import psycopg
from cryptography.fernet import Fernet
from generation_domain.values import fingerprint, public_id, utcnow
from generation_infrastructure.models import (
    AdminUser,
    ApiKey,
    Artifact,
    Channel,
    Job,
    Node,
    ObjectRecord,
    Project,
    PublicModel,
    Tenant,
    Workflow,
    WorkflowRelease,
)
from generation_infrastructure.security import password_hash
from generation_infrastructure.settings import Settings
from generation_infrastructure.storage import LocalDevelopmentStore
from PIL import Image, ImageDraw
from psycopg import sql
from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

ROOT = Path(__file__).resolve().parents[1]
MEDIA = ROOT.parent / "docs/media"


def module(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError("capture_helper_missing")
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def seed(sessions: sessionmaker[Session], fixtures: Any, pepper: str, password: str, store: LocalDevelopmentStore) -> str:
    fixtures.seed_control(sessions, pepper, "http://127.0.0.1:18188", "0" * 40, {"keys": [1, 2], "user": 1})
    now = utcnow()
    with sessions.begin() as session:
        tenant = session.scalar(select(Tenant))
        project = session.scalar(select(Project))
        node = session.scalar(select(Node))
        key = session.scalar(select(ApiKey))
        channel = session.scalar(select(Channel))
        assert tenant and project and node and key and channel
        tenant.name = "DEMO · 合成演示租户"
        project.name = "图片 · 视频 · 音频演示"
        node.name = "DEMO Windows 01 · Synthetic"
        channel.name = "DEMO ComfyUI 01 · 合成渠道"
        for index, item in enumerate(session.scalars(select(ApiKey))):
            item.name = ("DEMO 内容应用", "DEMO 工作室客户端")[index]
        models = list(session.scalars(select(PublicModel)))
        for index, model in enumerate(models):
            model.alias = "demo-" + model.task_type.replace("_", "-")
            workflow = session.get(Workflow, session.get(WorkflowRelease, model.release_id).workflow_id)
            assert workflow
            workflow.name = {"text_to_image": "文生图", "image_to_image": "图生图", "references_to_image": "多参考图生图", "text_to_video": "文生视频", "references_to_video": "参考图生视频", "text_to_audio": "音频生成"}[model.task_type] + " · DEMO"
            workflow.draft = fixtures.manifest(model.task_type, "0" * 40)
            for offset in range(2):
                request = {"model": model.alias, "task_type": model.task_type, "input": {"prompt": "Synthetic documentation demo"}, "parameters": model.defaults}
                session.add(Job(tenant_id=tenant.id, project_id=project.id, key_id=key.id, model_alias=model.alias, task_type=model.task_type, state="queued", release_id=model.release_id, request=request, execution={"seed": index + offset}, request_fingerprint=fingerprint(request), execution_fingerprint=fingerprint({"demo": index, "offset": offset}), eligible_channels=[public_id("ch", channel.id)], accepted_at=now - timedelta(minutes=2 + index), deadline=now + timedelta(days=1), accepted_period=now.date().isoformat()))
        audio = fixtures.manifest("text_to_audio", "0" * 40)
        lab = Workflow(tenant_id=tenant.id, project_id=project.id, name="音频实验 · DEMO", task_type="text_to_audio", draft=audio)
        session.add(lab)
        session.flush()
        for version in (1, 2, 3):
            session.add(WorkflowRelease(tenant_id=tenant.id, project_id=project.id, workflow_id=lab.id, version=version, digest=fingerprint(audio), manifest=audio, validated=True, validation={"synthetic_documentation_only": True}))
        image_model = next(model for model in models if model.task_type == "text_to_image")
        for index, color in enumerate(("#087f8c", "#304f73", "#a16f45")):
            picture = Image.new("RGB", (800, 480), "#edf3f4")
            drawing = ImageDraw.Draw(picture)
            drawing.rounded_rectangle((65, 70, 735, 405), radius=32, fill=color)
            drawing.ellipse((490, 105, 685, 300), fill="#cde2de")
            drawing.polygon([(95, 365), (275, 170), (440, 365)], fill="#8ab2b4")
            drawing.text((100, 110), "SYNTHETIC DEMO / WORKFLOW OUTPUT", fill="white")
            stream = BytesIO()
            picture.save(stream, format="PNG")
            stream.seek(0)
            stored = store.put("readme-demo/" + uuid4().hex + ".png", stream, "image/png", 1024 * 1024)
            obj = ObjectRecord(tenant_id=tenant.id, project_id=project.id, object_key=stored.key, version_id=stored.version, bytes=stored.bytes, sha256=stored.sha256, media_type="image/png", verified_at=now, metadata_json={"width": 800, "height": 480}, expires_at=now + timedelta(days=7))
            job = Job(tenant_id=tenant.id, project_id=project.id, key_id=key.id, model_alias=image_model.alias, task_type="text_to_image", state="succeeded", release_id=image_model.release_id, request={"model": image_model.alias, "task_type": "text_to_image", "input": {"prompt": "Synthetic documentation illustration"}}, execution={"seed": index}, request_fingerprint=fingerprint({"demo_result": index}), execution_fingerprint=fingerprint({"demo_result": index}), eligible_channels=[public_id("ch", channel.id)], accepted_at=now - timedelta(minutes=5), terminal_at=now, deadline=now + timedelta(days=1), accepted_period=now.date().isoformat())
            session.add_all([obj, job])
            session.flush()
            session.add(Artifact(tenant_id=tenant.id, project_id=project.id, key_id=key.id, job_id=job.id, object_id=obj.id, position=0, tags=["synthetic-demo", "readme"]))
        session.add(AdminUser(username="demo-admin", password_hash=password_hash(password), role="administrator"))
        return public_id("wf", lab.id)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser-python", type=Path, default=Path(sys.base_prefix) / "python.exe")
    args = parser.parse_args()
    settings = Settings()
    url = make_url(settings.database_url.get_secret_value())
    if (url.host, url.port, url.database) != ("127.0.0.1", 55438, "render_control"):
        raise SystemExit("Capture requires the owned native development PostgreSQL instance")
    native = module("readme_owned_processes", "test-sub2api-native.py")
    fixtures = module("readme_synthetic_catalog", "native-provider-fixtures.py")
    run = uuid4().hex[:12]
    private = ROOT / ".local/readme-media" / run
    private.mkdir(parents=True)
    MEDIA.mkdir(parents=True, exist_ok=True)
    database = "render_readme_" + run
    origin = "https://127.0.0.1:" + str(native.port())
    pepper, password = secrets.token_urlsafe(48), secrets.token_urlsafe(24)
    env = {**os.environ, "PYTHONUTF8": "1", "GEN_ENVIRONMENT": "test", "GEN_DATABASE_URL": url.set(database=database).render_as_string(hide_password=False), "GEN_CREDENTIAL_PEPPER": pepper, "GEN_ENCRYPTION_KEY": Fernet.generate_key().decode(), "GEN_STORAGE_BACKEND": "local_development", "GEN_LOCAL_STORAGE_ROOT": str(private / "objects"), "GEN_SPOOL_ROOT": str(private / "spool"), "GEN_PUBLIC_ORIGIN": origin}
    env.pop("GEN_AUDIT_RETENTION_DAYS", None)
    owned = native.Processes(private)
    report: dict[str, Any] = {"passed": False, "synthetic_data": True, "real_comfyui": False, "gpu_generation_recorded": False, "cleanup": {"processes": False, "database": False, "credentials": False}}
    created, engine = False, None
    try:
        with psycopg.connect(host=url.host, port=url.port, user=url.username, password=url.password, dbname="postgres", autocommit=True) as admin:
            try:
                admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
                created = True
                with (private / "migration.log").open("wb") as log:
                    migration = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=120, creationflags=subprocess.CREATE_NO_WINDOW)  # noqa: S603 -- fixed owned migration
                if migration.returncode:
                    raise RuntimeError("demo_migration_failed")
                engine = create_engine(env["GEN_DATABASE_URL"])
                workflow = seed(sessionmaker(engine, expire_on_commit=False), fixtures, pepper, password, LocalDevelopmentStore(private / "objects"))
                process = owned.start("readme-api", [sys.executable, "-m", "uvicorn", "control_api.main:create_app", "--factory", "--host", "127.0.0.1", "--port", origin.rsplit(":", 1)[1], "--ssl-keyfile", str(ROOT / ".local/tls/localhost.key"), "--ssl-certfile", str(ROOT / ".local/tls/localhost.crt"), "--no-access-log", "--no-proxy-headers"], env)
                with httpx.Client(base_url=origin, verify=ssl.create_default_context(cafile=str(ROOT / ".local/tls/localhost.crt")), trust_env=False) as http:
                    native.wait_http(http, "/health/ready", process)
                fixture = private / "capture.secret.json"
                fixture.write_text(json.dumps({"origin": origin, "password": password, "workflow": workflow, "media": str(MEDIA), "private": str(private)}), encoding="utf-8")
                browser_env = {name: value for name, value in os.environ.items() if not name.upper().startswith(("GEN_", "PYTHON"))}
                with (private / "browser.log").open("wb") as log:
                    browser = subprocess.run([str(args.browser_python.resolve(strict=True)), "-X", "utf8", str(ROOT / "scripts/record-readme-browser.py"), "--fixture", str(fixture)], cwd=ROOT, env=browser_env, stdout=log, stderr=subprocess.STDOUT, timeout=300, creationflags=subprocess.CREATE_NO_WINDOW)  # noqa: S603 -- owned recorder and private fixture
                if browser.returncode:
                    raise RuntimeError("demo_browser_failed_inspect_private_log")
                report.update(json.loads((MEDIA / "capture.json").read_text(encoding="utf-8")))
            finally:
                owned.close()
                report["cleanup"]["processes"] = all(item["stopped"] for item in owned.stops)
                if engine is not None:
                    engine.dispose()
                (private / "capture.secret.json").unlink(missing_ok=True)
                report["cleanup"]["credentials"] = not (private / "capture.secret.json").exists()
                if created:
                    admin.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s AND pid<>pg_backend_pid()", (database,))
                    admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(database)))
                report["cleanup"]["database"] = True
    except Exception as error:
        report["passed"] = False
        report["error_class"] = type(error).__name__
        (private / "failure.trace.txt").write_text("".join(traceback.format_exception(error)), encoding="utf-8")
        frame = traceback.extract_tb(error.__traceback__)[-1]
        report["error_location"] = Path(frame.filename).name + ":" + str(frame.lineno)
    target = ROOT.parent / "docs/evidence/2026-10-25/readme-media-capture.json"
    if target.exists():
        target.with_name("readme-media-capture-previous-" + run + ".json").write_bytes(target.read_bytes())
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "cleanup": report["cleanup"], "private_run": run}))
    raise SystemExit(0 if report["passed"] and all(report["cleanup"].values()) else 1)


if __name__ == "__main__":
    main()
