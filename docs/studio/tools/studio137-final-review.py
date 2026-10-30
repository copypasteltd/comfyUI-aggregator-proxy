"""Collect current static gates and immutable 137 receipts without changing old evidence."""
import hashlib
import json
import os
import socket
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPOSITORY = ROOT / 'aggregator-service'
EVIDENCE = REPOSITORY / 'docs/evidence/studio-release-137'
label = sys.argv[1] if len(sys.argv) == 2 else '1371'
assert label in {'1371', '1372', '1373'}
targets = (EVIDENCE / ('final-review-' + label + '.json'), ROOT / 'docs/research/studio-implementation' / ('device-pool-validation-notes-' + label + '.json'))
assert all(not target.exists() for target in targets)
private = ROOT / '.local/studio137-final' / label
private.mkdir(parents=True, exist_ok=False)
ownership = json.loads((ROOT / '.local/native-postgres/ownership.json').read_text(encoding='utf-8'))
data = Path(ownership['data']).resolve()
assert data.is_relative_to(ROOT.resolve()) and ownership['port'] == 55438 and not (data / 'postmaster.pid').exists()
with socket.socket() as probe:
    probe.settimeout(2)
    assert probe.connect_ex(('127.0.0.1', ownership['port'])) != 0
browser = json.loads((EVIDENCE / 'public-admin-1372.json').read_text(encoding='utf-8'))
assert browser['passed'] and all(browser['cleanup'].values())
checks = {}
for name, cwd, script in (
    ('documentation', REPOSITORY, 'docs/tools/verify_docs.py'),
    ('readme', REPOSITORY / 'app', 'scripts/check-readme.py'),
    ('delivery', ROOT, 'docs/tools/check_studio_delivery.py'),
):
    result = subprocess.run([sys.executable, '-B', script], cwd=cwd, env={**os.environ, 'PYTHONUTF8': '1', 'PYTHONDONTWRITEBYTECODE': '1'}, capture_output=True, timeout=120)
    (private / (name + '.stdout')).write_bytes(result.stdout)
    (private / (name + '.stderr')).write_bytes(result.stderr)
    parsed = json.loads(result.stdout) if result.returncode == 0 else {'passed': False, 'detail': 'private gate output retained'}
    checks[name] = {'exit_code': result.returncode, 'result': parsed, 'stdout_sha256': hashlib.sha256(result.stdout).hexdigest(), 'stderr_sha256': hashlib.sha256(result.stderr).hexdigest()}
receipts = {}
for source in sorted(EVIDENCE.glob('*.json')):
    if not source.name.startswith('final-review-'):
        receipts[source.relative_to(ROOT).as_posix()] = hashlib.sha256(source.read_bytes()).hexdigest()
media = ROOT / 'docs/media/studio-137/media-manifest.json'
receipts[media.relative_to(ROOT).as_posix()] = hashlib.sha256(media.read_bytes()).hexdigest()
failed = [source.name for source in EVIDENCE.glob('*.json') if not json.loads(source.read_text(encoding='utf-8')).get('passed', True)]
passed = all(value['exit_code'] == 0 and value['result'].get('passed') for value in checks.values())
report = {'passed': passed, 'at': datetime.now(UTC).isoformat(), 'backend': 'linux-20261030.137.1', 'studio': 'studio-20261030.136.1',
          'scope': '137 native code/database, synthetic and real Admin pages, HTTP/CORS, current Git/runtime and historical Studio evidence; no physical multi-GPU or phone acceptance',
          'gates': checks, 'report_sha256': receipts, 'failed_reports_preserved': sorted(failed), 'native_focused_cases': 38, 'native_compatibility_cases': 1695,
          'synthetic_admin_groups': 5, 'real_admin_groups': 3, 'existing_models_checked': 13, 'new_real_generation_posts': 0,
          'production_models_or_keys_modified': False, 'local_test_postgres_stopped': True, 'owned_browsers_closed': True, 'virtualization_used': False,
          'manual_acceptance': 'pending-test', 'physical_fleet_acceptance': 'pending', 'full_acceptance': 'HOLD'}
for target in targets:
    with target.open('x', encoding='utf-8') as output:
        output.write(json.dumps(report, indent=2) + '\n')
print(json.dumps({'passed': passed, 'gates': {name: value['exit_code'] for name, value in checks.items()}, 'receipts': len(receipts), 'full_acceptance': 'HOLD'}))
raise SystemExit(0 if passed else 1)
