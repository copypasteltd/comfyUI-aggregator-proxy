"""Open CORS on the dedicated public HTTP edge, preserving private listeners."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CONFIG = Path('/etc/comfyui-aggregator/nginx.conf')
SKILLS = CONFIG.with_name('public-skills.conf')
UNITS = tuple(f'comfy-aggregator-{name}.service' for name in ('edge', 'api', 'runner', 'gc', 'http-worker'))
NGINX = ['/usr/bin/nginx', '-e', 'stderr', '-p', '/var/lib/comfyui-aggregator/edge/', '-c', str(CONFIG), '-t']
MARKER = '# Managed unrestricted public CORS 145'
TOKEN = re.compile(r'''\#[^\n]*|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s{};\#"']+|[{};]''')
HEADERS = '''add_header Access-Control-Allow-Origin $http_origin always;
add_header Access-Control-Allow-Credentials "true" always;
add_header Access-Control-Allow-Methods $http_access_control_request_method always;
add_header Access-Control-Allow-Headers $http_access_control_request_headers always;
add_header Access-Control-Allow-Private-Network "true" always;
add_header Access-Control-Expose-Headers "X-Request-ID, X-Studio-Contract, X-Studio-Submission-Lookup, ETag, Last-Modified, Retry-After, Content-Range, Accept-Ranges, Content-Disposition, Content-Length, Content-Type" always;
add_header Access-Control-Max-Age 600 always;
add_header Vary "Origin, Access-Control-Request-Method, Access-Control-Request-Headers, Access-Control-Request-Private-Network" always;'''
PREFLIGHT = '''set $comfy_public_cors_preflight 0;
if ($request_method = OPTIONS) { set $comfy_public_cors_preflight 1; }
if ($http_origin = "") { set $comfy_public_cors_preflight 0; }
if ($http_access_control_request_method = "") { set $comfy_public_cors_preflight 0; }
if ($comfy_public_cors_preflight = 1) { return 204; }'''
HIDE = '\n'.join(f'proxy_hide_header {name};' for name in ('Access-Control-Allow-Origin', 'Access-Control-Allow-Credentials', 'Access-Control-Allow-Methods', 'Access-Control-Allow-Headers', 'Access-Control-Allow-Private-Network', 'Access-Control-Expose-Headers', 'Access-Control-Max-Age'))


def blocks(text: str) -> list[dict[str, Any]]:
    root: dict[str, Any] = {'header': (), 'body': 0, 'end': len(text), 'directives': [], 'parent': None, 'pending': []}
    result, stack = [root], [root]
    for match in TOKEN.finditer(text):
        lexeme = match.group()
        if lexeme.startswith('#'):
            continue
        current = stack[-1]
        if lexeme == '{':
            pending = current['pending']
            if not pending:
                raise ValueError('Unlabelled Nginx block')
            child = {'header': tuple(value for value, _, _ in pending), 'body': match.end(), 'end': None, 'directives': [], 'parent': current, 'pending': []}
            current['pending'] = []
            result.append(child)
            stack.append(child)
        elif lexeme == '}':
            if len(stack) == 1 or current['pending']:
                raise ValueError('Unbalanced Nginx configuration')
            current['end'] = match.start()
            stack.pop()
        elif lexeme == ';':
            pending = current['pending']
            if not pending:
                raise ValueError('Empty Nginx directive')
            current['directives'].append((tuple(value for value, _, _ in pending), pending[0][1], match.end()))
            current['pending'] = []
        else:
            current['pending'].append((lexeme, match.start(), match.end()))
    if len(stack) != 1 or root['pending']:
        raise ValueError('Incomplete Nginx configuration')
    return result


def render(text: str, *, public_server: bool) -> str:
    if MARKER in text:
        raise ValueError('Managed CORS already present; inspect rather than duplicate it')
    parsed = blocks(text)
    if public_server:
        candidates = [block for block in parsed if block['header'] == ('server',) and any(tokens[0] == 'listen' and len(tokens) > 1 and tokens[1] == '39443' for tokens, _, _ in block['directives'])]
        if len(candidates) != 1:
            raise ValueError('Expected exactly one dedicated public 39443 server')
        selected = candidates[0]
        targets = [block for block in parsed if selected['body'] <= block['body'] <= selected['end']]
    else:
        selected = None
        targets = [block for block in parsed if block['header'] and block['header'][0] == 'location']
        if not targets:
            raise ValueError('No static Skill locations to update')
    edits: list[tuple[int, int, str]] = []
    for block in targets:
        headers = [entry for entry in block['directives'] if entry[0][0] == 'add_header']
        for tokens, start, end in headers:
            if len(tokens) > 1 and tokens[1].lower().startswith('access-control-'):
                edits.append((start, end, ''))
        if block is selected or headers:
            policy = MARKER + '\n' + HEADERS
            if block is selected:
                policy += '\n' + HIDE + '\n' + PREFLIGHT
            edits.append((block['body'], block['body'], '\n' + policy + '\n'))
    for start, end, replacement in sorted(edits, reverse=True):
        text = text[:start] + replacement + text[end:]
    blocks(text)
    return text


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_write(path: Path, data: bytes, mode: int = 0o644) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix='.cors-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def command(args: list[str]) -> str:
    result = subprocess.run(args, capture_output=True, text=True, timeout=20, check=True)  # noqa: S603 -- fixed service/config commands
    return result.stdout.strip()


def service_snapshot() -> dict[str, str]:
    result = {}
    for name in UNITS:
        result[name] = command(['/usr/bin/systemctl', 'show', name, '-p', 'MainPID', '--value'])
        if result[name] == '0' or command(['/usr/bin/systemctl', 'is-active', name]) != 'active':
            raise RuntimeError('An application service is not active')
    return result


def verify() -> dict[str, Any]:
    checks = []
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for path in ('/v1/models', '/admin/api/v1/session', '/studio/', '/skill.md', '/skills/media-gen/1.3.0/skill.md', '/internal/health', '/missing-cors-check'):
        origin = 'https://public-cors-check.invalid'
        headers = {'Origin': origin, 'Access-Control-Request-Method': 'PROPFIND', 'Access-Control-Request-Headers': 'authorization,content-type,x-custom-header', 'Access-Control-Request-Private-Network': 'true'}
        request = urllib.request.Request('http://127.0.0.1:39443' + path, headers=headers, method='OPTIONS')
        with opener.open(request, timeout=5) as response:
            actual = response.headers
            assert response.status == 204 and actual['Access-Control-Allow-Origin'] == origin
            assert actual['Access-Control-Allow-Methods'] == 'PROPFIND'
            assert actual['Access-Control-Allow-Headers'] == headers['Access-Control-Request-Headers']
            assert actual['Access-Control-Allow-Private-Network'] == 'true' and actual['Access-Control-Allow-Credentials'] == 'true'
            assert len(actual.get_all('Access-Control-Allow-Origin')) == 1
            checks.append({'path': path, 'status': response.status, 'passed': True})
    for path, expected in (('/health/live', 200), ('/health/ready', 200), ('/admin/', 200), ('/studio/', 200), ('/skill.md', 200), ('/v1/models', 401), ('/internal/health', 404)):
        request = urllib.request.Request('http://127.0.0.1:39443' + path, headers={'Origin': 'null'})
        try:
            response = opener.open(request, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            assert response.status == expected and response.headers['Access-Control-Allow-Origin'] == 'null'
            assert len(response.headers.get_all('Access-Control-Allow-Origin')) == 1
            checks.append({'path': path, 'status': response.status, 'passed': True})
    return {'passed': True, 'checks': checks}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-config-sha256', required=True)
    parser.add_argument('--expected-skills-sha256', required=True)
    args = parser.parse_args()
    if os.name != 'posix' or os.geteuid() != 0:
        raise SystemExit('Run on the authorized native Linux host as root')
    originals = {CONFIG: CONFIG.read_bytes(), SKILLS: SKILLS.read_bytes()}
    if digest(originals[CONFIG]) != args.expected_config_sha256 or digest(originals[SKILLS]) != args.expected_skills_sha256:
        raise SystemExit('Current edge configuration changed; inspect its digest before applying')
    updated = {CONFIG: render(originals[CONFIG].decode(), public_server=True).encode(), SKILLS: render(originals[SKILLS].decode(), public_server=False).encode()}
    services = service_snapshot()
    release = str(Path('/opt/comfyui-aggregator/current').resolve())
    stamp = datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')
    backup = Path('/var/lib/comfyui-aggregator-deploy') / ('cors-' + stamp)
    backup.mkdir(mode=0o700)
    for path, data in originals.items():
        atomic_write(backup / path.name, data, 0o600)
    try:
        for path, data in updated.items():
            atomic_write(path, data)
        command(NGINX)
        command(['/usr/bin/systemctl', 'reload', 'comfy-aggregator-edge.service'])
        for attempt in range(10):
            try:
                proof = verify()
                break
            except (AssertionError, urllib.error.URLError):
                if attempt == 9:
                    raise
                time.sleep(0.3)
        if services != service_snapshot() or release != str(Path('/opt/comfyui-aggregator/current').resolve()):
            raise RuntimeError('Existing application process or release changed')
    except BaseException:
        for path, data in originals.items():
            atomic_write(path, data)
        command(NGINX)
        command(['/usr/bin/systemctl', 'reload', 'comfy-aggregator-edge.service'])
        raise
    report = {'at': datetime.now(UTC).isoformat(), 'passed': True, 'scope': 'Dedicated public HTTP edge CORS only; actual route/auth/private-ingress controls remain.', 'backup': str(backup), 'before_sha256': {path.name: digest(data) for path, data in originals.items()}, 'after_sha256': {path.name: digest(data) for path, data in updated.items()}, 'application_release': release, 'service_pids': services, 'service_pids_unchanged': True, 'http': proof, 'virtualization_used': False}
    atomic_write(backup / 'result.json', (json.dumps(report, indent=2) + '\n').encode(), 0o600)
    print(json.dumps(report))


if __name__ == '__main__':
    main()
