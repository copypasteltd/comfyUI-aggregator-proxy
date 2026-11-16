"""Adjust inspected native editor burst capacity on the dedicated HTTP service."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from open_cors import atomic_write, blocks

EDGE = Path('/etc/comfyui-aggregator/nginx.conf')
API = Path('/etc/systemd/system/comfy-aggregator-api.service')
VALIDATE = ['/usr/bin/nginx', '-e', 'stderr', '-p', '/var/lib/comfyui-aggregator/edge/', '-c', str(EDGE), '-t']


def render(edge: str, api: str) -> tuple[str, str]:
    parsed = blocks(edge)
    events = [b for b in parsed if b['header'] == ('events',)]
    if len(events) != 1:
        raise ValueError('Expected one events block')
    connections = [d for d in events[0]['directives'] if d[0][0] == 'worker_connections']
    if len(connections) != 1 or connections[0][0] != ('worker_connections', '128'):
        raise ValueError('Connection capacity differs from inspected source')
    root = parsed[0]
    if any(d[0][0] == 'worker_rlimit_nofile' for d in root['directives']):
        raise ValueError('An existing file limit requires separate inspection')
    _, start, end = connections[0]
    edge = 'worker_rlimit_nofile 4096;\n' + edge[:start] + 'worker_connections 2048;' + edge[end:]
    lines = api.splitlines(keepends=True)
    commands = [i for i, line in enumerate(lines) if line.startswith('ExecStart=')]
    if len(commands) != 1 or lines[commands[0]].count('--limit-concurrency 128 ') != 1:
        raise ValueError('API command differs from inspected source')
    lines[commands[0]] = lines[commands[0]].replace('--limit-concurrency 128 ', '--limit-concurrency 512 ', 1)
    blocks(edge)
    return edge, ''.join(lines)


def run(command: list[str]) -> None:
    subprocess.run(command, check=True, capture_output=True, timeout=40)  # noqa: S603 -- fixed local service command


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-edge-sha256', required=True)
    parser.add_argument('--expected-api-sha256', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    before = [p.read_bytes() for p in (EDGE, API)]
    if [hashlib.sha256(b).hexdigest() for b in before] != [args.expected_edge_sha256, args.expected_api_sha256]:
        raise SystemExit('Configuration changed since inspection')
    after = [s.encode() for s in render(*(b.decode() for b in before))]
    if not args.apply:
        print(json.dumps({'validated': True, 'apply': False, 'edge_connections': 2048, 'api_concurrency': 512}))
        return
    backup = Path('/var/lib/comfyui-aggregator-deploy') / ('native-capacity-' + datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ'))
    backup.mkdir(mode=0o700)
    for index, content in enumerate(before):
        atomic_write(backup / str(index), content, 0o600)
    try:
        for path, content in zip((EDGE, API), after, strict=True):
            atomic_write(path, content)
        run(VALIDATE)
        run(['/usr/bin/systemctl', 'daemon-reload'])
        run(['/usr/bin/systemctl', 'reload', 'comfy-aggregator-edge.service'])
        run(['/usr/bin/systemctl', 'restart', 'comfy-aggregator-api.service'])
        run(['/usr/bin/systemctl', 'is-active', 'comfy-aggregator-api.service', 'comfy-aggregator-edge.service'])
    except Exception:
        for path, content in zip((EDGE, API), before, strict=True):
            atomic_write(path, content)
        run(['/usr/bin/systemctl', 'daemon-reload'])
        run(VALIDATE)
        run(['/usr/bin/systemctl', 'reload', 'comfy-aggregator-edge.service'])
        run(['/usr/bin/systemctl', 'restart', 'comfy-aggregator-api.service'])
        raise
    print(json.dumps({'applied': True, 'before_sha256': [hashlib.sha256(b).hexdigest() for b in before], 'after_sha256': [hashlib.sha256(b).hexdigest() for b in after], 'edge_connections': 2048, 'worker_file_limit': 4096, 'api_concurrency': 512, 'tls_or_user_proxy_changed': False}))


if __name__ == '__main__':
    main()
