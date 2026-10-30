"""Verify actual PNG fixture bytes and that no owned Web test browser remains."""
import base64
import hashlib
import io
import json
import shutil
import subprocess
from pathlib import Path

from PIL import Image, __version__ as pillow_version

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / 'docs/research/studio-implementation'
old = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j2ioAAAAASUVORK5CYII=')
try:
    Image.open(io.BytesIO(old)).verify()
    raise AssertionError('The historical fixture must reproduce its checksum error')
except SyntaxError as error:
    assert 'bad header checksum' in str(error)
    old_error = str(error)
fixtures = []
for name in ['web-original.png', 'web-reference.png']:
    file = ROOT / 'docs/media/studio-147/fixtures' / name
    data = file.read_bytes()
    Image.open(io.BytesIO(data)).verify()
    with Image.open(io.BytesIO(data)) as image:
        image.load()
        assert image.mode == 'RGB' and image.size == (1, 1)
    encoded = base64.b64encode(data).decode()
    assert encoded in (ROOT / 'docs/tools/verify_studio_archive_import.mjs').read_text(encoding='utf-8')
    if name == 'web-original.png':
        assert encoded in (ROOT / 'docs/tools/verify_studio_managed_cleanup_ui.mjs').read_text(encoding='utf-8')
    fixtures.append({'file': file.relative_to(ROOT).as_posix(), 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(), 'mode': 'RGB', 'size': [1, 1], 'crc_verified': True, 'full_decode_passed': True})
command = r'''$studioOwned = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^(chrome|firefox|Playwright|MiniBrowser|WebKit.*)\.exe$' -and ($_.CommandLine -like '*\.comfy-studio-native\studio147\*' -or $_.CommandLine -like '*\无限画布\.local\studio147\*') } | Select-Object ProcessId,Name); ConvertTo-Json -InputObject @($studioOwned) -Compress'''
result = subprocess.run([shutil.which('pwsh') or 'powershell', '-NoProfile', '-Command', command], capture_output=True, text=True, check=True)
remaining = json.loads(result.stdout)
assert remaining == [], 'Keep unfinished owned browsers explicit; do not close user browsers'
value = {'passed': True, 'scope': 'Independent PNG integrity/decode and exact owned-profile process inspection, not a product source/build/API change.', 'pillow_version': pillow_version, 'old_fixture': {'bytes': len(old), 'sha256': hashlib.sha256(old).hexdigest(), 'verification_passed': False, 'error': old_error}, 'fixtures': fixtures, 'owned_Web_test_browser_processes_remaining': remaining, 'user_browsers_closed': False, 'virtualization_used': False}
with (EVIDENCE / 'Web-fixtures-147.json').open('x', encoding='utf-8') as output:
    output.write(json.dumps(value, indent=2) + '\n')
names_file = ROOT / '.local/studio147/receipts.json'
names = json.loads(names_file.read_text(encoding='utf-8'))
names['fixture_validation'] = 'Web-fixtures-147.json'
names_file.write_text(json.dumps(names, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'passed': True, 'valid_fixtures': len(fixtures), 'owned_Web_browsers_remaining': 0}))
