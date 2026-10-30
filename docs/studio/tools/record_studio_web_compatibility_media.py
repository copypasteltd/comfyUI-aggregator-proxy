"""Encode complete selected owned browser pages; no application/API mutation."""
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / 'docs/research/studio-implementation'
PRIVATE = ROOT / '.local/studio147'
MEDIA = ROOT / 'docs/media/studio-147'
FFMPEG = ROOT.parent / '生图生视API封装/app/.local/toolchains/ffmpeg-8.1/bin/ffmpeg.exe'
names = json.loads((PRIVATE / 'receipts.json').read_text(encoding='utf-8'))
sources, clips = [], []
for key, field, stem in [('archive_firefox', 'private_videos', 'Firefox-Web-project-restore'), ('cleanup_webkit', 'private_recordings', 'WebKit-Web-local-cleanup')]:
    source = EVIDENCE / names[key]
    report = json.loads(source.read_text(encoding='utf-8'))
    assert report['passed'] and all(report['cleanup'].values())
    assert not report.get('unexpected_requests') and not report.get('page_errors')
    video = (ROOT / report[field][0]).resolve()
    assert video.is_relative_to(PRIVATE.resolve()) and video.is_file()
    sources.append({'browser_report': source.relative_to(ROOT).as_posix(), 'sha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'selected_page_index': 0, 'selected_recording_sha256': hashlib.sha256(video.read_bytes()).hexdigest()})
    mp4, gif = MEDIA / (stem + '.mp4'), MEDIA / (stem + '.gif')
    commands = [
        [str(FFMPEG), '-v', 'error', '-n', '-i', str(video), '-an', '-vf', 'fps=15,scale=960:-2', '-c:v', 'libx264', '-crf', '23', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', '-metadata', 'title=Synthetic Web compatibility; no API or GPU generation', str(mp4)],
        [str(FFMPEG), '-v', 'error', '-n', '-i', str(mp4), '-filter_complex', 'fps=6,scale=720:-1:flags=lanczos,split[s0][s1];[s0]palettegen=max_colors=128[p];[s1][p]paletteuse=dither=bayer:bayer_scale=3', '-loop', '0', str(gif)],
    ]
    for command in commands:
        encoded = subprocess.run(command, capture_output=True)
        assert encoded.returncode == 0, 'Preserve outputs and inspect the owned encoding failure'
    for file in [mp4, gif]:
        decoded = subprocess.run([str(FFMPEG), '-v', 'error', '-i', str(file), '-f', 'null', 'NUL'], capture_output=True)
        assert decoded.returncode == 0 and file.stat().st_size > 0
        clips.append({'file': file.relative_to(ROOT).as_posix(), 'bytes': file.stat().st_size, 'sha256': hashlib.sha256(file.read_bytes()).hexdigest(), 'full_decode_passed': True})
value = {'passed': True, 'scope': 'Complete selected owned public Web page recordings: Firefox synthetic project ZIP restore and Windows WebKit synthetic controlled-original cleanup. These are selected pages, not the complete multi-page suite or GPU generation; WebKit Cookie isolation is unaccepted separately.', 'sources': sources, 'clips': clips, 'owned_browsers_closed_before_encoding': True, 'full_selected_recordings_included': True, 'actual_generation': False, 'static_release': 'canvas-20261030.146.1', 'new_product_release': False}
with (EVIDENCE / 'Web-compatibility-media-147.json').open('x', encoding='utf-8') as output:
    output.write(json.dumps(value, indent=2) + '\n')
names['media'] = 'Web-compatibility-media-147.json'
(PRIVATE / 'receipts.json').write_text(json.dumps(names, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'passed': True, 'clips': len(clips), 'full_decode_passed': True, 'actual_generation': False}))
