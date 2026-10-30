"""Inspect only owned test profiles and encode one complete public composer page."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE, PRIVATE, MEDIA = ROOT / 'docs/research/studio-implementation', ROOT / '.local/studio150', ROOT / 'docs/media/studio-150'
FFMPEG = ROOT.parent / '生图生视API封装/app/.local/toolchains/ffmpeg-8.1/bin/ffmpeg.exe'
names_file = PRIVATE / 'receipts.json'
names = json.loads(names_file.read_text(encoding='utf-8'))
reports = {key: json.loads((EVIDENCE / names[key]).read_text(encoding='utf-8')) for key in ['local_chromium','local_firefox','public_chromium','public_firefox','baseline','live']}
assert all(all(record['cleanup'].values()) and not record.get('resource_close_errors') for record in reports.values())
command = r'''$studioOwned = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^(chrome|firefox|Playwright|MiniBrowser|WebKit.*)\.exe$' -and ($_.CommandLine -like '*\.comfy-studio-native\studio150\*' -or $_.CommandLine -like '*\无限画布\.local\studio150\*') } | Select-Object ProcessId,Name); ConvertTo-Json -InputObject @($studioOwned) -Compress'''
process = subprocess.run([shutil.which('pwsh') or 'powershell', '-NoProfile', '-Command', command], capture_output=True, text=True, check=True)
remaining = json.loads(process.stdout); assert remaining == [], 'Inspect unfinished owned browsers; do not close user browsers'
cleanup = {'passed': True, 'owned_browser_processes_remaining': remaining, 'owned_profiles_closed': True, 'reported_pages_contexts_servers_closed': True, 'user_browsers_closed': False, 'virtualization_used': False}
cleanup_name = 'composer-process-cleanup-150.json'
with (EVIDENCE / cleanup_name).open('x', encoding='utf-8') as output: output.write(json.dumps(cleanup, indent=2) + '\n')
report = reports['public_chromium']; assert report['passed'] and report['public_static_site'] and len(report['checks']) == 23
source = (ROOT / report['full_selected_page_recording']).resolve(); assert source.is_file() and source.is_relative_to(PRIVATE.resolve())
mp4, gif = MEDIA / 'canvas-reference-creation-public.mp4', MEDIA / 'canvas-reference-creation-public.gif'
commands = [
    [str(FFMPEG), '-nostdin', '-v', 'error', '-n', '-i', str(source), '-an', '-vf', 'fps=15,scale=960:-2', '-c:v', 'libx264', '-crf', '23', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', '-metadata', 'title=Public canvas reference creation forms; synthetic API results, not GPU generation', str(mp4)],
    [str(FFMPEG), '-nostdin', '-v', 'error', '-n', '-i', str(mp4), '-filter_complex', 'fps=6,scale=720:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128[p];[b][p]paletteuse=dither=bayer:bayer_scale=3', '-loop', '0', str(gif)],
]
for command in commands:
    encoded = subprocess.run(command, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    assert encoded.returncode == 0, 'Keep the recorded attempt and inspect the encoding failure'
clips = []
for file in [mp4, gif]:
    decoded = subprocess.run([str(FFMPEG), '-nostdin', '-v', 'error', '-i', str(file), '-f', 'null', 'NUL'], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    assert decoded.returncode == 0 and file.stat().st_size
    clips.append({'file': file.relative_to(ROOT).as_posix(), 'bytes': file.stat().st_size, 'sha256': hashlib.sha256(file.read_bytes()).hexdigest(), 'full_decode_passed': True})
value = {'passed': True, 'scope': 'Complete selected public Chromium page demonstrating actual canvas reference selection, forms, validation, explicit upload retry, eleven published examples and result binding. API media are synthetic; not GPU, other browsers or full Web acceptance.', 'source_report': names['public_chromium'], 'source_report_sha256': hashlib.sha256((EVIDENCE / names['public_chromium']).read_bytes()).hexdigest(), 'selected_recording_sha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'clips': clips, 'full_selected_recordings_included': True, 'owned_browsers_closed_before_encoding': True, 'actual_generation': False, 'static_release': 'canvas-20261031.150.1'}
media_name = 'composer-media-150.json'
with (EVIDENCE / media_name).open('x', encoding='utf-8') as output: output.write(json.dumps(value, indent=2) + '\n')
names.update({'process_cleanup': cleanup_name, 'media': media_name}); names_file.write_text(json.dumps(names, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'passed': True, 'clips': len(clips), 'full_decode_passed': True, 'owned_browser_processes_remaining': remaining}))
