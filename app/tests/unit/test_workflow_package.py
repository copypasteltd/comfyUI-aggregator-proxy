from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from generation_domain.errors import DomainError
from generation_domain.values import fingerprint
from generation_domain.workflow_imports import parse_source
from generation_domain.workflows import TASK_TYPES, compile_graph

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import workflow_package as package  # noqa: E402 -- repository preparation command under test


def draft(task_type: str) -> dict:
    audio, video, model3d = task_type == "text_to_audio", task_type.endswith("video"), task_type == "image_to_3d"
    sources = ["seed", "output_prefix"] if model3d else ["prompt", "seed", "output_prefix"]
    if task_type in ("image_to_image", "image_to_3d"):
        sources += ["source_image"]
    if task_type.startswith("references_"):
        sources += ["reference_0", "reference_1"]
    return {
        "format_version": "1.2" if model3d else "1.1" if audio else "1.0", "task_type": task_type,
        "workflow_api": {"1": {"class_type": "SyntheticGenerator", "inputs": {s: "" for s in sources if s != "output_prefix"}}, "2": {"class_type": "SyntheticSave", "inputs": {"result": ["1", 0], "filename_prefix": ""}}},
        "bindings": [{"source": s, "node_id": "2" if s == "output_prefix" else "1", "input_name": "filename_prefix" if s == "output_prefix" else s} for s in sources],
        "output": {"adapter": "model3d-glb-v1" if model3d else "audio-files-v1" if audio else "video-files-v1" if video else "save-image-v1", "final_node_ids": ["2"], "history_field": "3d" if model3d else "audio" if audio else "videos" if video else "images", "expected_count": 1, "max_bytes": 1048576, "mime_types": ["model/gltf-binary" if model3d else "audio/wav" if audio else "video/mp4" if video else "image/png"]},
        "presets": [{"quality": "standard", **({} if model3d else {"seconds": 2, "audio_format": "wav", "sample_rate": 24000, "channels": 1} if audio else {"size": "1024x1024", **({"seconds": 2} if video else {})}), "values": {}}],
        "dependency_lock": {"models": [{"path": "checkpoints/model.bin"}], "custom_nodes": [{"directory": "synthetic-node"}]},
        "runtime_profiles": [{"id": "draft-gpu", "gpu_hardware_id": "synthetic-gpu", "execution_timeout_seconds": 300, "approved": True, "evidence_id": "old-evidence", "peak_vram_bytes": 1024, "warm_p95_milliseconds": 1}],
        "supported_parameters": ["quality", "seed", "n"] + ([] if model3d else ["seconds" if audio else "size"]),
    }


@pytest.fixture
def inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    root = tmp_path / "comfy"
    (root / "models/checkpoints").mkdir(parents=True)
    (root / "models/checkpoints/model.bin").write_bytes(b"synthetic-local-model")
    (root / "custom_nodes/synthetic-node").mkdir(parents=True)
    monkeypatch.setattr(package, "revision", lambda _root: "a" * 40)
    source = tmp_path / "draft.json"
    source.write_text(json.dumps(draft("text_to_image")), encoding="utf-8")
    return source, root, tmp_path / "package"


@pytest.mark.parametrize("task_type", sorted(TASK_TYPES))
def test_packages_round_trip_through_existing_import_and_execution_compiler(inputs, task_type: str) -> None:
    source, root, output = inputs
    document = draft(task_type)
    source.write_text(json.dumps(document), encoding="utf-8")
    result = package.build(source, root, output)
    assert result["passed"] and result["local_dependencies_checked"]
    assert result["runtime_verified"] is result["production_approved"] is False
    built, kind = parse_source((output / "manifest.json").read_bytes())
    assert kind == "manifest-v1" and built["task_type"] == task_type
    assert built["dependency_lock"]["models"] == [{"path": "checkpoints/model.bin", "sha256": hashlib.sha256(b"synthetic-local-model").hexdigest(), "bytes": 21}]
    assert built["dependency_lock"]["comfy_commit"] == "a" * 40
    assert built["runtime_profiles"][0]["approved"] is False
    assert all(built["runtime_profiles"][0][key] is None for key in ("evidence_id", "peak_vram_bytes", "warm_p95_milliseconds"))
    parameters = {k: v for k, v in built["presets"][0].items() if k not in ("values", "audio_format", "sample_rate", "channels")}
    parameters["seed"] = 2**53 + 7
    supplied = {"source_image": {"asset_id": "source"}} if task_type == "image_to_3d" else {"prompt": "synthetic controlled prompt"}
    if task_type.startswith("references_"):
        supplied["references"] = [{"asset_id": "one", "role": "character"}, {"asset_id": "two", "role": "scene"}]
    execution = {"parameters": parameters, "input": supplied}
    graph = compile_graph(built, execution, {"source_image": "inputs/source.png", "reference_0": "inputs/one.png", "reference_1": "inputs/two.png"}, "outputs/sample")
    assert graph["1"]["inputs"]["seed"] == 2**53 + 7
    if task_type.startswith("references_"):
        assert graph["1"]["inputs"]["reference_1"] == "inputs/two.png"
    if task_type in ("image_to_image", "image_to_3d"):
        assert graph["1"]["inputs"]["source_image"] == "inputs/source.png"
    assert package.verify(output)["local_dependencies_checked"] is False
    assert package.verify(output, root)["local_dependencies_checked"] is True
    assert json.loads(source.read_text(encoding="utf-8")) == document
    assert {p.name for p in output.iterdir()} == package.FILES


