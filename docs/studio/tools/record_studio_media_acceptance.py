"""Bind three actual browser media-action receipts to one frozen Web build."""
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from package_independent_canvas import source_digest

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / 'docs/research/studio-implementation'
FRONT = ROOT / 'infinite-canvas'
public = '--public' in sys.argv
arguments = [argument for argument in sys.argv[1:] if argument != '--public']
assert len(arguments) == 4
paths = [(ROOT / argument).resolve() for argument in arguments]
assert all(file.parent == EVIDENCE.resolve() and file.is_file() for file in paths)
build, *records = [json.loads(file.read_text(encoding='utf-8')) for file in paths]
assert build['passed'] and build['source_before'] == build['source_after'] == source_digest()
revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=FRONT, text=True).strip()
code_record = ROOT / '.local/studio149/code-revision.json'
if public:
    revision = json.loads(code_record.read_text(encoding='utf-8'))['code_revision']
assert subprocess.run(['git', 'diff', '--quiet', revision, '--', 'web', 'VERSION', 'CHANGELOG.md'], cwd=FRONT).returncode == 0
engines = ['chromium', 'firefox', 'webkit']
for engine, file, record in zip(engines, paths[1:], records, strict=True):
    assert record['passed'] and record['browser_engine'] == engine and record['public_static_site'] == public and not record['baseline_reproduction']
    assert all(record['cleanup'].values()) and not record['resource_close_errors']
    assert record['static_bytes_unchanged'] and not record['page_errors'] and not record['unexpected_requests']
    assert record['production_api_requests'] == record['generation_posts'] == 0 and not record['product_timeouts_changed']
    assert record['build_report'] == paths[0].relative_to(ROOT).as_posix()
    assert len(record['checks']) == 12 and len(record['downloads']) == 13 and record['download_events'] == 13
    assert len(record['originals_before']) == 8 and len(record['originals_after']) == 10
    assert all(item['actual_extension'] == '.' + item['expected_extension'] for item in record['downloads'])
    assert len(record['screenshots']) == 3
    assert record['native_browser']['playwright_version'] == '1.63.0'
    assert record['native_browser']['persistent_owned_profiles'] and record['native_browser']['ascii_runtime_verified']
    if engine == 'webkit': assert record['valid_fixture_playback_accepted'] is False
    else: assert record['valid_fixture_playback_accepted'].startswith('loaded and decoded only')
    for screenshot in record['screenshots']:
        raw = (ROOT / screenshot['file']).read_bytes()
        assert len(raw) == screenshot['bytes'] and hashlib.sha256(raw).hexdigest() == screenshot['sha256']
value = {'passed': True, 'at': datetime.now(UTC).isoformat(), 'source_revision': revision, 'source_sha256': build['source_after'],
         'build_report': paths[0].relative_to(ROOT).as_posix(), 'public_static_site': public, 'engines': engines,
         'functional_groups_passed': sum(len(record['checks']) for record in records), 'actual_downloads_verified': 39,
         'screenshots_verified': 9, 'receipts': {engine: file.name for engine, file in zip(engines, paths[1:], strict=True)},
         'receipt_sha256': {file.name: hashlib.sha256(file.read_bytes()).hexdigest() for file in paths},
         'Windows_WebKit_playback_accepted': False, 'Windows_WebKit_Cookie_isolation_accepted': False, 'actual_Safari_accepted': False,
         'original_bytes_verified': True, 'browser_cleanup_confirmed': True, 'production_api_requests': 0, 'generation_posts': 0,
         'scope': 'Three native Windows engines, actual file chooser/download and native decoder errors on frozen Web bytes; small synthetic fixtures, not full playback, all codecs, actual Safari, keyboard/touch/background or GPU acceptance'}
name = 'media-actions-' + ('public-' if public else '') + 'acceptance-149.json'
with (EVIDENCE / name).open('x', encoding='utf-8') as output: output.write(json.dumps(value, indent=2) + '\n')
if not public:
    with code_record.open('x', encoding='utf-8') as output: output.write(json.dumps({'code_revision': revision, 'documentation_base': 'd5b391a9276d224737f25fb90bd185e2a301710b', 'static_release': 'canvas-20261031.149.1', 'recovery_release': 'canvas-20261031.149.0', 'new_product_release': True}, indent=2) + '\n')
print(json.dumps({'passed': True, 'report': name, 'groups': value['functional_groups_passed'], 'actual_downloads': 39}))
