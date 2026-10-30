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
parser.add_argument("--promote-only", action="store_true", help="No old-UI roundtrip after a forward browser-storage upgrade")
parser.add_argument("--local-blob-resources", action="store_true", help="Allow the GLB loader to read embedded texture blobs in the Studio CSP")
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
assert manifest.get("browser_storage_version", 1) < 2 or args.promote_only, "Forward browser storage cannot rehearse an old UI rollback"
steps = []
config = Path('/etc/comfyui-aggregator/nginx.conf')
config_before = None
def reload_edge():
    subprocess.run(['/usr/bin/nginx', '-t', '-e', 'stderr', '-p', '/var/lib/comfyui-aggregator/edge/', '-c', str(config)], check=True)
    subprocess.run(['systemctl', 'reload', 'comfy-aggregator-edge.service'], check=True)
try:
    if args.local_blob_resources:
        assert args.promote_only
        config_before = config.read_bytes()
        text = config_before.decode()
        needle = "worker-src 'self'; connect-src http: https:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
        assert text.count(needle) == 1
        backup = TASK / (target.name + '-nginx-before-glb.conf')
        assert not backup.exists()
        backup.write_bytes(config_before)
        config.write_text(text.replace(needle, needle.replace('https:;', 'https: blob:;'), 1))
        reload_edge()
    for value in ((target,) if args.promote_only else (target, old, target)):
        tools["symlink"](value, link)
        output = subprocess.check_output(["curl", "--fail", "--silent", "-H", "Host: 43.165.167.179", "http://127.0.0.1:39443/studio/"])
        assert hashlib.sha256(output).digest() == hashlib.sha256((value / "studio/index.html").read_bytes()).digest()
        steps.append(value.name)
except BaseException:
    tools["symlink"](old, link)
    if config_before is not None:
        config.write_bytes(config_before)
        reload_edge()
    raise
result = {"passed": True, "steps": steps, "actual_old_and_new_static_packages": True, "browser_storage_modified": False, "backend_restarted": False, "source_sha256": manifest["source_sha256"], "archive_sha256": build["archive_sha256"]}
result.update({"old_ui_roundtrip_performed": not args.promote_only, "browser_storage_downgrade_tested": False})
result.update({"studio_blob_connect_allowed": args.local_blob_resources, "dedicated_edge_reloaded": args.local_blob_resources, "worker_restarted": False})
receipt = TASK / ("static-upgrade-" + target.name + ".json")
assert not receipt.exists()
receipt.write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result))
