"""Fixed, reviewed deployment actions for the authorised aggregator host.

Credentials and detailed transcripts remain in ignored workspace .local/.
This does not install a second backend or touch another application's listener.
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from studio_remote import connect

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / ".local/studio-ops"
APP = ROOT / "aggregator-service/app"
STAGE = "/home/ubuntu/.comfy-aggregator-upload/studio131"
PROTECTED = "/var/lib/comfyui-aggregator-deploy/studio131"


def run(script: str) -> dict:
    client = connect()
    try:
        stdin, stdout, stderr = client.exec_command("sudo -n bash -s", timeout=55)
        stdin.write("set -eu\n" + script)
        stdin.channel.shutdown_write()
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        code = stdout.channel.recv_exit_status()
        return {"exit_code": code, "stdout": out, "stderr": err}
    finally:
        client.close()


def upload(files: dict[Path, str]) -> None:
    client = connect()
    try:
        ftp = client.open_sftp()
        try:
            try:
                ftp.mkdir(STAGE, mode=0o700)
            except OSError:
                if not ftp.stat(STAGE).st_mode & 0o40000:
                    raise
            for source, name in files.items():
                if not source.resolve().is_relative_to(ROOT.resolve()) or not source.is_file() or "/" in name:
                    raise RuntimeError("Unowned deployment source")
                ftp.put(str(source), STAGE + "/" + name)
                ftp.chmod(STAGE + "/" + name, 0o600)
        finally:
            ftp.close()
    finally:
        client.close()


def main() -> int:
    action = sys.argv[1] if len(sys.argv) == 2 else ""
    if action == "backup-fix":
        sources = {
            APP / "deploy/native-linux/backup-db.py": "backup-db.py",
            APP / "deploy/native-linux/comfy-aggregator-backup.service": "comfy-aggregator-backup.service",
        }
        upload(sources)
        checks = "\n".join(f"echo '{hashlib.sha256(path.read_bytes()).hexdigest()}  {STAGE}/{name}' | sha256sum --check --status" for path, name in sources.items())
        script = f"""{checks}
install -d -m 0700 '{PROTECTED}'
test ! -e '{PROTECTED}/backup-original.py'
cp -p /opt/comfyui-aggregator/ops/backup-db.py '{PROTECTED}/backup-original.py'
cp -p /etc/systemd/system/comfy-aggregator-backup.service '{PROTECTED}/backup-original.service'
stat -c 'PROTECTED_CONFIG_MODE=%a OWNER=%U GROUP=%G' /etc/comfyui-aggregator/cloud.env
install -o root -g root -m 0644 '{STAGE}/backup-db.py' /opt/comfyui-aggregator/ops/backup-db.py
install -o root -g root -m 0644 '{STAGE}/comfy-aggregator-backup.service' /etc/systemd/system/comfy-aggregator-backup.service
systemctl daemon-reload
systemctl reset-failed comfy-aggregator-backup.service
systemctl start --no-block comfy-aggregator-backup.service
systemctl show comfy-aggregator-backup.service -p ActiveState -p SubState -p Result -p ExecMainStatus
"""
    elif action == "backup-status":
        script = """systemctl show comfy-aggregator-backup.service -p ActiveState -p SubState -p Result -p ExecMainStatus
python3 - <<'PY'
from pathlib import Path
import json,hashlib
p=max(Path('/var/lib/comfyui-aggregator/backups').glob('control-*.json'),key=lambda p:p.stat().st_mtime)
m=json.loads(p.read_text());d=p.with_suffix('.dump')
assert m['archive_list_verified'] and d.stat().st_size==m['bytes'] and hashlib.sha256(d.read_bytes()).hexdigest()==m['sha256']
print(json.dumps(m))
PY
"""
    else:
        raise SystemExit("Choose backup-fix or backup-status")
    result = run(script)
    receipt = {"action": action, "at": datetime.now(UTC).isoformat(), **result}
    label = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    (PRIVATE / f"deploy-{action}-{label}.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt))
    return result["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
