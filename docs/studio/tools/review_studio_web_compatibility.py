"""Review evidence-only Web continuation and mirror its own final receipt."""
import hashlib
import json
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote

from package_independent_canvas import source_digest

ROOT = Path(__file__).resolve().parents[2]
FRONT = ROOT / 'infinite-canvas'
PRIVATE = ROOT / '.local/studio147'
EVIDENCE = ROOT / 'docs/research/studio-implementation'
TARGET = FRONT / 'docs/studio'
names = json.loads((PRIVATE / 'receipts.json').read_text(encoding='utf-8'))
records = {key: json.loads((EVIDENCE / name).read_text(encoding='utf-8')) for key, name in names.items()}
assert all(value['passed'] for value in records.values())
revision = json.loads((PRIVATE / 'code-revision.json').read_text(encoding='utf-8'))
assert revision['new_product_release'] is False
assert revision['code_revision'] == 'db56cae3634da0361074e84024ccd5b428d189fa'
assert revision['static_release'] == records['delivery_baseline']['release_id'] == 'canvas-20261030.146.1'
build = records['build']
assert build['source_before'] == build['source_after'] == source_digest()
assert build['unit_tests'] == 203 and build['unit_failures'] == 0
assert all(item['exit_code'] == 0 for item in build['stages'])
assert subprocess.run(['git', 'diff', '--quiet', revision['code_revision'], '--', 'web', 'VERSION', 'CHANGELOG.md'], cwd=FRONT).returncode == 0
groups = {'refresh_firefox': 3, 'refresh_webkit': 3, 'cleanup_firefox': 10, 'cleanup_webkit': 10, 'archive_firefox': 14, 'archive_webkit': 14, 'catalog_firefox': 11}
screenshots = 0
for key, count in groups.items():
    value = records[key]
    assert len(value['checks']) == count and value['static_bytes_unchanged']
    assert value['build_report'] == 'docs/research/studio-implementation/' + names['build']
    assert all(value['cleanup'].values()) and not value.get('resource_close_errors')
    assert not value.get('page_errors') and not value.get('unexpected_requests')
    assert value['native_browser']['playwright_version'] == '1.63.0'
    assert value['native_browser']['persistent_owned_profiles'] and value['native_browser']['ascii_runtime_verified']
    assert value['native_browser']['product_timeouts_changed'] is False
    engine = key.rsplit('_', 1)[1]
    assert value['browser_engine'] == engine
    assert value['native_browser']['native_revision'] == {'firefox': '1543', 'webkit': '2359'}[engine]
    if key != 'catalog_firefox': assert value['public_static_site']
    else:
        assert value['origins']['native_non_loopback']
        gets = [item for item in value['api_requests'] if item['method'] == 'GET']
        assert len(gets) >= 6 and all(item['bearer_present'] and not item['cookie_present'] and item['origin_matches_canvas'] for item in gets)
    if key.startswith('archive_'): assert len(value['rejected_archives']) == 12 and value['suite'] == '147'
    for item in value['screenshots']:
        file = ROOT / item['file']
        assert hashlib.sha256(file.read_bytes()).hexdigest() == item['sha256']
        screenshots += 1
assert sum(groups.values()) == 65
for engine in ['chromium', 'firefox', 'webkit']:
    value = records['credentials_' + engine]
    assert len(value['cases']) == 7 and value['include_positive_controls']
    assert value['omit_cookies_absent'] == (engine != 'webkit')
    assert value['product_code_loaded'] is False and value['production_api_requests'] == 0
    assert all(value['cleanup'].values())
    assert all(item['cookie_synthetic_only'] for item in value['received'])
fixtures = records['fixture_validation']
assert fixtures['owned_Web_test_browser_processes_remaining'] == []
assert fixtures['old_fixture']['verification_passed'] is False
for item in fixtures['fixtures']:
    data = (ROOT / item['file']).read_bytes()
    assert len(data) == item['bytes'] == 69 and hashlib.sha256(data).hexdigest() == item['sha256']
    assert item['crc_verified'] and item['full_decode_passed']
media = records['media']
assert media['owned_browsers_closed_before_encoding'] and media['full_selected_recordings_included']
assert not media['actual_generation'] and not media['new_product_release']
for item in media['clips']:
    file = ROOT / item['file']
    assert len(file.read_bytes()) == item['bytes'] and hashlib.sha256(file.read_bytes()).hexdigest() == item['sha256'] and item['full_decode_passed']
assert len(media['clips']) == 4
host = records['host']
for field in ['api_pointer_unchanged', 'api_configuration_unchanged', 'api_services_unchanged', 'static_pointer_unchanged', 'static_configuration_unchanged', 'static_service_unchanged', 'ssh_session_closed']:
    assert host[field]
assert host['service_restarts'] == 0 and not host['api_code_written']
assert host['compared_static_pointer'].endswith('/canvas-20261030.146.1')
assert not records['api_boundary']['api_workspaces_written']
failures = ['cleanup-refresh-public-firefox-2026-10-30T15-07-56-788Z.json', 'archive-import-public-firefox-2026-10-30T15-11-29-918Z.json', 'independent-browser-webkit-2026-10-30T15-11-49-620Z.json']
failures += [file.name for file in EVIDENCE.glob('Web-engine-diagnostic-*.json') if not json.loads(file.read_text(encoding='utf-8'))['passed']]
for name in failures:
    value = json.loads((EVIDENCE / name).read_text(encoding='utf-8'))
    assert value['passed'] is False and all(value['cleanup'].values())
    assert (TARGET / 'research/studio-implementation' / name).read_bytes() == (EVIDENCE / name).read_bytes()
