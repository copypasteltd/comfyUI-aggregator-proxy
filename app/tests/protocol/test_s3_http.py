"""Real SDK/socket evidence against a synthetic S3 protocol endpoint, not S3/IAM acceptance."""
from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

import pytest
from cryptography.fernet import Fernet
from generation_domain.errors import DomainError
from generation_infrastructure.settings import Settings
from generation_infrastructure.storage import S3Store

VERSION = "opaque/+version=="
DATA = b"synthetic-image-video-audio-data"
KEY = "outputs/synthetic/object"


class Endpoint(ThreadingHTTPServer):
    mode = "valid"

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), Handler)
        self.versions = {VERSION: DATA}
        self.requests: list[dict[str, Any]] = []
        self.counter = 0


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: Endpoint

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def reply(self, status: int, data: bytes, **headers: str) -> None:
        self.send_response(status)
        self.send_header("Connection", "close")
        if self.server.mode != "no_length":
            self.send_header("Content-Length", headers.pop("length", str(len(data))))
        for key, value in headers.items():
            self.send_header(key.replace("_", "-"), value)
        self.end_headers()
        self.close_connection = True
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def record_request(self) -> dict[str, list[str]]:
        address = urlsplit(self.path)
        query = parse_qs(address.query, keep_blank_values=True)
        self.server.requests.append({"method": self.command, "key": unquote(address.path), "query": query, "range": self.headers.get("Range"), "signed": self.headers.get("Authorization", "").startswith("AWS4-HMAC-SHA256 ")})
        return query

    def do_GET(self) -> None:
        query = self.record_request()
        xml = 'xmlns="http://s3.amazonaws.com/doc/2006-03-01/"'
        if "versioning" in query:
            return self.reply(200, f'<VersioningConfiguration {xml}><Status>Enabled</Status></VersioningConfiguration>'.encode())
        if "acl" in query:
            return self.reply(200, f'<AccessControlPolicy {xml}><Owner><ID>synthetic-owner</ID></Owner><AccessControlList/></AccessControlPolicy>'.encode())
        if "policyStatus" in query:
            return self.reply(200, f'<PolicyStatus {xml}><IsPublic>false</IsPublic></PolicyStatus>'.encode())
        if self.server.mode == "denied":
            return self.reply(403, b'<Error><Code>AccessDenied</Code><Message>synthetic-private-sdk-details</Message></Error>')
        version = query["versionId"][0]
        data = self.server.versions[version]
        interval = self.headers["Range"].removeprefix("bytes=").split("-")
        start, end = map(int, interval)
        selected = data[start:end + 1]
        headers = {"x_amz_version_id": version, "Content_Range": f"bytes {start}-{end}/{len(data)}"}
        mode = self.server.mode
        if mode == "wrong_version":
            headers["x_amz_version_id"] = "wrong"
        elif mode == "missing_version":
            del headers["x_amz_version_id"]
        elif mode == "wrong_range":
            headers["Content_Range"] = f"bytes {start+1}-{end+1}/{len(data)}"
        elif mode == "missing_range":
            del headers["Content_Range"]
        elif mode == "wrong_length":
            headers["length"] = str(len(selected) + 1)
        elif mode == "truncated":
            headers["length"] = str(len(selected))
            selected = selected[:-1]
        elif mode == "whole_object":
            return self.reply(200, data, **headers)
        self.reply(206, selected, **headers)

    def do_PUT(self) -> None:
        self.record_request()
        data = self.rfile.read(int(self.headers["Content-Length"]))
        assert self.headers["x-amz-meta-sha256"] == hashlib.sha256(data).hexdigest()
        self.server.counter += 1
        version = f"opaque/+version{self.server.counter}=="
        self.server.versions[version] = data
        self.reply(200, b"", x_amz_version_id=version, ETag='"synthetic-not-a-sha256"')

    def do_DELETE(self) -> None:
        query = self.record_request()
        version = query["versionId"][0]
        self.server.versions.pop(version, None)
        self.reply(204, b"", x_amz_version_id=version)


@pytest.fixture
def endpoint(tmp_path: Path) -> Iterator[tuple[S3Store, Endpoint, list[Any]]]:
    server = Endpoint()
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), name="owned-synthetic-s3", daemon=True)
    settings = Settings(_env_file=None, environment="test", database_url="postgresql+psycopg://synthetic@127.0.0.1/unused", credential_pepper="p" * 43, encryption_key=Fernet.generate_key().decode(), public_origin="https://127.0.0.1", storage_backend="s3", s3_endpoint=f"http://127.0.0.1:{server.server_port}", s3_bucket="synthetic-private", s3_access_key="synthetic-access", s3_secret_key="synthetic-secret", spool_root=tmp_path)
    store = S3Store(settings)
    original = store.client.get_object
    bodies: list[Any] = []

    def track(**kwargs: Any) -> dict[str, Any]:
        result = original(**kwargs)
        bodies.append(result["Body"])
        return result

    store.client.get_object = track
    thread.start()
    try:
        yield store, server, bodies
    finally:
        for body in bodies:
            body.close()
        store.client.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        assert not thread.is_alive()


