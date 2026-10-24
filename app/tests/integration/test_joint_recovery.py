"""Native PostgreSQL and physical files; synthetic media is not ComfyUI acceptance."""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from cryptography.fernet import Fernet
from generation_application.ports import BusinessIdentity
from generation_domain.errors import DomainError
from generation_domain.values import parse_id, utcnow
from generation_infrastructure.auth import AuthRepository
from generation_infrastructure.content import ContentRepository
from generation_infrastructure.jobs import JobRepository
from generation_infrastructure.leases import LeaseRepository
from generation_infrastructure.models import (
    AdminUser,
    ApiCredential,
    ApiKey,
    Artifact,
    Asset,
    Attempt,
    Channel,
    ChannelRevision,
    GpuOccupancy,
    Job,
    ManagementOperation,
    ObjectRecord,
    OutboxEvent,
    WorkerCommand,
)
from generation_infrastructure.restore_verification import (
    RecoveryCheckError,
    RecoveryLimits,
    create_checkpoint,
    load_checkpoint,
    load_recovery_key,
    verify_checkpoint,
    write_exclusive,
)
from generation_infrastructure.security import SecretVault, issue_secret, verifier
from generation_infrastructure.settings import Settings
from generation_infrastructure.storage import LocalDevelopmentStore
from PIL import Image
from psycopg import sql
from pydantic import SecretStr
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]
KEY = b"R" * 32


def options(settings: Settings, database: str | None = None) -> dict[str, Any]:
    url = make_url(settings.database_url.get_secret_value())
    return {"host": url.host, "port": url.port, "user": url.username, "password": url.password, "dbname": database or url.database, "connect_timeout": 5, **url.query}


@pytest.fixture
def recovery_source(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, tmp_path: Path, audio_wav: Path) -> dict[str, Any]:
    settings = test_settings.model_copy(update={"local_storage_root": tmp_path / "source-objects"})
    identity = catalog["identity"]
    vault = SecretVault(settings.encryption_key.get_secret_value())
    ciphertext = vault.encrypt("synthetic channel authorization")
    with pg.begin() as session:
        session.get(Channel, catalog["channel_ids"][0]).encrypted_auth = ciphertext
        session.add(ChannelRevision(channel_id=catalog["channel_ids"][0], revision=1, config={"encrypted_auth": ciphertext}))
    result = JobRepository(pg).create(identity, {"model": "synthetic-image", "task_type": "text_to_image", "input": {"prompt": "synthetic recovery test"}}, "synthetic-recovery-job", "synthetic-recovery-request")
    job_id = parse_id("job", result["id"])
    claim = LeaseRepository(pg, vault, allow_synthetic_catalog=True).claim(catalog["node_id"], catalog["boot_id"])
    assert claim is not None
    identifier, token = issue_secret("sk-gen")
    store = LocalDevelopmentStore(settings.local_storage_root)
    buffer = BytesIO()
    Image.new("RGB", (16, 16), "navy").save(buffer, format="PNG")
    image = buffer.getvalue()
    # The verifier checks durable bytes, not media decode/quality. The video
    # deliberately identifies itself as synthetic; actual codecs have separate tests.
    payloads = [(image, "image/png"), (image, "image/png"), (b"synthetic video recovery bytes", "video/mp4"), (audio_wav.read_bytes(), "audio/wav")]
    objects, contents = [], []
    with pg.begin() as session:
        session.get(Job, job_id).state = "reconciling"
        occupancy = session.get(GpuOccupancy, catalog["gpu_id"])
        assert occupancy is not None
        occupancy.uncertain = True
        attempt = session.get(Attempt, occupancy.attempt_id)
        attempt.channel_snapshot = {**attempt.channel_snapshot, "encrypted_auth": ciphertext}
        key = session.get(ApiKey, identity.key_id)
        key.policy = {**key.policy, "scopes": [*key.policy["scopes"], "content:sensitive"]}
        session.add(ApiCredential(key_id=key.id, identifier=identifier, verifier=verifier(token, settings.credential_pepper.get_secret_value()), pepper_version=settings.credential_pepper_version))
        user = AdminUser(username="synthetic-recovery", password_hash="unused synthetic hash", role="administrator")
        session.add(user)
        session.flush()
        operation = ManagementOperation(actor_id=user.id, command="probe_channel", state="pending")
        session.add(operation)
        session.flush()
        session.add(WorkerCommand(operation_id=operation.id, node_id=catalog["node_id"], channel_id=catalog["channel_ids"][0], command="probe_channel", payload={}, authorization_snapshot=ciphertext))
        for index, (data, mime) in enumerate(payloads):
            stored = store.put(f"recovery/synthetic/{index}", BytesIO(data), mime, 1024**2)
            obj = ObjectRecord(tenant_id=identity.tenant_id, project_id=identity.project_id, object_key=stored.key, version_id=stored.version, bytes=stored.bytes, sha256=stored.sha256, media_type=mime, expires_at=utcnow() + timedelta(days=1), verified_at=utcnow())
            session.add(obj)
            session.flush()
            content = Asset(tenant_id=identity.tenant_id, project_id=identity.project_id, key_id=identity.key_id, object_id=obj.id) if index == 0 else Artifact(tenant_id=identity.tenant_id, project_id=identity.project_id, key_id=identity.key_id, object_id=obj.id, job_id=job_id, position=index-1)
            session.add(content)
            session.flush()
            objects.append(obj)
            contents.append(content.id)
        outbox_count = session.scalar(select(func.count()).select_from(OutboxEvent))
    return {"settings": settings, "store": store, "token": token, "objects": objects, "contents": contents, "payloads": payloads, "job_id": job_id, "outbox": outbox_count, "identity": identity}


