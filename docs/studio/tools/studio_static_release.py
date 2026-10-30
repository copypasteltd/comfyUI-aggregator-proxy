"""Publish an owned frontend plan; original API and services stay unchanged."""
import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT.parent / '生图生视API封装/app/.local/deploy-ssh-venv/Lib/site-packages'))
from studio_remote import connect
from studio_release_guard import sha

parser = argparse.ArgumentParser()
parser.add_argument('plan')
parser.add_argument('action', choices=['prepare', 'promote', 'status'])
arguments = parser.parse_args()
plan_path = (ROOT / arguments.plan).resolve()
assert plan_path.is_relative_to((ROOT / '.local').resolve())
plan = json.loads(plan_path.read_text(encoding='utf-8'))
assert set(plan) == {'original', 'new', 'recovery', 'operation'}
assert all(re.fullmatch(r'canvas-\d{8}\.\d+\.\d+', plan[name]) for name in ['original', 'new', 'recovery'])
assert re.fullmatch(r'[a-z0-9-]+', plan['operation'])
task = '/var/lib/infinite-canvas-deploy/' + plan['operation']
stage = '/home/ubuntu/.infinite-canvas-upload/' + plan['operation']
suffix = '-' + datetime.now(UTC).strftime('%Y%m%dT%H%M%S%f') if arguments.action == 'status' else ''
receipt = plan_path.parent / ('static-' + arguments.action + suffix + '.json')
assert not receipt.exists(), 'Inspect the existing operation rather than repeating a mutation'
client = connect()
try:
    if arguments.action == 'prepare':
        files = {ROOT / 'docs/tools' / name: name for name in ['studio_safe_release_host.py', 'studio_release_guard.py', 'studio_static_release_host.py']}
        files[plan_path] = 'release-plan.json'
        for name in [plan['new'], plan['recovery']]:
            package = ROOT / '.local/independent-canvas-package' / name
            files[package / (name + '.tar.gz')] = name + '.tar.gz'
            files[package / 'package.json'] = name + '.package.json'
        assert all(file.is_file() for file in files)
        with (plan_path.parent / 'static-upload-attempt.json').open('x', encoding='utf-8') as output:
            output.write(json.dumps({'task': task, 'stage': stage, 'files': {name: sha(file.read_bytes()) for file, name in files.items()}}, indent=2) + '\n')
        stdin, stdout, stderr = client.exec_command(f"set -eu\ntest ! -e '{stage}'\ninstall -d -m 0700 '{stage}'\n", timeout=55)
        stdin.channel.shutdown_write(); assert stdout.channel.recv_exit_status() == 0, 'Inspect existing upload directory'
        with client.open_sftp() as sftp:
            for file, name in files.items(): sftp.put(str(file), stage + '/' + name)
        lines = ['set -eu', f"test ! -e '{task}'", f"install -d -m 0700 '{task}'"]
        for file, name in files.items(): lines += [f"echo '{sha(file.read_bytes())}  {stage}/{name}' | sha256sum --check --status", f"install -o root -g root -m 0600 '{stage}/{name}' '{task}/{name}'"]
        lines.append(f"python3 -B '{task}/studio_static_release_host.py' prepare {plan['operation']}")
        stdin, stdout, stderr = client.exec_command('sudo -n bash -s', timeout=55)
        stdin.write('\n'.join(lines) + '\n'); stdin.channel.shutdown_write()
    else:
        stdin, stdout, stderr = client.exec_command(f"sudo -n python3 -B '{task}/studio_static_release_host.py' {arguments.action} {plan['operation']}", timeout=55)
        stdin.channel.shutdown_write()
    value = {'exit_code': stdout.channel.recv_exit_status(), 'stdout': stdout.read().decode('utf-8', 'replace'), 'stderr': stderr.read().decode('utf-8', 'replace')}
    with receipt.open('x', encoding='utf-8') as output: output.write(json.dumps(value, indent=2) + '\n')
    assert value['exit_code'] == 0, 'Inspect the recorded original operation before retrying'
    result = json.loads(value['stdout']); assert result['passed']
    print(json.dumps({'passed': True, 'action': arguments.action, 'receipt': receipt.relative_to(ROOT).as_posix(), 'api_written': False, 'service_restarts': 0}))
finally:
    client.close()
