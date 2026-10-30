"""Publish complete selected owned migration-page recordings after browser cleanup."""
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / 'docs/research/studio-implementation'
PRIVATE = ROOT / '.local/studio148'
MEDIA = ROOT / 'docs/media/studio-148'
FFMPEG = ROOT.parent / '生图生视API封装/app/.local/toolchains/ffmpeg-8.1/bin/ffmpeg.exe'
names = json.loads((PRIVATE / 'receipts.json').read_text(encoding='utf-8'))
cleanup = json.loads((EVIDENCE / names['process_cleanup']).read_text(encoding='utf-8'))
assert cleanup['passed'] and not cleanup['owned_browser_processes_remaining']
sources, clips = [], []
for key, stem in [('stage2', 'WebKit-mixed-originals-migration'), ('stage3', 'Firefox-mixed-originals-recovery')]:
    report_file = EVIDENCE / names[key]
    report = json.loads(report_file.read_text(encoding='utf-8'))
    assert report['passed'] and all(report['cleanup'].values()) and not report['resource_close_errors']
    source = (ROOT / report['private_recording']).resolve()
    assert source.is_file() and source.is_relative_to(PRIVATE.resolve())
    sources.append({'report': report_file.relative_to(ROOT).as_posix(), 'report_sha256': hashlib.sha256(report_file.read_bytes()).hexdigest(), 'selected_recording_sha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'full_selected_recording': True})
    mp4, gif = MEDIA / (stem + '.mp4'), MEDIA / (stem + '.gif')
    commands = [
        [str(FFMPEG), '-nostdin', '-v', 'error', '-n', '-i', str(source), '-an', '-vf', 'fps=15,scale=960:-2', '-c:v', 'libx264', '-crf', '23', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', '-metadata', 'title=Synthetic Web ZIP migration; not GPU generation or WebKit playback acceptance', str(mp4)],
        [str(FFMPEG), '-nostdin', '-v', 'error', '-n', '-i', str(mp4), '-filter_complex', 'fps=6,scale=720:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128[p];[b][p]paletteuse=dither=bayer:bayer_scale=3', '-loop', '0', str(gif)],
    ]
    for command in commands:
        result = subprocess.run(command, capture_output=True)
        assert result.returncode == 0, 'Preserve outputs and inspect the owned media encoding failure'
    for file in [mp4, gif]:
        decoded = subprocess.run([str(FFMPEG), '-nostdin', '-v', 'error', '-i', str(file), '-f', 'null', 'NUL'], capture_output=True)
        assert decoded.returncode == 0 and file.stat().st_size
        clips.append({'file': file.relative_to(ROOT).as_posix(), 'bytes': file.stat().st_size, 'sha256': hashlib.sha256(file.read_bytes()).hexdigest(), 'full_decode_passed': True})
value = {'passed': True, 'scope': 'Two complete selected owned pages: Windows WebKit ZIP migration with unsupported local playback, then Firefox public-origin restore. Synthetic local files, not GPU generation, all four stages in one film, actual Safari or WebKit decoder acceptance.', 'sources': sources, 'clips': clips, 'owned_browsers_closed_before_encoding': True, 'full_selected_recordings_included': True, 'actual_generation': False, 'static_release': 'canvas-20261030.146.1', 'new_product_release': False}
name = 'Web-migration-media-148.json'
with (EVIDENCE / name).open('x', encoding='utf-8') as output:
    output.write(json.dumps(value, indent=2) + '\n')
names['media'] = name
(PRIVATE / 'receipts.json').write_text(json.dumps(names, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'passed': True, 'published_clips': len(clips), 'full_decode_passed': True, 'new_product_release': False}))
