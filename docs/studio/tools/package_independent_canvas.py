"""Package browser-verified frontend bytes and MIT notice without any API files."""
import hashlib
import io
import json
import re
import subprocess
import sys
import tarfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'infinite-canvas'
RELEASE = 'canvas-20261030.138.1'
PRIVATE = ROOT / '.local/independent-canvas-package'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def source_digest():
    web = SOURCE / 'web'
    files = ['package.json', 'package-lock.json', 'tsconfig.json', 'vite.config.ts', 'index.html', 'maintenance.html', '../VERSION', '../CHANGELOG.md']
    for directory in ('src', 'tests', 'public'):
        files.extend(file.relative_to(web).as_posix() for file in (web / directory).rglob('*') if file.is_file())
    value = hashlib.sha256()
    for name in sorted(files):
        value.update(name.encode())
        value.update(b'\0')
        value.update((web / name).read_bytes())
    return value.hexdigest()


def main():
    arguments = sys.argv[1:]
    release = RELEASE
    if arguments[:1] == ['--release']:
        release, arguments = arguments[1], arguments[2:]
    composer_path = None
    if '--composer-report' in arguments:
        index = arguments.index('--composer-report')
        composer_path = (ROOT / arguments[index + 1]).resolve()
        arguments = arguments[:index] + arguments[index + 2:]
    assert re.fullmatch(r'canvas-\d{8}\.\d+\.\d+', release)
    suffix = ''.join(release.split('.')[1:])
    private = PRIVATE if release == RELEASE else PRIVATE / release
    assert len(arguments) in {3, 4, 5, 6, 7}
    if release in {'canvas-20261030.141.1', 'canvas-20261030.142.1'}:
        assert len(arguments) == 4, 'Archive UI failure/recovery evidence required'
    if release == 'canvas-20261030.143.1':
        assert len(arguments) == 5, 'Archive and historical-reference deletion evidence required'
    if release == 'canvas-20261030.144.1':
        assert len(arguments) == 5, 'Archive and local media/animation evidence required'
    if release in {'canvas-20261030.145.1', 'canvas-20261030.146.1'}:
        assert len(arguments) == 6, 'Archive, cleanup UI and native protocol evidence required'
    if release == 'canvas-20261031.149.1':
        assert len(arguments) == 7, 'Archive, cleanup, native protocol and three-engine media actions evidence required'
    if release == 'canvas-20261031.150.1':
        assert len(arguments) == 3 and composer_path, 'Frozen native catalog and composer main-flow evidence required'
    build_path, browser_path, boundary_path = [ROOT / value for value in arguments[:3]]
    build, browser, boundary = [json.loads(value.read_text(encoding='utf-8')) for value in (build_path, browser_path, boundary_path)]
    assert all(value['passed'] for value in (build, browser, boundary))
    assert all(browser['cleanup'].values()) and browser['static_bytes_unchanged']
    assert build['source_before'] == build['source_after'] == build['copied_source_after'] == source_digest()
    clean = json.loads((ROOT / build['clean_install_reused']['file']).read_text(encoding='utf-8'))
    dist = ROOT / clean['private_logs'] / 'repository/web/dist'
    assert dist.resolve().is_relative_to((ROOT / '.local').resolve())
    subprocess.run(['git', 'diff', '--exit-code', '--', 'web', 'VERSION', 'CHANGELOG.md'], cwd=SOURCE, check=True, stdout=subprocess.DEVNULL)
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=SOURCE, text=True).strip()
    contents = {}
    for name, expected in build['static_artifact_sha256'].items():
        data = (dist / name).read_bytes()
        assert digest(data) == expected
        contents['studio/' + name] = data
    contents['studio/licenses/infinite-canvas-MIT.txt'] = (SOURCE / 'LICENSE').read_bytes()
    manifest = {'format_version': 1, 'release_id': release, 'at': datetime.now(UTC).isoformat(),
                'source_revision': revision, 'source_sha256': build['source_after'],
                'build_report': build_path.relative_to(ROOT).as_posix(), 'build_report_sha256': digest(build_path.read_bytes()),
                'browser_report': browser_path.relative_to(ROOT).as_posix(), 'browser_report_sha256': digest(browser_path.read_bytes()),
                'api_boundary_report': boundary_path.relative_to(ROOT).as_posix(), 'api_boundary_report_sha256': digest(boundary_path.read_bytes()),
                'api_modified': False, 'api_origin': 'http://43.165.167.179:39443', 'mode': 'managed', 'base': '/studio/',
                'application_backend': False, 'virtualization_used': False,
                'files': [{'path': name, 'bytes': len(data), 'sha256': digest(data)} for name, data in sorted(contents.items())]}
    if release in {'canvas-20261030.146.1', 'canvas-20261031.149.1', 'canvas-20261031.150.1'}:
        from studio_release_guard import CONTRACT
        contract = json.loads((SOURCE / 'web/src/services/studio-storage-contract.json').read_text(encoding='utf-8'))
        assert contract == CONTRACT
        manifest['storage_contract'] = contract
    if composer_path:
        assert composer_path.is_relative_to((ROOT / 'docs/research/studio-implementation').resolve())
        composer = json.loads(composer_path.read_text(encoding='utf-8'))
        assert composer['passed'] and not composer['baseline_reproduction'] and not composer['public_static_site']
        assert all(composer['cleanup'].values()) and composer['static_bytes_unchanged'] and len(composer['submissions']) == 12
        assert (ROOT / composer['build_report']).resolve() == build_path.resolve()
        assert composer['server_errors'] == composer['page_errors'] == composer['forbidden_requests'] == []
        manifest.update({'composer_report': composer_path.relative_to(ROOT).as_posix(), 'composer_report_sha256': digest(composer_path.read_bytes()), 'unchanged_storage_regression_not_rerun': True})
    if len(arguments) >= 4:
        extra_path = (ROOT / arguments[3]).resolve()
        assert extra_path.is_relative_to((ROOT / 'docs/research/studio-implementation').resolve())
        extra = json.loads(extra_path.read_text(encoding='utf-8'))
        assert extra['passed'] and all(extra['cleanup'].values()) and extra['static_bytes_unchanged']
        assert (ROOT / extra['build_report']).resolve() == build_path.resolve()
        manifest.update({'archive_report': extra_path.relative_to(ROOT).as_posix(), 'archive_report_sha256': digest(extra_path.read_bytes())})
    if len(arguments) >= 5:
        reference_path = (ROOT / arguments[4]).resolve()
        assert reference_path.is_relative_to((ROOT / 'docs/research/studio-implementation').resolve())
        reference = json.loads(reference_path.read_text(encoding='utf-8'))
        assert reference['passed'] and all(reference['cleanup'].values()) and reference['static_bytes_unchanged']
        assert reference['actual_api_requests'] == reference['generation_posts'] == 0
        assert (ROOT / reference['build_report']).resolve() == build_path.resolve()
        if release in {'canvas-20261030.145.1', 'canvas-20261030.146.1', 'canvas-20261031.149.1'}:
            assert reference['public_static_site'] is False and len(reference['checks']) >= 10
            assert reference['unexpected_requests'] == reference['page_errors'] == []
            manifest.update({'managed_cleanup_report': reference_path.relative_to(ROOT).as_posix(), 'managed_cleanup_report_sha256': digest(reference_path.read_bytes())})
        elif release == 'canvas-20261030.144.1':
            assert reference['public_static_site'] is False and len(reference['checks']) >= 14
            assert reference['native_inventory']['row_and_version_snapshots_identical']
            assert reference['native_inventory']['calls'] == {'blob': 0, 'writes': 0, 'fetch': 0}
            manifest.update({'media_tools_report': reference_path.relative_to(ROOT).as_posix(), 'media_tools_report_sha256': digest(reference_path.read_bytes())})
        else:
            manifest.update({'reference_deletion_report': reference_path.relative_to(ROOT).as_posix(), 'reference_deletion_report_sha256': digest(reference_path.read_bytes())})
    if len(arguments) >= 6:
        protocol_path = (ROOT / arguments[5]).resolve()
        assert protocol_path.is_relative_to((ROOT / 'docs/research/studio-implementation').resolve())
        protocol = json.loads(protocol_path.read_text(encoding='utf-8'))
        assert protocol['passed'] and all(protocol['cleanup'].values()) and len(protocol['checks']) >= 18
        assert protocol['api_requests'] == protocol['page_errors'] == []
        assert protocol['source_revision'] == revision
        manifest.update({'managed_cleanup_protocol_report': protocol_path.relative_to(ROOT).as_posix(), 'managed_cleanup_protocol_report_sha256': digest(protocol_path.read_bytes())})
    if len(arguments) == 7:
        media_path = (ROOT / arguments[6]).resolve()
        assert media_path.is_relative_to((ROOT / 'docs/research/studio-implementation').resolve())
        media = json.loads(media_path.read_text(encoding='utf-8'))
        assert media['passed'] and media['source_sha256'] == build['source_after']
        assert media['engines'] == ['chromium', 'firefox', 'webkit']
        assert media['functional_groups_passed'] >= 36 and not media['Windows_WebKit_playback_accepted']
        manifest.update({'media_actions_report': media_path.relative_to(ROOT).as_posix(), 'media_actions_report_sha256': digest(media_path.read_bytes())})
    contents['canvas-deployment.json'] = (json.dumps(manifest, indent=2) + '\n').encode()
    private.mkdir(parents=True, exist_ok=True)
    archive = private / (release + '.tar.gz')
    assert not archive.exists()
    with tarfile.open(archive, 'w:gz') as target:
        for name, data in sorted(contents.items()):
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), 0o644
            target.addfile(info, io.BytesIO(data))
    result = {'passed': True, 'release_id': release, 'archive_sha256': digest(archive.read_bytes()), 'archive_bytes': archive.stat().st_size,
              'source_revision': revision, 'source_sha256': build['source_after'], 'static_files': len(contents) - 1,
              'original_browser_verified_files': len(build['static_artifact_sha256']), 'additional_mit_notice': True, 'api_files': 0, 'api_modified': False}
    with (private / 'package.json').open('x', encoding='utf-8') as output:
        output.write(json.dumps(result, indent=2) + '\n')
    with (ROOT / f'docs/research/studio-implementation/independent-package-{suffix}.json').open('x', encoding='utf-8') as output:
        output.write(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
