"""Publish verified Skill assets through the existing native Nginx edge (Linux root)."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

CONFIG = Path("/etc/comfyui-aggregator/nginx.conf")
INCLUDE = CONFIG.with_name("public-skills.conf")
PUBLIC = Path("/var/lib/comfyui-aggregator/public-skills/media-gen")
APPLICATION = Path("/opt/comfyui-aggregator/current")
UNITS = [f"comfy-aggregator-{name}.service" for name in ("api", "runner", "gc", "http-worker", "edge")]
NGINX = ["/usr/bin/nginx", "-e", "stderr", "-p", "/var/lib/comfyui-aggregator/edge/", "-c", str(CONFIG), "-t"]
MARKER = b"        include /etc/comfyui-aggregator/public-skills.conf;\n"
ANCHOR = b"        location = / { return 302 /admin/; }"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_write(path: Path, data: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".skill-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def point_to(target: str | None) -> None:
    pointer = PUBLIC / "current"
    if target is None:
        pointer.unlink(missing_ok=True)
        return
    temporary = PUBLIC / f".current-{os.getpid()}"
    try:
        temporary.symlink_to(target, target_is_directory=True)
        os.replace(temporary, pointer)
    finally:
        temporary.unlink(missing_ok=True)


def run(arguments: list[str]) -> str:
    result = subprocess.run(arguments, capture_output=True, text=True, timeout=30, check=False)  # noqa: S603 -- fixed service/config commands, no shell
    if result.returncode:
        raise RuntimeError(f"{arguments[0]} exited {result.returncode}: {result.stderr[-2000:]}")
    return result.stdout.strip()


def service_snapshot() -> dict[str, str]:
    result = {name: run(["/usr/bin/systemctl", "show", name, "-p", "MainPID", "--value"]) for name in UNITS}
    if any(pid == "0" or run(["/usr/bin/systemctl", "is-active", name]) != "active" for name, pid in result.items()):
        raise RuntimeError("An existing application service is not running")
    return result


def request(path: str, method: str = "GET", headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], bytes]:
    req = urllib.request.Request("http://127.0.0.1:39443" + path, method=method, headers={"Host": "43.165.167.179", **(headers or {})})
    try:
        response = urllib.request.urlopen(req, timeout=10)  # noqa: S310 -- fixed loopback listener, never sends credentials
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, {key.lower(): value for key, value in response.headers.items()}, response.read()


def verify_http(version: str, data: bytes) -> dict[str, object]:
    status, headers, body = request("/skill.md", headers={"Origin": "https://any-origin.example"})
    assert status == 200 and body == data
    assert headers["content-type"] == "text/markdown; charset=utf-8"
    assert headers["cache-control"] == "no-cache" and headers["access-control-allow-origin"] == "https://any-origin.example"
    assert headers["access-control-allow-credentials"] == "true"
    assert headers["x-content-type-options"] == "nosniff" and headers.get("etag")
    status, head, body = request("/skill.md", "HEAD")
    assert status == 200 and not body and int(head["content-length"]) == len(data)
    status, _, body = request("/skill.md", headers={"If-None-Match": headers["etag"]})
    assert status == 304 and not body
    preflight, cors, _ = request("/skill.md", "OPTIONS", {"Origin": "https://second-origin.example", "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "Authorization,X-Custom-Header", "Access-Control-Request-Private-Network": "true"})
    assert preflight == 204, f"Public Skill preflight returned {preflight} instead of 204"
    assert cors["access-control-allow-origin"] == "https://second-origin.example"
    assert cors["access-control-allow-methods"] == "GET" and cors["access-control-allow-headers"] == "Authorization,X-Custom-Header"
    assert cors["access-control-allow-private-network"] == "true" and cors["access-control-allow-credentials"] == "true"
    status, history, body = request(f"/skills/media-gen/{version}/skill.md")
    assert status == 200 and body == data and "immutable" in history["cache-control"]
    assert request("/skills/media-gen/0.0.0/skill.md")[0] == 404
    assert request(f"/skills/media-gen/{version}/unknown.txt")[0] == 404
    assert request("/internal/health")[0] == 404
    assert request("/health/live")[0] == 200
    assert request("/v1/models")[0] == 401
    return {"get_head": True, "conditional_get": True, "cors_options": True, "immutable_version": True, "missing_paths_404": True, "health_live": True, "business_auth_preserved": True, "private_ingress_preserved": True, "sha256": digest(data)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--expected-config-sha256", required=True)
    parser.add_argument("--snippet", type=Path, default=Path(__file__).with_name("public-skills.nginx.conf"))
    args = parser.parse_args()
    if os.name != "posix" or os.geteuid() != 0:
        raise SystemExit("Use the existing native Linux host as root; this tool never starts local services")
    before = CONFIG.read_bytes()
    if digest(before) != args.expected_config_sha256:
        raise SystemExit("Nginx config changed: inspect and supply its current SHA-256 before publishing")
    manifest = json.loads((args.release_dir / "release.json").read_text("utf-8"))
    version = manifest["version"]
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise SystemExit("Invalid release version")
    names = ["skill.md", "release.json", "SHA256SUMS.txt", f"media-gen-skill-{version}.zip"]
    payload = {name: (args.release_dir / name).read_bytes() for name in names}
    expected = {name: checksum for checksum, name in (line.split("  ", 1) for line in payload["SHA256SUMS.txt"].decode().splitlines())}
    if expected != {name: digest(payload[name]) for name in ("skill.md", names[-1])}:
        raise SystemExit("Release checksums do not match")
    if manifest["single_file"]["sha256"] != digest(payload["skill.md"]) or manifest["single_file"]["bytes"] != len(payload["skill.md"]):
        raise SystemExit("Single-file source manifest does not match")
    with zipfile.ZipFile(args.release_dir / names[-1]) as archive:
        assert archive.testzip() is None
        assert set(archive.namelist()) == {"media-gen-skill/" + name for name in [*manifest["files"], "release.json"]}
        assert archive.read("media-gen-skill/release.json") == payload["release.json"]
        assert all(digest(archive.read("media-gen-skill/" + name)) == value for name, value in manifest["files"].items())
    snippet = args.snippet.read_bytes()
    assert b"public-skills/media-gen" in snippet and b"/skill.md" in snippet
    if MARKER in before:
        assert before.count(MARKER) == 1
        updated = before
    else:
        assert before.count(ANCHOR) == 1 and b"listen 39443;" in before.split(ANCHOR)[0]
        updated = before.replace(ANCHOR, MARKER + ANCHOR, 1)
    previous_include = INCLUDE.read_bytes() if INCLUDE.exists() else None
    PUBLIC.mkdir(parents=True, exist_ok=True, mode=0o755)
    pointer = PUBLIC / "current"
    assert pointer.is_symlink() or not pointer.exists(), "current must be a managed symlink"
    old_pointer = os.readlink(pointer) if pointer.is_symlink() else None
    destination = PUBLIC / version
    if destination.exists():
        if destination.is_symlink() or set(path.name for path in destination.iterdir()) != set(names) or any((destination / name).read_bytes() != data for name, data in payload.items()):
            raise SystemExit("An immutable version already exists with different bytes")
    else:
        stage = Path(tempfile.mkdtemp(prefix=".release-", dir=PUBLIC))
        try:
            for name, data in payload.items():
                atomic_write(stage / name, data)
            stage.chmod(0o755)
            stage.rename(destination)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
    services, application = service_snapshot(), str(APPLICATION.resolve())
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")  # noqa: UP017 -- deployment host supports Python 3.10
    backup = CONFIG.with_name(f"nginx.conf.before-skill-{stamp}")
    backup.write_bytes(before)
    backup.chmod(0o600)
    try:
        atomic_write(INCLUDE, snippet)
        atomic_write(CONFIG, updated)
        run(NGINX)
        point_to(version)
        run(["/usr/bin/systemctl", "reload", "comfy-aggregator-edge.service"])
        # Graceful reload acknowledges the master before new workers accept connections.
        for attempt in range(10):
            try:
                proof = verify_http(version, payload["skill.md"])
                break
            except (AssertionError, urllib.error.URLError):
                if attempt == 9:
                    raise
                time.sleep(0.3)
        assert service_snapshot() == services and str(APPLICATION.resolve()) == application
    except BaseException:
        point_to(old_pointer)
        atomic_write(CONFIG, before)
        if previous_include is None:
            INCLUDE.unlink(missing_ok=True)
        else:
            atomic_write(INCLUDE, previous_include)
        run(NGINX)
        run(["/usr/bin/systemctl", "reload", "comfy-aggregator-edge.service"])
        raise
    result = {"at": datetime.now(timezone.utc).isoformat(), "version": version, "source_revision": manifest["source_revision"], "application_release": application, "services_unchanged": True, "service_pids": services, "previous_pointer": old_pointer, "current_pointer": version, "config_before_sha256": digest(before), "config_after_sha256": digest(CONFIG.read_bytes()), "snippet_sha256": digest(snippet), "backup": str(backup), "http": proof}  # noqa: UP017 -- deployment host supports Python 3.10
    atomic_write(PUBLIC / f"deployment-{stamp}.json", (json.dumps(result, indent=2) + "\n").encode())
    print(json.dumps(result))


if __name__ == "__main__":
    main()
