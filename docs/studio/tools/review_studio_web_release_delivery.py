"""Review actual Web release evidence without changing product or API state."""
import hashlib
import json
import re
import subprocess
import tarfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote

from package_independent_canvas import source_digest
from studio_release_guard import CONTRACT, compatible

ROOT = Path(__file__).resolve().parents[2]
FRONT, PRIVATE = ROOT / 'infinite-canvas', ROOT / '.local/studio146'
EVIDENCE = ROOT / 'docs/research/studio-implementation'
names = json.loads((PRIVATE / 'receipts.json').read_text(encoding='utf-8'))
records = {key: json.loads((EVIDENCE / name).read_text(encoding='utf-8')) for key, name in names.items()}
assert all(record['passed'] for record in records.values())
build, package, delivery = (records[key] for key in ['build', 'package', 'delivery'])
revision = json.loads((PRIVATE / 'code-revision.json').read_text(encoding='utf-8'))['code_revision']
assert package['source_revision'] == revision == 'db56cae3634da0361074e84024ccd5b428d189fa'
assert build['source_before'] == build['source_after'] == build['copied_source_after'] == package['source_sha256'] == source_digest()
assert build['unit_tests'] == 203 and build['unit_failures'] == 0
assert {item['name'] for item in build['stages']} == {'unit', 'typecheck', 'build'}
assert all(item['exit_code'] == 0 for item in build['stages'])
assert not build['virtualization_used'] and not build['force_or_peer_bypass']
reused = build['clean_install_reused']
assert hashlib.sha256((ROOT / reused['file']).read_bytes()).hexdigest() == reused['sha256']
assert subprocess.run(['git', 'diff', '--quiet', revision, '--', 'web', 'VERSION', 'CHANGELOG.md'], cwd=FRONT).returncode == 0
assert json.loads((FRONT / 'web/src/services/studio-storage-contract.json').read_text(encoding='utf-8')) == CONTRACT

def manifest(release):
    archive = ROOT / f'.local/independent-canvas-package/{release}/{release}.tar.gz'
    with tarfile.open(archive) as package_file:
        value = json.load(package_file.extractfile('canvas-deployment.json'))
        for entry in value['files']:
            data = package_file.extractfile(entry['path']).read()
            assert len(data) == entry['bytes'] and hashlib.sha256(data).hexdigest() == entry['sha256']
    return value

current, recovery, previous = (manifest('canvas-20261030.' + number) for number in ['146.1', '146.0', '145.1'])
compatible(current, recovery)
assert current['storage_contract'] == recovery['storage_contract'] == CONTRACT
assert recovery['files'] == previous['files']
assert recovery['source_revision'] == previous['source_revision'] == 'ae0ca22e996c16c137b2ed35ceab7c1c3e492044'
assert records['recovery_package']['application_bytes_identical_to_source']
assert records['recovery_package']['recovery_source_release'] == previous['release_id']
guard = records['guard_unit']
assert guard['tests'] == 12 and guard['exit_code'] == 0 and guard['source_unchanged']
for file, expected in guard['source_sha256'].items():
    assert hashlib.sha256((ROOT / 'docs/tools' / file).read_bytes()).hexdigest() == expected
control = records['control_v2']
assert control['before'] == control['after'] and control['api_unchanged']
assert not control['static_pointer_changed'] and control['service_restarts'] == 0 and control['original_controller_preserved']
for file, expected in control['controller_sha256'].items():
    assert hashlib.sha256((ROOT / 'docs/tools' / file).read_bytes()).hexdigest() == expected

protocol = records['protocol']
assert len(protocol['checks']) == 18 and protocol['source_revision'] == revision
assert not protocol['api_requests'] and not protocol['page_errors'] and all(protocol['cleanup'].values())
screenshots = 0
groups = {'synthetic_browser': 11, 'cleanup_native': 10, 'cleanup_public': 10, 'archive_native': 14, 'archive_public': 14, 'refresh_native': 3, 'refresh_public': 3, 'media_native': 14}
for key, count in groups.items():
    value = records[key]
    assert len(value['checks']) == count and value['static_bytes_unchanged']
    assert all(value['cleanup'].values()) and len(value['cleanup']) >= 3
    assert (ROOT / value['build_report']).resolve() == (EVIDENCE / names['build']).resolve()
    for field in ['unexpected_requests', 'page_errors']:
        if field in value: assert value[field] == [], (key, field)
    for item in value['screenshots']:
        file = ROOT / item['file']
        assert file.is_file() and hashlib.sha256(file.read_bytes()).hexdigest() == item['sha256']
        screenshots += 1
for key in ['cleanup_public', 'archive_public', 'refresh_public']:
    assert records[key]['public_static_site']
