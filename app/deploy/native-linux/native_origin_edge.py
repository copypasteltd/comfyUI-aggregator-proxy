"""Add bounded native WebSocket forwarding to the inspected public HTTP server."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from open_cors import atomic_write, blocks

MARKER = "# Managed native ComfyUI origin 146"
CONFIG = Path("/etc/comfyui-aggregator/nginx.conf")
VALIDATE = ["/usr/bin/nginx", "-e", "stderr", "-p", "/var/lib/comfyui-aggregator/edge/", "-c", str(CONFIG), "-t"]


def render(source: str) -> str:
    if MARKER in source:
        raise ValueError("Native routing already installed; inspect instead of duplicating")
    parsed = blocks(source)
    public = [block for block in parsed if block["header"] == ("server",) and any(tokens[:2] == ("listen", "39443") for tokens, _, _ in block["directives"])]
    http = [block for block in parsed if block["header"] == ("http",)]
    if len(public) != 1 or len(http) != 1:
        raise ValueError("Expected one dedicated HTTP/high-port server")
    target = public[0]
    locations = [block for block in parsed if block["parent"] is target and block["header"] == ("location", "/")]
    if len(locations) != 1 or any("/origin/" in block["header"] for block in parsed):
        raise ValueError("Ambiguous existing origin routing")
    hosts = [tokens[2] for tokens, _, _ in locations[0]["directives"] if tokens[:2] == ("proxy_set_header", "Host")]
    if len(hosts) != 1:
        raise ValueError("Missing inspected upstream Host")
    mapping = '\n    ' + MARKER + '\n    map $http_upgrade $comfy_origin_connection { default upgrade; "" close; }\n'
    location = f'''
        {MARKER}
        location ^~ /origin/ {{
            proxy_pass http://127.0.0.1:32880;
            proxy_http_version 1.1;
            proxy_set_header Host {hosts[0]};
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection $comfy_origin_connection;
            proxy_set_header X-Forwarded-For $remote_addr;
            proxy_set_header X-Forwarded-Host "";
            proxy_set_header X-Forwarded-Proto http;
            proxy_buffering off;
            proxy_request_buffering off;
            proxy_read_timeout 300s;
            proxy_send_timeout 300s;
        }}
'''
    edits = [(http[0]["body"], mapping), (target["end"], location)]
    for offset, value in sorted(edits, reverse=True):
        source = source[:offset] + value + source[offset:]
    blocks(source)
    return source


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    before = CONFIG.read_bytes()
    if hashlib.sha256(before).hexdigest() != args.expected_sha256:
        raise SystemExit("Configuration changed since inspection")
    after = render(before.decode()).encode()
    if not args.apply:
        print(json.dumps({"validated_render": True, "apply": False}))
        return
    backup = Path("/var/lib/comfyui-aggregator-deploy") / ("pre-origin-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + ".nginx.conf")
    atomic_write(backup, before, 0o600)
    try:
        atomic_write(CONFIG, after)
        subprocess.run(VALIDATE, check=True, capture_output=True, timeout=20)  # noqa: S603 -- fixed service command
        subprocess.run(["/usr/bin/systemctl", "reload", "comfy-aggregator-edge.service"], check=True, capture_output=True, timeout=20)  # noqa: S603 -- fixed service command
    except Exception:
        atomic_write(CONFIG, before)
        subprocess.run(VALIDATE, check=True, capture_output=True, timeout=20)  # noqa: S603 -- validated rollback
        subprocess.run(["/usr/bin/systemctl", "reload", "comfy-aggregator-edge.service"], check=True, capture_output=True, timeout=20)  # noqa: S603 -- validated rollback
        raise
    print(json.dumps({"native_websocket_routing": True, "before_sha256": args.expected_sha256, "after_sha256": hashlib.sha256(after).hexdigest(), "preserved_cors_and_private_listeners": True}))


if __name__ == "__main__":
    main()
