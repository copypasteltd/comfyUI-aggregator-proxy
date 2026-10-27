from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from windows_worker.configuration import WorkerSettings
from windows_worker.environment import commit, verify_environment


def test_native_git_dependency_check_without_process_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    located = shutil.which("git.exe")
    assert located, "Native Git is required by the Windows development environment"
    executable = Path(located)
    root = tmp_path / "repo"
    root.mkdir()
    for arguments in (["init", str(root)], ["-C", str(root), "-c", "user.name=Worker protocol", "-c", "user.email=worker@example.invalid", "commit", "--allow-empty", "-m", "fixture"]):
        subprocess.run([str(executable), *arguments], capture_output=True, check=True, timeout=15)  # noqa: S603 -- explicit native Git, temporary owned repository
    expected = commit(root, executable)
    monkeypatch.setenv("PATH", os.environ["SystemRoot"] + "\\System32")
    settings = WorkerSettings(control_origin="https://control.invalid", credential_file=tmp_path / "unused", state_root=tmp_path / "state", gpus=[], channels=[], git_executable=executable)
    assert commit(root, settings.git_executable) == expected
    manifest = {"format_version": "1.0", "task_type": "text_to_image", "workflow_api": {"1": {"class_type": "FixtureOutput", "inputs": {"prompt": "", "seed": 1, "prefix": "fixture"}}}, "bindings": [{"source": source, "node_id": "1", "input_name": field} for source, field in (("prompt", "prompt"), ("seed", "seed"), ("output_prefix", "prefix"))], "output": {"adapter": "save-image-v1", "final_node_ids": ["1"], "expected_count": 1}, "presets": [{"quality": "standard", "size": "512x512", "values": {}}], "dependency_lock": {"comfy_commit": expected, "models": [], "custom_nodes": []}, "runtime_profiles": [], "supported_parameters": []}
    assert verify_environment(root, manifest, settings.git_executable)["environment_verified"]
    with pytest.raises(subprocess.CalledProcessError):
        commit(tmp_path / "absent", settings.git_executable)


@pytest.mark.parametrize("selection", ["missing/git.exe", "wrong.exe", "git.exe"])
def test_configured_git_requires_existing_native_executable_file(tmp_path: Path, selection: str) -> None:
    selected = tmp_path / selection
    if selection == "git.exe":
        selected.mkdir()
    elif selection == "wrong.exe":
        selected.write_bytes(b"fixture")
    with pytest.raises((ValueError, OSError)):
        WorkerSettings(control_origin="https://control.invalid", credential_file=tmp_path / "unused", state_root=tmp_path / "state", gpus=[], channels=[], git_executable=selected)
