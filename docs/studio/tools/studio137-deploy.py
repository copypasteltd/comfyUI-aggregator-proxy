"""Fixed authorised host actions; credentials and raw transcripts remain private."""
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT.parent / '生图生视API封装/app/.local/deploy-ssh-venv/Lib/site-packages'))
import studio_deploy as deploy  # noqa: E402

TOOLS = Path(__file__).resolve().parent
deploy.STAGE = '/home/ubuntu/.comfy-aggregator-upload/studio137'
PROTECTED = '/var/lib/comfyui-aggregator-deploy/studio137'
BUILD = ROOT / '.local/studio-cloud-build'


def transfer(files):
    deploy.upload(files)
    return f"install -d -m 0700 '{PROTECTED}'\n" + '\n'.join(f"echo '{hashlib.sha256(source.read_bytes()).hexdigest()}  {deploy.STAGE}/{name}' | sha256sum --check --status\ninstall -o root -g root -m 0600 '{deploy.STAGE}/{name}' '{PROTECTED}/{name}'" for source, name in files.items()) + '\n'


def main():
    action = sys.argv[1]
    if action == 'probe':
        script = transfer({TOOLS / 'studio137-host.py': 'studio137-host.py', TOOLS / 'studio131-host.py': 'studio131-host.py'})
        script += f"/opt/comfyui-aggregator/current/venv/bin/python -B '{PROTECTED}/studio137-host.py' probe\nsystemctl is-active comfy-aggregator-api comfy-aggregator-runner comfy-aggregator-http-worker comfy-aggregator-edge\n"
    elif action == 'prepare':
        script = transfer({TOOLS / 'studio137-host.py': 'studio137-host.py', TOOLS / 'studio131-host.py': 'studio131-host.py', TOOLS / 'studio137-prepare.sh': 'prepare.sh', BUILD / 'linux-20261030.137.1-build.json': 'cloud-build.json', BUILD / 'linux-20261030.137.1.tar.gz': 'linux-20261030.137.1.tar.gz'})
        script += f"test ! -e '{PROTECTED}/prepare.state'\nnohup bash '{PROTECTED}/prepare.sh' > '{PROTECTED}/prepare.log' 2>&1 < /dev/null &\necho preparation_started\n"
    elif action == 'promote':
        script = f"test ! -e '{PROTECTED}/promote.state'\nprintf 'running\\n' > '{PROTECTED}/promote.state'\nnohup bash -c '/opt/comfyui-aggregator/current/venv/bin/python -B {PROTECTED}/studio137-host.py promote && echo passed > {PROTECTED}/promote.state || echo failed > {PROTECTED}/promote.state' > '{PROTECTED}/promote.log' 2>&1 < /dev/null &\necho promotion_started\n"
    elif action == 'status':
        script = '\n'.join(f"test ! -f '{PROTECTED}/{name}' || cat '{PROTECTED}/{name}'" for name in ('prepare.state', 'prepared.json', 'promote.state', 'promote.json', 'admin-binding.json'))
    elif action == 'admin-binding':
        script = transfer({TOOLS / 'studio137-host.py': 'studio137-host.py'})
        script += f"/opt/comfyui-aggregator/current/venv/bin/python -B '{PROTECTED}/studio137-host.py' admin-binding\n"
    else:
        raise SystemExit('Choose probe, prepare, promote, admin-binding or status')
    value = deploy.run(script)
    result = {'action': action, 'at': datetime.now(UTC).isoformat(), **value}
    label = datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')
    (ROOT / '.local/studio-ops' / ('deploy137-' + action + '-' + label + '.json')).write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result))
    raise SystemExit(value['exit_code'])


if __name__ == '__main__':
    main()
