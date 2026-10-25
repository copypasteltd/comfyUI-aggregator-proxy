"""Explicitly prepare the pinned WinSW build asset; never execute or install it."""

from __future__ import annotations

import argparse
import base64
import json
import shutil
import sys
import urllib.request
from pathlib import Path

from release_bundle import SERVICE_TOOL_FILES, ReleaseError, digest_file

ROOT = Path(__file__).resolve().parents[1]


def verify(directory: Path) -> None:
    if directory.is_symlink() or directory.is_junction():
        raise ReleaseError("service_tool_source_link")
    for name, expected in SERVICE_TOOL_FILES.items():
        path = directory / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size != expected["bytes"] or digest_file(path) != expected["sha256"]:
            raise ReleaseError("service_tool_source_digest")


def fetch(url: str, *, binary: bool) -> bytes:
    if url not in {"https://api.github.com/repos/winsw/winsw/releases/assets/93386826?download=1", "https://api.github.com/repos/winsw/winsw/contents/LICENSE.txt?ref=v2.12.0"}:
        raise ReleaseError("service_tool_source_not_allowlisted")
    request = urllib.request.Request(url, headers={"Accept": "application/octet-stream" if binary else "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "render-control-pinned-service-tools"})  # noqa: S310 -- exact official HTTPS source allowlist above
    with urllib.request.urlopen(request, timeout=25) as response:  # noqa: S310 -- literal official HTTPS GitHub URLs; authenticated content hashes before any use
        return response.read(700000)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--source", type=Path, help="Existing directory with the exact pinned binary and MIT license")
    selection.add_argument("--download", action="store_true", help="Explicitly fetch the two pinned files from official GitHub")
    args = parser.parse_args()
    target = ROOT / ".local/toolchains/winsw-2.12.0"
    created = False
    try:
        if target.exists():
            verify(target)
        else:
            source = args.source.resolve(strict=True) if args.source else None
            if source:
                verify(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.parent.is_symlink() or target.parent.is_junction():
                raise ReleaseError("service_tool_target_link")
            target.mkdir(exist_ok=False)
            created = True
            if source:
                for name in SERVICE_TOOL_FILES:
                    shutil.copyfile(source / name, target / name)
            else:
                (target / "WinSW.NET461.exe").write_bytes(fetch("https://api.github.com/repos/winsw/winsw/releases/assets/93386826?download=1", binary=True))
                license_data = json.loads(fetch("https://api.github.com/repos/winsw/winsw/contents/LICENSE.txt?ref=v2.12.0", binary=False))
                (target / "LICENSE.txt").write_bytes(base64.b64decode(license_data["content"], validate=False))
            verify(target)
        print(json.dumps({"prepared": True, "version": "2.12.0", "files": 2, "downloaded": bool(created and args.download), "executables_started": False, "services_registered": False}))
    except Exception as exc:
        if created:
            for name in SERVICE_TOOL_FILES:
                (target / name).unlink(missing_ok=True)
            target.rmdir()
        print(json.dumps({"error": str(exc) if isinstance(exc, ReleaseError) else "service_tool_preparation_failed"}), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