def test_local_verification_rejects_changed_weight_with_unchanged_size(inputs) -> None:
    source, root, output = inputs
    package.build(source, root, output)
    (root / "models/checkpoints/model.bin").write_bytes(b"changed---local-model")
    with pytest.raises(package.PackageError, match="local_dependency_drift"):
        package.verify(output, root)


@pytest.mark.parametrize("member", ["manifest.json", "provenance.json"])
def test_package_tampering_and_incomplete_writes_are_detected(inputs, member: str) -> None:
    source, root, output = inputs
    package.build(source, root, output)
    (output / member).write_bytes(b"{}")
    with pytest.raises(package.PackageError, match="package_digest_mismatch"):
        package.verify(output)
    (output / member).unlink()
    with pytest.raises(package.PackageError, match="package_members_invalid"):
        package.verify(output)


def test_existing_output_is_preserved_and_extra_files_are_rejected(inputs) -> None:
    source, root, output = inputs
    package.build(source, root, output)
    before = {p.name: p.read_bytes() for p in output.iterdir()}
    with pytest.raises(FileExistsError):
        package.build(source, root, output)
    assert before == {p.name: p.read_bytes() for p in output.iterdir()}
    (output / "model.bin").write_bytes(b"not allowed")
    with pytest.raises(package.PackageError, match="package_members_invalid"):
        package.verify(output)


@pytest.mark.parametrize("field,value", [("path", "../outside.bin"), ("path", "C:/outside.bin"), ("path", "checkpoints/model.bin:stream"), ("path", "checkpoints/NUL.bin"), ("path", "checkpoints/model.bin.")])
def test_dependency_path_rejection_does_not_create_a_package(inputs, field: str, value: str) -> None:
    source, root, output = inputs
    data = draft("text_to_image")
    data["dependency_lock"]["models"][0][field] = value
    source.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises((package.PackageError, DomainError)):
        package.build(source, root, output)
    assert not output.exists()


def test_duplicate_dependencies_and_oversized_models_are_rejected(inputs) -> None:
    source, root, output = inputs
    data = draft("text_to_image")
    data["dependency_lock"]["models"] *= 2
    source.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(package.PackageError, match="duplicate_dependency"):
        package.build(source, root, output)
    source.write_text(json.dumps(draft("text_to_image")), encoding="utf-8")
    with pytest.raises(package.PackageError, match="model_size_or_type_invalid"):
        package.build(source, root, output, maximum=2)
    assert not output.exists()


def test_ui_graph_and_duplicate_json_keys_do_not_become_manifests(inputs) -> None:
    source, root, output = inputs
    for data in (b'{"nodes": [], "links": []}', b'{"format_version":"1.0","format_version":"1.1"}'):
        source.write_bytes(data)
        with pytest.raises(DomainError):
            package.build(source, root, output)
    assert not output.exists()


def test_forged_offline_approval_is_rejected_even_with_recomputed_checksums(inputs) -> None:
    source, root, output = inputs
    package.build(source, root, output)
    document = json.loads((output / "manifest.json").read_bytes())
    document["runtime_profiles"][0]["approved"] = True
    (output / "manifest.json").write_text(json.dumps(document), encoding="utf-8")
    provenance = json.loads((output / "provenance.json").read_bytes())
    provenance["manifest_digest"] = fingerprint(document)
    (output / "provenance.json").write_text(json.dumps(provenance), encoding="utf-8")
    (output / "checksums.json").write_text(json.dumps({n: hashlib.sha256((output / n).read_bytes()).hexdigest() for n in ("manifest.json", "provenance.json")}), encoding="utf-8")
    with pytest.raises(package.PackageError, match="offline_runtime_approval_invalid"):
        package.verify(output)


