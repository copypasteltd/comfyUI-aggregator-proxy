"""Package the tested 145.1 app bytes as an explicit v5 recovery release."""
import io
import json
import tarfile
from datetime import UTC, datetime
from pathlib import Path

from studio_release_guard import CONTRACT, manifest_files, require, sha

ROOT = Path(__file__).resolve().parents[2]
SOURCE_ID, RELEASE = 'canvas-20261030.145.1', 'canvas-20261030.146.0'
SOURCE = ROOT / '.local/independent-canvas-package' / SOURCE_ID
TARGET = ROOT / '.local/independent-canvas-package' / RELEASE
package = json.loads((SOURCE / 'package.json').read_text(encoding='utf-8'))
archive = SOURCE / (SOURCE_ID + '.tar.gz')
require(package['passed'] and sha(archive.read_bytes()) == package['archive_sha256'], 'Original recovery archive changed')
require(package['source_revision'] == 'ae0ca22e996c16c137b2ed35ceab7c1c3e492044', 'Unexpected legacy source')
protocol_path = ROOT / 'docs/research/studio-implementation/managed-cleanup-protocol-2026-10-30T13-17-46-730Z.json'
protocol = json.loads(protocol_path.read_text(encoding='utf-8'))
require(protocol['passed'] and protocol['source_revision'] == package['source_revision'] and len(protocol['checks']) == 18 and all(protocol['cleanup'].values()), 'Missing native v5 proof')
with tarfile.open(archive) as source:
    original_raw = source.extractfile('canvas-deployment.json').read()
    manifest = json.loads(original_raw)
    files = manifest_files(manifest)
    require(manifest['release_id'] == SOURCE_ID, 'Wrong recovery source')
    payload = {name: source.extractfile(name).read() for name in files}
    for name, entry in files.items():
        require(len(payload[name]) == entry['bytes'] and sha(payload[name]) == entry['sha256'], 'Original recovery file changed')
manifest.update({'release_id': RELEASE, 'at': datetime.now(UTC).isoformat(), 'storage_contract': CONTRACT,
                 'recovery_source': {'release_id': SOURCE_ID, 'manifest_sha256': sha(original_raw), 'archive_sha256': package['archive_sha256'], 'native_protocol_report': protocol_path.relative_to(ROOT).as_posix(), 'native_protocol_report_sha256': sha(protocol_path.read_bytes())}})
payload['canvas-deployment.json'] = (json.dumps(manifest, indent=2) + '\n').encode()
TARGET.mkdir()
target = TARGET / (RELEASE + '.tar.gz')
with tarfile.open(target, 'w:gz') as output:
    for name, data in sorted(payload.items()):
        member = tarfile.TarInfo(name); member.size = len(data); member.mode = 0o644
        output.addfile(member, io.BytesIO(data))
result = {**package, 'release_id': RELEASE, 'archive_bytes': target.stat().st_size, 'archive_sha256': sha(target.read_bytes()), 'recovery_source_release': SOURCE_ID, 'application_bytes_identical_to_source': True, 'storage_contract': CONTRACT}
for file in [TARGET / 'package.json', ROOT / 'docs/research/studio-implementation/independent-package-1460.json']:
    with file.open('x', encoding='utf-8') as output: output.write(json.dumps(result, indent=2) + '\n')
print(json.dumps({'passed': True, 'release_id': RELEASE, 'static_files': len(files), 'application_bytes_identical_to_source': True, 'api_modified': False}))
