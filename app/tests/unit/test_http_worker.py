from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import httpx
import pytest
from generation_contracts.workflows import WorkflowManifest
from generation_domain.errors import DomainError
from generation_domain.values import fingerprint
from generation_domain.workflows import validate_manifest
from generation_infrastructure.commands import environment_receipt_valid
from pydantic import ValidationError
from windows_worker import api_environment
from windows_worker.api_environment import observe_api_environment
from windows_worker.configuration import WorkerSettings
from windows_worker.environment import file_digest, verify_environment
from windows_worker.http_output import download_output, output_locator


@pytest.fixture
def api_manifest(audio_manifest: dict[str, Any]) -> dict[str, Any]:
    audio_manifest['workflow_api']['1']['inputs']['model_name'] = 'synthetic.safetensors'
    audio_manifest['dependency_lock'] = {'mode': 'comfy-api-only-v1', 'comfy_version': 'synthetic-1', 'node_classes': ['SyntheticAudio', 'SaveAudio'], 'model_inputs': [{'class_type': 'SyntheticAudio', 'input_name': 'model_name', 'value': 'synthetic.safetensors'}]}
    return audio_manifest


def test_api_manifest_is_explicit_and_cannot_claim_native_files(api_manifest: dict[str, Any], tmp_path: Path) -> None:
    WorkflowManifest.model_validate(api_manifest)
    validate_manifest(api_manifest, 'text_to_audio')
    with pytest.raises(DomainError, match='API'):
        verify_environment(tmp_path, api_manifest)
    mixed = deepcopy(api_manifest)
    mixed['dependency_lock']['comfy_commit'] = 'a' * 40
    with pytest.raises(ValidationError):
        WorkflowManifest.model_validate(mixed)
    with pytest.raises(DomainError):
        validate_manifest(mixed, 'text_to_audio')


@pytest.mark.parametrize('change', ['missing_class', 'extra_class', 'wrong_model', 'duplicate_class'])
def test_api_declaration_must_describe_frozen_graph(api_manifest: dict[str, Any], change: str) -> None:
    lock = api_manifest['dependency_lock']
    if change == 'missing_class':
        lock['node_classes'].pop()
    elif change == 'extra_class':
        lock['node_classes'].append('Unknown')
    elif change == 'duplicate_class':
        lock['node_classes'].append('SaveAudio')
    else:
        lock['model_inputs'][0]['value'] = 'other.safetensors'
    with pytest.raises(DomainError):
        validate_manifest(api_manifest, 'text_to_audio')


@pytest.mark.parametrize('change', ['ok', 'version', 'node', 'model'])
def test_api_observation_is_honest_and_checks_current_directory(api_manifest: dict[str, Any], monkeypatch: pytest.MonkeyPatch, change: str) -> None:
    closed = []
    def handler(request: httpx.Request) -> httpx.Response:
        data = {'system': {'comfyui_version': 'changed' if change == 'version' else 'synthetic-1'}} if request.url.path == '/system_stats' else {'SyntheticAudio': {'input': {'required': {'model_name': [['other' if change == 'model' else 'synthetic.safetensors']]}}}, **({} if change == 'node' else {'SaveAudio': {}})}
        return httpx.Response(200, stream=httpx.ByteStream(json.dumps(data).encode()))
    class Client:
        http = httpx.Client(base_url="http://synthetic", transport=httpx.MockTransport(handler))
        def __init__(self, *_args: Any) -> None:
            pass
        def close(self) -> None:
            self.http.close()
            closed.append(True)
    monkeypatch.setattr(api_environment, 'ComfyClient', Client)
    if change == 'ok':
        result = observe_api_environment('http://127.0.0.1:8188', None, api_manifest)
        assert result == {'environment_verified': False, 'api_profile_verified': True, 'environment_digest': fingerprint({'mode': 'comfy-api-only-v1', 'dependency_lock': api_manifest['dependency_lock']})}
        assert environment_receipt_valid({'manifest': api_manifest}, result)
        assert not environment_receipt_valid({'manifest': api_manifest}, {'environment_verified': True})
        assert not environment_receipt_valid({'manifest': {'dependency_lock': {}}}, result)
    else:
        with pytest.raises(DomainError):
            observe_api_environment('http://127.0.0.1:8188', None, api_manifest)
    assert closed == [True]


