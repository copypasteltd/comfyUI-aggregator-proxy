"""Small valid originals and deliberately invalid decoder fixtures, native only."""
import hashlib
import json
import shutil
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
MEDIA = ROOT / 'docs/media/studio-149/fixtures'
MEDIA.mkdir(parents=True, exist_ok=False)
previous = json.loads((ROOT / 'docs/research/studio-implementation/Web-migration-fixtures-148.json').read_text(encoding='utf-8'))
fixtures = []
for name in ['synthetic.wav', 'synthetic.flac', 'synthetic.mp4', 'synthetic.webm']:
    source = next(item for item in previous['fixtures'] if item['name'] == name)
    data = (ROOT / source['file']).read_bytes()
    assert len(data) == source['bytes'] and hashlib.sha256(data).hexdigest() == source['sha256'] and source['integrity_and_format_verified']
    shutil.copyfile(ROOT / source['file'], MEDIA / name)
    fixtures.append({**source, 'file': (MEDIA / name).relative_to(ROOT).as_posix(), 'expected_decodable': True})
for name, format, mime in [('original.jpg', 'JPEG', 'image/jpeg'), ('variant.webp', 'WEBP', 'image/webp')]:
    file = MEDIA / name
    Image.new('RGB', (16, 12), (123, 177, 59)).save(file, format=format)
    Image.open(file).verify(); Image.open(file).load()
    fixtures.append({'file': file.relative_to(ROOT).as_posix(), 'name': name, 'mime_type': mime, 'width': 16, 'height': 12, 'bytes': file.stat().st_size, 'sha256': hashlib.sha256(file.read_bytes()).hexdigest(), 'expected_decodable': True})
for name, mime in [('invalid-audio.wav', 'audio/wav'), ('invalid-video.bin', 'application/x-synthetic-unsupported')]:
    file = MEDIA / name
    file.write_bytes(b'SYNTHETIC INVALID MEDIA: decoder refusal is expected; preserve these original bytes.\n')
    fixtures.append({'file': file.relative_to(ROOT).as_posix(), 'name': name, 'mime_type': mime, 'bytes': file.stat().st_size, 'sha256': hashlib.sha256(file.read_bytes()).hexdigest(), 'expected_decodable': False})
value = {'passed': True, 'scope': 'Six small valid local originals and two deliberately invalid media originals for actual native error events and lossless download; not GPU generation or general codec acceptance.', 'fixtures': fixtures, 'virtualization_used': False}
with (ROOT / 'docs/research/studio-implementation/media-actions-fixtures-149.json').open('x', encoding='utf-8') as output:
    output.write(json.dumps(value, indent=2) + '\n')
print(json.dumps({'passed': True, 'fixtures': len(fixtures), 'deliberately_invalid': 2}))
