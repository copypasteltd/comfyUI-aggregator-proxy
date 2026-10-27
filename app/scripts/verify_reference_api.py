"""Submit a reference generation through the public API and verify its download.

Set COMFY_AGGREGATOR_API_KEY and pass a stable --request-id. Keep the output
directory between retries: its private state prevents duplicate asset/job submits.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--mode', choices=('text-image', 'image-edit', 'image-refs', 'text-video', 'video-first-frame', 'video-refs'), required=True)
    parser.add_argument('--prompt', required=True)
    parser.add_argument('--reference', type=Path, action='append', default=[])
    parser.add_argument('--request-id', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--size', default='720x1080')
    parser.add_argument('--quality', default='standard', choices=('standard', 'draft', 'high'))
    parser.add_argument('--seconds', type=float)
    parser.add_argument('--seed', type=int, default=20261027)
    parser.add_argument('--timeout', type=int, default=7200)
    result = parser.parse_args()
    expected = 2 if result.mode.endswith('-refs') else 1 if result.mode in ('image-edit', 'video-first-frame') else 0
    if len(result.reference) != expected:
        parser.error(f'{result.mode} requires exactly {expected} reference image(s)')
    if not 60 <= result.timeout <= 14400:
        parser.error('--timeout must be between 60 and 14400 seconds')
    if not result.request_id.isascii() or not 1 <= len(result.request_id) <= 80 or any(not char.isalnum() and char not in '-_' for char in result.request_id):
        parser.error('--request-id must contain 1-80 letters, digits, hyphens or underscores')
    if not 0 <= result.seed <= 2147483647:
        parser.error('--seed must be between 0 and 2147483647')
    return result


def save(path: Path, state: dict[str, Any]) -> None:
    temporary = path.with_suffix('.staged')
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def body(args: argparse.Namespace, assets: list[str]) -> tuple[str, dict[str, Any]]:
    value: dict[str, Any] = {'model': args.model, 'prompt': args.prompt, 'size': args.size, 'quality': args.quality, 'workflow_options': {'seed': args.seed}}
    video = 'video' in args.mode
    if args.seconds is not None:
        value['seconds'] = args.seconds
    if args.mode == 'image-edit':
        value['source_image'] = {'asset_id': assets[0]}
    elif args.mode == 'video-first-frame':
        value['input_reference'] = {'asset_id': assets[0]}
    elif args.mode.endswith('-refs'):
        value['references'] = [{'asset_id': asset, 'role': role} for asset, role in zip(assets, ('subject', 'style'), strict=True)]
    if not video:
        value.update(n=1, response_format='url')
    return '/v1/videos' if video else '/v1/images/generations/async' if args.mode == 'text-image' else '/v1/images/edits/async', value


def run(args: argparse.Namespace) -> dict[str, Any]:
    secret = os.environ.get('COMFY_AGGREGATOR_API_KEY')
    if not secret:
        raise ValueError('Set COMFY_AGGREGATOR_API_KEY before running this script')
    args.output.mkdir(parents=True, exist_ok=True)
    state_path = args.output / 'request-state.json'
    state = json.loads(state_path.read_text('utf-8')) if state_path.exists() else {}
    sources = [path.read_bytes() for path in args.reference]
    specification = {'origin': args.base_url.rstrip('/'), 'model': args.model, 'mode': args.mode, 'prompt': args.prompt, 'size': args.size, 'quality': args.quality, 'seconds': args.seconds, 'seed': args.seed, 'reference_sha256': [hashlib.sha256(data).hexdigest() for data in sources], 'request_id': args.request_id}
    digest = hashlib.sha256(json.dumps(specification, sort_keys=True).encode()).hexdigest()
    if state and state.get('request_digest') != digest:
        raise ValueError('Output directory contains a different request; preserve it and choose a new directory')
    state.setdefault('request_digest', digest)
    state.setdefault('assets', [])
    save(state_path, state)
    prefix = 'reference-check-' + args.request_id
    with httpx.Client(base_url=args.base_url.rstrip('/'), trust_env=False, timeout=45, headers={'Authorization': 'Bearer ' + secret}) as client:
        for index, data in enumerate(sources):
            if index < len(state['assets']):
                continue
            filename = args.reference[index].name
            response = client.post('/v1/assets', files={'file': (filename, data)}, headers={'Idempotency-Key': prefix + f'-asset-{index}'})
            response.raise_for_status()
            state['assets'].append(response.json()['id'])
            save(state_path, state)
        endpoint, request = body(args, state['assets'])
        if 'job_id' not in state:
            response = client.post(endpoint, json=request, headers={'Idempotency-Key': prefix + '-job'})
            response.raise_for_status()
            state['job_id'] = response.json()['id']
            save(state_path, state)
        replay = client.post(endpoint, json=request, headers={'Idempotency-Key': prefix + '-job'})
        replay.raise_for_status()
        if replay.json()['id'] != state['job_id']:
            raise RuntimeError('Idempotency replay returned a different job')
        print(json.dumps({'job_id': state['job_id'], 'model': args.model, 'replay_same_job': True}), flush=True)
        deadline = time.monotonic() + args.timeout
        last_status = None
        while True:
            response = client.get('/v1/jobs/' + state['job_id'])
            response.raise_for_status()
            job = response.json()
            if job['status'] != last_status:
                print(json.dumps({'job_id': job['id'], 'status': job['status']}), flush=True)
                last_status = job['status']
            if job['status'] == 'succeeded':
                break
            if job['status'] in ('failed', 'cancelled', 'expired'):
                state['terminal'] = job
                save(state_path, state)
                raise RuntimeError('Generation ended with status ' + job['status'])
            if time.monotonic() >= deadline:
                raise TimeoutError('Observation ended; rerun with the same directory and request-id to resume the same job')
            time.sleep(5)
        if len(job.get('artifacts', [])) != 1:
            raise RuntimeError('Expected exactly one published artifact')
        artifact = job['artifacts'][0]
        identifier = artifact['id'] if isinstance(artifact, dict) else artifact
        metadata_response = client.get('/v1/content/' + identifier)
        metadata_response.raise_for_status()
        metadata = metadata_response.json()
        endpoint = '/v1/content/' + identifier + '/download'
        response = client.get(endpoint)
        response.raise_for_status()
        data = response.content
        sha256 = hashlib.sha256(data).hexdigest()
        if len(data) != metadata['bytes'] or sha256 != metadata['sha256']:
            raise RuntimeError('Downloaded bytes do not match the published artifact')
        dimensions = metadata.get('metadata', {})
        if (dimensions.get('width'), dimensions.get('height')) != tuple(map(int, args.size.split('x'))):
            raise RuntimeError('Published output dimensions do not match the requested size')
        head = client.head(endpoint)
        head.raise_for_status()
        if head.content or int(head.headers['content-length']) != len(data):
            raise RuntimeError('HEAD response is inconsistent with the download')
        part = client.get(endpoint, headers={'Range': 'bytes=0-63'})
        if part.status_code != 206 or part.content != data[:64]:
            raise RuntimeError('Range response does not match the downloaded bytes')
        destination = args.output / ('output.mp4' if 'video' in args.mode else 'output.png')
        destination.write_bytes(data)
        report = {'model': args.model, 'mode': args.mode, 'job_id': job['id'], 'status': job['status'], 'replay_same_job': True, 'content_id': identifier, 'bytes': len(data), 'sha256': sha256, 'metadata': dimensions, 'expires_at': metadata['expires_at'], 'completed_at': job.get('completed_at'), 'head_200': True, 'range_206': True}
        state['result'] = report
        save(state_path, state)
        save(args.output / 'verification.json', report)
        return report


if __name__ == '__main__':
    try:
        print(json.dumps(run(arguments()), ensure_ascii=False, indent=2))
    except (httpx.HTTPError, ValueError, RuntimeError, TimeoutError) as error:
        # Never include headers, request bodies, or raw exception text in output.
        raise SystemExit(type(error).__name__ + ': inspect the saved request state and authenticated task status') from None
