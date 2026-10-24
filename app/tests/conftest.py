from __future__ import annotations

import math
import os
import struct
import wave
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from generation_application.ports import BusinessIdentity
from generation_contracts.defaults import default_key_policy, default_limits
from generation_domain.values import fingerprint, public_id, utcnow
from generation_infrastructure.models import (
    ApiKey,
    Base,
    Channel,
    Gpu,
    Job,
    Node,
    Project,
    PublicModel,
    Tenant,
    Workflow,
    WorkflowBinding,
    WorkflowRelease,
)
from generation_infrastructure.settings import Settings
from pydantic import SecretStr
from sqlalchemy import case, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def audio_manifest() -> dict[str, Any]:
    return {"format_version": "1.1", "task_type": "text_to_audio", "workflow_api": {"1": {"class_type": "SyntheticAudio", "inputs": {"text": "", "seed": 0}}, "2": {"class_type": "SaveAudio", "inputs": {"audio": ["1", 0], "filename_prefix": ""}}}, "bindings": [{"source": "prompt", "node_id": "1", "input_name": "text"}, {"source": "seed", "node_id": "1", "input_name": "seed"}, {"source": "output_prefix", "node_id": "2", "input_name": "filename_prefix"}], "output": {"adapter": "audio-files-v1", "final_node_ids": ["2"], "history_field": "audio", "expected_count": 1, "max_bytes": 1048576, "mime_types": ["audio/wav"]}, "presets": [{"quality": "standard", "seconds": 2, "audio_format": "wav", "sample_rate": 24000, "channels": 1, "values": {}}], "dependency_lock": {"comfy_commit": "a" * 40, "models": [], "custom_nodes": []}, "runtime_profiles": [], "supported_parameters": ["quality", "seconds", "seed", "n"]}


@pytest.fixture
def audio_wav(tmp_path: Path) -> Path:
    path = tmp_path / "synthetic.wav"
    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, 24000, 12000, "NONE", "not compressed"))
        output.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(i * 2 * math.pi * 440 / 24000))) for i in range(12000)))
    return path


def capacity_sample(**overrides: Any) -> dict[str, Any]:
    """Explicit synthetic healthy resource budget, never a real host measurement."""
    from generation_contracts.capacity import WorkerCapacity
    value = {"protocol": "native-capacity-v1", "sequence": 1, "observed_ago_ms": 0,
             "free_disk_bytes": 100 * 1024**3, "available_memory_bytes": 8 * 1024**3, "spool_bytes": 0,
             "reserved_bytes": 0, "pending_uploads": 0, "minimum_free_disk_bytes": 5 * 1024**3,
             "minimum_available_memory_bytes": 1024**3, "maximum_spool_bytes": 20 * 1024**3,
             "generation_reserve_bytes": 2 * 1024**3, "maximum_uploads": 2, **overrides}
    reasons = WorkerCapacity.model_construct(**value).derived_reasons()
    return WorkerCapacity.model_validate({**value, "reasons": reasons, "status": "unavailable" if reasons == ["observation_unavailable"] else "blocked" if reasons else "ready"}).model_dump()


@pytest.fixture(scope="session")
def test_settings(tmp_path_factory: pytest.TempPathFactory) -> Settings:
    settings = Settings()
    url = make_url(settings.database_url.get_secret_value()).set(database="render_control_test")
    if url.host != "127.0.0.1" or url.port != 55438 or url.database != "render_control_test":
        raise RuntimeError("Tests require the isolated project-native PostgreSQL database")
    return settings.model_copy(update={"database_url": SecretStr(url.render_as_string(hide_password=False)), "environment": "test", "local_storage_root": tmp_path_factory.mktemp("private-objects")})


@pytest.fixture(scope="session")
def pg_factory(test_settings: Settings) -> Iterator[sessionmaker[Session]]:
    variable = "GEN_DATABASE_URL"
    original = os.environ.get(variable)
    try:
        os.environ[variable] = test_settings.database_url.get_secret_value()
        command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    finally:
        if original is None:
            os.environ.pop(variable, None)
        else:
            os.environ[variable] = original
    engine = create_engine(test_settings.database_url.get_secret_value(), pool_size=20, max_overflow=5, pool_pre_ping=True, connect_args={"connect_timeout": 3, "options": "-c lock_timeout=15000 -c statement_timeout=30000"})
    yield sessionmaker(engine, expire_on_commit=False)
    engine.dispose()