def checkpoint(source: dict[str, Any], limits: RecoveryLimits = RecoveryLimits()) -> Any:
    with psycopg.connect(**options(source["settings"])) as connection:
        return create_checkpoint(connection, source["settings"], source["store"], KEY, limits)


def test_native_archive_files_and_reloaded_secrets_restore_together(recovery_source: dict[str, Any], pg: sessionmaker[Session], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = recovery_source
    settings, store = source["settings"], source["store"]
    database = "render_joint_test_" + uuid4().hex[:12]
    archive = tmp_path / "source.dump"
    config, key_file, checkpoint_file = (tmp_path / name for name in ("restored.env", "recovery.key", "checkpoint.json"))
    tools = ROOT / ".local/toolchains/postgres-18.6/pgsql/bin"
    child_env = {**os.environ, "PGPASSWORD": options(settings)["password"] or ""}
    restored_store = LocalDevelopmentStore(tmp_path / "restored-objects")
    engine = None
    created = False
    with psycopg.connect(**options(settings, "postgres"), autocommit=True) as admin:
        try:
            with psycopg.connect(**options(settings)) as connection:
                connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                snapshot = connection.execute("SELECT pg_export_snapshot()").fetchone()[0]
                original = create_checkpoint(connection, settings, store, KEY)
                dsn = {k: v for k, v in options(settings).items() if k != "password" and v is not None}
                subprocess.run([str(tools / "pg_dump.exe"), "--format=custom", "--snapshot=" + snapshot, "--file=" + str(archive), "--dbname=" + psycopg.conninfo.make_conninfo(**dsn), "--no-password"], env=child_env, capture_output=True, check=True, timeout=30)  # noqa: S603 -- owned isolated test database, fixed native tool
            assert original.objects.records == 4 and original.secrets.encrypted_values == 4
            assert original.objects.media.model_dump() == {"images": 2, "videos": 1, "audio": 1, "other": 0}
            for obj in source["objects"]:
                destination = restored_store._path(obj.object_key, obj.version_id)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(store._path(obj.object_key, obj.version_id), destination)
            admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(database)))
            created = True
            subprocess.run([str(tools / "pg_restore.exe"), "--single-transaction", "--exit-on-error", "--no-owner", "--no-privileges", "--dbname=" + psycopg.conninfo.make_conninfo(**{**dsn, "dbname": database}), "--no-password", str(archive)], env=child_env, capture_output=True, check=True, timeout=30)  # noqa: S603 -- freshly tracked disposable database only
            values = {"GEN_ENVIRONMENT": "test", "GEN_DATABASE_URL": make_url(settings.database_url.get_secret_value()).set(database=database).render_as_string(hide_password=False), "GEN_CREDENTIAL_PEPPER": settings.credential_pepper.get_secret_value(), "GEN_CREDENTIAL_PEPPER_VERSION": str(settings.credential_pepper_version), "GEN_ENCRYPTION_KEY": settings.encryption_key.get_secret_value(), "GEN_PUBLIC_ORIGIN": settings.public_origin, "GEN_STORAGE_BACKEND": "local_development", "GEN_LOCAL_STORAGE_ROOT": restored_store.root.as_posix(), "GEN_SPOOL_ROOT": (tmp_path / "spool").as_posix()}
            write_exclusive(config, "".join(name + "=" + json.dumps(value) + "\n" for name, value in values.items()).encode())
            write_exclusive(key_file, base64.urlsafe_b64encode(KEY) + b"\n")
            write_exclusive(checkpoint_file, json.dumps(original.model_dump()).encode())
            for name in list(os.environ):
                if name.upper().startswith("GEN_"):
                    monkeypatch.delenv(name)
            restored_settings = Settings(_env_file=config)
            reloaded_key = load_recovery_key(key_file)
            expected = load_checkpoint(checkpoint_file, reloaded_key)
            with psycopg.connect(**options(restored_settings)) as connection:
                verification = verify_checkpoint(connection, restored_settings, restored_store, reloaded_key, expected)
                assert verification["passed"] and verification["database_read_only"]
                assert verification["objects"] == 4 and verification["reconciliation_required_before_dispatch"]
                # The verifier itself must be byte-for-byte read-only over all rows.
                assert create_checkpoint(connection, restored_settings, restored_store, reloaded_key).objects == original.objects
                assert verify_checkpoint(connection, restored_settings, restored_store, reloaded_key, expected)["passed"]
            engine = create_engine(restored_settings.database_url.get_secret_value())
            sessions = sessionmaker(engine, expire_on_commit=False)
            with sessions() as session:
                assert session.get(Job, source["job_id"]).state == "reconciling"
                assert session.scalar(select(GpuOccupancy)).uncertain is True
                assert session.scalar(select(func.count()).select_from(OutboxEvent)) == source["outbox"]
            identity = AuthRepository(sessions, restored_settings).business(source["token"], "127.0.0.1")
            content = ContentRepository(sessions, restored_store)
            for index, identifier in enumerate(source["contents"]):
                download = content.download(identity, identifier, "bytes=0-7", asset=index == 0)
                assert download.status == 206 and b"".join(download.data) == source["payloads"][index][0][:8]
                head = content.download(identity, identifier, None, asset=index == 0, head=True)
                assert not b"".join(head.data) and int(head.headers["Content-Length"]) == len(source["payloads"][index][0])
                other = BusinessIdentity(identity.tenant_id, identity.project_id, uuid4(), identity.policy)
                with pytest.raises(DomainError):
                    content.download(other, identifier, None, asset=index == 0)
            with sessions.begin() as session:
                credential = session.scalar(select(ApiCredential))
                credential.revoked_at = utcnow()
            with pytest.raises(DomainError, match="API Key"):
                AuthRepository(sessions, restored_settings).business(source["token"], "127.0.0.1")
        finally:
            if engine is not None:
                engine.dispose()
            if created:
                assert database.startswith("render_joint_test_")
                admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database)))
            for path in (config, key_file, checkpoint_file, archive):
                path.unlink(missing_ok=True)
    assert not any(path.exists() for path in (config, key_file, checkpoint_file, archive))