for key in ['archive_native', 'archive_public']:
    assert records[key]['suite'] == '146' and len(records[key]['rejected_archives']) == 12
assert records['refresh_baseline']['baseline_1451'] and records['refresh_baseline']['regression_observed']
assert records['refresh_public']['browser_engine'] == 'chromium' and not records['refresh_public']['regression_observed']
assert len(records['cleanup_public']['screenshots']) == 6
assert all(item['viewport_only'] for item in records['cleanup_public']['screenshots'])
assert records['media_native']['native_inventory']['calls'] == {'blob': 0, 'writes': 0, 'fetch': 0}
assert records['media_native']['native_inventory']['row_and_version_snapshots_identical']
closed = records['media_native']['closed_resources']
assert closed['frames'] == closed['bitmaps'] == closed['image_urls'] == closed['observers'] == 0
assert all(value['lost'] for value in closed['contexts'])

web = records['rollback_web']
assert len(web['checks']) == 8 and all(web['cleanup'].values())
assert web['actual_api_requests'] == web['generation_posts'] == 0
assert not web['unexpected_requests'] and not web['page_errors']
assert web['raw_rollback_data_sha256'] == web['committed_data_sha256']
assert web['raw_before_restore_data_sha256'] == web['raw_after_restore_data_sha256']
assert web['committed_semantic_sha256'] == web['after_semantic_sha256']
assert web['reject_v4']['refused'] and web['reject_v4']['pointer_unchanged']
assert web['initial_restore']['to'] == web['restore']['to'] == 'canvas-20261030.146.1'
assert web['rollback']['to'] == 'canvas-20261030.146.0'
for key in ['initial_restore', 'rollback', 'restore']:
    value = web[key]
    assert value['api_unchanged'] and value['before']['api'] == value['after']['api']
    assert not value['configuration_written'] and not value['static_service_restarted'] and not value['browser_database_touched_by_host']
    assert value['before']['static']['service'] == value['after']['static']['service']
    assert value['storage_contract'] == CONTRACT
for item in web['screenshots']:
    assert hashlib.sha256((ROOT / item['file']).read_bytes()).hexdigest() == item['sha256']
    screenshots += 1

assert package['release_id'] == delivery['release_id'] == 'canvas-20261030.146.1'
assert package['archive_bytes'] == 1545721 and package['archive_sha256'] == delivery['package_sha256']
assert package['static_files'] == delivery['static_files_verified'] == 114
assert delivery['retained_previous_assets_verified'] == len(delivery['retained_paths']) == 688
assert len(delivery['routes']) == 15 and delivery['routes']['/studio/maintenance.html'] == 200
assert all(delivery['routes'][route] == 404 for route in ['/v1/models', '/admin/', '/internal/v1/register', '/api/agents'])
for key in ['api_pointer_unchanged', 'api_configuration_unchanged', 'api_services_unchanged', 'original_api_cors', 'api_healthy', 'compatible_recovery_browser_verified']:
    assert delivery[key]
assert delivery['api_services_before'] == delivery['api_services_after']
assert not delivery['static_service_restarted'] and not delivery['static_configuration_written'] and not delivery['virtualization_used']
assert not delivery['generation_submitted_by_verifier'] and delivery['api_files_in_package'] == package['api_files'] == 0
assert delivery['storage_contract'] == CONTRACT and delivery['delivery_scope'] == 'Web application only'
assert records['api_boundary']['api_workspaces'] == records['api_boundary_after']['api_workspaces']
for key in ['api_boundary', 'api_boundary_after']:
    assert not records[key]['api_workspaces_written'] and not records[key]['api_changes_allowed']

media = records['media']
assert len(media['clips']) == 4 and media['owned_browsers_closed_before_encoding'] and media['full_selected_recordings_included']
assert not media['actual_generation']
for item in media['sources']:
    assert hashlib.sha256((ROOT / item['browser_report']).read_bytes()).hexdigest() == item['sha256']
for item in media['clips']:
    file = ROOT / item['file']
    assert file.stat().st_size == item['bytes'] and hashlib.sha256(file.read_bytes()).hexdigest() == item['sha256'] and item['full_decode_passed']
failures = ['safe-rollback-Web-2026-10-30T14-15-53-328Z.json', 'safe-rollback-Web-2026-10-30T14-34-55-364Z.json', 'cleanup-refresh-public-2026-10-30T14-38-45-331Z.json', 'cleanup-refresh-cross-engine-interrupted-1461.json', 'cleanup-refresh-2026-10-30T13-59-54-916Z.json']
for name in failures:
    assert not json.loads((EVIDENCE / name).read_text(encoding='utf-8'))['passed']
    assert (FRONT / 'docs/studio/research/studio-implementation' / name).read_bytes() == (EVIDENCE / name).read_bytes()
