"""Review the actual frozen composer release, declared failures, public evidence and unchanged API."""
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
from urllib.parse import unquote

from PIL import Image
from package_independent_canvas import source_digest

ROOT = Path(__file__).resolve().parents[2]
FRONT, PRIVATE = ROOT / 'infinite-canvas', ROOT / '.local/studio150'
EVIDENCE, TARGET = ROOT / 'docs/research/studio-implementation', FRONT / 'docs/studio'
names = json.loads((PRIVATE / 'receipts.json').read_text(encoding='utf-8'))
records = {key: json.loads((EVIDENCE / name).read_text(encoding='utf-8')) for key, name in names.items()}
required = ['build', 'dual_origin', 'local_chromium', 'local_firefox', 'public_chromium', 'baseline', 'package', 'recovery', 'prepared', 'promoted', 'delivery', 'api_boundary_before', 'api_boundary_after', 'release_guard', 'live_terminal', 'live_key_cleanup', 'process_cleanup', 'media']
assert all(records[key]['passed'] for key in required)
code = json.loads((PRIVATE / 'code-revision.json').read_text(encoding='utf-8'))
revision = code['code_revision']
build, package, delivery = records['build'], records['package'], records['delivery']
assert code['static_release'] == 'canvas-20261031.150.1' and code['recovery_release'] == 'canvas-20261031.150.0'
assert package['source_revision'] == revision and build['source_after'] == package['source_sha256'] == source_digest()
assert build['source_before'] == build['copied_source_after'] == build['source_after']
assert build['unit_tests'] == 207 and build['unit_failures'] == 0 and all(stage['exit_code'] == 0 for stage in build['stages'])
assert subprocess.run(['git', 'diff', '--quiet', revision, '--', 'web', 'VERSION', 'CHANGELOG.md'], cwd=FRONT).returncode == 0
sha = lambda value: hashlib.sha256(value).hexdigest()
assert len(records['dual_origin']['checks']) == 11 and all(records['dual_origin']['cleanup'].values())
groups, screenshots = 0, 0
for key, engine, public in [('local_chromium','chromium',False),('local_firefox','firefox',False),('public_chromium','chromium',True)]:
    record = records[key]
    assert record['browser_engine'] == engine and record['public_static_site'] == public and not record['baseline_reproduction']
    assert record['build_report'] == 'docs/research/studio-implementation/' + names['build']
    assert record['static_bytes_unchanged'] and all(record['cleanup'].values()) and not record['resource_close_errors']
    assert record['server_errors'] == record['page_errors'] == record['forbidden_requests'] == []
    assert len(record['checks']) == 23 and len(record['submissions']) == 12
    assert len({item['body']['model'] for item in record['submissions']}) == 11
    for item in record['api_requests']:
        if item['method'] != 'OPTIONS': assert item['bearer_present'] and not item['cookie_present']
    for item in record['screenshots']:
        data = (ROOT / item['file']).read_bytes()
        assert len(data) == item['bytes'] and sha(data) == item['sha256']
        Image.open(io.BytesIO(data)).verify(); screenshots += 1
    groups += len(record['checks'])
assert groups == 69
for key in ['public_firefox', 'failed_public_firefox_retry']:
    record = records[key]
    assert not record['passed'] and record['public_static_site'] and record['browser_engine'] == 'firefox'
    assert all(record['cleanup'].values()) and not record['resource_close_errors']
assert len(records['public_firefox']['checks']) == 17 and len(records['failed_public_firefox_retry']['checks']) == 0
assert records['baseline']['baseline_reproduction'] and not records['baseline']['submissions']
assert records['live']['generation_posts'] == 1 and records['live']['final_job_status'] == 'failed' and not records['live']['passed']
assert all(records['live']['cleanup'].values()) and not records['live']['forbidden_requests']
assert records['live_terminal']['job_id'] == records['live']['job_id'] and records['live_terminal']['error_code'] == 'preparation_transport_failure'
assert not records['live_terminal']['generation_succeeded'] and not records['live_terminal']['replacement_generation_submitted']
terminal_bytes = (PRIVATE / 'live-original-terminal.json').read_bytes()
terminal = json.loads(terminal_bytes)
assert terminal['id'] == records['live']['job_id'] and terminal['status'] == 'failed' and terminal['error']['code'] == records['live_terminal']['error_code']
assert records['live_key_cleanup']['owned_temporary_key_disabled'] and records['live_key_cleanup']['revoked_business_access'] == 401
assert records['process_cleanup']['owned_browser_processes_remaining'] == []
assert records['prepared']['api_unchanged'] and records['prepared']['recovery_application_bytes_identical_to_source']
assert records['promoted']['from'] == 'canvas-20261031.149.1' and records['promoted']['to'] == code['static_release']
assert records['prepared']['before']['api'] == records['promoted']['before']['api'] == records['promoted']['after']['api']
assert not records['promoted']['configuration_written'] and not records['promoted']['static_service_restarted']
assert delivery['release_id'] == code['static_release'] and delivery['source_revision'] == revision
assert delivery['static_files_verified'] == package['static_files'] and len(delivery['routes']) == 20
assert delivery['api_services_unchanged'] and delivery['static_service_unchanged']
assert records['api_boundary_before']['api_workspaces'] == records['api_boundary_after']['api_workspaces']
with tarfile.open(ROOT / '.local/independent-canvas-package' / code['static_release'] / (code['static_release'] + '.tar.gz')) as archive:
    manifest = json.load(archive.extractfile('canvas-deployment.json'))
