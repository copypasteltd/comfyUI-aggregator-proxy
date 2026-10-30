"""Read-only native host and public HTTP checks for the current static release."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import Request, urlopen

from studio_remote import connect

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / ".local/studio-cloud-build"
build = json.loads((PRIVATE / "static-build.json").read_text(encoding="utf-8"))
manifest = json.loads((PRIVATE / (build["release_id"] + ".json")).read_text(encoding="utf-8"))
client = connect()
try:
    command = """python3 - <<'PY'
import json,subprocess
from pathlib import Path
names=['comfy-aggregator-api.service','comfy-aggregator-runner.service','comfy-aggregator-http-worker.service','comfy-aggregator-edge.service']
print(json.dumps({'backend':Path('/opt/comfyui-aggregator/current').resolve().name,'studio':Path('/opt/comfyui-aggregator/studio-current').resolve().name,'services':{name:subprocess.check_output(['systemctl','is-active',name],text=True).strip() for name in names},'worker_pid':int(subprocess.check_output(['systemctl','show','--value','-p','MainPID','comfy-aggregator-http-worker.service'],text=True))}))
PY
"""
    _, output, error = client.exec_command(command, timeout=30)
    host = json.loads(output.read())
    assert output.channel.recv_exit_status() == 0, error.read().decode()
finally:
    client.close()
backend_build = json.loads((PRIVATE / "build.json").read_text(encoding="utf-8"))
assert host["studio"] == build["release_id"] and host["backend"] == backend_build["release_id"]
assert all(value == "active" for value in host["services"].values()) and host["worker_pid"] == 803588
expected_index = next(item["sha256"] for item in manifest["files"] if item["path"] == "studio/index.html")
responses = []
for endpoint in ("/studio/", "/studio/models", "/admin/", "/health/ready"):
    with urlopen("http://43.165.167.179:39443" + endpoint, timeout=30) as response:
        digest = hashlib.sha256(response.read()).hexdigest()
        assert response.status == 200
        if endpoint.startswith("/studio/"):
            assert digest == expected_index
        responses.append({"path": endpoint, "status": response.status, "content_type": response.headers.get("Content-Type"), "body_sha256": digest})
cors = []
for origin in ("http://storage-validation.invalid", "https://another-source.invalid"):
    request = Request("http://43.165.167.179:39443/v1/jobs/lookup", method="OPTIONS", headers={"Origin": origin, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization,idempotency-key,x-expected-release-digest,content-type"})
    with urlopen(request, timeout=30) as response:
        allowed = response.headers.get("Access-Control-Allow-Origin")
        assert response.status == 200 and allowed in ("*", origin)
        cors.append({"status": response.status, "requested_origin": origin, "allow_origin": allowed})
result = {"passed": True, "at": datetime.now(UTC).isoformat(), "scope": "Read-only public HTTP, CORS preflight and native host; no restart, database or GPU mutation", **{key: value for key, value in host.items() if key != "worker_pid"}, "original_worker_pid_unchanged": True, "responses": responses, "cors": cors, "virtualization_used": False}
target = ROOT / "docs/research/studio-implementation" / ("live-final-health-" + build["release_id"].replace("studio-20261030.", "").replace(".", "") + ".json")
with target.open("x", encoding="utf-8") as file:
    file.write(json.dumps(result, indent=2) + "\n")
print(json.dumps(result))
