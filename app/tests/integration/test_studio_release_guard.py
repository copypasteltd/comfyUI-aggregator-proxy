from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from typing import Any

import pytest
from generation_domain.errors import DomainError
from generation_domain.values import fingerprint
from generation_infrastructure.admin_catalog import CatalogAdministration
from generation_infrastructure.jobs import JobRepository
from generation_infrastructure.models import Idempotency, Job, PublicModel, WorkflowRelease
from generation_infrastructure.settings import Settings
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration
REQUEST = {"model": "synthetic-image", "task_type": "text_to_image", "input": {"prompt": "synthetic studio release guard"}}


def test_full_glb_catalog_projection_keeps_3d_without_size_or_image_mime(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, model3d_manifest: dict[str, Any]) -> None:
    with pg.begin() as session:
        original = session.get(WorkflowRelease, catalog["release_id"])
        release = WorkflowRelease(tenant_id=original.tenant_id, project_id=original.project_id, workflow_id=original.workflow_id, version=2, digest=fingerprint(model3d_manifest), manifest=model3d_manifest, validated=True, validation={"synthetic": True})
        session.add(release)
        session.flush()
        model = session.scalar(select(PublicModel).where(PublicModel.alias == REQUEST["model"]))
        model.task_type = "image_to_3d"
        model.release_id = release.id
        model.defaults = {"quality": "standard", "n": 1}
    repository = CatalogAdministration(pg, test_settings)
    models = repository.public_models(catalog["identity"])["data"]
    capabilities = repository.public_models(catalog["identity"], capabilities=True)["data"]
    assert len(models) == len(capabilities) == 1
    assert capabilities[0]["task_type"] == "image_to_3d"
    assert capabilities[0]["presets"] == [{"quality": "standard"}]
    assert capabilities[0]["output"]["media_types"] == ["model/gltf-binary"]


@pytest.mark.parametrize("change", ["image_mime", "size", "seconds"])
def test_glb_capability_contract_rejects_image_fields(model3d_manifest: dict[str, Any], change: str) -> None:
    from generation_contracts.discovery import PublishedCapability
    from pydantic import ValidationError

    item = {"id": "synthetic-mesh", "object": "model", "created": 1, "owned_by": "render-control", "task_type": "image_to_3d", "asynchronous": True, "release": {"version": 1, "digest": "a" * 64}, "supported_parameters": ["quality", "seed", "n"], "presets": [{"quality": "standard"}], "output": {"media_types": ["model/gltf-binary"], "expected_count": 1, "max_bytes": 1048576}}
    PublishedCapability.model_validate(item)
    if change == "image_mime":
        item["output"] = {**item["output"], "media_types": ["image/png"]}
    else:
        item["presets"] = [{"quality": "standard", change: "512x512" if change == "size" else 1}]
    with pytest.raises(ValidationError):
        PublishedCapability.model_validate(item)


def test_wrong_digest_rolls_back_admission_and_allows_correct_original_token(pg: sessionmaker[Session], catalog: dict[str, Any]) -> None:
    jobs = JobRepository(pg)
    token = "studio-original-token-0001"
    with pytest.raises(DomainError) as caught:
        jobs.create(catalog["identity"], REQUEST, token, "studio-test", expected_release_digest="0" * 64)
    assert caught.value.code == "model_release_changed" and caught.value.status == 409
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 0
        assert session.scalar(select(func.count()).select_from(Idempotency)) == 0
    created = jobs.create(catalog["identity"], REQUEST, token, "studio-test", expected_release_digest=catalog["release_digest"])
    assert created["status"] == "queued"


def test_replay_precedes_alias_unpublication_but_changed_header_conflicts(pg: sessionmaker[Session], catalog: dict[str, Any]) -> None:
    jobs = JobRepository(pg)
    token = "studio-original-token-0002"
    created = jobs.create(catalog["identity"], REQUEST, token, "studio-test", expected_release_digest=catalog["release_digest"])
    with pg.begin() as session:
        model = session.scalar(select(PublicModel).where(PublicModel.alias == REQUEST["model"]))
        model.published = False
    assert jobs.create(catalog["identity"], REQUEST, token, "studio-replay", expected_release_digest=catalog["release_digest"])["id"] == created["id"]
    for digest in (None, "0" * 64):
        with pytest.raises(DomainError, match="同一幂等键"):
            jobs.create(catalog["identity"], REQUEST, token, "studio-changed", expected_release_digest=digest)
    with pg() as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 1


def test_legacy_replay_still_uses_legacy_fingerprint(pg: sessionmaker[Session], catalog: dict[str, Any]) -> None:
    jobs = JobRepository(pg)
    created = jobs.create(catalog["identity"], REQUEST, "studio-legacy-token-0003", "studio-test")
    replay = jobs.create(catalog["identity"], REQUEST, "studio-legacy-token-0003", "studio-test", expected_release_digest=None)
    assert replay["id"] == created["id"]


def test_pending_admin_rebind_blocks_new_admission_until_digest_can_be_checked(pg: sessionmaker[Session], catalog: dict[str, Any]) -> None:
    started = threading.Event()
    with pg.begin() as editor:
        model = editor.scalar(select(PublicModel).where(PublicModel.alias == REQUEST["model"]).with_for_update())
        source = editor.get(WorkflowRelease, catalog["release_id"])
        replacement = WorkflowRelease(tenant_id=source.tenant_id, project_id=source.project_id, workflow_id=source.workflow_id, version=2, digest="d" * 64, manifest=deepcopy(source.manifest), validated=True, validation={"synthetic_fixture": True})
        editor.add(replacement)
        editor.flush()
        model.release_id = replacement.id
        editor.flush()
        blocker = editor.scalar(text("SELECT pg_backend_pid()"))
        pool = ThreadPoolExecutor(max_workers=1)
        def submit() -> dict[str, Any]:
            started.set()
            return JobRepository(pg).create(catalog["identity"], REQUEST, "studio-rebind-token-0004", "studio-race", expected_release_digest=catalog["release_digest"])
        pending = pool.submit(submit)
        try:
            assert started.wait(3)
            deadline = time.monotonic() + 3
            observed = False
            while time.monotonic() < deadline:
                with pg() as observer:
                    observed = bool(observer.scalar(text("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE datname=current_database() AND :blocker=ANY(pg_blocking_pids(pid)))"), {"blocker": blocker}))
                if observed:
                    break
                assert not pending.done(), "admission bypassed the Admin model-row lock"
                time.sleep(0.02)
            assert observed and not pending.done()
        except BaseException:
            editor.rollback()
            pool.shutdown(wait=True)
            raise
    try:
        with pytest.raises(DomainError) as caught:
            pending.result(timeout=5)
        assert caught.value.code == "model_release_changed"
        with pg() as session:
            assert session.scalar(select(func.count()).select_from(Job)) == 0
    finally:
        pool.shutdown(wait=True)
