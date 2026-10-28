"""Build a reproducible, credential-free Skill archive from committed sources."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
ROOT = APP.parent
SKILL = APP / "skills/media-gen-skill"
FILES = ("SKILL.md", "VERSION", "agents/openai.yaml", "references/api.md", "scripts/media_gen.py")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    destination = args.output.resolve()
    if not destination.is_relative_to(APP / ".local") or destination.exists():
        raise SystemExit("Choose a new output directory under app/.local")
    git = shutil.which("git.exe") or shutil.which("git")
    if git is None:
        raise SystemExit("Native Git is required")
    revision = subprocess.check_output([git, "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()  # noqa: S603 -- fixed native Git arguments
    payload = {name: (SKILL / name).read_bytes() for name in FILES}
    for name, data in payload.items():
        tracked = subprocess.check_output([git, "show", revision + ":app/skills/media-gen-skill/" + name], cwd=ROOT)  # noqa: S603 -- fixed native Git arguments
        if tracked != data:
            raise SystemExit("Commit Skill sources before packaging")
    version = payload["VERSION"].decode().strip()
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise SystemExit("Invalid Skill version")
    compile(payload["scripts/media_gen.py"], "media_gen.py", "exec")
    subprocess.run([sys.executable, str(SKILL / "scripts/media_gen.py"), "--help"], check=True, stdout=subprocess.DEVNULL, timeout=10)  # noqa: S603 -- packaged client help, no credentials or network
    record = {"format_version": 1, "name": "media-gen-skill", "version": version, "source_revision": revision,
              "default_base_url": "https://gen.sub2share.xyz", "python_minimum": "3.10", "external_dependencies": [],
              "files": {name: hashlib.sha256(data).hexdigest() for name, data in sorted(payload.items())}}
    payload["release.json"] = (json.dumps(record, ensure_ascii=False, indent=2) + "\n").encode()
    destination.mkdir(parents=True)
    archive = destination / f"media-gen-skill-{version}.zip"
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as output:
        for name, data in sorted(payload.items()):
            info = zipfile.ZipInfo("media-gen-skill/" + name, (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            output.writestr(info, data)
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    (destination / "SHA256SUMS.txt").write_text(checksum + "  " + archive.name + "\n", encoding="utf-8")
    (destination / "release.json").write_bytes(payload["release.json"])
    with zipfile.ZipFile(archive) as check:
        assert len(check.namelist()) == len(payload)
        assert check.testzip() is None
        assert all(check.read("media-gen-skill/" + name) == data for name, data in payload.items())
    print(json.dumps({"archive": str(archive), "bytes": archive.stat().st_size, "sha256": checksum, "version": version, "source_revision": revision}))


if __name__ == "__main__":
    main()
