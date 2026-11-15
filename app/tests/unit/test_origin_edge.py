from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "deploy/native-linux"))
spec = importlib.util.spec_from_file_location("native_origin_edge", ROOT / "deploy/native-linux/native_origin_edge.py")
assert spec and spec.loader
edge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(edge)


def test_native_origin_edge_preserves_cors_and_private_servers() -> None:
    source = (ROOT / "deploy/native-linux/nginx.conf.example").read_text()
    # New-install template already contains the equivalent origin configuration.
    a = source.index("    map $http_upgrade")
    b = source.index("    server {", a)
    source = source[:a] + source[b:]
    a = source.index("        location ^~ /origin/")
    b = source.index("        location / {", a)
    source = source[:a] + source[b:]
    private = '\nserver { listen 127.0.0.1:39446; location / { return 404; } }\n'
    source = source[:-2] + private + source[-2:]
    actual = edge.render(source)
    assert actual.count("proxy_set_header Upgrade $http_upgrade;") == 1
    assert "map $http_upgrade $comfy_origin_connection" in actual
    assert private in actual
    assert source.count("add_header Access-Control-") == actual.count("add_header Access-Control-")
    assert "listen 39443;" in actual and "proxy_set_header Host PUBLIC_HOST;" in actual
    with pytest.raises(ValueError):
        edge.render(actual)


@pytest.mark.parametrize("fragment", ["listen 39443;", "proxy_set_header Host PUBLIC_HOST;"])
def test_native_origin_edge_refuses_ambiguous_config(fragment: str) -> None:
    source = (ROOT / "deploy/native-linux/nginx.conf.example").read_text()
    with pytest.raises(ValueError):
        edge.render(source.replace(fragment, ""))
