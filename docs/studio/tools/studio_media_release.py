"""Publish only independent static media UI; preserve API/services/configuration."""
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT.parent / '生图生视API封装/app/.local/deploy-ssh-venv/Lib/site-packages'))
from studio_remote import connect
from studio_release_guard import sha

PRIVATE = ROOT / '.local/studio149'
PRIVATE.mkdir(parents=True, exist_ok=True)
TASK = '/var/lib/infinite-canvas-deploy/149-media-release'
STAGE = '/home/ubuntu/.infinite-canvas-upload/149-media-release'
action = sys.argv[1] if len(sys.argv) == 2 else ''
assert action in {'prepare', 'promote', 'rollback', 'restore', 'reject-v4', 'status'}
operation = 'media149-publish'
suffix = '-' + datetime.now(UTC).strftime('%Y%m%dT%H%M%S%f') if action == 'status' else ''
receipt = PRIVATE / ('remote-' + action + suffix + '.json')
assert not receipt.exists(), 'Inspect the existing operation; do not repeat a mutation'
client = connect()
try:
    if action == 'prepare':
        files = {ROOT / 'docs/tools' / name: name for name in ['studio_safe_release_host.py', 'studio_release_guard.py', 'studio_media_release_host.py']}
        for release in ['canvas-20261031.149.1', 'canvas-20261031.149.0']:
            package = ROOT / '.local/independent-canvas-package' / release
            files[package / (release + '.tar.gz')] = release + '.tar.gz'
            files[package / 'package.json'] = release + '.package.json'
        assert all(file.is_file() for file in files)
        attempt = PRIVATE / 'remote-upload-attempt.json'
        with attempt.open('x', encoding='utf-8') as output:
            output.write(json.dumps({'task': TASK, 'stage': STAGE, 'files': {name: sha(file.read_bytes()) for file, name in files.items()}}) + '\n')
        stdin, stdout, stderr = client.exec_command(f"set -eu\ntest ! -e '{STAGE}'\ninstall -d -m 0700 '{STAGE}'\n", timeout=55)
        stdin.channel.shutdown_write()
        assert stdout.channel.recv_exit_status() == 0, 'Upload directory already exists or could not be created; inspect state'
        with client.open_sftp() as sftp:
            for source, name in files.items(): sftp.put(str(source), STAGE + '/' + name)
        lines = ['set -eu', f"test ! -e '{TASK}'", f"install -d -m 0700 '{TASK}'"]
        for source, name in files.items():
            lines.extend([f"echo '{sha(source.read_bytes())}  {STAGE}/{name}' | sha256sum --check --status", f"install -o root -g root -m 0600 '{STAGE}/{name}' '{TASK}/{name}'"])
        lines.append(f"python3 -B '{TASK}/studio_media_release_host.py' prepare {operation}")
        stdin, stdout, stderr = client.exec_command('sudo -n bash -s', timeout=55)
        stdin.write('\n'.join(lines) + '\n'); stdin.channel.shutdown_write()
    else:
        stdin, stdout, stderr = client.exec_command(f"sudo -n python3 -B '{TASK}/studio_media_release_host.py' {action} {operation}", timeout=55)
        stdin.channel.shutdown_write()
    result = {'exit_code': stdout.channel.recv_exit_status(), 'stdout': stdout.read().decode('utf-8', 'replace'), 'stderr': stderr.read().decode('utf-8', 'replace')}
    with receipt.open('x', encoding='utf-8') as output:
        output.write(json.dumps(result, indent=2) + '\n')
    assert result['exit_code'] == 0, 'Static operation incomplete; inspect its original status before retrying'
    value = json.loads(result['stdout']); assert value['passed']
    print(json.dumps({'passed': True, 'action': action, 'receipt': receipt.relative_to(ROOT).as_posix(), 'api_written': False, 'service_restarts': 0}))
finally:
    client.close()
