"""Review actual independent theme/example artifacts, mirrors and unchanged API boundaries."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import unquote

from package_independent_canvas import source_digest
from studio_api_boundary import snapshot
from prepare_independent_docs import CURRENT_TOOLS

ROOT = Path(__file__).resolve().parents[2]
DOCS, FRONT, PRIVATE = ROOT / 'docs', ROOT / 'infinite-canvas', ROOT / '.local/studio151'
EVIDENCE = DOCS / 'research/studio-implementation'
names = json.loads((PRIVATE / 'receipts.json').read_text(encoding='utf-8'))
records = {key: json.loads((EVIDENCE / name).read_text(encoding='utf-8')) for key, name in names.items()}
build, package, delivery = [records[key] for key in ['build', 'package', 'delivery']]
assert build['passed'] and build['unit_tests'] == 218 and build['unit_failures'] == 0
assert all(stage['exit_code'] == 0 for stage in build['stages'])
assert build['source_before'] == build['source_after'] == build['copied_source_after'] == source_digest() == package['source_sha256'] == delivery['source_sha256']
assert package['source_revision'] == delivery['source_revision'] == json.loads((PRIVATE / 'code-revision.json').read_text(encoding='utf-8'))['source_revision']
assert delivery['passed'] and delivery['release_id'] == package['release_id'] == 'canvas-20261031.153.1'
assert delivery['package_sha256'] == package['archive_sha256']
assert records['recovery']['passed'] and records['recovery']['recovery_source_release'] == 'canvas-20261031.151.2'
assert records['recovery']['application_bytes_identical_to_source']
contract = json.loads((FRONT / 'web/src/services/studio-storage-contract.json').read_text(encoding='utf-8'))
assert contract == delivery['storage_contract'] == records['recovery']['storage_contract']
assert all(delivery[key] for key in ['api_healthy', 'api_pointer_unchanged', 'api_configuration_unchanged', 'api_services_unchanged', 'static_configuration_unchanged', 'static_service_unchanged', 'ssh_session_closed'])
assert delivery['service_restarts'] == 0 and delivery['api_workspaces_written'] is False and delivery['generation_posts'] == 0
assert len(delivery['routes']) == 23 and delivery['routes']['/studio/library'] == delivery['routes']['/studio/library/'] == 200
assert not records['failed_public_route']['passed']
assert [row['status'] for row in records['failed_public_route']['observed_routes']] == [301, 403]
assert snapshot() == json.loads((ROOT / '.local/standalone-api-baseline.json').read_text(encoding='utf-8'))
assert subprocess.check_output(['git', 'diff', '7dcaaa5123cf0fa599fa12eb3b24c1653dde5863', 'HEAD', '--', 'web/src/styles/reference', 'web/public/fonts', 'web/src/services/studio-storage-contract.json'], cwd=FRONT) == b''
assert subprocess.check_output(['git', 'status', '--porcelain=v1', '--', 'web', 'CHANGELOG.md'], cwd=FRONT) == b''
assert not records['early_build']['passed'], 'Intermediate nonfrozen build must not become release evidence'
accepted, failed, screen_count, checks = [], [], 0, 0
for key in ['local_chromium', 'local_firefox', 'public_chromium']:
    report = records[key]
    assert report['build_report'] == 'docs/research/studio-implementation/' + names['build']
    assert report['static_bytes_unchanged'] and all(report['cleanup'].values()) and not report.get('resource_close_errors')
    if not report['passed']:
        failed.append(key)
        assert report.get('failure')
        continue
    accepted.append(key)
    assert not report['errors'] and not report['page_errors'] and len(report['submissions']) == 8
    assert {row['body']['task_type'] for row in report['submissions']} == {'text_to_image', 'image_to_image', 'references_to_image', 'text_to_video', 'references_to_video', 'text_to_audio', 'image_to_3d'}
    assert {row['path'] for row in report['requests'] if row['path'].endswith('/download')} == {f"/v1/content/{row['content_id']}/download" for row in report['submissions']}
    assert all(not row['cookie_present'] for row in report['requests'])
    assert any('before the application module' in row for row in report['checks'])
    assert any('excluding generated images' in row for row in report['checks'])
    assert any('unknown stored theme' in row for row in report['checks'])
    assert any('direct reload' in row for row in report['checks'])
    for row in report['screenshots']:
        file = ROOT / row['file']; assert file.is_file() and hashlib.sha256(file.read_bytes()).hexdigest() == row['sha256']
    screen_count += len(report['screenshots']); checks += len(report['checks'])
assert 'local_chromium' in accepted and 'public_chromium' in accepted
gallery = records['gallery']
assert gallery['passed'] and gallery['gallery_only'] and gallery['public_static_site']
assert all(gallery['cleanup'].values()) and not gallery.get('resource_close_errors')
assert not gallery['submissions'] and all(row['method'] == 'GET' for row in gallery['requests'])
assert not gallery['errors'] and not gallery['page_errors'] and len(gallery['screenshots']) == 4
for row in gallery['screenshots']:
    file = ROOT / row['file']; assert hashlib.sha256(file.read_bytes()).hexdigest() == row['sha256']
screen_count += len(gallery['screenshots'])
assert records['media']['passed'] and records['media']['owned_browsers_remaining'] == 0 and records['media']['actual_generation'] is False
for row in records['media']['clips']:
    file = ROOT / row['file']; assert row['complete_decode_passed'] and file.stat().st_size == row['bytes'] and hashlib.sha256(file.read_bytes()).hexdigest() == row['sha256']
    assert file.read_bytes() == (FRONT / row['file'].replace('docs/', 'docs/studio/', 1)).read_bytes()
for row in records['assets']['files']:
    file = ROOT / row['file']; assert hashlib.sha256(file.read_bytes()).hexdigest() == row['sha256']
    assert file.read_bytes() == (ROOT / row['source']).read_bytes()
for name in names.values():
    assert (EVIDENCE / name).read_bytes() == (FRONT / 'docs/studio/research/studio-implementation' / name).read_bytes()
for name in CURRENT_TOOLS:
    assert (DOCS / 'tools' / name).read_bytes() == (FRONT / 'docs/studio/tools' / name).read_bytes(), 'Stale current delivery tool mirror'
progress = (DOCS / '10-开发进度与交付记录.md').read_text(encoding='utf-8')
assert set(re.findall(r'^\| (F-\d\d) \|', progress, re.M)) == {f'F-{index:02d}' for index in range(1,47)}
assert all(f'I-{index}' in progress for index in range(173,181))
for name in ['README.md', 'README.en.md', 'CHANGELOG.studio.md']:
    text = (FRONT / name).read_text(encoding='utf-8'); assert '153.1' in text and 'HOLD' in text
links = 0
future = {EVIDENCE / 'independent-review-151.json', FRONT / 'docs/studio/research/studio-implementation/independent-review-151.json'}
for file in [DOCS / name for name in ['36-明暗主题与预设创作案例.md', '10-开发进度与交付记录.md', '21-独立画布部署与使用.md', '23-独立画布更新记录.md', 'README.md']] + [FRONT / name for name in ['README.md', 'README.en.md', 'CHANGELOG.studio.md']] + list((FRONT / 'docs/studio').glob('*.md')):
    for link in re.findall(r'!?\[[^\]\n]*\]\(([^\s\n)]+)\)', file.read_text(encoding='utf-8')):
        if '://' in link or link.startswith('#'): continue
        path = (file.parent / unquote(link.split('#')[0])).resolve()
        assert path in future or path.is_file() or path.is_dir(), f'Broken local link: {file.name}: {link}'
        links += 1
result = {'passed': True, 'source_revision': package['source_revision'], 'source_sha256': package['source_sha256'], 'release': package['release_id'], 'unit_tests': 218, 'complete_ui_groups': checks, 'verified_screenshots': screen_count, 'accepted_browser_runs': accepted, 'unaccepted_browser_runs': failed, 'public_firefox_retested': False, 'actual_generation_posts': 0, 'owned_browsers_remaining': 0, 'valid_local_links': links, 'current_tools_mirrored': len(CURRENT_TOOLS), 'api_written': False, 'service_restarts': 0, 'recovery_prepared': True, 'actual_151_rollback_exercised': False, 'full_acceptance': 'HOLD/pending-test', 'scope': 'Light/dark themes and eight editable examples in the accepted native/public browser runs, synthetic API/media only. Does not supersede the earlier real video failure, public Firefox, WebKit/Safari, quota or complete Web/fleet acceptance.'}
raw = (json.dumps(result, indent=2) + '\n').encode()
for target in [EVIDENCE / 'independent-review-151.json', FRONT / 'docs/studio/research/studio-implementation/independent-review-151.json']:
    with target.open('xb') as output: output.write(raw)
print(json.dumps(result))
