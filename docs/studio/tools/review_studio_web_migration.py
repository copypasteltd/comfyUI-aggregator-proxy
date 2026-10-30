"""Review actual mixed-original Web ZIP hand-offs without rebuilding frozen code."""
import hashlib
import io
import json
import re
import shutil
import subprocess
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote

from PIL import Image
from package_independent_canvas import source_digest

ROOT = Path(__file__).resolve().parents[2]
FRONT, PRIVATE = ROOT / 'infinite-canvas', ROOT / '.local/studio148'
EVIDENCE, TARGET = ROOT / 'docs/research/studio-implementation', FRONT / 'docs/studio'
names = json.loads((PRIVATE / 'receipts.json').read_text(encoding='utf-8'))
records = {key: json.loads((EVIDENCE / name).read_text(encoding='utf-8')) for key, name in names.items()}
assert all(item['passed'] for item in records.values())
revision = json.loads((PRIVATE / 'code-revision.json').read_text(encoding='utf-8'))
assert revision == {'code_revision': 'db56cae3634da0361074e84024ccd5b428d189fa', 'documentation_base': 'f2460f8c5d82851e21f613bb3a363059a10a1e6b', 'static_release': 'canvas-20261030.146.1', 'new_product_release': False}
build = records['build']
assert build['source_before'] == build['source_after'] == source_digest()
assert build['unit_tests'] == 203 and build['unit_failures'] == 0
assert all(stage['exit_code'] == 0 for stage in build['stages'])
assert records['delivery_baseline']['release_id'] == revision['static_release']
assert subprocess.run(['git', 'diff', '--quiet', revision['code_revision'], '--', 'web', 'VERSION', 'CHANGELOG.md'], cwd=FRONT).returncode == 0
sha = lambda data: hashlib.sha256(data).hexdigest()
fixtures = records['fixtures']['fixtures']
assert len(fixtures) == 8 and not records['fixtures']['virtualization_used']
for item in fixtures:
    data = (ROOT / item['file']).read_bytes()
    assert len(data) == item['bytes'] and sha(data) == item['sha256'] and item['integrity_and_format_verified']
fixture_set = sorted((item['sha256'], item['bytes'], item['mime_type']) for item in fixtures)
expected_engines = ['firefox', 'chromium', 'webkit', 'firefox']
groups, screenshots, origins = [], 0, []
for index, engine in enumerate(expected_engines):
    record = records['stage' + str(index)]
    assert record['stage'] == index and record['browser_engine'] == engine
    assert record['chain_id'] == 'dba1704d-03cc-4fd9-8fee-abbe1485734b'
    assert record['static_bytes_unchanged'] and not record['new_product_release']
    assert record['build_report'] == 'docs/research/studio-implementation/' + names['build']
    assert all(record['cleanup'].values()) and not record['resource_close_errors']
    assert not record['page_errors'] and not record['unexpected_requests']
    assert record['production_api_requests'] == record['generation_posts'] == 0
    assert record['native_browser']['playwright_version'] == '1.63.0'
    assert record['native_browser']['native_revision'] == {'firefox': '1543', 'chromium': '1243', 'webkit': '2359'}[engine]
    assert record['native_browser']['persistent_owned_profiles'] and record['native_browser']['ascii_runtime_verified']
    assert not record['native_browser']['product_timeouts_changed']
    state = record['snapshot']
    assert len(state['projects']) == 1 and len(state['projects'][0]['nodes']) == 9
    assert len(state['projects'][0]['connections']) == 5 and len(state['files']) == 8
    assert all(count == 0 for count in state['tasks'].values())
    assert sorted((file['sha256'], file['bytes'], file['mimeType']) for file in state['files']) == fixture_set
    image = next(node for node in state['projects'][0]['nodes'] if node['id'] == 'original-image')['metadata']
    assert image['references'][0] == image['references'][2] and image['references'][1] == image['storageKey']
    assert image['prompt'] == 'image:managed-v1:migration-web-reference-png'
    assert next(node for node in state['projects'][0]['nodes'] if node['id'] == 'model-glb')['type'] == 'studio:model3d'
    export = record['export']
    raw = (ROOT / export['file']).read_bytes()
    assert len(raw) == export['bytes'] and sha(raw) == export['sha256']
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        manifest = json.loads(archive.read('projects.json'))
        assert manifest['app'] == 'infinite-canvas' and manifest['version'] == 3
        files = manifest['projects'][0]['files']
        assert sorted((file['sha256'], file['bytes'], file['mimeType']) for file in files) == fixture_set
        assert len(archive.namelist()) == 9
        for file in files:
            original = archive.read(file['path'])
            assert len(original) == file['bytes'] and sha(original) == file['sha256']
        assert any(file['mimeType'] == 'video/webm' and file['path'].endswith('.webm') for file in files)
        encoded = json.dumps(manifest)
        for secret in ['synthetic-migration-cookie', 'synthetic-migration-header', 'synthetic-migration-query', 'synthetic-migration-fragment']:
            assert secret not in encoded
    if index:
        previous = records['stage' + str(index - 1)]
        assert record['input_archive']['file'] == previous['export']['file']
        assert record['input_archive']['sha256'] == previous['export']['sha256']
        assert state['projects'][0]['id'] != previous['snapshot']['projects'][0]['id']
        assert not {file['key'] for file in state['files']} & {file['key'] for file in previous['snapshot']['files']}
        task = next(node for node in state['projects'][0]['nodes'] if node['id'] == 'old-task')
        assert task['type'] == 'text' and '不会重新提交' in task['metadata']['content']
    observations = record['playback_observations']
    assert len(observations) == 4 and all(item['source_is_local_blob'] for item in observations)
    assert all(item['error_code'] == 4 and item['ready_state'] == 0 for item in observations) if engine == 'webkit' else all(item['error_code'] is None and item['ready_state'] == 4 and item['duration'] == 1 for item in observations)
    assert len(record['checks']) == (7 if index == 0 else 9)
    groups.append(len(record['checks'])); origins.append(record['origin'])
    assert len(record['screenshots']) == 3
    for item in record['screenshots']:
        data = (ROOT / item['file']).read_bytes()
        assert len(data) == item['bytes'] and sha(data) == item['sha256']
        Image.open(io.BytesIO(data)).verify()
        screenshots += 1
