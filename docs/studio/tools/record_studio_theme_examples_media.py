"""Encode the complete owned public example session after all owned browsers close."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
report_path = (ROOT / sys.argv[1]).resolve()
assert report_path.is_relative_to(ROOT / 'docs/research/studio-implementation')
report = json.loads(report_path.read_text(encoding='utf-8'))
assert report['passed'] and report['public_static_site'] and all(report['cleanup'].values())
assert len(report['submissions']) == 8 and not report.get('resource_close_errors')
command = r'''$studio151Owned = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^(chrome|firefox|Playwright|MiniBrowser|WebKit.*)\.exe$' -and ($_.CommandLine -like '*\.comfy-studio-native\studio151\*' -or $_.CommandLine -like '*\无限画布\.local\studio151\*') } | Select-Object ProcessId, ParentProcessId)
ConvertTo-Json -InputObject $studio151Owned -Compress'''
owned = json.loads(subprocess.check_output(['powershell.exe', '-NoProfile', '-Command', command], text=True, encoding='utf-8-sig').strip() or '[]')
assert owned == [], 'Close the exact owned sessions before encoding; do not affect user browsers'
source = (ROOT / report['full_selected_page_recording']).resolve()
assert source.is_relative_to(ROOT / '.local/studio151') and source.is_file()
ffmpeg = ROOT.parent / '生图生视API封装/app/.local/toolchains/ffmpeg-8.1/bin/ffmpeg.exe'
media = ROOT / 'docs/media/studio-151'
media.mkdir(parents=True, exist_ok=True)
clips = []
for extension in ['mp4', 'gif']:
    target = media / ('themes-and-examples-public.' + extension)
    assert not target.exists()
    arguments = [str(ffmpeg), '-hide_banner', '-loglevel', 'error', '-nostdin', '-threads', '2', '-i', str(source)]
    if extension == 'mp4':
        arguments += ['-vf', 'fps=2,scale=720:-2:flags=lanczos', '-c:v', 'libx264', '-threads', '2', '-preset', 'veryfast', '-crf', '23', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', '-an']
    else:
        arguments += ['-filter_complex_threads', '1', '-filter_complex', '[0:v]fps=2,scale=720:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128[p];[b][p]paletteuse=dither=bayer:bayer_scale=5', '-an']
    subprocess.run(arguments + [str(target)], check=True, stdout=subprocess.DEVNULL)
    subprocess.run([str(ffmpeg), '-hide_banner', '-loglevel', 'error', '-nostdin', '-xerror', '-threads', '2', '-i', str(target), '-f', 'null', '-'], check=True, stdout=subprocess.DEVNULL)
    clips.append({'file': target.relative_to(ROOT).as_posix(), 'bytes': target.stat().st_size, 'sha256': hashlib.sha256(target.read_bytes()).hexdigest(), 'complete_decode_passed': True})
value = {'passed': True, 'browser_report': report_path.relative_to(ROOT).as_posix(), 'browser_report_sha256': hashlib.sha256(report_path.read_bytes()).hexdigest(), 'complete_source_recording_sha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'clips': clips, 'full_selected_page_included': True, 'owned_browsers_remaining': 0, 'native_cpu_encoding': True, 'virtualization_used': False, 'actual_generation': False, 'scope': 'Full public Web theme and example UI session, using synthetic API/media. No trimmed/reconstructed screen sequence or real GPU result claim.'}
with (ROOT / 'docs/research/studio-implementation/theme-examples-media-151.json').open('x', encoding='utf-8') as output:
    output.write(json.dumps(value, indent=2) + '\n')
print(json.dumps({'passed': True, 'clips': clips, 'owned_browsers_remaining': 0}))
