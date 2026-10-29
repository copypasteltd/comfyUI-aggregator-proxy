"""Apply a verified static package and rehearse the expected old/new pointers."""
import argparse
from pathlib import Path
import hashlib
import json
import runpy
import subprocess
import re

TASK = Path("/var/lib/comfyui-aggregator-deploy/studio131")
BASE = Path("/opt/comfyui-aggregator")
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--build", default="static-build-1312.json")
parser.add_argument("--previous", default="studio-20261030.131.1")
args = parser.parse_args()
assert re.fullmatch(r"static-build-\d+\.json", args.build)
assert re.fullmatch(r"studio-\d{8}\.\d+\.\d+", args.previous)
tools = runpy.run_path(str(TASK / "studio131-host.py"))
build = json.loads((TASK / args.build).read_text())
assert re.fullmatch(r"studio-\d{8}\.\d+\.\d+", build["release_id"])
target = BASE / "studio-releases" / build["release_id"]
link = BASE / "studio-current"
old = link.resolve()
assert old.name == args.previous and old.name != target.name
manifest = tools["unpack"](target.name + ".tar.gz", target, "studio-deployment.json", build["archive_sha256"])
assert manifest["release_id"] == target.name
steps = []
try:
    for value in (target, old, target):
        tools["symlink"](value, link)
        output = subprocess.check_output(["curl", "--fail", "--silent", "-H", "Host: 43.165.167.179", "http://127.0.0.1:39443/studio/"])
        assert hashlib.sha256(output).digest() == hashlib.sha256((value / "studio/index.html").read_bytes()).digest()
        steps.append(value.name)
except BaseException:
    tools["symlink"](old, link)
    raise
result = {"passed": True, "steps": steps, "actual_old_and_new_static_packages": True, "browser_storage_modified": False, "backend_restarted": False, "source_sha256": manifest["source_sha256"], "archive_sha256": build["archive_sha256"]}
receipt = TASK / ("static-upgrade-" + target.name + ".json")
assert not receipt.exists()
receipt.write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result))