def test_real_sdk_sends_signed_version_and_range_and_closes(endpoint: tuple[S3Store, Endpoint, list[Any]]) -> None:
    store, server, bodies = endpoint
    assert b"".join(store.read(KEY, VERSION, 3, 7)) == DATA[3:8]
    assert server.requests == [{"method": "GET", "key": "/synthetic-private/" + KEY, "query": {"versionId": [VERSION]}, "range": "bytes=3-7", "signed": True}]
    assert bodies[0]._raw_stream.closed


def test_recovery_uses_real_sdk_original_full_version_and_audio_counter(endpoint: tuple[S3Store, Endpoint, list[Any]]) -> None:
    from generation_infrastructure.restore_verification import (
        Budget,
        RecoveryLimits,
        _inspect_objects,
    )
    store, server, bodies = endpoint
    result = _inspect_objects(store, [(KEY, VERSION, len(DATA), hashlib.sha256(DATA).hexdigest(), "audio/wav")], Budget(RecoveryLimits()))
    assert result.records == 1 and result.bytes == len(DATA) and result.media.audio == 1
    assert server.requests[0]["query"] == {"versionId": [VERSION]}
    assert server.requests[0]["range"] == f"bytes=0-{len(DATA)-1}" and server.requests[0]["signed"]
    assert bodies[0]._raw_stream.closed


@pytest.mark.parametrize("mode", ["wrong_version", "missing_version", "wrong_range", "missing_range", "wrong_length", "no_length", "whole_object"])
def test_real_http_invalid_headers_never_yield_bytes(endpoint: tuple[S3Store, Endpoint, list[Any]], mode: str) -> None:
    store, server, bodies = endpoint
    server.mode = mode
    with pytest.raises(DomainError) as error:
        next(store.read(KEY, VERSION, 3, 7))
    assert error.value.code == "storage_response_invalid"
    assert len(server.requests) == 1 and bodies[0]._raw_stream.closed


def test_real_http_truncated_body_is_not_successful(endpoint: tuple[S3Store, Endpoint, list[Any]]) -> None:
    store, server, bodies = endpoint
    server.mode = "truncated"
    with pytest.raises(DomainError) as error:
        b"".join(store.read(KEY, VERSION, 3, 7))
    assert error.value.code in ("storage_unavailable", "object_truncated")
    assert bodies[0]._raw_stream.closed


def test_real_sdk_permission_error_is_redacted(endpoint: tuple[S3Store, Endpoint, list[Any]]) -> None:
    store, server, bodies = endpoint
    server.mode = "denied"
    with pytest.raises(DomainError) as error:
        next(store.read(KEY, VERSION, 3, 7))
    assert error.value.code == "storage_unavailable" and "synthetic-private" not in str(error.value)
    assert bodies == [] and len(server.requests) == 1


def test_real_sdk_preflight_put_readback_range_delete_preserves_other_version(endpoint: tuple[S3Store, Endpoint, list[Any]]) -> None:
    store, server, bodies = endpoint
    store.check_private_versioned_bucket()
    for media_type in ["image/png", "video/mp4", "audio/wav"]:
        payload = ("synthetic-" + media_type).encode()
        obj = store.put(KEY, BytesIO(payload), media_type, 1024)
        assert obj.sha256 == hashlib.sha256(payload).hexdigest()
        assert b"".join(store.read(KEY, obj.version, 1, 4)) == payload[1:5]
        store.delete(KEY, obj.version)
        assert obj.version not in server.versions and server.versions[VERSION] == DATA
    assert all(item["signed"] for item in server.requests)
    assert all(body._raw_stream.closed for body in bodies)


def test_real_sdk_presign_keeps_version_and_expiry_cap(endpoint: tuple[S3Store, Endpoint, list[Any]]) -> None:
    store, server, _ = endpoint
    query = parse_qs(urlsplit(store.signed_url(KEY, VERSION, 1000)).query)
    assert query["versionId"] == [VERSION]
    if "X-Amz-Expires" in query:
        assert query["X-Amz-Expires"] == ["900"]
    else:
        assert 898 <= int(query["Expires"][0]) - time.time() <= 900
    assert server.requests == []
