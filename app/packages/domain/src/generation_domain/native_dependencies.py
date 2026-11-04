from __future__ import annotations

import re
from typing import Any

from generation_domain.errors import DomainError

NATIVE_DEPENDENCY_FEATURE = "native-dependency-roots-v1"
NATIVE_ROOT_NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
PRIVATE_PARTS = frozenset({".git", "__pycache__", ".cache", "cache", "jobs", "input", "output", "private", ".env"})


def validate_native_dependencies(manifest: dict[str, Any]) -> None:
    from generation_domain.workflows import relative_file

    lock = manifest["dependency_lock"]
    roots = lock.get("native_roots", [])
    if not isinstance(roots, list) or len(roots) > 8 or roots and (manifest["format_version"] != "1.3" or lock.get("mode")):
        raise DomainError("native_dependencies_invalid", "原生辅助依赖仅用于本地 1.3 发布包，最多八个根", 422)
    names: set[str] = set()
    count = 0
    for root in roots:
        if not isinstance(root, dict) or set(root) != {"name", "files"} or not isinstance(root["name"], str) or not NATIVE_ROOT_NAME.fullmatch(root["name"]) or root["name"] in names or not isinstance(root["files"], list) or not root["files"]:
            raise DomainError("native_dependencies_invalid", "原生辅助依赖必须固定唯一根名称及非空文件清单", 422)
        names.add(root["name"])
        paths: set[str] = set()
        for item in root["files"]:
            count += 1
            if count > 4096 or not isinstance(item, dict) or set(item) != {"path", "sha256", "bytes"} or not isinstance(item["path"], str) or not 1 <= len(item["path"]) <= 512 or not isinstance(item["sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", item["sha256"]) or type(item["bytes"]) is not int or not 0 < item["bytes"] <= 1024**4:
                raise DomainError("native_dependencies_invalid", "原生依赖须固定受控相对文件、SHA-256 和有界大小", 422)
            path = relative_file(item["path"])
            if path.casefold() in paths or any(part.casefold() in PRIVATE_PARTS or any(ord(c) < 32 for c in part) for part in path.split("/")):
                raise DomainError("native_dependencies_invalid", "原生依赖不能重复或引用客户文件、缓存和私有配置", 422)
            paths.add(path.casefold())
