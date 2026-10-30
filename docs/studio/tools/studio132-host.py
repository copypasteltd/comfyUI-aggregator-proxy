"""Additive native API update using the previously verified deployment helpers."""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

TASK = Path("/var/lib/comfyui-aggregator-deploy/studio132")
spec = importlib.util.spec_from_file_location("studio_host_core", TASK / "studio131-host.py")
assert spec and spec.loader
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)
core.TASK = TASK
core.RELEASE_ID = "linux-20261030.132.2"
core.RELEASE = core.BASE / "releases" / core.RELEASE_ID
core.OLD = core.BASE / "releases/linux-20261030.131.1"

if __name__ == "__main__":
    action = sys.argv[1]
    if action == "extract":
        assert (core.BASE / "current").resolve() == core.OLD and not core.RELEASE.exists()
        build = json.loads((TASK / "cloud-build.json").read_text())
        assert build["release_id"] == core.RELEASE_ID
        manifest = core.unpack(core.RELEASE_ID + ".tar.gz", core.RELEASE, "linux-deployment.json", build["archive_sha256"])
        assert manifest["release_id"] == core.RELEASE_ID and manifest["application_source_revision"] == build["application_source_revision"]
        assert not manifest["database_schema_changed"] and not manifest["worker_protocol_changed"]
        print(json.dumps({"archive_verified": True, "release_id": core.RELEASE_ID}))
    elif action == "verify-installed":
        core.verify_installed()
    elif action == "promote":
        assert (TASK / "prepare.state").read_text().strip() == "prepared"
        assert (core.BASE / "current").resolve() == core.OLD
        backup = core.check_backup()
        result = {"passed": True, "action": "promote-1322", "at": datetime.now(UTC).isoformat(), "backup": backup, "steps": [core.switch(core.RELEASE)], "database_migration_executed": False, "worker_restarted": False, "static_pointer_changed": False}
        with (TASK / "promote.json").open("x") as output:
            output.write(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result))
    elif action == "probe":
        print(json.dumps(core.db_probe()))
    else:
        raise SystemExit("Unknown fixed 132 deployment action")
