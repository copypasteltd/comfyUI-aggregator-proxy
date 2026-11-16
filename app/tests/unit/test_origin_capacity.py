"""Capacity edits touch only inspected limits, preserving routes and CORS."""

import importlib.util
import sys
from pathlib import Path

import pytest


def load():
    directory = Path(__file__).resolve().parents[2] / 'deploy/native-linux'
    sys.path.insert(0, str(directory))
    try:
        spec = importlib.util.spec_from_file_location('native_capacity_test', directory / 'native_origin_capacity.py')
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(directory))


def test_capacity_preserves_every_other_directive_and_service_setting() -> None:
    module = load()
    edge = '# retained\nworker_processes 1;\nevents { worker_connections 128; }\nhttp { server { listen 39443; add_header Access-Control-Allow-Origin $http_origin always; location /origin/ { proxy_pass http://127.0.0.1:32880; } } }\n'
    api = '[Service]\nEnvironmentFile=/private/env\nExecStart=/python -m uvicorn --limit-concurrency 128 --workers 1\nMemoryMax=512M\n'
    changed_edge, changed_api = module.render(edge, api)
    assert changed_edge.removeprefix('worker_rlimit_nofile 4096;\n').replace('worker_connections 2048;', 'worker_connections 128;') == edge
    assert changed_api.replace('--limit-concurrency 512 ', '--limit-concurrency 128 ') == api
    with pytest.raises(ValueError):
        module.render(changed_edge, changed_api)


def test_capacity_rejects_ambiguous_or_changed_configuration() -> None:
    module = load()
    edge = 'events { worker_connections 128; }'
    api = '[Service]\nExecStart=/python --limit-concurrency 128 --workers 1\n'
    for changed in [edge * 2, edge.replace('128', '256'), edge.replace('128;', '128; worker_connections 128;'), 'worker_rlimit_nofile 2048;\n' + edge]:
        with pytest.raises(ValueError):
            module.render(changed, api)
    for changed in [api * 2, api.replace('128', '256'), '[Service]\nExecStart=/python\n']:
        with pytest.raises(ValueError):
            module.render(edge, changed)
