"""Publish only reviewed non-secret release metadata, retaining earlier failed checks."""
import json
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / '.local/studio-ops'
EVIDENCE = ROOT / 'aggregator-service/docs/evidence/studio-release-137'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(name, value):
    with (EVIDENCE / name).open('x', encoding='utf-8') as output:
        output.write(json.dumps(value, indent=2) + '\n')


decoder = json.JSONDecoder()
prepared = promoted = None
for source in sorted(PRIVATE.glob('deploy137-status-*.json')):
    receipt = read(source)
    assert receipt['exit_code'] == 0
    text = receipt['stdout']
    while '{' in text:
        start = text.index('{')
        value, end = decoder.raw_decode(text[start:])
        text = text[start + end:]
        if 'wheel_payload_files_verified' in value:
            prepared = value
        if 'database_migration_executed' in value:
            promoted = value
assert prepared['passed'] and promoted['passed']
binding = read(sorted(PRIVATE.glob('deploy137-admin-binding-*.json'))[-1])
assert binding['exit_code'] == 0
binding = json.loads(binding['stdout'])
assert binding['passed'] and binding['configuration_fields_changed'] == ['admin_dist']
inspection = read(PRIVATE / 'admin137-inspection.json')
assert inspection['exit_code'] == 0
inspection = json.loads(inspection['stdout'])
admin_build = read(sorted(EVIDENCE.glob('admin-build-*.json'))[-1])
build = read(ROOT / '.local/studio-cloud-build/linux-20261030.137.1-build.json')
assert build['passed'] and prepared['source_revision'] == build['application_source_revision']
write('build-1371.json', build)
write('prepared-1371.json', prepared)
write('promotion-1371.json', promoted)
write('admin-binding-1371.json', binding)
write('live-admin-prebinding-1371.json', {'passed': False, 'recorded_at': datetime.now(UTC).isoformat(),
      'check': 'public Admin index equals new Admin build', 'failure': 'configured Admin directory remained pinned to the old 128.1 release',
      'observed_configured_directory': inspection['admin_dist'], 'observed_configured_index_sha256': inspection['index_sha256'],
      'expected_index_sha256': admin_build['dist_sha256']['index.html'], 'resolution_receipt': 'admin-binding-1371.json',
      'scope': 'failed deployment read check before static-directory binding repair; retained without rewriting'})
print(json.dumps({'passed': True, 'public_receipts': 5, 'raw_config_or_credentials_published': False}))
