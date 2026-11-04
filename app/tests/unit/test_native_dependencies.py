from __future__ import annotations

import hashlib
import os
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from generation_contracts.workflows import WorkflowManifest
from generation_domain.errors import DomainError
from generation_domain.native_dependencies import NATIVE_DEPENDENCY_FEATURE
from generation_domain.workflow_inputs import INPUT_FEATURE, supports_inputs
from generation_domain.workflows import validate_manifest
from generation_infrastructure.commands import environment_receipt_valid
from windows_worker.configuration import WorkerSettings
from windows_worker.environment import verify_environment


def locked_file(path: str, data: bytes) -> dict[str, Any]:
    return {"path": path, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def native_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(manifest)
    result["dependency_lock"]["native_roots"] = [{"name": "native_3d", "files": [locked_file("worker.py", b"# native\n")]}]
    return result


def test_native_files_are_verified_without_executing_or_disclosing_the_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, multimodal_manifest: dict[str, Any]) -> None:
    root, native = tmp_path / "comfy", tmp_path / "native"
    root.mkdir()
    native.mkdir()
    manifest = native_manifest(multimodal_manifest)
    (native / "worker.py").write_bytes(b"# native\n")
    monkeypatch.setattr("windows_worker.environment.commit", lambda *args: manifest["dependency_lock"]["comfy_commit"])
    first = verify_environment(root, manifest, native_roots={"native_3d": native})
    assert first["environment_verified"] and first["native_dependencies_verified"] and str(native) not in str(first)
    assert environment_receipt_valid({"manifest": manifest}, first)
    assert not environment_receipt_valid({"manifest": manifest}, {"environment_verified": True})
    assert first == verify_environment(root, manifest, native_roots={"native_3d": native})
    (native / "worker.py").write_bytes(b"# change\n")
    with pytest.raises(DomainError) as error:
        verify_environment(root, manifest, native_roots={"native_3d": native})
    assert error.value.code == "native_dependency_mismatch"
    with pytest.raises(DomainError) as error:
        verify_environment(root, manifest)
    assert error.value.code == "native_dependency_not_allowlisted"
    (native / "worker.py").unlink()
    with pytest.raises(DomainError) as error:
        verify_environment(root, manifest, native_roots={"native_3d": native})
    assert error.value.code == "native_dependency_unavailable"


def test_native_dependency_junction_cannot_escape_an_operator_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, multimodal_manifest: dict[str, Any]) -> None:
    root, native, outside = (tmp_path / name for name in ("comfy", "native", "outside"))
    for directory in (root, native, outside):
        directory.mkdir()
    (outside / "worker.py").write_bytes(b"# native\n")
    link = native / "linked"
    if os.name == "nt":
        subprocess.run([str(Path(os.environ["SystemRoot"]) / "System32/cmd.exe"), "/d", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True, check=True, timeout=10)  # noqa: S603 -- creates an owned temporary junction; no shell interpolation
    else:
        link.symlink_to(outside, target_is_directory=True)
    manifest = native_manifest(multimodal_manifest)
    manifest["dependency_lock"]["native_roots"][0]["files"][0]["path"] = "linked/worker.py"
    monkeypatch.setattr("windows_worker.environment.commit", lambda *args: manifest["dependency_lock"]["comfy_commit"])
    with pytest.raises(DomainError) as error:
        verify_environment(root, manifest, native_roots={"native_3d": native})
    assert error.value.code in ("unsafe_path", "native_dependency_unsafe")


@pytest.mark.parametrize("variant", ["absolute", "traversal", "cache", "private", "duplicate_case", "duplicate_root", "empty", "bad_hash", "boolean_bytes", "over_budget", "legacy"])
def test_native_lock_rejects_uncontrolled_or_ambiguous_files(multimodal_manifest: dict[str, Any], variant: str) -> None:
    manifest = native_manifest(multimodal_manifest)
    root = manifest["dependency_lock"]["native_roots"][0]
    file = root["files"][0]
    if variant in ("absolute", "traversal", "cache", "private"):
        file["path"] = {"absolute": "C:/private/key", "traversal": "../private/key", "cache": "cache/model.bin", "private": "private/key.json"}[variant]
    elif variant == "duplicate_case":
        root["files"].append({**file, "path": "WORKER.PY"})
    elif variant == "duplicate_root":
        manifest["dependency_lock"]["native_roots"].append(deepcopy(root))
    elif variant == "empty":
        root["files"] = []
    elif variant == "bad_hash":
        file["sha256"] = "g" * 64
    elif variant == "boolean_bytes":
        file["bytes"] = True
        with pytest.raises(ValueError):
            WorkflowManifest.model_validate(manifest)
    elif variant == "over_budget":
        file["bytes"] = 1024**4 + 1
    else:
        manifest["format_version"] = "1.0"
    with pytest.raises(DomainError):
        validate_manifest(manifest, manifest["task_type"])


def test_native_worker_feature_is_required_only_for_declared_dependencies(multimodal_manifest: dict[str, Any]) -> None:
    manifest = native_manifest(multimodal_manifest)
    assert not supports_inputs(manifest, [INPUT_FEATURE])
    assert supports_inputs(manifest, [INPUT_FEATURE, NATIVE_DEPENDENCY_FEATURE])
    assert not supports_inputs({"format_version": "1.3"}, [INPUT_FEATURE], native_required=True)
    assert supports_inputs(multimodal_manifest, [INPUT_FEATURE])


@pytest.mark.parametrize("variant", ["valid", "private", "customer", "broad", "relative", "http"])
def test_native_roots_are_local_operator_configuration_only(tmp_path: Path, variant: str) -> None:
    comfy, native, private = (tmp_path / name for name in ("comfy", "native", "private"))
    for directory in (comfy, native, private, comfy / "input"):
        directory.mkdir()
    selected = {"private": private, "customer": comfy / "input", "broad": tmp_path, "relative": Path("relative/native")}.get(variant, native)
    channel = {"base_url": "http://127.0.0.1:8188", "hardware_id": "synthetic-native", "comfy_root": comfy, "native_dependency_roots": {"native_3d": selected}}
    if variant == "http":
        channel.update(connection_mode="remote_http", comfy_root=None)
    value = {"control_origin": "https://control.invalid", "credential_file": private / "credential.json", "state_root": private / "state", "allow_api_only": True, "gpus": [{"hardware_id": "synthetic-native", "name": "Synthetic GPU"}], "channels": [channel]}
    if variant == "valid":
        assert WorkerSettings.model_validate(value).channels[0].native_dependency_roots == {"native_3d": native.resolve()}
    else:
        with pytest.raises(ValueError):
            WorkerSettings.model_validate(value)