webkit_catalog = json.loads((EVIDENCE / 'independent-browser-webkit-2026-10-30T15-11-49-620Z.json').read_text(encoding='utf-8'))
assert len(webkit_catalog['checks']) == 10
assert any(item['cookie_present'] for item in webkit_catalog['api_requests'] if item['method'] == 'GET')
assert all(webkit_catalog['cleanup'].values())
for name in names.values():
    assert (TARGET / 'research/studio-implementation' / name).read_bytes() == (EVIDENCE / name).read_bytes()

progress = (TARGET / '10-开发进度与交付记录.md').read_text(encoding='utf-8')
backlog = {f'F-{n:02}' for n in range(1, 47)}
assert set(re.findall(r'^\| (F-\d+) \|', progress, re.M)) == backlog
assert all(f'| I-{number} |' in progress for number in range(142, 150))
for file in [FRONT / 'README.md', FRONT / 'README.en.md', TARGET / '32-Web浏览器兼容性与验证边界.md']:
    text = file.read_text(encoding='utf-8')
    assert all(value in text for value in ['146.1', 'WebKit', 'Firefox', '38', '27', 'HOLD'])
for suffix in ['.mdx', '.zh-CN.mdx']:
    for kind in ['todo', 'pending-test']:
        text = (FRONT / f'docs/content/docs/progress/{kind}{suffix}').read_text(encoding='utf-8')
        assert all(value in text for value in ['147', '146.1', 'WebKit', 'Firefox'])
tools = ['studio_web_browser.mjs', 'diagnose_studio_web_engines.mjs', 'diagnose_studio_web_credentials.mjs', 'verify_studio_cleanup_refresh_race.mjs', 'verify_studio_managed_cleanup_ui.mjs', 'verify_studio_archive_import.mjs', 'verify_independent_canvas.mjs', 'verify_independent_host_unchanged.py', 'record_studio_web_compatibility_media.py', 'record_studio_web_compatibility_fixtures.py', 'review_studio_web_compatibility.py']
tool_sha = {}
for name in tools:
    source = ROOT / 'docs/tools' / name
    assert source.read_bytes() == (TARGET / 'tools' / name).read_bytes()
    tool_sha[name] = hashlib.sha256(source.read_bytes()).hexdigest()
markdown = [FRONT / 'README.md', FRONT / 'README.en.md', *TARGET.rglob('*.md')]
output = EVIDENCE / 'independent-review-147.json'
mirror_output = TARGET / 'research/studio-implementation' / output.name
links = []
for file in markdown:
    for raw in re.findall(r'!?\[[^\]\n]*\]\(([^\s\n)]+)\)', file.read_text(encoding='utf-8')):
        if raw.startswith(('http:', 'https:', '#', 'data:', 'mailto:', 'app:')): continue
        target = (file.parent / unquote(raw).split('#')[0]).resolve()
        assert target.is_relative_to(FRONT.resolve()) and (target.exists() or target == mirror_output.resolve()), f'Invalid doc link: {file.name}: {raw}'
        links.append(target)
secrets = [json.loads((ROOT / '.local' / directory / name).read_text(encoding='utf-8'))['secret'].encode() for directory, name in [('studio138', 'read-only-key.json'), ('studio139', 'generation-key.json'), ('studio140', 'generation-key.json')]]
for file in [FRONT / 'README.md', FRONT / 'README.en.md', *TARGET.rglob('*')]:
    if file.is_file() and file.suffix.lower() in {'.md', '.json', '.mdx', '.py', '.mjs'}:
        assert all(secret not in file.read_bytes() for secret in secrets), 'Known private Key in public documentation'
value = {'passed': True, 'at': datetime.now(UTC).isoformat(), 'scope': 'Evidence147 on frozen146.1, Web only:65 new passed functional groups, exact native browser/profile cleanup, valid fixtures, three-engine minimal Cookie contrast and honest Windows WebKit auth failure. Not full Safari/OS/media/background/quota/fault acceptance.', 'code_revision': revision['code_revision'], 'documentation_base': revision['documentation_base'], 'static_release': revision['static_release'], 'new_product_release': False, 'source_sha256': source_digest(), 'frozen_203_test_build_reused_not_rerun': True, 'receipts': names, 'receipt_sha256': {key: hashlib.sha256((EVIDENCE / name).read_bytes()).hexdigest() for key, name in names.items()}, 'functional_groups_passed': sum(groups.values()), 'functional_groups': groups, 'public_markdown_files': len(markdown), 'valid_local_links': len(links), 'verified_screenshots': screenshots, 'test_tool_sha256': tool_sha, 'private_owned_keys_absent': True, 'api_workspaces_written': False, 'api_services_restarted': False, 'static_service_restarted': False, 'static_pointer_changed': False, 'production_business_api_requests': 0, 'actual_generation_posts': 0, 'delivery_scope': 'Web application only', 'Windows_WebKit_Cookie_isolation_accepted': False, 'actual_Safari_accepted': False, 'minimal_diagnostic_not_product_acceptance': True, 'full_backlog_ids_preserved': sorted(backlog), 'browser_cleanup_confirmed': True, 'earlier_failures_preserved': failures, 'native_clients_required': False, 'full_acceptance': 'HOLD/pending-test'}
with output.open('x', encoding='utf-8') as file:
    file.write(json.dumps(value, indent=2) + '\n')
shutil.copyfile(output, mirror_output)
assert all(target.exists() for target in links)
print(json.dumps({key: value[key] for key in ['passed', 'functional_groups_passed', 'verified_screenshots', 'valid_local_links', 'browser_cleanup_confirmed', 'Windows_WebKit_Cookie_isolation_accepted', 'full_acceptance']}))
