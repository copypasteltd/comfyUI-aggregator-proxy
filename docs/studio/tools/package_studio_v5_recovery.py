"""Package verified application bytes as an explicit compatible recovery release."""
import argparse
import io
import json
import re
import tarfile
from datetime import UTC, datetime
from pathlib import Path

from studio_release_guard import CONTRACT, manifest_files, require, sha

ROOT = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser()
parser.add_argument('--source', default='canvas-20261030.145.1')
parser.add_argument('--release', default='canvas-20261030.146.0')
parser.add_argument('--expected-source-revision', default='ae0ca22e996c16c137b2ed35ceab7c1c3e492044')
parser.add_argument('--protocol', default='docs/research/studio-implementation/managed-cleanup-protocol-2026-10-30T13-17-46-730Z.json')
parser.add_argument('--delivery-proof', help='Exact already-deployed source verification with the same complete storage contract')
arguments = parser.parse_args()
SOURCE_ID, RELEASE = arguments.source, arguments.release
require(re.fullmatch(r'canvas-\d{8}\.\d+\.\d+', SOURCE_ID) and re.fullmatch(r'canvas-\d{8}\.\d+\.0', RELEASE) and SOURCE_ID != RELEASE, 'Invalid recovery identity')
require(re.fullmatch(r'[a-f0-9]{40}', arguments.expected_source_revision), 'Expected frozen revision is required')
SOURCE = ROOT / '.local/independent-canvas-package' / SOURCE_ID
TARGET = ROOT / '.local/independent-canvas-package' / RELEASE
package = json.loads((SOURCE / 'package.json').read_text(encoding='utf-8'))
archive = SOURCE / (SOURCE_ID + '.tar.gz')
require(package['passed'] and sha(archive.read_bytes()) == package['archive_sha256'], 'Original recovery archive changed')
require(package['source_revision'] == arguments.expected_source_revision, 'Unexpected frozen recovery source')
protocol_path = (ROOT / (arguments.delivery_proof or arguments.protocol)).resolve()
require(protocol_path.is_relative_to((ROOT / 'docs/research/studio-implementation').resolve()), 'Protocol must be owned public evidence')
protocol = json.loads(protocol_path.read_text(encoding='utf-8'))
if arguments.delivery_proof:
    require(protocol['passed'] and protocol['release_id'] == SOURCE_ID and protocol['source_revision'] == package['source_revision']
            and protocol['source_sha256'] == package['source_sha256'] and protocol['storage_contract'] == CONTRACT
            and protocol['package_sha256'] == package['archive_sha256'], 'Missing exact deployed-source compatibility proof')
else:
    require(protocol['passed'] and protocol['source_revision'] == package['source_revision'] and len(protocol['checks']) == 18 and all(protocol['cleanup'].values()), 'Missing native v5 proof')
with tarfile.open(archive) as source:
    original_raw = source.extractfile('canvas-deployment.json').read()
    manifest = json.loads(original_raw)
    require(manifest.get('storage_contract', CONTRACT) == CONTRACT, 'Recovery source declares an incompatible storage contract')
    if arguments.delivery_proof:
        require(manifest.get('storage_contract') == CONTRACT and protocol['static_files_verified'] == len(manifest['files']), 'Deployed source lacks the exact complete contract or inventory')
    files = manifest_files(manifest)
    require(manifest['release_id'] == SOURCE_ID, 'Wrong recovery source')
    payload = {name: source.extractfile(name).read() for name in files}
    for name, entry in files.items():
        require(len(payload[name]) == entry['bytes'] and sha(payload[name]) == entry['sha256'], 'Original recovery file changed')
proof_name = 'deployed_source_report' if arguments.delivery_proof else 'native_protocol_report'
manifest.update({'release_id': RELEASE, 'at': datetime.now(UTC).isoformat(), 'storage_contract': CONTRACT,
                 'recovery_source': {'release_id': SOURCE_ID, 'manifest_sha256': sha(original_raw), 'archive_sha256': package['archive_sha256'], proof_name: protocol_path.relative_to(ROOT).as_posix(), proof_name + '_sha256': sha(protocol_path.read_bytes())}})
payload['canvas-deployment.json'] = (json.dumps(manifest, indent=2) + '\n').encode()
TARGET.mkdir()
target = TARGET / (RELEASE + '.tar.gz')
with tarfile.open(target, 'w:gz') as output:
    for name, data in sorted(payload.items()):
        member = tarfile.TarInfo(name); member.size = len(data); member.mode = 0o644
        output.addfile(member, io.BytesIO(data))
result = {**package, 'release_id': RELEASE, 'archive_bytes': target.stat().st_size, 'archive_sha256': sha(target.read_bytes()), 'recovery_source_release': SOURCE_ID, 'application_bytes_identical_to_source': True, 'storage_contract': CONTRACT}
suffix = ''.join(RELEASE.split('.')[1:])
for file in [TARGET / 'package.json', ROOT / ('docs/research/studio-implementation/independent-package-' + suffix + '.json')]:
    with file.open('x', encoding='utf-8') as output: output.write(json.dumps(result, indent=2) + '\n')
print(json.dumps({'passed': True, 'release_id': RELEASE, 'static_files': len(files), 'application_bytes_identical_to_source': True, 'api_modified': False}))
