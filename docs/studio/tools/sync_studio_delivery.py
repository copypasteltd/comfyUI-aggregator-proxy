"""Mirror reviewed source/docs into the already authorised delivery repository.

Never copies Git internals, node_modules, build output or ignored private data.
The canonical workspace remains the place to edit the frontend.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "infinite-canvas"
REPOSITORY = ROOT / "aggregator-service"
DESTINATION = REPOSITORY / "app/apps/studio_web"
DOCS = REPOSITORY / "docs/studio"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def copy(source: Path, target: Path) -> None:
    if not source.resolve().is_relative_to(ROOT.resolve()) or not target.resolve().is_relative_to(REPOSITORY.resolve()) or source.is_symlink() or source.is_junction():
        raise RuntimeError("Mirror path is not an owned ordinary file")
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists() or source.read_bytes() != target.read_bytes():
        shutil.copyfile(source, target)


if __name__ == "__main__":
    assert (REPOSITORY / ".git").is_dir()
    tracked = subprocess.check_output(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", "web", "VERSION", "CHANGELOG.md", "LICENSE", ".gitignore"], cwd=SOURCE).decode("utf-8").split("\0")
    sources = {}
    for name in sorted(set(tracked) - {""}):
        if any(part in {"node_modules", ".local", "dist", ".git"} for part in Path(name).parts):
            raise RuntimeError("Private/generated directory unexpectedly selected")
        original = SOURCE / name
        if not original.is_file():
            continue
        copy(original, DESTINATION / name)
        sources[name] = sha(original)
    for original in sorted((ROOT / "docs").rglob("*")):
        if original.is_file() and not any(part in {"__pycache__", ".local", "node_modules", ".git"} for part in original.relative_to(ROOT / "docs").parts):
            copy(original, DOCS / original.relative_to(ROOT / "docs"))
    for language in ("", ".en"):
        text = (ROOT / f"README{language}.md").read_text(encoding="utf-8")
        text = text.replace("](docs/", "](").replace("](README.en.md)", "](USAGE.en.md)").replace("](README.md)", "](USAGE.md)")
        (DOCS / f"USAGE{language}.md").write_text(text, encoding="utf-8")
    # Source links in archived research now point at the public delivery tree.
    for document in DOCS.glob("*.md"):
        original_bytes = document.read_bytes()
        text = document.read_text(encoding="utf-8")
        text = text.replace("](../infinite-canvas/", "](../../app/apps/studio_web/")
        text = text.replace("](../aggregator-service/docs/", "](../")
        text = text.replace("](../../生图生视API封装/docs/", "](../")
        text = text.replace("](../../" + quote("生图生视API封装") + "/docs/", "](../")
        if document.name == "README.md":
            text = text.replace("](../README.md)", "](USAGE.md)").replace("](../README.en.md)", "](USAGE.en.md)")
        newline = "\r\n" if b"\r\n" in original_bytes else "\n"
        document.write_bytes(text.replace("\n", newline).encode("utf-8"))
    (DESTINATION / "README.md").write_text("""# Managed ComfyUI Studio / 托管创作画布

[中文完整说明](../../../docs/studio/USAGE.md) · [English guide](../../../docs/studio/USAGE.en.md) · [All 46 requirements and progress](../../../docs/studio/10-开发进度与交付记录.md)

This is the reviewed delivery mirror. Canonical development is in the separate 无限画布/infinite-canvas workspace. Build web/ with Node 22.14.0, npm 10.9.2 and npm ci; set VITE_STUDIO_MODE=managed and VITE_BASE=/studio/, then npm run build. Serve web/dist/ with the documented SPA/MIME/CSP rules. No Agent, Docker or WSL is required. Admin and business APIs remain in the existing aggregator.

The main document rejects arbitrary scripts/plugins; JSON Schema compiles only inside an isolated Worker. Business requests use Bearer credentials with cookies omitted. Seven task types share the original durable service; the canvas is not a second queue. Current release/real-media/mobile limitations are recorded in the progress document.

MIT upstream attribution and original Lime/font licenses are retained; see UPSTREAM.md. Historical research tools under docs/studio/tools are workspace-specific evidence tools, not an automatic production installer. Credentials and private raw logs are not included.
""", encoding="utf-8")
    (DESTINATION / "UPSTREAM.md").write_text("""# Upstream and design provenance

- Project: https://github.com/basketikun/infinite-canvas
- Upstream version: v0.20.0
- Base commit: 9cd3deacbcc598361807afc16c059a80dd18b6f9
- License: MIT; original LICENSE included without modification.
- This reviewed managed adaptation preserves the upstream author attribution. It is not an upstream release.
- Required visual source: 04 · 青柠 / Lime, https://html.cafe/x5b381ea1 . Original CSS/font declarations, hashes and licenses are retained under web/src/styles/reference/ and web/public/fonts/; adapters are separate.
- Three.js 0.186.1 / MIT powers the on-demand static GLB viewer; its original license is retained in web/public/licenses/three-MIT.txt. No decoder CDN or external model-resource loading is enabled.
- delivery-source.json records every copied source file's digest. Static build and deployment artifacts have their own evidence; a source mirror does not assert deployment or GPU acceptance.
""", encoding="utf-8")
    manifest = {"format_version": 1, "upstream_commit": "9cd3deacbcc598361807afc16c059a80dd18b6f9", "recorded_at": datetime.now(UTC).isoformat(), "source_files": sources, "canonical_source": "无限画布/infinite-canvas", "secrets_and_generated_dependencies_included": False}
    previous_manifest = DESTINATION / "delivery-source.json"
    if previous_manifest.exists():
        previous = json.loads(previous_manifest.read_text(encoding="utf-8"))
        if previous.get("source_files") == sources:
            manifest["recorded_at"] = previous["recorded_at"]
    (DESTINATION / "delivery-source.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"source_files": len(sources), "destination": str(DESTINATION), "docs_mirrored": True}))
