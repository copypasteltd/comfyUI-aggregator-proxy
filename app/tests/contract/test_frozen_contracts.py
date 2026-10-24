from __future__ import annotations

import hashlib
import json
from importlib.resources import files
from pathlib import Path
from typing import Any

import pytest
from generation_contracts.capacity import WorkerCapacity
from generation_contracts.gateway import GatewayRequestIdentity
from generation_contracts.validation import validate

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = json.loads((ROOT / "docs/contracts/fixtures.json").read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", [c for c in FIXTURES if c["schema"] in {"gateway-catalog-publication.schema.json", "gateway-catalog-replay.schema.json", "gateway-catalog-stamp.schema.json", "gateway-catalog-receipt.schema.json"}], ids=lambda c: c["id"])
def test_gateway_catalog_dto_matches_frozen_fixture(case: dict[str, Any]) -> None:
    from generation_contracts.gateway_catalogs import (
        CatalogPublication,
        CatalogReceipt,
        CatalogReplay,
        CatalogStamp,
    )
    model = {"gateway-catalog-publication.schema.json": CatalogPublication, "gateway-catalog-replay.schema.json": CatalogReplay, "gateway-catalog-stamp.schema.json": CatalogStamp, "gateway-catalog-receipt.schema.json": CatalogReceipt}[case["schema"]]
    if case["valid"]:
        assert model.model_validate(case["data"]).model_dump(exclude_unset=True) == case["data"]
    else:
        with pytest.raises(ValueError):
            model.model_validate(case["data"])


@pytest.mark.parametrize("case", [c for c in FIXTURES if c["schema"] == "admin-job-cancellations.schema.json"], ids=lambda c: c["id"])
def test_cancellation_batch_dto_matches_frozen_fixture(case: dict[str, Any]) -> None:
    from generation_contracts.job_cancellations import JobCancellationBatch
    if case["valid"]:
        assert JobCancellationBatch.model_validate(case["data"]).model_dump() == case["data"]
    else:
        with pytest.raises(ValueError):
            JobCancellationBatch.model_validate(case["data"])


@pytest.mark.parametrize("case", [c for c in FIXTURES if c["schema"] == "workflow-sync-retry.schema.json"], ids=lambda c: c["id"])
def test_workflow_retry_dto_matches_frozen_fixture(case: dict[str, Any]) -> None:
    from generation_contracts.workflow_retries import WorkflowSyncRetry
    if case["valid"]:
        assert WorkflowSyncRetry.model_validate(case["data"]).model_dump() == case["data"]
    else:
        with pytest.raises(ValueError):
            WorkflowSyncRetry.model_validate(case["data"])


@pytest.mark.parametrize("case", FIXTURES, ids=[c["id"] for c in FIXTURES])
def test_frozen_fixture(case: dict[str, Any]) -> None:
    name = case["schema"].removesuffix(".schema.json")
    if case["valid"]:
        validate(name, case["data"])
    else:
        with pytest.raises(ValueError):
            validate(name, case["data"])


def test_packaged_contracts_match_frozen_source() -> None:
    hashes = json.loads((ROOT / "app/config/contract-hashes.json").read_text())
    for name, expected in hashes.items():
        source = (ROOT / "docs/contracts" / name).read_bytes()
        assert hashlib.sha256(source).hexdigest() == expected
        if name == "job-state-machine.json":
            destination = files("generation_domain").joinpath(name)
        elif name == "endpoint-catalog.json":
            destination = files("generation_contracts").joinpath(name)
        else:
            destination = files("generation_contracts").joinpath("schemas", name)
        assert source == destination.read_bytes()


@pytest.mark.parametrize("case", [c for c in FIXTURES if c["schema"] == "admin-audit-retention.schema.json"], ids=lambda c: c["id"])
def test_audit_retention_dto_matches_frozen_fixture(case: dict[str, Any]) -> None:
    from generation_contracts.audit_retention import AuditRetentionView
    if case["valid"]:
        assert AuditRetentionView.model_validate(case["data"]).model_dump() == case["data"]
    else:
        with pytest.raises(ValueError):
            AuditRetentionView.model_validate(case["data"])


@pytest.mark.parametrize("case", [c for c in FIXTURES if c["schema"] == "admin-session.schema.json"], ids=lambda c: c["id"])
def test_session_dto_matches_frozen_fixture(case: dict[str, Any]) -> None:
    from generation_contracts.admin_sessions import SessionActivity, SessionLifetime
    model = SessionActivity if isinstance(case["data"], dict) and "activity" in case["data"] else SessionLifetime
    if case["valid"]:
        assert model.model_validate(case["data"]).model_dump() == case["data"]
    else:
        with pytest.raises(ValueError):
            model.model_validate(case["data"])


@pytest.mark.parametrize("case", [c for c in FIXTURES if c["schema"] == "spec-isolation.schema.json"], ids=lambda c: c["id"])
def test_isolation_dto_matches_frozen_mutation_fixture(case: dict[str, Any]) -> None:
    from generation_contracts.spec_isolation import SpecIsolationAction, SpecIsolationCreate
    from pydantic import TypeAdapter
    adapter = TypeAdapter(SpecIsolationAction if "action" in case["data"] else SpecIsolationCreate)
    if case["valid"]:
        assert adapter.validate_python(case["data"]).model_dump() == case["data"]
    else:
        with pytest.raises(ValueError):
            adapter.validate_python(case["data"])


@pytest.mark.parametrize("case", [c for c in FIXTURES if c["schema"] == "gateway-request-identity.schema.json"], ids=lambda c: c["id"])
def test_gateway_recovery_dto_matches_frozen_fixture(case: dict[str, Any]) -> None:
    if case["valid"]:
        assert GatewayRequestIdentity.model_validate(case["data"]).model_dump() == case["data"]
    else:
        with pytest.raises(ValueError):
            GatewayRequestIdentity.model_validate(case["data"])


@pytest.mark.parametrize("case", [c for c in FIXTURES if c["schema"] == "worker-capacity.schema.json"], ids=lambda c: c["id"])
def test_capacity_dto_matches_frozen_fixture(case: dict[str, Any]) -> None:
    if case["valid"]:
        assert WorkerCapacity.model_validate(case["data"]).model_dump() == case["data"]
    else:
        with pytest.raises(ValueError):
            WorkerCapacity.model_validate(case["data"])


@pytest.mark.parametrize("case", [c for c in FIXTURES if c["schema"] == "recovery-checkpoint.schema.json"], ids=lambda c: c["id"])
def test_checkpoint_dto_matches_frozen_fixture(case: dict[str, Any]) -> None:
    from generation_contracts.recovery_checkpoint import RecoveryCheckpoint
    if case["valid"]:
        assert RecoveryCheckpoint.model_validate(case["data"]).model_dump() == case["data"]
    else:
        with pytest.raises(ValueError):
            RecoveryCheckpoint.model_validate(case["data"])


@pytest.mark.parametrize("case", [c for c in FIXTURES if c["schema"] == "admin-audit-query.schema.json"], ids=lambda c: c["id"])
def test_audit_query_dto_matches_frozen_fixture(case: dict[str, Any]) -> None:
    from generation_contracts.audit_queries import AuditQuery
    if case["valid"]:
        query = AuditQuery.model_validate(case["data"])
        assert query.model_dump(exclude_unset=True) == case["data"]
    else:
        with pytest.raises(ValueError):
            AuditQuery.model_validate(case["data"])


@pytest.mark.parametrize("case", [c for c in FIXTURES if c["schema"] == "admin-channel-query.schema.json"], ids=lambda c: c["id"])
def test_channel_query_dto_matches_frozen_fixture(case: dict[str, Any]) -> None:
    from generation_contracts.channel_queries import ChannelQuery
    if case["valid"]:
        query = ChannelQuery.model_validate(case["data"])
        assert query.model_dump(exclude_unset=True) == case["data"]
    else:
        with pytest.raises(ValueError):
            ChannelQuery.model_validate(case["data"])


@pytest.mark.parametrize("case", [c for c in FIXTURES if c["schema"] == "admin-channel-deletion.schema.json"], ids=lambda c: c["id"])
def test_channel_deletion_dto_matches_frozen_fixture(case: dict[str, Any]) -> None:
    from generation_contracts.channel_deletion import ChannelDeletionCheck
    if case["valid"]:
        assert ChannelDeletionCheck.model_validate(case["data"]).model_dump() == case["data"]
    else:
        with pytest.raises(ValueError):
            ChannelDeletionCheck.model_validate(case["data"])


@pytest.mark.parametrize("case", [c for c in FIXTURES if c["schema"] == "worker-command-reconciliation.schema.json"], ids=lambda c: c["id"])
def test_command_reconciliation_dto_matches_frozen_fixture(case: dict[str, Any]) -> None:
    from generation_contracts.command_recovery import (
        CommandReconciliation,
        CommandReconciliationResult,
    )
    model = CommandReconciliation if isinstance(case["data"], dict) and "boot_id" in case["data"] else CommandReconciliationResult
    if case["valid"]:
        assert model.model_validate(case["data"]).model_dump() == case["data"]
    else:
        with pytest.raises(ValueError):
            model.model_validate(case["data"])
@pytest.mark.parametrize("case", [c for c in FIXTURES if c["schema"] in ("admin-alert-query.schema.json", "admin-alert-failure.schema.json")], ids=lambda c: c["id"])
def test_alert_dtos_match_frozen_fixture(case: dict[str, Any]) -> None:
    from generation_contracts.alert_queries import AlertFailure, AlertQuery
    model = AlertQuery if case["schema"] == "admin-alert-query.schema.json" else AlertFailure
    if case["valid"]:
        assert model.model_validate(case["data"]).model_dump(exclude_unset=True) == case["data"]
    else:
        with pytest.raises(ValueError):
            model.model_validate(case["data"])


@pytest.mark.parametrize("case", [c for c in FIXTURES if c["schema"] in ("admin-model-catalog-query.schema.json", "admin-model-mapping-query.schema.json", "admin-model-mapping-options.schema.json")], ids=lambda c: c["id"])
def test_model_catalog_dtos_match_frozen_fixture(case: dict[str, Any]) -> None:
    from generation_contracts.model_catalog import (
        ModelCatalogQuery,
        ModelMappingOptions,
        ModelMappingQuery,
    )
    model = {"admin-model-catalog-query.schema.json": ModelCatalogQuery, "admin-model-mapping-query.schema.json": ModelMappingQuery, "admin-model-mapping-options.schema.json": ModelMappingOptions}[case["schema"]]
    if case["valid"]:
        assert model.model_validate(case["data"]).model_dump(exclude_unset=True) == case["data"]
    else:
        with pytest.raises(ValueError):
            model.model_validate(case["data"])


@pytest.mark.parametrize("case", [c for c in FIXTURES if c["schema"] in ("key-generation-options-query.schema.json", "key-generation-options.schema.json")], ids=lambda c: c["id"])
def test_key_generation_dtos_match_frozen_fixture(case: dict[str, Any]) -> None:
    from generation_contracts.key_generation_options import KeyGenerationOptions, KeyGenerationQuery
    model = KeyGenerationQuery if case["schema"] == "key-generation-options-query.schema.json" else KeyGenerationOptions
    if case["valid"]:
        assert model.model_validate(case["data"]).model_dump(exclude_unset=True) == case["data"]
    else:
        with pytest.raises(ValueError):
            model.model_validate(case["data"])


@pytest.mark.parametrize("case", [c for c in FIXTURES if c["schema"] in ("admin-workflow-catalog-query.schema.json", "admin-workflow-catalog-page.schema.json", "admin-workflow-sync-options-query.schema.json", "admin-workflow-sync-options.schema.json")], ids=lambda c: c["id"])
def test_workflow_directory_dtos_match_frozen_fixture(case: dict[str, Any]) -> None:
    from generation_contracts.workflow_directory import (
        WorkflowDirectoryPage,
        WorkflowDirectoryQuery,
        WorkflowSyncOptions,
        WorkflowSyncOptionsQuery,
    )
    model = {"admin-workflow-catalog-query.schema.json": WorkflowDirectoryQuery, "admin-workflow-catalog-page.schema.json": WorkflowDirectoryPage, "admin-workflow-sync-options-query.schema.json": WorkflowSyncOptionsQuery, "admin-workflow-sync-options.schema.json": WorkflowSyncOptions}[case["schema"]]
    if case["valid"]:
        assert model.model_validate(case["data"]).model_dump(exclude_unset=True) == case["data"]
    else:
        with pytest.raises(ValueError):
            model.model_validate(case["data"])


@pytest.mark.parametrize("case", [c for c in FIXTURES if c["schema"] in ("admin-workflow-history-query.schema.json", "admin-workflow-history-page.schema.json", "admin-workflow-diff-query.schema.json", "admin-workflow-diff.schema.json")], ids=lambda c: c["id"])
def test_workflow_history_dtos_match_frozen_fixture(case: dict[str, Any]) -> None:
    from generation_contracts.workflow_history import (
        WorkflowDiff,
        WorkflowDiffQuery,
        WorkflowHistoryPage,
        WorkflowHistoryQuery,
    )
    model = {"admin-workflow-history-query.schema.json": WorkflowHistoryQuery, "admin-workflow-history-page.schema.json": WorkflowHistoryPage, "admin-workflow-diff-query.schema.json": WorkflowDiffQuery, "admin-workflow-diff.schema.json": WorkflowDiff}[case["schema"]]
    if case["valid"]:
        assert model.model_validate(case["data"]).model_dump(exclude_unset=True) == case["data"]
    else:
        with pytest.raises(ValueError):
            model.model_validate(case["data"])


@pytest.mark.parametrize("case", [c for c in FIXTURES if c["schema"] in ["admin-workflow-draft-restore.schema.json", "admin-workflow-draft-restored.schema.json"]], ids=lambda c: c["id"])
def test_workflow_restoration_dto_matches_frozen_fixture(case: dict[str, Any]) -> None:
    from generation_contracts.workflow_restoration import (
        WorkflowDraftRestore,
        WorkflowDraftRestored,
    )
    model = WorkflowDraftRestore if case["schema"] == "admin-workflow-draft-restore.schema.json" else WorkflowDraftRestored
    if case["valid"]:
        assert model.model_validate(case["data"]).model_dump() == case["data"]
    else:
        with pytest.raises(ValueError):
            model.model_validate(case["data"])


def test_workflow_restoration_result_requires_a_single_revision_increment() -> None:
    from generation_contracts.workflow_restoration import WorkflowDraftRestored
    with pytest.raises(ValueError):
        WorkflowDraftRestored(workflow_id="wf_" + "a" * 32, source_release_id="wfr_" + "b" * 32, source_digest="c" * 64, previous_revision=7, revision=9)


@pytest.mark.parametrize("case", [c for c in FIXTURES if c["schema"] in ["admin-workflow-lifecycle.schema.json", "admin-workflow-lifecycle-result.schema.json"]])
def test_workflow_lifecycle_dto_matches_frozen_fixture(case: dict[str, Any]) -> None:
    from generation_contracts.workflow_lifecycle import WorkflowLifecycle, WorkflowLifecycleResult
    model = WorkflowLifecycle if case["schema"] == "admin-workflow-lifecycle.schema.json" else WorkflowLifecycleResult
    if case["valid"]:
        model.model_validate(case["data"])
    else:
        with pytest.raises(ValueError):
            model.model_validate(case["data"])


def test_workflow_lifecycle_result_increases_revision_once() -> None:
    from generation_contracts.workflow_lifecycle import WorkflowLifecycleResult
    with pytest.raises(ValueError):
        WorkflowLifecycleResult(workflow_id="wf_" + "a" * 32, archived=True, previous_revision=1, revision=3)
