"""Review frozen 149 media UI, actual local/public files, docs and API isolation."""
import hashlib
import io
import json
import re
import shutil
import subprocess
import tarfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote

from PIL import Image
from package_independent_canvas import source_digest

ROOT = Path(__file__).resolve().parents[2]
FRONT, PRIVATE = ROOT / 'infinite-canvas', ROOT / '.local/studio149'
EVIDENCE, TARGET = ROOT / 'docs/research/studio-implementation', FRONT / 'docs/studio'
names = json.loads((PRIVATE / 'receipts.json').read_text(encoding='utf-8'))
records = {key: json.loads((EVIDENCE / name).read_text(encoding='utf-8')) for key, name in names.items()}
assert all(record['passed'] for record in records.values())
code = json.loads((PRIVATE / 'code-revision.json').read_text(encoding='utf-8'))
revision = code['code_revision']
assert code['static_release'] == 'canvas-20261031.149.1' and code['recovery_release'] == 'canvas-20261031.149.0'
build, package, delivery = records['build'], records['package'], records['delivery']
assert package['source_revision'] == revision and build['source_after'] == package['source_sha256'] == source_digest()
assert build['unit_tests'] == 203 and build['unit_failures'] == 0 and all(stage['exit_code'] == 0 for stage in build['stages'])
assert build['source_before'] == build['copied_source_after'] == build['source_after']
assert subprocess.run(['git', 'diff', '--quiet', revision, '--', 'web', 'VERSION', 'CHANGELOG.md'], cwd=FRONT).returncode == 0
sha = lambda data: hashlib.sha256(data).hexdigest()
assert len(records['protocol']['checks']) == 18 and records['protocol']['source_revision'] == revision
assert len(records['dual_origin']['checks']) == 11 and all(records['dual_origin']['cleanup'].values())
assert records['dual_origin']['build_report'] == 'docs/research/studio-implementation/' + names['build']
assert records['release_guard']['tests'] == 12
for key, count, public in [('archive_local',14,False),('archive_public',14,True),('cleanup_local',10,False),('cleanup_public',10,True)]:
    record = records[key]
    assert record['public_static_site'] == public and len(record['checks']) == count and all(record['cleanup'].values())
    assert not record['resource_close_errors'] and not record['page_errors'] and not record['unexpected_requests']
    assert record['build_report'] == 'docs/research/studio-implementation/' + names['build'] and record['static_bytes_unchanged']
groups, downloads, screenshots = 0, 0, 0
for prefix, public in [('local', False), ('public', True)]:
    consolidated = records['media_' + prefix]
    assert consolidated['functional_groups_passed'] == 36 and consolidated['actual_downloads_verified'] == 39
    assert consolidated['source_revision'] == revision and consolidated['source_sha256'] == build['source_after']
    assert not consolidated['Windows_WebKit_playback_accepted'] and not consolidated['actual_Safari_accepted']
    for engine in ['chromium', 'firefox', 'webkit']:
        record = records[prefix + '_' + engine]
        assert record['passed'] and record['public_static_site'] == public and record['browser_engine'] == engine
        assert consolidated['receipts'][engine] == names[prefix + '_' + engine]
        assert consolidated['receipt_sha256'][names[prefix + '_' + engine]] == sha((EVIDENCE / names[prefix + '_' + engine]).read_bytes())
        assert all(record['cleanup'].values()) and not record['resource_close_errors'] and not record['page_errors'] and not record['unexpected_requests']
        assert len(record['checks']) == 12 and len(record['downloads']) == record['download_events'] == 13
        assert record['production_api_requests'] == record['generation_posts'] == 0 and record['static_bytes_unchanged']
        assert record['native_browser']['persistent_owned_profiles'] and record['native_browser']['ascii_runtime_verified']
        assert len(record['originals_before']) == 8 and len(record['originals_after']) == 10
        if engine == 'webkit': assert record['valid_fixture_playback_accepted'] is False
        else: assert record['valid_fixture_playback_accepted'].startswith('loaded and decoded only')
        assert len(record['screenshots']) == 3
        for item in record['screenshots']:
            data = (ROOT / item['file']).read_bytes()
            assert len(data) == item['bytes'] and sha(data) == item['sha256']
            Image.open(io.BytesIO(data)).verify(); screenshots += 1
        groups += len(record['checks']); downloads += len(record['downloads'])