for name in names.values():
    assert (FRONT / 'docs/studio/research/studio-implementation' / name).read_bytes() == (EVIDENCE / name).read_bytes()

progress = (FRONT / 'docs/studio/10-开发进度与交付记录.md').read_text(encoding='utf-8')
backlog = {f'F-{n:02}' for n in range(1, 47)}
assert set(re.findall(r'^\| (F-\d+) \|', progress, re.M)) == backlog
assert all(f'| I-{n} |' in progress for n in range(134, 142))
for file in [FRONT / 'README.md', FRONT / 'README.en.md', FRONT / 'docs/studio/21-独立画布部署与使用.md', FRONT / 'docs/studio/31-Web交付范围与兼容静态回退.md']:
    text = file.read_text(encoding='utf-8')
    assert '146.1' in text and 'Web' in text and 'HOLD' in text and '203' in text
    assert '31-' in text or file.name.startswith('31-')
detail = (FRONT / 'docs/studio/31-Web交付范围与兼容静态回退.md').read_text(encoding='utf-8')
assert '<!-- CURRENT-EVIDENCE -->' not in detail and all(f'RB-{n:02}' in detail for n in range(1, 11))
for suffix in ['.mdx', '.zh-CN.mdx']:
    for name in ['pending-test', 'todo']:
        text = (FRONT / f'docs/content/docs/progress/{name}{suffix}').read_text(encoding='utf-8')
        assert '146.1' in text and 'Web' in text
markdown = [FRONT / 'README.md', FRONT / 'README.en.md', *(FRONT / 'docs/studio').rglob('*.md')]
links = 0
for file in markdown:
    for raw in re.findall(r'!?\[[^\]\n]*\]\(([^\s\n)]+)\)', file.read_text(encoding='utf-8')):
        if raw.startswith(('http:', 'https:', '#', 'data:', 'mailto:', 'app:')): continue
        target = (file.parent / unquote(raw).split('#')[0]).resolve()
        assert target.exists() and target.is_relative_to(FRONT.resolve()), f'Invalid local link: {file.name}: {raw}'
        links += 1
secrets = [json.loads((ROOT / '.local' / directory / name).read_text(encoding='utf-8'))['secret'].encode() for directory, name in [('studio138', 'read-only-key.json'), ('studio139', 'generation-key.json'), ('studio140', 'generation-key.json')]]
for file in [FRONT / 'README.md', FRONT / 'README.en.md', *(FRONT / 'docs/studio').rglob('*'), *(FRONT / 'web/src').rglob('*')]:
    if file.is_file() and file.suffix.lower() in {'.md', '.json', '.ts', '.tsx', '.mdx', '.py', '.mjs'}:
        assert all(secret not in file.read_bytes() for secret in secrets), 'Private owned Key in public content'
value = {'passed': True, 'at': datetime.now(UTC).isoformat(), 'scope': 'Web-only independent146.1; complete storage-contract guard, latest-snapshot fix, real static rollback/restore, frozen build, native/public regressions, selected page recordings and read-only API boundary. Not full browser/version/fault/actual-quota/fleet acceptance.', 'code_revision': revision, 'source_sha256': package['source_sha256'], 'receipts': names, 'receipt_sha256': {key: hashlib.sha256((EVIDENCE / name).read_bytes()).hexdigest() for key, name in names.items()}, 'public_markdown_files': len(markdown), 'valid_local_links': links, 'verified_screenshots': screenshots, 'private_owned_keys_absent': True, 'api_workspaces_written': False, 'api_services_restarted': False, 'static_service_restarted': False, 'actual_generation_posts': 0, 'delivery_scope': 'Web application only', 'compatible_v5_static_rollback_verified': True, 'raw_records_identical_across_static_switches': True, 'render_save_differences_explicitly_scoped': True, 'supported_browser_evidence': ['chromium'], 'incomplete_browser_checks_not_counted': ['firefox', 'webkit'], 'native_clients_required': False, 'full_backlog_ids_preserved': sorted(backlog), 'browser_cleanup_confirmed': True, 'earlier_failures_preserved': True, 'full_acceptance': 'HOLD/pending-test'}
with (EVIDENCE / 'independent-review-1461.json').open('x', encoding='utf-8') as output:
    output.write(json.dumps(value, indent=2) + '\n')
print(json.dumps({key: value[key] for key in ['passed', 'public_markdown_files', 'valid_local_links', 'verified_screenshots', 'browser_cleanup_confirmed', 'api_workspaces_written', 'full_acceptance']}))
