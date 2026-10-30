"""137 Admin types/build in an isolated native workspace, borrowing dependencies read-only."""
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


def main():
    label = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    work = ROOT / ".local/studio137-admin" / label
    work.mkdir(parents=True)
    native = ROOT.parent / ".comfy-studio-native/studio137-admin" / label
    assert native.resolve() == work.resolve()
    env = {**os.environ, "TEMP": str(native), "TMP": str(native), "NODE_COMPILE_CACHE": str(native / "node-compile-cache")}

    def run(args, name):
        with (work / (name + ".log")).open("wb") as output:
            subprocess.run([str(item) for item in args], cwd=native, env=env, stdout=output, stderr=output, check=True, creationflags=subprocess.CREATE_NO_WINDOW)

    shutil.copyfile(APP / "config/openapi.json", work / "openapi.json")
    run([NODE, DEPENDENCIES / "openapi-typescript/bin/cli.js", native / "openapi.json", "-o", native / "api.d.ts"], "generate")
    (ADMIN / "src/generated/api.d.ts").write_bytes((work / "api.d.ts").read_bytes())
    shutil.copytree(ADMIN / "src", work / "src")
    if (ADMIN / "public").exists():
        shutil.copytree(ADMIN / "public", work / "public")
    for name in ("index.html", "package.json", "tsconfig.json", "vite.config.ts"):
        shutil.copyfile(ADMIN / name, work / name)
    dependency_link = native / "node_modules"
    run(["cmd.exe", "/d", "/c", "mklink", "/J", dependency_link, DEPENDENCIES], "dependency-link")
    assert dependency_link.is_junction() and dependency_link.resolve() == DEPENDENCIES.resolve()
    config = work / "vite.config.ts"
    value = config.read_text(encoding="utf-8")
    assert value.count("export default defineConfig({") == 1
    config.write_text(value.replace("export default defineConfig({", "export default defineConfig({\n  root: " + json.dumps(str(work)) + ",\n  cacheDir: " + json.dumps(str(work / "cache")) + ",", 1), encoding="utf-8")
    run([NODE, DEPENDENCIES / "vue-tsc/bin/vue-tsc.js", "--noEmit"], "typecheck")
    run([NODE, DEPENDENCIES / "vite/bin/vite.js", "build", "--configLoader", "runner"], "build")
    source = {path.relative_to(ADMIN).as_posix(): sha(path) for path in (ADMIN / "src").rglob("*") if path.is_file()}
    assert all(sha(work / name) == digest for name, digest in source.items())
    result = {"passed": True, "at": datetime.now(UTC).isoformat(), "scope": "137 Admin generation/type/build; not browser or real GPU acceptance", "node": "24.22.1", "source_sha256": source, "openapi_sha256": sha(APP / "config/openapi.json"), "generated_types_sha256": sha(ADMIN / "src/generated/api.d.ts"), "typecheck_passed": True, "build_passed": True, "dist_sha256": {path.relative_to(work / "dist").as_posix(): sha(path) for path in (work / "dist").rglob("*") if path.is_file()}, "private_workspace": work.relative_to(ROOT).as_posix(), "logs_sha256": {name: sha(work / (name + ".log")) for name in ("generate", "dependency-link", "typecheck", "build")}, "owned_dependency_junction_retained": True, "dependency_tree_used_read_only": True, "virtualization_used": False}
    target = APP.parent / "docs/evidence/studio-release-137" / ("admin-build-" + label + ".json")
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as output:
        output.write(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"passed": True, "private_workspace": result["private_workspace"], "evidence": target.name, "dist_files": len(result["dist_sha256"])}))


if __name__ == "__main__":
    main()