assert groups == 72 and downloads == 78 and screenshots == 18
assert records['process_cleanup']['owned_browser_processes_remaining'] == [] and records['process_cleanup']['owned_profiles_closed']
assert records['prepared']['api_unchanged'] and records['prepared']['recovery_application_bytes_identical_to_source']
assert not records['prepared']['current_pointer_changed'] and not records['prepared']['static_service_restarted']
assert records['promoted']['from'] == 'canvas-20261030.146.1' and records['promoted']['to'] == code['static_release']
assert records['promoted']['before']['api'] == records['promoted']['after']['api'] == records['prepared']['before']['api']
assert records['promoted']['api_unchanged'] and not records['promoted']['configuration_written'] and not records['promoted']['static_service_restarted']
assert delivery['source_revision'] == revision and delivery['release_id'] == code['static_release']
assert delivery['static_files_verified'] == package['static_files'] and delivery['retained_previous_assets_verified'] == len(delivery['retained_paths'])
assert len(delivery['routes']) == 20 and delivery['api_services_unchanged'] and delivery['static_service_unchanged']
for key in ['api_boundary_before', 'api_boundary_after']: assert not records[key]['api_workspaces_written']
assert records['api_boundary_before']['api_workspaces'] == records['api_boundary_after']['api_workspaces']
with tarfile.open(ROOT / '.local/independent-canvas-package' / code['static_release'] / (code['static_release'] + '.tar.gz')) as archive:
    manifest = json.load(archive.extractfile('canvas-deployment.json'))
assert manifest['source_revision'] == revision and manifest['storage_contract'] == records['prepared']['storage_contract']
assert manifest['media_actions_report_sha256'] == sha((EVIDENCE / names['media_local']).read_bytes())
for item in records['media']['clips']:
    data = (ROOT / item['file']).read_bytes()
    assert len(data) == item['bytes'] and sha(data) == item['sha256'] and item['full_decode_passed']
assert records['media']['owned_browsers_closed_before_encoding'] and records['media']['full_selected_recordings_included']
failures, baseline_reproduced = [], False
for file in EVIDENCE.glob('media-actions-*.json'):
    record = json.loads(file.read_text(encoding='utf-8'))
    if record['passed']: continue
    assert all(record['cleanup'].values()) and not record.get('resource_close_errors')
    assert file.read_bytes() == (TARGET / 'research/studio-implementation' / file.name).read_bytes()
    failures.append(file.name)
    if record.get('baseline_reproduction') and record['downloads']:
        original = record['downloads'][0]
        baseline_reproduced |= original['expected_extension'] == 'webm' and original['actual_extension'] == '.mp4'
assert baseline_reproduced
failed_build = EVIDENCE / 'clean-build-2026-10-30T16-03-32-219Z.json'
assert not json.loads(failed_build.read_text(encoding='utf-8'))['passed'] and failed_build.read_bytes() == (TARGET / 'research/studio-implementation' / failed_build.name).read_bytes()
for name in names.values(): assert (EVIDENCE / name).read_bytes() == (TARGET / 'research/studio-implementation' / name).read_bytes()
backlog = {f'F-{number:02}' for number in range(1, 47)}
progress = (TARGET / '10-开发进度与交付记录.md').read_text(encoding='utf-8')
assert set(re.findall(r'^\| (F-\d+) \|', progress, re.M)) == backlog
assert all(f'| I-{number} |' in progress for number in range(157, 165))
for file in [FRONT / 'README.md', FRONT / 'README.en.md', TARGET / '34-Web媒体原件下载与播放失败处理.md']:
    text = file.read_text(encoding='utf-8')
    assert all(word in text for word in ['149.1', '78', 'WebKit', 'HOLD', 'WebM'])
for suffix in ['.mdx', '.zh-CN.mdx']:
    for kind in ['todo', 'pending-test']:
        text = (FRONT / f'docs/content/docs/progress/{kind}{suffix}').read_text(encoding='utf-8')
        assert '{/* independent149:start */}' in text and '78' in text and '149.1' in text