def test_http_connection_requires_opt_in_and_has_no_local_root(tmp_path: Path) -> None:
    config = {'control_origin': 'http://127.0.0.1:39443', 'allow_http': True, 'credential_file': tmp_path/'key.json', 'state_root': tmp_path, 'gpus': [{'hardware_id': 'synthetic', 'name': 'Synthetic remote GPU'}], 'channels': [{'base_url': 'http://127.0.0.1:8188', 'hardware_id': 'synthetic', 'connection_mode': 'remote_http'}]}
    with pytest.raises(ValidationError):
        WorkerSettings.model_validate(config)
    config['allow_api_only'] = True
    settings = WorkerSettings.model_validate(config)
    with pytest.raises(DomainError):
        _ = settings.channels[0].local_root
    with pytest.raises(DomainError):
        settings.channel('http://127.0.0.1:9999')
    config['channels'][0]['comfy_root'] = tmp_path
    with pytest.raises(ValidationError):
        WorkerSettings.model_validate(config)


@pytest.mark.parametrize('prefix', [0, 7, 1048613, -1])
async def test_http_copy_resumes_exact_prefix_and_checks_stable_source(tmp_path: Path, prefix: int) -> None:
    payload = b'Synthetic HTTP output\x00' * 100000
    target = tmp_path/'outputs'/'synthetic-attempt'/'0.wav'
    if prefix:
        target.parent.mkdir(parents=True)
        target.write_bytes(payload if prefix == -1 else payload[:prefix])
    calls = []
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        assert request.url.path == '/view' and request.headers['accept-encoding'] == 'identity'
        return httpx.Response(200, headers={'content-length': str(len(payload)), 'etag': 'synthetic'}, content=payload if request.method == 'GET' else b'')
    async def verify(path: Path, maximum: int) -> tuple[str, int]:
        return file_digest(path, maximum)
    async with httpx.AsyncClient(base_url='http://synthetic', transport=httpx.MockTransport(handler)) as client:
        result = await download_output(client, {'filename':'0.wav','type':'output'}, target, len(payload), tmp_path, verify)
    assert result == (hashlib.sha256(payload).hexdigest(), len(payload))
    assert target.read_bytes() == payload and calls == ['GET','HEAD']


@pytest.mark.parametrize('variant', ['different', 'longer', 'truncated', 'too_large', 'changed', 'hardlink'])
async def test_http_copy_never_accepts_different_or_unstable_output(tmp_path: Path, variant: str) -> None:
    payload = b'Synthetic data' * 100
    target = tmp_path/'outputs'/'synthetic-attempt'/'0.wav'
    target.parent.mkdir(parents=True)
    original = b'keep unique bytes' if variant in ('different','hardlink') else payload+b'extra' if variant == 'longer' else b''
    target.write_bytes(original)
    if variant == 'hardlink':
        (tmp_path/'retained.wav').hardlink_to(target)
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={'content-length': str(len(payload)), 'etag': 'changed' if request.method == 'HEAD' and variant == 'changed' else 'original'}, content=(payload[:-1] if variant == 'truncated' else payload) if request.method == 'GET' else b'')
    async def verify(path: Path, maximum: int) -> tuple[str, int]:
        return file_digest(path, maximum)
    async with httpx.AsyncClient(base_url='http://synthetic', transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(DomainError):
            await download_output(client, {'filename':'0.wav','type':'output'}, target, len(payload)-1 if variant == 'too_large' else len(payload), tmp_path, verify)
    if variant in ('different','longer','too_large','hardlink'):
        assert target.read_bytes() == original


@pytest.mark.parametrize('item', [{'filename':'../secret','type':'output'}, {'filename':'file','subfolder':'../secret','type':'temp'}, {'filename':'file','type':'input'}, {'filename':'C:secret','type':'output'}])
def test_http_output_rejects_foreign_file_locations(item: dict[str, Any]) -> None:
    with pytest.raises(DomainError):
        output_locator(item)
