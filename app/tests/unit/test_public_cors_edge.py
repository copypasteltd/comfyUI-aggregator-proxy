from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[2] / 'deploy/native-linux/open_cors.py'
SPEC = importlib.util.spec_from_file_location('public_cors_edge', SOURCE)
assert SPEC and SPEC.loader
edge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(edge)


def test_public_transform_preserves_private_listener_and_existing_static_policy() -> None:
    private = '''server {
    listen 127.0.0.1:39446;
    allow 127.0.0.1;
    deny all;
    location /internal/ { proxy_pass http://127.0.0.1:32880; }
}'''
    public = '''server {
    listen 39443;
    include /etc/comfyui-aggregator/public-skills.conf;
    location /studio/ {
        # Quoted braces/semicolons must not change the parser's scope.
        add_header Content-Security-Policy "default-src 'self'; img-src data:;" always;
        add_header X-Example "literal { value; }" always;
        root /preserved/studio;
    }
    location /v1/ { proxy_pass http://127.0.0.1:32880; }
    location ^~ /internal/ { return 404; }
}'''
    rendered = edge.render('http {\n' + public + '\n' + private + '\n}', public_server=True)
    assert private in rendered
    assert 'add_header Content-Security-Policy "default-src \'self\'; img-src data:;" always;' in rendered
    assert 'add_header X-Example "literal { value; }" always;' in rendered
    assert 'location ^~ /internal/ { return 404; }' in rendered
    assert 'proxy_pass http://127.0.0.1:32880;' in rendered
    assert rendered.count('add_header Access-Control-Allow-Origin $http_origin always;') == 2
    assert 'if ($comfy_public_cors_preflight = 1) { return 204; }' in rendered
    assert 'proxy_hide_header Access-Control-Allow-Origin;' in rendered


def test_static_transform_replaces_restrictive_headers_without_losing_assets() -> None:
    original = '''location = /skill.md {
    alias /preserved/skill.md;
    add_header Cache-Control no-cache always;
    add_header Access-Control-Allow-Origin "*" always;
    add_header Access-Control-Allow-Methods "GET, HEAD, OPTIONS" always;
    add_header Access-Control-Allow-Headers "If-None-Match" always;
    if ($request_method = OPTIONS) { return 204; }
    limit_except GET OPTIONS { deny all; }
}'''
    rendered = edge.render(original, public_server=False)
    assert 'alias /preserved/skill.md;' in rendered
    assert 'limit_except GET OPTIONS { deny all; }' in rendered
    assert 'add_header Cache-Control no-cache always;' in rendered
    assert '"GET, HEAD, OPTIONS"' not in rendered
    assert 'add_header Access-Control-Allow-Headers "If-None-Match"' not in rendered
    assert rendered.count('add_header Access-Control-Allow-Origin ') == 1
    assert 'Access-Control-Allow-Private-Network "true" always;' in rendered


@pytest.mark.parametrize('source', ['http { server { listen 443; } }', 'http { server { listen 39443; } server { listen 39443; } }'])
def test_ambiguous_or_missing_public_listener_is_refused(source: str) -> None:
    with pytest.raises(ValueError, match='exactly one'):
        edge.render(source, public_server=True)


def test_duplicate_application_is_refused() -> None:
    rendered = edge.render('http { server { listen 39443; } }', public_server=True)
    with pytest.raises(ValueError, match='already present'):
        edge.render(rendered, public_server=True)


@pytest.mark.parametrize('source', ['http { server { listen 39443;', 'http { server { listen 39443; } }}'])
def test_incomplete_configuration_is_refused(source: str) -> None:
    with pytest.raises(ValueError):
        edge.render(source, public_server=True)


def test_repository_static_snippet_keeps_unrestricted_headers_for_future_skill_updates() -> None:
    source = SOURCE.with_name('public-skills.nginx.conf').read_text(encoding='utf-8')
    locations = [block for block in edge.blocks(source) if block['header'] and block['header'][0] == 'location' and any(tokens[0] == 'alias' for tokens, _, _ in block['directives'])]
    assert len(locations) == 2
    for location in locations:
        directives = {tokens[1]: tokens[2] for tokens, _, _ in location['directives'] if tokens[0] == 'add_header'}
        assert directives['Access-Control-Allow-Origin'] == '$http_origin'
        assert directives['Access-Control-Allow-Headers'] == '$http_access_control_request_headers'
        assert directives['Access-Control-Allow-Methods'] == '$http_access_control_request_method'
        assert directives['Access-Control-Allow-Credentials'] == '"true"'
        assert directives['Access-Control-Allow-Private-Network'] == '"true"'


def test_fresh_public_template_preserves_the_same_policy() -> None:
    source = SOURCE.with_name('nginx.conf.example').read_text(encoding='utf-8')
    server = next(block for block in edge.blocks(source) if block['header'] == ('server',))
    directives = {tokens[1]: tokens[2] for tokens, _, _ in server['directives'] if tokens[0] == 'add_header'}
    assert directives['Access-Control-Allow-Origin'] == '$http_origin'
    assert directives['Access-Control-Allow-Methods'] == '$http_access_control_request_method'
    assert directives['Access-Control-Allow-Headers'] == '$http_access_control_request_headers'
    assert directives['Access-Control-Allow-Private-Network'] == '"true"'
    assert 'proxy_hide_header Access-Control-Allow-Origin;' in source
    assert 'if ($comfy_public_cors_preflight = 1) { return 204; }' in source
