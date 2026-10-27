from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path
from typing import Any

from generation_domain.errors import DomainError
from generation_domain.values import fingerprint
from generation_domain.workflows import relative_file, validate_manifest


def safe_path(root: Path, relative: str) -> Path:
    target = (root / relative_file(relative)).resolve()
    if root.resolve() not in target.parents:
        raise DomainError("unsafe_path", "ComfyUI 文件路径越界", 422)
    return target


def file_digest(path: Path, maximum: int) -> tuple[str, int]:
    digest, size = hashlib.sha256(), 0
    with path.open("rb") as source:
        while data := source.read(1024 * 1024):
            size += len(data)
            if size > maximum:
                raise DomainError("file_too_large", "依赖或输出文件超过声明大小", 422)
            digest.update(data)
    return digest.hexdigest(), size


def commit(root: Path, git_executable: Path | None = None) -> str:
    executable = str(git_executable) if git_executable is not None else shutil.which("git")
    if not executable:
        raise DomainError("git_unavailable", "缺少固定依赖版本校验所需的原生 Git")
    result = subprocess.run([executable, "-c", "safe.directory=" + str(root), "-C", str(root), "rev-parse", "HEAD"], capture_output=True, timeout=10, check=True)  # noqa: S603 -- operator-selected local repository, no global Git configuration changes
    return result.stdout.decode("ascii").strip()


def verify_environment(root: Path, manifest: dict[str, Any], git_executable: Path | None = None) -> dict[str, Any]:
    validate_manifest(manifest, manifest["task_type"])
    lock = manifest["dependency_lock"]
    if lock.get("mode") == "comfy-api-only-v1":
        raise DomainError("dependency_mode_mismatch", "API 模式发布包必须由显式启用的 HTTP Worker 执行")
    if commit(root, git_executable) != lock["comfy_commit"]:
        raise DomainError("comfy_version_mismatch", "本机 ComfyUI 提交与发布包不一致")
    checked = []
    for model in lock["models"]:
        target = safe_path(root / "models", model["path"])
        digest, size = file_digest(target, model["bytes"])
        if digest != model["sha256"] or size != model["bytes"]:
            raise DomainError("model_digest_mismatch", "模型文件大小或摘要不符")
        checked.append({"path": model["path"], "sha256": digest, "bytes": size})
    for node in lock["custom_nodes"]:
        if commit(safe_path(root / "custom_nodes", node["directory"]), git_executable) != node["commit"]:
            raise DomainError("custom_node_mismatch", "自定义节点提交与发布包不一致")
    return {"environment_verified": True, "environment_digest": fingerprint({"dependency_lock": lock, "models": checked})}
