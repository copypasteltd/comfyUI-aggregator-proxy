"""Generate small synthetic originals with native CPU tools, without API work."""
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
MEDIA = ROOT / 'docs/media/studio-148/fixtures'
MEDIA.mkdir(parents=True, exist_ok=True)
FFMPEG = ROOT.parent / '生图生视API封装/app/.local/toolchains/ffmpeg-8.1/bin/ffmpeg.exe'
FFPROBE = FFMPEG.with_name('ffprobe.exe')
for name in ['web-original.png', 'web-reference.png']:
    target = MEDIA / name
    assert not target.exists()
    shutil.copyfile(ROOT / 'docs/media/studio-147/fixtures' / name, target)
upload = MEDIA / 'actual-file-input.png'
assert not upload.exists()
Image.new('RGB', (16, 8), (75, 164, 211)).save(upload, format='PNG')
for name, options in [
    ('synthetic.wav', ['-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=16000:duration=1', '-c:a', 'pcm_s16le']),
    ('synthetic.flac', ['-f', 'lavfi', '-i', 'sine=frequency=660:sample_rate=16000:duration=1', '-c:a', 'flac']),
    ('synthetic.mp4', ['-f', 'lavfi', '-i', 'testsrc2=size=64x48:rate=12:duration=1', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-movflags', '+faststart']),
    ('synthetic.webm', ['-f', 'lavfi', '-i', 'testsrc2=size=64x48:rate=12:duration=1', '-c:v', 'libvpx', '-pix_fmt', 'yuv420p', '-deadline', 'good']),
]:
    result = subprocess.run([str(FFMPEG), '-nostdin', '-v', 'error', '-n', *options, '-threads', '1', str(MEDIA / name)], capture_output=True)
    assert result.returncode == 0, 'Preserve existing synthetic outputs and inspect encoding failure'
glb_source = ROOT / 'docs/media/studio-144/media-2026-10-30T12-26-55-868Z/synthetic-two-animations.glb'
assert not (MEDIA / 'synthetic-animated.glb').exists()
shutil.copyfile(glb_source, MEDIA / 'synthetic-animated.glb')
fixtures = []
mime = {'png': 'image/png', 'wav': 'audio/wav', 'flac': 'audio/flac', 'mp4': 'video/mp4', 'webm': 'video/webm', 'glb': 'model/gltf-binary'}
for file in sorted(MEDIA.iterdir()):
    extension = file.suffix[1:]
    data = file.read_bytes()
    item = {'file': file.relative_to(ROOT).as_posix(), 'name': file.name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(), 'mime_type': mime[extension]}
    if extension == 'png':
        Image.open(file).verify()
        with Image.open(file) as image:
            image.load()
            item['width'], item['height'] = image.size
    elif extension == 'glb':
        assert data[:4] == b'glTF' and int.from_bytes(data[4:8], 'little') == 2 and int.from_bytes(data[8:12], 'little') == len(data)
        body = json.loads(data[20:20 + int.from_bytes(data[12:16], 'little')])
        assert len(body['animations']) == 2 and all('uri' not in value for value in body['buffers'] + body['images'])
        item['embedded_animation_clips'] = 2
        item['source_fixture'] = glb_source.relative_to(ROOT).as_posix()
    else:
        decoded = subprocess.run([str(FFMPEG), '-nostdin', '-v', 'error', '-i', str(file), '-f', 'null', 'NUL'], capture_output=True)
        assert decoded.returncode == 0
        probe = subprocess.run([str(FFPROBE), '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(file)], capture_output=True, check=True)
        metadata = json.loads(probe.stdout)
        item['media'] = {key: value for key, value in metadata['streams'][0].items() if key in ['codec_name', 'codec_type', 'width', 'height', 'sample_rate', 'channels', 'duration']}
        item['duration_seconds'] = float(metadata['format']['duration'])
    item['integrity_and_format_verified'] = True
    fixtures.append(item)
value = {'passed': True, 'scope': 'Small synthetic originals for actual ZIP migration; native CPU FFmpeg/Pillow, not API/GPU outputs or production throughput.', 'fixtures': fixtures, 'virtualization_used': False, 'production_api_requests': 0}
with (ROOT / 'docs/research/studio-implementation/Web-migration-fixtures-148.json').open('x', encoding='utf-8') as output:
    output.write(json.dumps(value, indent=2) + '\n')
print(json.dumps({'passed': True, 'fixtures': len(fixtures), 'production_api_requests': 0}))