@pytest.mark.parametrize("fault", ["same_count_row", "file_missing", "file_hash", "file_appended", "master", "pepper", "pepper_version", "recovery_key", "signature"])
def test_joint_recovery_detects_content_and_key_failures(recovery_source: dict[str, Any], pg: sessionmaker[Session], fault: str) -> None:
    source = recovery_source
    expected = checkpoint(source)
    settings, key, store = source["settings"], KEY, source["store"]
    expected_code = "recovery_database_mismatch"
    if fault == "same_count_row":
        with pg.begin() as session:
            row = session.get(Job, source["job_id"])
            row.request = {**row.request, "synthetic_tampered": True}
    elif fault.startswith("file_"):
        obj = source["objects"][-1]
        path = store._path(obj.object_key, obj.version_id)
        if fault == "file_missing":
            path.unlink()
        elif fault == "file_appended":
            path.write_bytes(path.read_bytes() + b"extra")
        else:
            data = path.read_bytes()
            path.write_bytes(bytes([data[0] ^ 255]) + data[1:])
        expected_code = "recovery_object_(unavailable|mismatch)"
    elif fault == "master":
        settings = settings.model_copy(update={"encryption_key": SecretStr(Fernet.generate_key().decode())})
        expected_code = "recovery_runtime_key_mismatch"
    elif fault == "pepper":
        settings = settings.model_copy(update={"credential_pepper": SecretStr("wrong synthetic pepper with enough length")})
        expected_code = "recovery_runtime_key_mismatch"
    elif fault == "pepper_version":
        settings = settings.model_copy(update={"credential_pepper_version": settings.credential_pepper_version + 1})
        expected_code = "recovery_runtime_key_mismatch"
    elif fault == "recovery_key":
        key, expected_code = b"x" * 32, "recovery_signature_invalid"
    else:
        expected = expected.model_copy(update={"signature": "0" * 64})
        expected_code = "recovery_signature_invalid"
    with psycopg.connect(**options(settings)) as connection, pytest.raises(RecoveryCheckError, match=expected_code):
        verify_checkpoint(connection, settings, store, key, expected)