assert sum(groups) == 34 and screenshots == 12
assert origins[0] == origins[3] == 'http://43.165.167.179:39445'
assert origins[1] != origins[2] and all(origin.startswith('http://192.168.31.95:') for origin in origins[1:3])
cleanup = records['process_cleanup']
assert cleanup['owned_profiles_closed'] and cleanup['owned_browser_processes_remaining'] == []
failures = []
for file in EVIDENCE.glob('Web-migration-*.json'):
    value = json.loads(file.read_text(encoding='utf-8'))
    if value['passed']: continue
    assert all(value['cleanup'].values()) and not value.get('resource_close_errors')
    assert file.read_bytes() == (TARGET / 'research/studio-implementation' / file.name).read_bytes()
    failures.append(file.name)
assert len(failures) == 3
media = records['media']
assert media['owned_browsers_closed_before_encoding'] and media['full_selected_recordings_included']
assert not media['actual_generation'] and not media['new_product_release']
assert len(media['clips']) == 4
for item in media['clips']:
    data = (ROOT / item['file']).read_bytes()
    assert len(data) == item['bytes'] and sha(data) == item['sha256'] and item['full_decode_passed']
host = records['host']
for field in ['api_pointer_unchanged', 'api_configuration_unchanged', 'api_services_unchanged', 'static_pointer_unchanged', 'static_configuration_unchanged', 'static_service_unchanged', 'ssh_session_closed']:
    assert host[field]
assert host['service_restarts'] == 0 and not host['api_code_written']
assert not records['api_boundary']['api_workspaces_written']
for name in names.values():
    assert (EVIDENCE / name).read_bytes() == (TARGET / 'research/studio-implementation' / name).read_bytes()
backlog = {f'F-{number:02}' for number in range(1, 47)}
progress = (TARGET / '10-开发进度与交付记录.md').read_text(encoding='utf-8')
assert set(re.findall(r'^\| (F-\d+) \|', progress, re.M)) == backlog
assert all(f'| I-{number} |' in progress for number in range(150, 157))
for file in [FRONT / 'README.md', FRONT / 'README.en.md', TARGET / '33-Web混合媒体跨浏览器备份恢复.md']:
    text = file.read_text(encoding='utf-8')
    assert all(word in text for word in ['148', '146.1', '34', 'WebKit', 'Firefox', 'HOLD', 'WebM'])
