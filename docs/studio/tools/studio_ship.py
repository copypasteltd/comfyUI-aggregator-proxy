"""Explicit prepare/promote/rehearse/static actions for the authorised HTTP host."""
from __future__ import annotations

import hashlib
import json
import shlex
import sys
from datetime import UTC, datetime
from pathlib import Path

from studio_deploy import APP, PRIVATE, PROTECTED, ROOT, STAGE, run, upload

TOOLS = Path(__file__).resolve().parent
BUILD = ROOT / ".local/studio-cloud-build"


def transfer(sources):
    upload(sources)
    checks = "\n".join(f"echo '{hashlib.sha256(path.read_bytes()).hexdigest()}  {STAGE}/{name}' | sha256sum --check --status" for path, name in sources.items())
    return checks + "\n" + "\n".join(f"install -o root -g root -m 0600 {shlex.quote(STAGE + '/' + name)} {shlex.quote(PROTECTED + '/' + name)}" for name in sources.values())


def main():
    action = sys.argv[1] if len(sys.argv) == 2 else ""
    if action == "prepare":
        sources = {TOOLS / "studio131-prepare.sh": "prepare.sh", TOOLS / "studio131-host.py": "studio131-host.py", BUILD / "build.json": "cloud-build.json", BUILD / "static-build.json": "static-build.json", BUILD / "linux-20261030.131.1.tar.gz": "linux-20261030.131.1.tar.gz", BUILD / "studio-20261030.131.1.tar.gz": "studio-20261030.131.1.tar.gz"}
        script = transfer(sources) + f"\ntest ! -e '{PROTECTED}/prepare.state'\nnohup bash '{PROTECTED}/prepare.sh' > '{PROTECTED}/prepare.log' 2>&1 < /dev/null &\necho prepare_started\n"
    elif action == "status":
        script = f"""test ! -f '{PROTECTED}/prepare.state' || cat '{PROTECTED}/prepare.state'
test ! -f '{PROTECTED}/prepared.json' || cat '{PROTECTED}/prepared.json'
for task in promote rehearse; do
    test ! -f '{PROTECTED}/'$task'.state' || cat '{PROTECTED}/'$task'.state'
    test ! -f '{PROTECTED}/'$task'.json' || cat '{PROTECTED}/'$task'.json'
done
"""
    elif action in {"promote", "rehearse"}:
        script = f"""test ! -e '{PROTECTED}/{action}.state'
printf 'running\\n' > '{PROTECTED}/{action}.state'
nohup bash -c '/opt/comfyui-aggregator/current/venv/bin/python -B {PROTECTED}/studio131-host.py {action} && echo passed > {PROTECTED}/{action}.state || echo failed > {PROTECTED}/{action}.state' > '{PROTECTED}/{action}.log' 2>&1 < /dev/null &
echo {action}_started
"""
    elif action == "static":
        script = transfer({TOOLS / "studio131-static.conf": "studio131-static.conf"}) + f"""
python3 - <<'PY'
from pathlib import Path
import json,os,subprocess
base=Path('/opt/comfyui-aggregator'); task=Path('{PROTECTED}'); config=Path('/etc/comfyui-aggregator/nginx.conf')
assert json.loads((task/'promote.json').read_text())['passed']
target=base/'studio-releases/studio-20261030.131.1'; assert (target/'studio/index.html').is_file()
link=base/'studio-current'; assert not link.exists() and not link.is_symlink()
before=config.read_bytes(); (task/'nginx-before-studio.conf').write_bytes(before)
text=before.decode(); marker='        location = / {{ return 302 /admin/; }}'; assert text.count(marker)==1
new=text.replace(marker,marker+'\\n'+(task/'studio131-static.conf').read_text(),1)
link.symlink_to(target)
try:
    config.write_text(new)
    subprocess.run(['/usr/bin/nginx','-t','-e','stderr','-p','/var/lib/comfyui-aggregator/edge/','-c',str(config)],check=True)
    subprocess.run(['systemctl','reload','comfy-aggregator-edge.service'],check=True)
except BaseException:
    config.write_bytes(before);link.unlink();raise
print(json.dumps({{'passed':True,'studio_path':'/studio/','static_release':target.name,'other_nginx_reloaded':False}}))
PY
"""
    elif action == "static-rehearse":
        script = f"""python3 - <<'PY'
from pathlib import Path
import json,subprocess,hashlib
base=Path('/opt/comfyui-aggregator'); link=base/'studio-current'; target=link.resolve(); seed=base/'studio-releases/studio-20261030.131.1-rollback-rehearsal'
assert target.name=='studio-20261030.131.1' and not seed.exists()
# First publication has no previous Studio payload. Use a verified same-byte seed;
# this exercises atomic static pointers, not compatibility with an older UI version.
subprocess.run(['cp','-a',str(target),str(seed)],check=True)
original=hashlib.sha256((target/'studio/index.html').read_bytes()).hexdigest()
try:
    for value in (seed,target):
        pending=base/'studio-current-rehearsal'; assert not pending.is_symlink() and not pending.exists(); pending.symlink_to(value); pending.replace(link)
        result=subprocess.check_output(['curl','--fail','--silent','-H','Host: 43.165.167.179','http://127.0.0.1:39443/studio/'])
        assert hashlib.sha256(result).hexdigest()==original
finally:
    if link.resolve()!=target:
        pending=base/'studio-current-restore';pending.symlink_to(target);pending.replace(link)
print(json.dumps({{'passed':True,'atomic_pointer_roundtrip':True,'same_byte_seed':True,'older_ui_compatibility_tested':False,'browser_storage_modified':False}}))
PY
"""
    elif action == "static-upgrade":
        sources = {TOOLS / "studio131-static-upgrade.py": "studio131-static-upgrade.py", BUILD / "static-build.json": "static-build-1312.json", BUILD / "studio-20261030.131.2.tar.gz": "studio-20261030.131.2.tar.gz"}
        script = transfer(sources) + f"\npython3 '{PROTECTED}/studio131-static-upgrade.py'\n"
    elif action in {"static-upgrade-1313", "static-upgrade-1314", "static-upgrade-1315", "static-upgrade-1316"}:
        release, previous, number = {"static-upgrade-1313": ("studio-20261030.131.3", "studio-20261030.131.2", "1313"), "static-upgrade-1314": ("studio-20261030.131.4", "studio-20261030.131.3", "1314"), "static-upgrade-1315": ("studio-20261030.131.5", "studio-20261030.131.4", "1315"), "static-upgrade-1316": ("studio-20261030.131.6", "studio-20261030.131.5", "1316")}[action]
        assert json.loads((BUILD / "static-build.json").read_text())["release_id"] == release
        name = "static-build-" + number + ".json"
        sources = {TOOLS / "studio131-static-upgrade.py": "studio131-static-upgrade.py", BUILD / "static-build.json": name, BUILD / (release + ".tar.gz"): release + ".tar.gz"}
        script = transfer(sources) + f"\npython3 '{PROTECTED}/studio131-static-upgrade.py' --build {name} --previous {previous}\n"
    elif action == "failure-log":
        script = f"for task in prepare promote rehearse; do test ! -f '{PROTECTED}/'$task'.log' || tail -n 30 '{PROTECTED}/'$task'.log'; done"
    else:
        raise SystemExit("Choose prepare/status/promote/rehearse/static/static-rehearse/failure-log")
    result = run(script)
    label = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    receipt = {"action": action, "at": datetime.now(UTC).isoformat(), **result}
    (PRIVATE / f"ship-{action}-{label}.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt))
    return result["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