@pytest.mark.parametrize("limit", [RecoveryLimits(rows=1), RecoveryLimits(row_bytes=10), RecoveryLimits(objects=1), RecoveryLimits(object_bytes=1)])
def test_database_and_object_bounds(recovery_source: dict[str, Any], limit: RecoveryLimits) -> None:
    with pytest.raises(RecoveryCheckError, match="recovery_(database|object)_limit_exceeded"):
        checkpoint(recovery_source, limit)


def test_readwrite_transaction_is_rejected(recovery_source: dict[str, Any]) -> None:
    source = recovery_source
    with psycopg.connect(**options(source["settings"])) as connection:
        connection.execute("SELECT 1")
        with pytest.raises(RecoveryCheckError, match="recovery_readonly_snapshot_required"):
            create_checkpoint(connection, source["settings"], source["store"], KEY)


@pytest.mark.parametrize("location", ["channels", "channel_revisions", "attempts", "worker_commands"])
def test_each_current_historic_and_inflight_ciphertext_is_checked(recovery_source: dict[str, Any], location: str) -> None:
    source = recovery_source
    queries = {"channels": "UPDATE channels SET encrypted_auth='invalid' WHERE encrypted_auth IS NOT NULL", "channel_revisions": "UPDATE channel_revisions SET config=jsonb_set(config,'{encrypted_auth}','\"invalid\"')", "attempts": "UPDATE attempts SET channel_snapshot=jsonb_set(channel_snapshot,'{encrypted_auth}','\"invalid\"')", "worker_commands": "UPDATE worker_commands SET authorization_snapshot='invalid'"}
    with psycopg.connect(**options(source["settings"])) as connection:
        connection.execute(queries[location])
    with pytest.raises(RecoveryCheckError, match="recovery_secret_invalid"):
        checkpoint(source)
