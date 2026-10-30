"""Refresh generated API types, then check the unchanged Admin in an owned workspace."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "aggregator-service/app"
ADMIN = APP / "apps/admin_web"
DEPENDENCIES = ROOT.parent / "生图生视API封装/app/apps/admin_web/node_modules"
NODE = ROOT.parent / "生图生视API封装/app/.local/toolchains/node-24.22.1/node-v24.22.1-win-x64/node.exe"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    label = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    work = ROOT / ".local/studio-admin-types" / label
    work.mkdir(parents=True)
    native_work = ROOT.parent / ".comfy-studio-native/studio-admin-types" / label
    assert native_work.resolve() == work.resolve()
    generated = work / "api.d.ts"
    types = ADMIN / "src/generated/api.d.ts"
    previous = sha(types)
    env = {**os.environ, "TEMP": str(native_work), "TMP": str(native_work), "PYTHONDONTWRITEBYTECODE": "1"}

    def run(args, name):
        with (work / (name + ".log")).open("wb") as output:
            subprocess.run([str(value) for value in args], cwd=native_work, env=env, stdout=output, stderr=output, check=True, creationflags=subprocess.CREATE_NO_WINDOW)

    # Redocly's Windows file resolver does not decode a non-ASCII input URL.
    # Reuse the verified native-path alias; the exact input bytes stay under ROOT.
    shutil.copyfile(APP / "config/openapi.json", work / "openapi.json")
    assert sha(work / "openapi.json") == sha(APP / "config/openapi.json")
    run([NODE, DEPENDENCIES / "openapi-typescript/bin/cli.js", native_work / "openapi.json", "-o", native_work / "api.d.ts"], "generate")
    types.write_bytes(generated.read_bytes())
    assert types.read_bytes() == generated.read_bytes()
    for name in ("src", "public"):
        if (ADMIN / name).exists():
            shutil.copytree(ADMIN / name, work / name)
    for name in ("index.html", "package.json", "tsconfig.json", "vite.config.ts"):
        shutil.copyfile(ADMIN / name, work / name)
    dependency_link = native_work / "node_modules"
    run(["cmd.exe", "/d", "/c", "mklink", "/J", dependency_link, DEPENDENCIES], "dependency-link")
    assert dependency_link.is_junction() and dependency_link.resolve() == DEPENDENCIES.resolve()
    # The runner loader avoids writing Vite's temporary config into the borrowed
    # dependency tree. Build cache, emitted files and logs stay in this workspace.
    config = work / "vite.config.ts"
    text = config.read_text(encoding="utf-8")
    assert text.count("export default defineConfig({") == 1
    config.write_text(text.replace("export default defineConfig({", "export default defineConfig({\n  root: " + json.dumps(str(work)) + ",\n  cacheDir: " + json.dumps(str(work / "cache")) + ",", 1), encoding="utf-8")
    run([NODE, DEPENDENCIES / "vue-tsc/bin/vue-tsc.js", "--noEmit"], "typecheck")
    run([NODE, DEPENDENCIES / "vite/bin/vite.js", "build", "--configLoader", "runner"], "build")
    assert types.read_bytes() == generated.read_bytes()
    compatibility_path = APP.parent / "docs/evidence/studio-release-132/studio-8eb8130972b846d6bf39443b4a876a71.json"
    compatibility = json.loads(compatibility_path.read_text(encoding="utf-8"))
    assert compatibility["passed"] and compatibility["sources_unchanged"]
    differences = {name: {"before": digest, "after": sha(APP.parent / name)} for name, digest in compatibility["source_sha256"].items() if sha(APP.parent / name) != digest}
    assert set(differences) == {"app/apps/admin_web/src/generated/api.d.ts"}
    dist = {path.relative_to(work / "dist").as_posix(): sha(path) for path in (work / "dist").rglob("*") if path.is_file()}
    result = {"passed": True, "at": datetime.now(UTC).isoformat(), "scope": "Generated API binding consistency and native Admin type/build check; the 1486-case Python regression remains unchanged", "node": "24.22.1", "openapi_sha256": sha(APP / "config/openapi.json"), "generated_types_before": differences["app/apps/admin_web/src/generated/api.d.ts"]["before"], "generated_types_at_check_start": previous, "generated_types_sha256": sha(types), "generated_copy_matches": True, "typecheck_passed": True, "build_passed": True, "source_differences_from_compatibility": differences, "python_sources_unchanged_from_compatibility": True, "compatibility_report": compatibility_path.relative_to(APP.parent).as_posix(), "compatibility_report_sha256": sha(compatibility_path), "dist_sha256": dist, "private_workspace": work.relative_to(ROOT).as_posix(), "logs_sha256": {name: sha(work / (name + ".log")) for name in ("generate", "dependency-link", "typecheck", "build")}, "owned_dependency_junction_retained": True, "dependency_tree_used_read_only": True, "virtualization_used": False}
    target = APP.parent / "docs/evidence/studio-release-132/admin-generated-types-1322.json"
    with target.open("x", encoding="utf-8") as output:
        output.write(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"passed": True, "generated_binding_updated": True, "native_admin_typecheck_and_build": True, "python_regression_sources_unchanged": True, "dist_files": len(dist)}))