assert manifest['storage_contract'] == records['prepared']['storage_contract'] and manifest['unchanged_storage_regression_not_rerun']
assert manifest['composer_report_sha256'] == sha((EVIDENCE / names['local_chromium']).read_bytes())
for item in records['media']['clips']:
    data = (ROOT / item['file']).read_bytes()
    assert len(data) == item['bytes'] and sha(data) == item['sha256'] and item['full_decode_passed']
assert records['media']['full_selected_recordings_included'] and not records['media']['actual_generation']
for name in names.values(): assert (EVIDENCE / name).read_bytes() == (TARGET / 'research/studio-implementation' / name).read_bytes()
backlog = {f'F-{number:02}' for number in range(1, 47)}
progress = (TARGET / '10-开发进度与交付记录.md').read_text(encoding='utf-8')
assert set(re.findall(r'^\| (F-\d+) \|', progress, re.M)) == backlog
assert all(f'| I-{number} |' in progress for number in range(165, 173))
for file in [FRONT / 'README.md', FRONT / 'README.en.md', TARGET / '35-画布参考创作与生成表单.md']:
    text = file.read_text(encoding='utf-8')
    assert all(word in text for word in ['150.1', '207', 'HOLD', 'preparation_transport_failure'])
for suffix in ['.mdx', '.zh-CN.mdx']:
    for kind in ['todo', 'pending-test']:
        text = (FRONT / f'docs/content/docs/progress/{kind}{suffix}').read_text(encoding='utf-8')
        assert '{/* independent150:start */}' in text and '150.1' in text
tools = ['verify_studio_composer.mjs', 'verify_studio_composer_live.mjs', 'studio_composer_live.py', 'studio_web_browser.mjs', 'studio_static_release.py', 'studio_static_release_host.py', 'studio_safe_release_host.py', 'studio_release_guard.py', 'verify_studio_media_release.py', 'package_independent_canvas.py', 'package_studio_v5_recovery.py', 'prepare_independent_docs.py', 'record_studio_composer_media.py', 'finalize_studio_composer_docs.py', 'review_studio_composer_delivery.py']
tool_sha = {}
for name in tools:
    data = (ROOT / 'docs/tools' / name).read_bytes()
    assert data == (TARGET / 'tools' / name).read_bytes(); tool_sha[name] = sha(data)
output = EVIDENCE / 'independent-review-150.json'; mirror = TARGET / 'research/studio-implementation' / output.name
links = []
for file in [FRONT / 'README.md', FRONT / 'README.en.md', *TARGET.rglob('*.md')]:
    for raw in re.findall(r'!?\[[^\]\n]*\]\(([^\s\n)]+)\)', file.read_text(encoding='utf-8')):
        if raw.startswith(('http:', 'https:', '#', 'data:', 'mailto:', 'app:')): continue
        target = (file.parent / unquote(raw).split('#')[0]).resolve()
        assert target.is_relative_to(FRONT.resolve()) and (target.exists() or target == mirror.resolve()), f'Invalid local link: {file.name}: {raw}'
        links.append(target)
secrets = [json.loads((ROOT / '.local' / directory / name).read_text(encoding='utf-8'))['secret'].encode() for directory, name in [('studio138','read-only-key.json'),('studio139','generation-key.json'),('studio140','generation-key.json'),('studio150','live-business-key.json')]]
for file in [FRONT / 'README.md', FRONT / 'README.en.md', *TARGET.rglob('*')]:
    if file.is_file() and file.suffix.lower() in {'.md','.json','.mdx','.py','.mjs'}: assert all(secret not in file.read_bytes() for secret in secrets), 'Private Key in public file'
value = {'passed': True, **code, 'source_sha256': source_digest(), 'receipts': names, 'receipt_sha256': {key: sha((EVIDENCE / name).read_bytes()) for key, name in names.items()}, 'native_and_public_composer_groups': groups, 'verified_screenshots': screenshots, 'valid_local_links': len(links), 'reviewed_current_tool_sha256': tool_sha, 'browser_cleanup_confirmed': True, 'private_owned_keys_absent': True, 'actual_generation_posts': 1, 'actual_generation_succeeded': False, 'actual_generation_error_code': 'preparation_transport_failure', 'temporary_key_disabled': True, 'api_workspaces_written': False, 'api_services_restarted': False, 'static_service_restarted': False, 'compatible_recovery_prepared': True, '150_actual_rollback_exercised': False, 'unchanged_storage_regression_not_rerun': True, 'delivery_scope': 'Web only', 'full_backlog_ids_preserved': sorted(backlog), 'full_acceptance': 'HOLD/pending-test', 'scope': 'Frozen native and public canvas forms, real reference inputs/validation/retry/source binding and original-result fixture integrity for eleven published examples. The one real video job failed at original-service preparation and is explicitly unaccepted; not GPU success, Safari, full Web or fleet acceptance.'}
value.update({'public_Firefox_accepted': False, 'public_Firefox_incomplete_runs_preserved': [names['public_firefox'], names['failed_public_firefox_retry']], 'partial_Firefox_groups_not_counted_as_complete': True, 'private_terminal_response_sha256': sha(terminal_bytes), 'scope': 'Frozen eleven-example forms and original-result fixture integrity: local/public Chromium plus local Firefox,69 complete groups. Two public Firefox observations are explicitly unaccepted. The one real video failed at original-service preparation. Not GPU success, complete Firefox/Safari, full Web or fleet acceptance.'})
with output.open('x', encoding='utf-8') as file: file.write(json.dumps(value, indent=2) + '\n')
shutil.copyfile(output, mirror); assert all(target.exists() for target in links)
print(json.dumps({key: value[key] for key in ['passed','native_and_public_composer_groups','verified_screenshots','actual_generation_succeeded','full_acceptance']}))
