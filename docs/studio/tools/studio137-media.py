"""Encode and fully decode the owned 137 Admin recording; no generation involved."""
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT.parent / '生图生视API封装/app/.local/toolchains/ffmpeg-8.1/bin'
TARGET = ROOT / 'docs/media/studio-137'
report = ROOT / 'aggregator-service/docs/evidence/studio-release-137/admin-browser-1371.json'
source = json.loads(report.read_text())
assert source['passed'] and source['owned_browser_closed'] and source['owned_server_closed']
video = ROOT / source['private_video']
assert video.resolve().is_relative_to((ROOT / '.local').resolve()) and video.is_file()
log = ROOT / '.local/studio137-browser/media.log'
with log.open('wb') as output:
    for name, arguments in (
        ('device-pool-tour.mp4', ['-vf', 'setpts=2*PTS,tpad=start_duration=1:stop_duration=2:stop_mode=clone', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '23', '-movflags', '+faststart']),
        ('device-pool-tour.gif', ['-filter_complex', 'setpts=2*PTS,fps=8,scale=960:-1:flags=lanczos,split[a][b];[a]palettegen[p];[b][p]paletteuse', '-loop', '0']),
    ):
        target = TARGET / name
        subprocess.run([str(TOOLS / 'ffmpeg.exe'), '-nostdin', '-y', '-i', str(video), *arguments, str(target)], check=True, stdout=output, stderr=output)
        subprocess.run([str(TOOLS / 'ffmpeg.exe'), '-nostdin', '-v', 'error', '-i', str(target), '-f', 'null', '-'], check=True, stdout=output, stderr=output)
files = []
for path in sorted(TARGET.iterdir()):
    if path.suffix not in ('.png', '.mp4', '.gif'):
        continue
    probe = json.loads(subprocess.check_output([str(TOOLS / 'ffprobe.exe'), '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)]))
    stream = probe['streams'][0]
    files.append({'file': path.name, 'bytes': path.stat().st_size, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'width': stream['width'], 'height': stream['height'], 'duration': probe['format'].get('duration'), 'fully_decoded': path.suffix in ('.mp4', '.gif')})
result = {'passed': True, 'at': datetime.now(UTC).isoformat(), 'scope': 'Admin synthetic browser recording, not real generation or phone evidence', 'source_report': report.relative_to(ROOT).as_posix(), 'source_report_sha256': hashlib.sha256(report.read_bytes()).hexdigest(), 'files': files, 'virtualization_used': False}
(TARGET / 'media-manifest.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'passed': True, 'files': len(files), 'movie_and_gif_fully_decoded': True}))