for suffix in ['.mdx', '.zh-CN.mdx']:
    for kind in ['todo', 'pending-test']:
        text = (FRONT / f'docs/content/docs/progress/{kind}{suffix}').read_text(encoding='utf-8')
        assert all(word in text for word in ['148', '146.1', '34', 'WebKit', 'Firefox'])
tools = ['prepare_studio_web_migration.py', 'verify_studio_web_migration.mjs', 'studio_web_browser.mjs', 'record_studio_web_migration_media.py', 'review_studio_web_migration.py', 'verify_independent_host_unchanged.py', 'prepare_independent_docs.py']
tool_sha = {}
for name in tools:
    data = (ROOT / 'docs/tools' / name).read_bytes()
    assert data == (TARGET / 'tools' / name).read_bytes()
    tool_sha[name] = sha(data)
output = EVIDENCE / 'independent-review-148.json'
mirror = TARGET / 'research/studio-implementation' / output.name
markdown = [FRONT / 'README.md', FRONT / 'README.en.md', *TARGET.rglob('*.md')]
links = []
for file in markdown:
    for raw in re.findall(r'!?\[[^\]\n]*\]\(([^\s\n)]+)\)', file.read_text(encoding='utf-8')):
        if raw.startswith(('http:', 'https:', '#', 'data:', 'mailto:', 'app:')): continue
        target = (file.parent / unquote(raw).split('#')[0]).resolve()
        assert target.is_relative_to(FRONT.resolve()) and (target.exists() or target == mirror.resolve()), f'Invalid link: {file.name}: {raw}'
        links.append(target)
secrets = [json.loads((ROOT / '.local' / directory / name).read_text(encoding='utf-8'))['secret'].encode() for directory, name in [('studio138', 'read-only-key.json'), ('studio139', 'generation-key.json'), ('studio140', 'generation-key.json')]]
for file in [FRONT / 'README.md', FRONT / 'README.en.md', *TARGET.rglob('*')]:
    if file.is_file() and file.suffix.lower() in {'.md', '.json', '.mdx', '.py', '.mjs'}:
        assert all(secret not in file.read_bytes() for secret in secrets), 'Known private business Key in public file'
value = {'passed': True, 'at': datetime.now(UTC).isoformat(), 'scope': 'Evidence148 on frozen146.1: actual synthetic mixed-original ZIP migration across three native engines/four fresh profiles/public plus LAN HTTP origins. Byte-identical migration acceptance only; Windows WebKit audio/video decoding and Cookie isolation, actual Safari, full manual/fault/quota/large-file acceptance remain pending.', **revision, 'source_sha256': source_digest(), 'frozen_203_test_build_reused_not_rerun': True, 'receipts': names, 'receipt_sha256': {key: sha((EVIDENCE / name).read_bytes()) for key, name in names.items()}, 'functional_groups_passed': sum(groups), 'functional_groups': groups, 'verified_screenshots': screenshots, 'valid_local_links': len(links), 'public_markdown_files': len(markdown), 'test_tool_sha256': tool_sha, 'earlier_failures_preserved': failures, 'browser_cleanup_confirmed': True, 'private_owned_keys_absent': True, 'Windows_WebKit_media_playback_accepted': False, 'Windows_WebKit_Cookie_isolation_accepted': False, 'actual_Safari_accepted': False, 'general_WebM_save_filename_correction_pending': True, 'api_workspaces_written': False, 'api_services_restarted': False, 'static_service_restarted': False, 'static_pointer_changed': False, 'actual_generation_posts': 0, 'full_backlog_ids_preserved': sorted(backlog), 'delivery_scope': 'Web application only', 'native_clients_required': False, 'full_acceptance': 'HOLD/pending-test'}
with output.open('x', encoding='utf-8') as file:
    file.write(json.dumps(value, indent=2) + '\n')
shutil.copyfile(output, mirror)
assert all(target.exists() for target in links)
print(json.dumps({key: value[key] for key in ['passed', 'functional_groups_passed', 'verified_screenshots', 'valid_local_links', 'browser_cleanup_confirmed', 'Windows_WebKit_media_playback_accepted', 'full_acceptance']}))
