"""Create an isolated, explicitly patched frontend for build-feasibility checks."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UPSTREAM = ROOT / "infinite-canvas"
TARGET = ROOT / ".local/assessment-native/probe"
EVIDENCE = ROOT / "docs/research/fusion-assessment-2026-10-30"


def main() -> None:
    TARGET.resolve().relative_to(ROOT.resolve())
    if TARGET.exists():
        raise SystemExit("Probe already exists; do not overwrite an earlier run.")
    names = subprocess.run(
        ["git", "-C", str(UPSTREAM), "ls-files", "-z"],
        check=True, capture_output=True, encoding="utf-8",
    ).stdout.split("\0")
    copied = []
    for name in names:
        if not (name.startswith("web/") or name in {"VERSION", "CHANGELOG.md", "AGENTS.md", "LICENSE"}):
            continue
        source = UPSTREAM / name
        destination = TARGET / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        copied.append(name)
    package_file = TARGET / "web/package.json"
    package = json.loads(package_file.read_text(encoding="utf-8"))
    removed = package["dependencies"].pop("@ant-design/pro-components")
    package_file.write_text(json.dumps(package, ensure_ascii=False, indent=4) + "\n", encoding="utf-8")
    provider_file = TARGET / "web/src/components/layout/app-providers.tsx"
    provider = provider_file.read_text(encoding="utf-8")
    for old in [
        'import { ProConfigProvider } from "@ant-design/pro-components";\n',
        "            <ProConfigProvider dark={dark}>\n",
        "            </ProConfigProvider>\n",
    ]:
        if provider.count(old) != 1:
            raise ValueError("Provider source changed; manual review required.")
        provider = provider.replace(old, "")
    provider_file.write_text(provider, encoding="utf-8")
    editor_file = TARGET / "web/src/components/layout/model-script-editor.tsx"
    editor = editor_file.read_text(encoding="utf-8")
    old = '                content: { height: "100dvh",'
    if editor.count(old) != 1:
        raise ValueError("Modal source changed; manual review required.")
    editor_file.write_text(editor.replace(old, '                container: { height: "100dvh",'), encoding="utf-8")
    changes = []
    for name in ["web/package.json", "web/src/components/layout/app-providers.tsx", "web/src/components/layout/model-script-editor.tsx"]:
        changes.append({
            "path": name,
            "before_sha256": hashlib.sha256((UPSTREAM / name).read_bytes()).hexdigest(),
            "after_sha256": hashlib.sha256((TARGET / name).read_bytes()).hexdigest(),
        })
    report = {
        "checked_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        "scope": "isolated build probe, not the downloaded upstream or an integrated release",
        "path": str(TARGET),
        "copied_files": len(copied),
        "removed_dependency": {"@ant-design/pro-components": removed},
        "reason": "Only ProConfigProvider is imported; retain existing antd ConfigProvider and theme. Pro components require antd 5, while this frontend uses antd 6.",
        "modal_fix": "Use Ant Design 6 styles.container instead of the old styles.content; verified against the installed type declaration.",
        "lock_file_status": "Copied original; npm installation must regenerate and verify it.",
        "changes": changes,
        "original_install": {"command": "npm ci --no-audit --no-fund", "exit_code": 1, "error": "ERESOLVE: pro-components peer antd ^5.11.2 conflicts with antd ^6.4.2"},
        "original_lock_missing_dependencies": ["i18next", "react-i18next"],
    }
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / "native-probe-preparation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"probe": str(TARGET), "copied_files": len(copied)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
