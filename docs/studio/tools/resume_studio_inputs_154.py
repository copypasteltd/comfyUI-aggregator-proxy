"""Read-only recovery of a recorded browser acceptance submission. Never creates a job."""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('label', choices=['text-image', 'named-edit', 'named-music'])
parser.add_argument('--wait', action='store_true')
args = parser.parse_args()
private = ROOT / '.local/studio-ops'
connection = json.loads((private / 'canvas-154-connection.json').read_text(encoding='utf-8'))
marker_file = private / f'canvas-154-once-{args.label}.json'
original = json.loads(marker_file.read_text(encoding='utf-8'))

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

opener = build_opener(NoRedirect())
def call(path, body=None, headers=None):
    assert path.startswith('/v1/') and '?' not in path
    request = Request(connection['origin'] + path, data=body, headers={'Authorization': 'Bearer ' + connection['key'], **(headers or {})})
    return opener.open(request, timeout=45)

report = {'passed': False, 'at': datetime.now(UTC).isoformat(), 'scope': 'Read-only original-job reconciliation and verified retrieval; no replacement generation or server changes.', 'label': args.label, 'new_generation_posts': 0, 'downloads': []}
try:
    job_id = original.get('job_id')
    if not job_id:
        if not all(original.get(key) for key in ['body', 'idempotency_key', 'release_digest']):
            raise RuntimeError('No observed submission identity. Inspect the original owned browser journal; no new job was created.')
        with call('/v1/jobs/lookup', original['body'].encode(), {'Content-Type': 'application/json', 'Idempotency-Key': original['idempotency_key'], 'X-Expected-Release-Digest': original['release_digest']}) as response:
            lookup = json.load(response)
        if lookup.get('outcome') != 'found':
            raise RuntimeError('Original submission not observed by read-only lookup; preserved without resubmission.')
        job_id = lookup['job']['id']
        original['job_id'] = job_id
        marker_file.write_text(json.dumps(original), encoding='utf-8')
    assert re.fullmatch(r'job_[a-f0-9]{32}', job_id)
    deadline = time.monotonic() + (600 if args.wait else 0)
    while True:
        with call('/v1/jobs/' + job_id) as response:
            job = json.load(response)
            retry = response.headers.get('Retry-After', '2')
        assert job['id'] == job_id
        report.update(job_id=job_id, status=job['status'], error_code=(job.get('error') or {}).get('code'))
        if job['status'] in ['succeeded', 'failed', 'cancelled', 'expired'] or time.monotonic() >= deadline:
            break
        time.sleep(min(30, max(1, float(retry))) if re.fullmatch(r'[0-9.]+', retry) else 2)
    if job['status'] == 'succeeded':
        target = ROOT / '.local/studio154/recovered'
        target.mkdir(parents=True, exist_ok=True)
        for artifact in job.get('artifacts', []):
            content_id = artifact['id']
            assert re.fullmatch(r'content_[a-f0-9]{32}', content_id)
            with call('/v1/content/' + content_id) as response:
                meta = json.load(response)
            suffix = {'image/png': 'png', 'image/jpeg': 'jpg', 'image/webp': 'webp', 'audio/flac': 'flac', 'audio/wav': 'wav', 'audio/mpeg': 'mp3', 'audio/ogg': 'ogg', 'video/mp4': 'mp4', 'video/webm': 'webm', 'model/gltf-binary': 'glb'}[meta['media_type']]
            destination = target / f'{content_id}.{suffix}'
            if destination.exists():
                with destination.open('rb') as stored:
                    assert destination.stat().st_size == meta['bytes'] and hashlib.file_digest(stored, 'sha256').hexdigest() == meta['sha256']
            else:
                partial = destination.with_suffix(destination.suffix + '.partial')
                digest, received = hashlib.sha256(), 0
                with call('/v1/content/' + content_id + '/download') as response, partial.open('xb') as output:
                    assert response.status == 200 and response.headers.get_content_type() == meta['media_type']
                    while chunk := response.read(1024 * 1024):
                        received += len(chunk)
                        assert received <= meta['bytes']
                        digest.update(chunk); output.write(chunk)
                assert received == meta['bytes'] and digest.hexdigest() == meta['sha256']
                partial.rename(destination)
            report['downloads'].append({'content_id': content_id, 'file': str(destination.relative_to(ROOT)).replace('\\', '/'), 'bytes': meta['bytes'], 'sha256': meta['sha256'], 'media_type': meta['media_type']})
        report['passed'] = bool(report['downloads'])
except HTTPError as error:
    report['failure'] = f'Original business API returned HTTP {error.code}; no generation retried.'
except Exception as error:
    report['failure'] = str(error).replace(connection['key'], '[redacted]')
stamp = datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')
target = ROOT / f'docs/research/studio-implementation/inputs-154-recovery-{args.label}-{stamp}.json'
target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'report': str(target.relative_to(ROOT)), **report}, ensure_ascii=False))
raise SystemExit(0 if report['passed'] else 1)
