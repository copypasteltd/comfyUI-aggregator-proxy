"""Build a committed additive 137 Linux release using the verified native 132.2 seed."""
import hashlib
import json
import shutil
import sys
import tarfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / 'aggregator-service/app'
PRIVATE = ROOT / '.local/studio-cloud-build'
RELEASE = 'linux-20261030.137.1'
sys.path.insert(0, str(APP / 'scripts'))
from release_source import source_revision, source_snapshot  # noqa: E402


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    snapshot = source_snapshot(APP)
    revision = source_revision(APP, snapshot)
    base = json.loads((PRIVATE / 'linux-20261030.132.2-build.json').read_text())
    seed = PRIVATE / 'linux-20261030.132.2.tar.gz'
    assert base['passed'] and sha(seed) == base['archive_sha256']
    stage = PRIVATE / RELEASE
    stage.mkdir(exist_ok=False)
    with tarfile.open(seed) as archive:
        manifest = json.load(archive.extractfile('linux-deployment.json'))
        declared = {item['path']: item for item in manifest['files']}
        for member in archive.getmembers():
            assert member.isfile() and not PurePosixPath(member.name).is_absolute() and '..' not in PurePosixPath(member.name).parts
            content = archive.extractfile(member).read()
            if member.name != 'linux-deployment.json':
                assert len(content) == declared[member.name]['bytes'] and hashlib.sha256(content).hexdigest() == declared[member.name]['sha256']
            if not member.name.startswith('admin/'):
                archive.extract(member, stage, filter='data')
    evidence = APP.parent / 'docs/evidence/studio-release-137'
    admin = json.loads(sorted(evidence.glob('admin-build-*.json'))[-1].read_text())
    assert admin['passed'] and admin['openapi_sha256'] == sha(APP / 'config/openapi.json')
    assert all(sha(APP / 'apps/admin_web' / name) == digest for name, digest in admin['source_sha256'].items())
    dist = ROOT / admin['private_workspace'] / 'dist'
    for name, digest in admin['dist_sha256'].items():
        assert sha(dist / name) == digest
        target = stage / 'admin' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(dist / name, target)
    requirements = stage / 'project-requirements.txt'
    raw, wheel_hashes = requirements.read_bytes(), {}
    wheels = sorted((APP / '.local/studio-wheels').glob('*.whl'))
    assert len(wheels) == 7
    for wheel in wheels:
        with zipfile.ZipFile(wheel) as archive:
            assert archive.testzip() is None
            for name in archive.namelist():
                if '.dist-info/' not in name and not name.endswith('/'):
                    matches = [base / name for folder in ('packages', 'apps') for base in (APP / folder).glob('*/src') if (base / name).is_file()]
                    assert len(matches) == 1 and matches[0].read_bytes() == archive.read(name)
        target = stage / 'wheels' / wheel.name
        previous = sha(target)
        assert raw.count(previous.encode()) == 1
        raw = raw.replace(previous.encode(), sha(wheel).encode())
        shutil.copyfile(wheel, target)
        wheel_hashes[wheel.name] = sha(wheel)
    requirements.write_bytes(raw)
    for folder in ('scripts', 'migrations', 'config'):
        for source in (APP / folder).rglob('*'):
            if source.is_file() and '__pycache__' not in source.parts:
                target = stage / folder / source.relative_to(APP / folder)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
    contract_root = APP / 'packages/contracts/src/generation_contracts'
    for source in [contract_root / 'endpoint-catalog.json', *sorted((contract_root / 'schemas').glob('*.json'))]:
        target = stage / 'contracts' / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    manifest.update({'created_at': datetime.now(UTC).isoformat(), 'release_id': RELEASE, 'base_linux_release': 'linux-20261030.132.2', 'base_linux_archive_sha256': sha(seed),
                     'application_source_revision': revision, 'current_source_sha256': snapshot, 'current_source_files_recorded': len(snapshot), 'project_wheels_rebuilt': True,
                     'rebuilt_projects': list(wheel_hashes), 'admin_rebuilt': True, 'admin_dist_sha256': admin['dist_sha256'], 'database_schema_changed': True,
                     'database_head': '04d137a00001', 'previous_database_head': '03d128a00001', 'worker_protocol_changed': False,
                     'default_legacy_routing': 'first_ready', 'physical_fleet_acceptance': 'pending'})
    manifest['files'] = [{'path': value.relative_to(stage).as_posix(), 'bytes': value.stat().st_size, 'sha256': sha(value)} for value in sorted(stage.rglob('*')) if value.is_file() and value.name != 'linux-deployment.json']
    (stage / 'linux-deployment.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    target = PRIVATE / (RELEASE + '.tar.gz')
    assert not target.exists()
    with tarfile.open(target, 'w:gz') as archive:
        for value in sorted(stage.rglob('*')):
            if value.is_file():
                archive.add(value, arcname=value.relative_to(stage).as_posix())
    assert source_snapshot(APP) == snapshot and source_revision(APP, snapshot) == revision
    result = {'passed': True, 'release_id': RELEASE, 'archive_sha256': sha(target), 'archive_bytes': target.stat().st_size,
              'application_source_revision': revision, 'wheel_sha256': wheel_hashes, 'source_files': len(snapshot), 'database_schema_changed': True,
              'database_head': '04d137a00001', 'worker_protocol_changed': False, 'admin_rebuilt': True, 'studio_static_changed': False, 'virtualization_used': False}
    with (PRIVATE / (RELEASE + '-build.json')).open('x', encoding='utf-8') as output:
        output.write(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