def reset_test_database(sessions: sessionmaker[Session]) -> None:
    # Keep real transactions/constraints while avoiding thousands of replaced
    # relation files and checkpoint fsync requests from per-test TRUNCATE.
    with sessions.begin() as session:
        identity = session.execute(text("SELECT current_database(),host(inet_server_addr()),current_setting('port')")).one()
        if tuple(identity) != ("render_control_test", "127.0.0.1", "55438"):
            raise RuntimeError("Refusing test cleanup outside the exact native test database")
        if session.scalar(text("SELECT tgenabled FROM pg_trigger WHERE tgrelid='gateway_request_fences'::regclass AND tgname='preserve_gateway_request_fence' AND NOT tgisinternal")) != "O":
            raise RuntimeError("Test cleanup requires the enabled gateway fence guard")
        # This one immutable-row trigger also prohibits deliberate fixture
        # cleanup. Its transactional ALTER lock isolates this owned reset;
        # failures roll the ALTER back. FK/CHECK triggers remain enabled.
        session.execute(text("ALTER TABLE gateway_request_fences DISABLE TRIGGER preserve_gateway_request_fence"))
        # Break the two nullable Job -> Attempt cycles only inside this owned
        # test reset. CHECK constraints and every foreign key remain active.
        session.execute(Job.__table__.update().values(current_attempt_id=None, retry_count=0, retry_after=None, retry_error_code=None, retry_source_attempt_id=None, state=case((Job.state == "retry_wait", "queued"), else_=Job.state)))
        for table in reversed(Base.metadata.sorted_tables):
            session.execute(table.delete())
        session.execute(text("ALTER TABLE gateway_request_fences ENABLE TRIGGER preserve_gateway_request_fence"))


@pytest.fixture
def pg(pg_factory: sessionmaker[Session]) -> Iterator[sessionmaker[Session]]:
    reset_test_database(pg_factory)
    yield pg_factory


@pytest.fixture
def catalog(pg: sessionmaker[Session]) -> dict[str, Any]:
    """Explicit synthetic catalog for protocol tests; no production publication evidence."""
    # Protocol/confirmation races must not accidentally test default rate gates.
    # Dedicated hierarchy/rate cases lower these budgets explicitly.
    limits = {**default_limits(), "rpm": {"mode": "bounded", "value": 60000}, "burst": {"mode": "bounded", "value": 10000}}
    with pg.begin() as session:
        tenant = Tenant(name="Synthetic tenant", limits=limits)
        session.add(tenant)
        session.flush()
        project = Project(tenant_id=tenant.id, name="Synthetic project", limits=limits)
        session.add(project)
        session.flush()
        policy = default_key_policy()
        for selection in ("models", "workflow_releases", "channels"):
            policy[selection] = {"mode": "all_authorized", "selected_ids": []}
        key = ApiKey(tenant_id=tenant.id, project_id=project.id, name="Synthetic key", policy=policy)
        node = Node(name="Synthetic worker", credential_identifier="1" * 32, credential_verifier="0" * 64, current_boot_id=uuid4(), observed_at=utcnow())
        node.inventory = {"capacity_sequence": 1, "capacity": {"boot_id": str(node.current_boot_id), "observed_at_ms": int(utcnow().timestamp() * 1000), "sample": capacity_sample()}}
        session.add_all([key, node])
        session.flush()
        gpu = Gpu(node_id=node.id, hardware_id="SYNTHETIC-GPU-1", name="Protocol GPU")
        workflow = Workflow(tenant_id=tenant.id, project_id=project.id, name="Synthetic workflow", task_type="text_to_image", draft={})
        session.add_all([gpu, workflow])
        session.flush()
        manifest = {"task_type": "text_to_image", "supported_parameters": [], "workflow_api": {"1": {"class_type": "SyntheticOutput", "inputs": {}}}}
        release = WorkflowRelease(tenant_id=tenant.id, project_id=project.id, workflow_id=workflow.id, version=1, digest=fingerprint(manifest), manifest=manifest, validated=True, validation={"test_only": True})
        channels = [Channel(tenant_id=tenant.id, project_id=project.id, node_id=node.id, gpu_id=gpu.id, name=f"Synthetic channel {i}", base_url=f"http://127.0.0.1:{18188+i}", enabled=True, desired_revision=1, applied_revision=1, verified_revision=1, observed_at=utcnow()) for i in range(2)]
        session.add_all([release, *channels])
        session.flush()
        for channel in channels:
            session.add(WorkflowBinding(tenant_id=tenant.id, project_id=project.id, channel_id=channel.id, release_id=release.id, desired_digest=release.digest, applied_digest=release.digest, verified_digest=release.digest, observed_at=utcnow()))
        model = PublicModel(tenant_id=tenant.id, project_id=project.id, alias="synthetic-image", task_type="text_to_image", release_id=release.id, channel_ids=[public_id("ch", c.id) for c in channels], defaults={"size": "1024x1024", "quality": "standard"}, published=True)
        session.add(model)
        return {"identity": BusinessIdentity(tenant.id, project.id, key.id, policy), "node_id": node.id, "boot_id": node.current_boot_id, "gpu_id": gpu.id, "channel_ids": [c.id for c in channels], "release_id": release.id, "release_digest": release.digest}