tools = ['prepare_studio_media_actions.py', 'verify_studio_media_actions.mjs', 'record_studio_media_acceptance.py', 'studio_web_browser.mjs', 'verify_studio_managed_cleanup_protocol.mjs', 'verify_studio_archive_import.mjs', 'verify_independent_host_unchanged.py', 'package_independent_canvas.py', 'package_studio_v5_recovery.py', 'studio_safe_release_host.py', 'studio_media_release_host.py', 'studio_media_release.py', 'verify_studio_media_release.py', 'prepare_independent_docs.py', 'record_studio_media_actions_media.py', 'finalize_studio_media_actions_docs.py', 'review_studio_media_actions.py']
tool_sha = {}  # Current reviewed scripts; individual earlier receipts retain their own execution history.
tools += ['verify_studio_managed_cleanup_ui.mjs', 'studio_api_boundary.py', 'studio_release_guard.py']
for name in tools:
    data = (ROOT / 'docs/tools' / name).read_bytes()
    assert data == (TARGET / 'tools' / name).read_bytes(); tool_sha[name] = sha(data)
output = EVIDENCE / 'independent-review-149.json'; mirror = TARGET / 'research/studio-implementation' / output.name
markdown = [FRONT / 'README.md', FRONT / 'README.en.md', *TARGET.rglob('*.md')]
links = []
for file in markdown:
    for raw in re.findall(r'!?\[[^\]\n]*\]\(([^\s\n)]+)\)', file.read_text(encoding='utf-8')):
        if raw.startswith(('http:', 'https:', '#', 'data:', 'mailto:', 'app:')): continue
        target = (file.parent / unquote(raw).split('#')[0]).resolve()
        assert target.is_relative_to(FRONT.resolve()) and (target.exists() or target == mirror.resolve()), f'Invalid local link: {file.name}: {raw}'
        links.append(target)
secrets = [json.loads((ROOT / '.local' / directory / name).read_text(encoding='utf-8'))['secret'].encode() for directory, name in [('studio138', 'read-only-key.json'), ('studio139', 'generation-key.json'), ('studio140', 'generation-key.json')]]
for file in [FRONT / 'README.md', FRONT / 'README.en.md', *TARGET.rglob('*')]:
    if file.is_file() and file.suffix.lower() in {'.md', '.json', '.mdx', '.py', '.mjs'}:
        assert all(secret not in file.read_bytes() for secret in secrets), 'Known private Key in public file'
value = {'passed': True, 'at': datetime.now(UTC).isoformat(), 'scope': 'Web149.1 original media formats/native-error fallback/focused actions, actual local/public native-engine files, related frozen regressions and independent static promotion. Not full playback, actual Safari, Windows WebKit Cookie/media acceptance, real quota, GPU or full Web acceptance.', **code, 'source_sha256': source_digest(), 'receipts': names, 'receipt_sha256': {key: sha((EVIDENCE / name).read_bytes()) for key, name in names.items()}, 'functional_media_groups_passed': groups, 'actual_original_downloads_verified': downloads, 'verified_media_screenshots': screenshots, 'valid_local_links': len(links), 'reviewed_current_tool_sha256': tool_sha, 'earlier_failed_media_reports_preserved': failures, 'old_public_WebM_filename_regression_reproduced': baseline_reproduced, 'first_failed_build_preserved': True, 'browser_cleanup_confirmed': True, 'private_owned_keys_absent': True, 'api_workspaces_written': False, 'api_services_restarted': False, 'static_service_restarted': False, 'actual_generation_posts': 0, 'Windows_WebKit_media_playback_accepted': False, 'Windows_WebKit_Cookie_isolation_accepted': False, 'actual_Safari_accepted': False, 'compatible_recovery_prepared': True, '149_actual_rollback_exercised': False, 'delivery_scope': 'Web application only', 'native_clients_required': False, 'full_backlog_ids_preserved': sorted(backlog), 'full_acceptance': 'HOLD/pending-test'}
with output.open('x', encoding='utf-8') as file: file.write(json.dumps(value, indent=2) + '\n')
shutil.copyfile(output, mirror)
assert all(target.exists() for target in links)
print(json.dumps({key: value[key] for key in ['passed', 'functional_media_groups_passed', 'actual_original_downloads_verified', 'verified_media_screenshots', 'valid_local_links', 'full_acceptance']}))
