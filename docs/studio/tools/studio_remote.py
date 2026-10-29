"""Pinned-host SSH inspection for the authorised server. Credentials stay in ignored .local/."""
import json
from pathlib import Path
import sys
import paramiko

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / ".local" / "studio-ops"

def connect():
    config = json.loads((PRIVATE / "connection.json").read_text(encoding="utf-8-sig"))
    client = paramiko.SSHClient()
    client.load_host_keys(str(PRIVATE / "known_hosts"))
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    client.connect(config["host"], port=config.get("port", 22), username=config["username"], password=config["password"], look_for_keys=False, allow_agent=False, timeout=12, banner_timeout=15, auth_timeout=15)
    return client

def execute(client, script):
    stdin, stdout, stderr = client.exec_command("bash -s", timeout=55)
    stdin.write(script)
    stdin.channel.shutdown_write()
    result = {"exit_code": stdout.channel.recv_exit_status(), "stdout": stdout.read().decode("utf-8", "replace"), "stderr": stderr.read().decode("utf-8", "replace")}
    return result

if __name__ == "__main__":
    if sys.argv[1:] not in (["inspect"], ["paths"], ["gateway"], ["test-host"]):
        raise SystemExit("Only read-only inspect and paths actions are available")
    client = connect()
    try:
        script = """set -eu
systemctl show comfy-aggregator-api comfy-aggregator-runner -p WorkingDirectory -p FragmentPath
sudo -n python3 - <<'PY'
from pathlib import Path
for folder in ['/etc/nginx/sites-enabled', '/etc/nginx/conf.d']:
    for path in sorted(Path(folder).glob('*')):
        if not path.is_file(): continue
        print('CONFIG', str(path), 'TARGET', str(path.resolve()))
        for line in path.read_text().splitlines():
            if line.strip().startswith(('listen ', 'server_name ', 'location ', 'root ', 'alias ', 'proxy_pass ')):
                print(line.strip())
PY
ss -ltn
df -h /
"""
        if sys.argv[1:] == ["paths"]:
            script = """set -eu
systemctl show nginx comfy-aggregator-api comfy-aggregator-runner -p ExecStart
sudo -n python3 - <<'PY'
from pathlib import Path
for folder in ['/www/server/panel/vhost/nginx', '/www/server/nginx/conf']:
    for path in sorted(Path(folder).glob('*.conf')):
        if not path.is_file(): continue
        content = path.read_text()
        if '39001' not in content and 'comfy' not in content: continue
        print('CONFIG', str(path), 'TARGET', str(path.resolve()))
        for line in content.splitlines():
            if line.strip().startswith(('listen ', 'server_name ', 'location ', 'root ', 'alias ', 'proxy_pass ', 'include ')):
                print(line.strip())
for folder in ['/opt/comfyui-aggregator','/var/lib/comfyui-aggregator','/etc/comfyui-aggregator']:
    path=Path(folder)
    print('DIRECTORY', folder)
    if path.exists():
        for child in path.iterdir(): print(child.name, '->', str(child.resolve()) if child.is_symlink() else '')
PY
"""
        if sys.argv[1:] == ["gateway"]:
            script = """set -eu
sudo -n python3 - <<'PY'
from pathlib import Path
for filename in ['/etc/comfyui-aggregator/nginx.conf', '/etc/init.d/nginx']:
    path=Path(filename)
    print('CONFIG', filename)
    if not path.exists(): continue
    for line in path.read_text().splitlines():
        if line.strip().startswith(('listen ', 'server_name ', 'location ', 'root ', 'alias ', 'proxy_pass ', 'include ', 'nginx_basedir=', 'CONF=')):
            print(line.strip())
release=Path('/opt/comfyui-aggregator/current').resolve()
print('RELEASE', str(release))
print('RELEASE_CHILDREN', [p.name for p in release.iterdir()])
for folder in [release/'venv/lib', Path('/opt/comfyui-aggregator/tools')]:
    if folder.exists(): print('DIRECTORY', str(folder), [p.name for p in folder.iterdir()])
PY
sudo -n /www/server/nginx/sbin/nginx -T 2>/dev/null | python3 -c "import sys; f=''; lines=[]; blocks=[]
for line in sys.stdin:
 if line.startswith('# configuration file '):
  if any('comfy' in x or '32880' in x or 'gen.sub2share' in x for x in lines): blocks.append((f,lines))
  f=line.strip();lines=[]
 else: lines.append(line.strip())
if any('comfy' in x or '32880' in x or 'gen.sub2share' in x for x in lines): blocks.append((f,lines))
for f,lines in blocks:
 print(f)
 for line in lines:
  if line.startswith(('listen ', 'server_name ', 'location ', 'root ', 'alias ', 'proxy_pass ', 'include ')): print(line)"
"""
        if sys.argv[1:] == ["test-host"]:
            script = """set -eu
free -m
systemctl show comfy-aggregator-postgres.service comfy-aggregator-edge.service -p ExecStart -p User -p Group -p MainPID
ss -ltn
sudo -n find /opt/comfyui-aggregator -maxdepth 4 -type f -name initdb
"""
        result = execute(client, script)
        PRIVATE.mkdir(parents=True, exist_ok=True)
        (PRIVATE / f"server-{sys.argv[1]}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False))
    finally:
        client.close()
