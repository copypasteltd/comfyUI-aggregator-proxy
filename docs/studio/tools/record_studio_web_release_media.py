"""Encode complete selected owned Web-page recordings after all browsers close."""
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / 'docs/research/studio-implementation'
PRIVATE = ROOT / '.local/studio146'
MEDIA = ROOT / 'docs/media/studio-146'
FFMPEG = ROOT.parent / '生图生视API封装/app/.local/toolchains/ffmpeg-8.1/bin/ffmpeg.exe'
names = json.loads((PRIVATE / 'receipts.json').read_text(encoding='utf-8'))
clips, sources = [], []
for key, index, stem in [('cleanup_public', 0, 'synthetic-Web-local-cleanup'), ('rollback_web', 4, 'synthetic-Web-compatible-recovery')]:
    source = EVIDENCE / names[key]
    report = json.loads(source.read_text(encoding='utf-8'))
    assert report['passed'] and all(report['cleanup'].values())
    assert report['actual_api_requests'] == report['generation_posts'] == 0
    video = (ROOT / report['private_recordings'][index]).resolve()
    assert video.is_file() and video.is_relative_to(PRIVATE.resolve())
    sources.append({'browser_report': source.relative_to(ROOT).as_posix(), 'sha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'selected_page_index': index, 'selected_recording_sha256': hashlib.sha256(video.read_bytes()).hexdigest()})
    mp4, gif = MEDIA / (stem + '.mp4'), MEDIA / (stem + '.gif')
    commands = [
        [str(FFMPEG), '-v', 'error', '-n', '-i', str(video), '-an', '-vf', 'fps=15,scale=960:-2', '-c:v', 'libx264', '-crf', '23', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', '-metadata', 'title=Synthetic Web local data; no API or GPU generation', str(mp4)],
        [str(FFMPEG), '-v', 'error', '-n', '-i', str(mp4), '-filter_complex', 'fps=6,scale=720:-1:flags=lanczos,split[s0][s1];[s0]palettegen=max_colors=128[p];[s1][p]paletteuse=dither=bayer:bayer_scale=3', '-loop', '0', str(gif)],
    ]
    for command in commands:
        encoded = subprocess.run(command, capture_output=True)
        assert encoded.returncode == 0, 'Owned encoding failed; preserve existing output and inspect'
    for file in [mp4, gif]:
        decoded = subprocess.run([str(FFMPEG), '-v', 'error', '-i', str(file), '-f', 'null', 'NUL'], capture_output=True)
        assert decoded.returncode == 0 and file.stat().st_size > 0
        clips.append({'file': file.relative_to(ROOT).as_posix(), 'bytes': file.stat().st_size, 'sha256': hashlib.sha256(file.read_bytes()).hexdigest(), 'full_decode_passed': True})
value = {'passed': True, 'scope': 'Complete selected owned public Web page recordings: explicit local cleanup and a compatible recovery rendering/saving a synthetic two-image project. These page videos do not present the entire multi-page release exercise or a GPU generation.', 'sources': sources, 'clips': clips, 'owned_browsers_closed_before_encoding': True, 'full_selected_recordings_included': True, 'actual_generation': False}
with (EVIDENCE / 'independent-media-1461.json').open('x', encoding='utf-8') as output:
    output.write(json.dumps(value, indent=2) + '\n')
print(json.dumps({'passed': True, 'clips': len(clips), 'full_decode_passed': True, 'actual_generation': False}))
