"""Build seven native pure-Python wheels offline; preserve all writes in this project."""
from __future__ import annotations

import hashlib
import argparse
import json
import os
import subprocess
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "aggregator-service/app"
sys.path.insert(0, str(APP / "scripts"))
from release_source import source_snapshot  # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--increment', choices=('132', '137'), default='132')
    arguments = parser.parse_args()
    before = source_snapshot(APP)
    label = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    private = ROOT / ".local/studio-wheel-build" / label
    private.mkdir(parents=True)
    output = APP / ".local/studio-wheels"
    output.mkdir(parents=True, exist_ok=True)
    environment = {**os.environ, "TMP": str(private), "TEMP": str(private), "PYTHONDONTWRITEBYTECODE": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1"}
    projects = ["packages/domain", "packages/contracts", "packages/application", "packages/infrastructure", "apps/control_api", "apps/control_runner", "apps/windows_worker"]
    with (private / "build.log").open("wb") as log:
        subprocess.run([sys.executable, "-B", "-m", "pip", "wheel", "--no-index", "--no-deps", "--no-build-isolation", "--no-cache-dir", "--wheel-dir", str(output), *[str(APP / name) for name in projects]], cwd=APP, env=environment, stdout=log, stderr=log, check=True)
    wheels = sorted(output.glob("*.whl"))
    assert len(wheels) == 7
    for wheel in wheels:
        with zipfile.ZipFile(wheel) as archive:
            assert archive.testzip() is None
            metadata = next(name for name in archive.namelist() if name.endswith(".dist-info/WHEEL"))
            assert "Root-Is-Purelib: true" in archive.read(metadata).decode()
    assert source_snapshot(APP) == before
    result = {"passed": True, "at": datetime.now(UTC).isoformat(), "projects": projects, "offline": True, "virtualization_used": False, "source_unchanged": True, "wheels": {wheel.name: hashlib.sha256(wheel.read_bytes()).hexdigest() for wheel in wheels}, "private_log": (private / "build.log").relative_to(ROOT).as_posix()}
    target = ROOT / ("aggregator-service/docs/evidence/studio-release-" + arguments.increment) / ("native-wheels-" + label + ".json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result))