@pytest.mark.skipif(os.name != "nt", reason="Windows junction behavior")
def test_junction_cannot_redirect_model_reads(inputs, tmp_path: Path) -> None:
    source, root, output = inputs
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "model.bin").write_bytes(b"outside")
    target = root / "models/checkpoints"
    (target / "model.bin").unlink()
    target.rmdir()
    import _winapi
    _winapi.CreateJunction(str(outside), str(target))
    try:
        with pytest.raises(package.PackageError, match="linked_path_not_allowed"):
            package.build(source, root, output)
    finally:
        target.rmdir()
    assert (outside / "model.bin").read_bytes() == b"outside"


def test_native_git_revision_refuses_dirty_worktrees_and_ancestor_repositories(tmp_path: Path) -> None:
    git = shutil.which("git")
    assert git is not None
    root = tmp_path / "comfy"
    root.mkdir()
    def run(*arguments: str) -> None:
        subprocess.run([git, "-C", str(root), *arguments], check=True, capture_output=True)  # noqa: S603 -- fixed synthetic local Git fixture
    run("init")
    (root / "engine.py").write_text("# synthetic fixture\n", encoding="utf-8")
    run("add", "engine.py")
    run("-c", "user.name=Workflow fixture", "-c", "user.email=workflow@example.invalid", "commit", "-m", "synthetic fixture")
    assert len(package.revision(root)) == 40
    (root / "engine.py").write_text("# changed\n", encoding="utf-8")
    with pytest.raises(package.PackageError, match="dependency_repository_dirty"):
        package.revision(root)
    nested = root / "nested"
    nested.mkdir()
    with pytest.raises(FileNotFoundError):
        package.revision(nested)


def test_cli_errors_do_not_print_private_workflow_or_paths(inputs, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    source, root, output = inputs
    source.write_text('{"private":"private-prompt-and-secret"}', encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["workflow-package.py", "build", "--manifest", str(source), "--comfy-root", str(root), "--output", str(output)])
    assert package.main() == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out)["passed"] is False
    assert "private-prompt-and-secret" not in captured.out
    assert str(root) not in captured.out and not captured.err


def test_actual_cli_build_import_and_local_reverification_with_native_git(inputs) -> None:
    source, root, output = inputs
    git = shutil.which("git")
    assert git is not None
    for repository in (root, root / "custom_nodes/synthetic-node"):
        (repository / ".gitignore").write_text("models/\ncustom_nodes/\n", encoding="utf-8")
        (repository / "fixture.py").write_text("# synthetic repository\n", encoding="utf-8")
        for args in (("init",), ("add", ".gitignore", "fixture.py"), ("-c", "user.name=Workflow fixture", "-c", "user.email=workflow@example.invalid", "commit", "-m", "synthetic native CLI fixture")):
            subprocess.run([git, "-C", str(repository), *args], check=True, capture_output=True)  # noqa: S603 -- fixed synthetic local repositories
    commands = [
        ["build", "--manifest", str(source), "--comfy-root", str(root), "--output", str(output)],
        ["verify", "--package", str(output)],
        ["verify", "--package", str(output), "--comfy-root", str(root)],
    ]
    receipts = []
    for args in commands:
        result = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "scripts/workflow-package.py"), *args], check=True, capture_output=True, timeout=60)  # noqa: S603 -- actual preparation command, owned local fixture paths
        receipt = json.loads(result.stdout)
        assert receipt["passed"] and receipt["production_approved"] is False
        assert not result.stderr
        receipts.append(receipt)
    assert len({r["manifest_digest"] for r in receipts}) == 1
    built, _ = parse_source((output / "manifest.json").read_bytes())
    assert built["dependency_lock"]["models"][0]["bytes"] == 21
    assert receipts[0]["local_dependencies_checked"] is receipts[2]["local_dependencies_checked"] is True
    assert receipts[1]["local_dependencies_checked"] is False


def test_model_write_observed_on_open_handle_is_rejected(inputs, monkeypatch: pytest.MonkeyPatch) -> None:
    _source, root, _output = inputs
    original = os.fstat
    calls = 0
    def modified(fd: int) -> os.stat_result:
        nonlocal calls
        calls += 1
        if calls == 2:
            (root / "models/checkpoints/model.bin").write_bytes(b"mutated---local-model")
        return original(fd)
    monkeypatch.setattr(package.os, "fstat", modified)
    with pytest.raises(package.PackageError, match="model_changed_during_read"):
        package.hash_model(root / "models/checkpoints/model.bin", 1000)
