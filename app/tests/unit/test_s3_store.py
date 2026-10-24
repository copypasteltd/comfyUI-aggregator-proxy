from __future__ import annotations

import hashlib
import traceback
from io import BytesIO
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from generation_domain.errors import DomainError
from generation_infrastructure.storage import CHUNK, S3Store

VERSION = "opaque/+version=="
KEY = "outputs/synthetic/object"


class Body(BytesIO):
    def __init__(self, value: bytes) -> None:
        super().__init__(value)
        self.read_sizes: list[int] = []

    def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        return super().read(size)


def response(data: bytes, *, start: int = 0, total: int | None = None) -> dict[str, Any]:
    return {"Body": Body(data), "VersionId": VERSION, "ContentLength": len(data), "ContentRange": f"bytes {start}-{start + len(data) - 1}/{total or start + len(data)}", "ResponseMetadata": {"HTTPStatusCode": 206}}


@pytest.fixture
def store(tmp_path: Path) -> S3Store:
    value = S3Store.__new__(S3Store)
    value.bucket, value.spool_root, value.client = "synthetic-private", tmp_path, Mock()
    return value


def test_exact_version_and_range_stream_with_bounded_reads(store: S3Store) -> None:
    data = b"x" * (2 * CHUNK + 7)
    reply = response(data, start=11)
    store.client.get_object.return_value = reply
    assert b"".join(store.read(KEY, VERSION, 11, 10 + len(data))) == data
    store.client.get_object.assert_called_once_with(Bucket=store.bucket, Key=KEY, VersionId=VERSION, Range=f"bytes=11-{10 + len(data)}")
    assert reply["Body"].read_sizes == [CHUNK, CHUNK, 7, 1] and reply["Body"].closed


@pytest.mark.parametrize("change", [
    {"VersionId": "other"}, {"VersionId": None}, {"VersionId": "null"},
    {"ResponseMetadata": {}}, {"ResponseMetadata": {"HTTPStatusCode": 200}},
    {"ContentRange": None}, {"ContentRange": "bytes 1-3/4"}, {"ContentRange": "bytes 0-2/*"},
    {"ContentRange": "bytes 0-2/2"}, {"ContentRange": "bytes 0-2/999999999999999999999"},
    {"ContentRange": "bytes 00-2/3 trailing"}, {"ContentLength": None}, {"ContentLength": 4}, {"ContentLength": True},
])
def test_mismatched_headers_reject_before_reading_and_close(store: S3Store, change: dict[str, Any]) -> None:
    reply = response(b"abc")
    body = reply["Body"]
    reply.update(change)
    store.client.get_object.return_value = reply
    with pytest.raises(DomainError) as error:
        next(store.read(KEY, VERSION, 0, 2))
    assert error.value.code == "storage_response_invalid" and error.value.status == 503
    assert body.closed and body.read_sizes == []


def test_missing_body_fails_closed(store: S3Store) -> None:
    reply = response(b"abc")
    reply["Body"].close()
    del reply["Body"]
    store.client.get_object.return_value = reply
    with pytest.raises(DomainError, match="响应"):
        next(store.read(KEY, VERSION, 0, 2))


@pytest.mark.parametrize(("start", "end"), [(-1, 2), (3, 2), (False, 2), (0, True), (0.0, 2), (0, "2")])
def test_invalid_ranges_never_contact_storage(store: S3Store, start: Any, end: Any) -> None:
    with pytest.raises(DomainError) as error:
        next(store.read(KEY, VERSION, start, end))
    assert error.value.code == "invalid_object_range"
    assert not store.client.mock_calls


@pytest.mark.parametrize("version", ["", "null", "x\ny", "x\x7fy", "x" * 1025, None])
@pytest.mark.parametrize("operation", ["read", "delete", "signed_url"])
def test_invalid_version_never_contacts_storage(store: S3Store, version: Any, operation: str) -> None:
    with pytest.raises(DomainError) as error:
        if operation == "read":
            next(store.read(KEY, version, 0, 2))
        elif operation == "delete":
            store.delete(KEY, version)
        else:
            store.signed_url(KEY, version, 60)
    assert error.value.code == "invalid_object_version" and not store.client.mock_calls


@pytest.mark.parametrize("operation", ["read", "delete", "signed_url", "put"])
def test_unsafe_key_never_contacts_storage(store: S3Store, operation: str) -> None:
    with pytest.raises(DomainError):
        if operation == "read":
            next(store.read("../outside", VERSION, 0, 2))
        elif operation == "delete":
            store.delete("../outside", VERSION)
        elif operation == "put":
            store.put("../outside", BytesIO(b"abc"), "audio/wav", 10)
        else:
            store.signed_url("../outside", VERSION, 60)
    assert not store.client.mock_calls


@pytest.mark.parametrize(("data", "code"), [(b"ab", "object_truncated"), (b"abcd", "storage_response_invalid")])
def test_body_size_mismatch_closes_stream(store: S3Store, data: bytes, code: str) -> None:
    reply = response(b"abc")
    reply["Body"] = body = Body(data)
    store.client.get_object.return_value = reply
    with pytest.raises(DomainError) as error:
        b"".join(store.read(KEY, VERSION, 0, 2))
    assert error.value.code == code and body.closed


def test_caller_closing_partial_stream_closes_body(store: S3Store) -> None:
    reply = response(b"x" * (CHUNK + 1))
    store.client.get_object.return_value = reply
    iterator = store.read(KEY, VERSION, 0, CHUNK)
    assert len(next(iterator)) == CHUNK
    iterator.close()  # type: ignore[attr-defined]
    assert reply["Body"].closed


def test_cleanup_failure_does_not_mask_original_protocol_error(store: S3Store) -> None:
    body = Mock()
    body.close.side_effect = OSError("synthetic-private-close-details")
    store.client.get_object.return_value = {**response(b"abc"), "Body": body, "VersionId": "wrong"}
    with pytest.raises(DomainError) as error:
        next(store.read(KEY, VERSION, 0, 2))
    assert error.value.code == "storage_response_invalid"
    assert "synthetic-private" not in "".join(traceback.format_exception(error.value))
    body.close.assert_called_once()


@pytest.mark.parametrize("during_body", [False, True])
def test_sdk_and_stream_failures_are_redacted(store: S3Store, during_body: bool) -> None:
    if during_body:
        body = Mock()
        body.read.side_effect = OSError("synthetic-private-credential-and-key")
        store.client.get_object.return_value = {**response(b"abc"), "Body": body}
    else:
        store.client.get_object.side_effect = ClientError({"Error": {"Code": "AccessDenied", "Message": "synthetic-private-credential-and-key"}}, "GetObject")
    with pytest.raises(DomainError) as error:
        next(store.read(KEY, VERSION, 0, 2))
    assert error.value.code == "storage_unavailable" and error.value.status == 503
    assert "synthetic-private-credential-and-key" not in "".join(traceback.format_exception(error.value))
    if during_body:
        body.close.assert_called_once()


def test_put_hashes_and_verifies_exact_version_before_returning(store: S3Store) -> None:
    data = b"synthetic-audio-data"
    sent = []

    def put(**kwargs: Any) -> dict[str, Any]:
        sent.append((kwargs["Body"].read(), kwargs["ContentLength"], kwargs["Metadata"]))
        return {"VersionId": VERSION}

    store.client.put_object.side_effect = put
    reply = response(data)
    store.client.get_object.return_value = reply
    result = store.put(KEY, BytesIO(data), "audio/wav", 100)
    assert result.version == VERSION and result.bytes == len(data) and result.sha256 == hashlib.sha256(data).hexdigest()
    assert sent == [(data, len(data), {"sha256": result.sha256})] and reply["Body"].closed


@pytest.mark.parametrize("data", [b"", b"x" * 11])
def test_empty_or_oversize_put_never_writes(store: S3Store, data: bytes) -> None:
    with pytest.raises(DomainError) as error:
        store.put(KEY, BytesIO(data), "video/mp4", 10)
    assert error.value.code in ("empty_file", "file_too_large") and not store.client.mock_calls


@pytest.mark.parametrize("version", [None, "", "null", "bad\nversion", "x" * 1025])
def test_put_without_valid_version_is_not_published(store: S3Store, version: Any) -> None:
    store.client.put_object.return_value = {"VersionId": version}
    with pytest.raises(DomainError) as error:
        store.put(KEY, BytesIO(b"abc"), "image/png", 10)
    assert error.value.code == "storage_versioning_required"
    store.client.get_object.assert_not_called()


@pytest.mark.parametrize("wrong_version", [False, True])
def test_put_readback_mismatch_cannot_return_stored_object(store: S3Store, wrong_version: bool) -> None:
    store.client.put_object.return_value = {"VersionId": VERSION}
    reply = response(b"xyz")
    if wrong_version:
        reply["VersionId"] = "wrong"
    store.client.get_object.return_value = reply
    with pytest.raises(DomainError) as error:
        store.put(KEY, BytesIO(b"abc"), "image/png", 10)
    assert error.value.code == ("storage_response_invalid" if wrong_version else "object_hash_mismatch")
    assert reply["Body"].closed


def test_delete_requires_exact_version_ack(store: S3Store) -> None:
    store.client.delete_object.return_value = {"VersionId": VERSION, "ResponseMetadata": {"HTTPStatusCode": 204}}
    store.delete(KEY, VERSION)
    store.client.delete_object.assert_called_once_with(Bucket=store.bucket, Key=KEY, VersionId=VERSION)


@pytest.mark.parametrize("reply", [{}, {"VersionId": "wrong", "ResponseMetadata": {"HTTPStatusCode": 204}}, {"VersionId": VERSION, "ResponseMetadata": {"HTTPStatusCode": 200}}])
def test_uncertain_delete_ack_must_remain_pending(store: S3Store, reply: dict[str, Any]) -> None:
    store.client.delete_object.return_value = reply
    with pytest.raises(DomainError) as error:
        store.delete(KEY, VERSION)
    assert error.value.code == "storage_response_invalid"


@pytest.mark.parametrize("seconds", [0, -1, True, 1.5])
def test_invalid_signature_lifetime_rejected_before_sdk(store: S3Store, seconds: Any) -> None:
    with pytest.raises(DomainError) as error:
        store.signed_url(KEY, VERSION, seconds)
    assert error.value.code == "invalid_url_lifetime" and not store.client.mock_calls


@pytest.mark.parametrize("seconds", [1, 60, 901])
def test_signed_url_preserves_version_and_caps_lifetime(store: S3Store, seconds: int) -> None:
    store.client.generate_presigned_url.return_value = "https://synthetic.invalid/signed"
    assert store.signed_url(KEY, VERSION, seconds) == "https://synthetic.invalid/signed"
    store.client.generate_presigned_url.assert_called_once_with("get_object", Params={"Bucket": store.bucket, "Key": KEY, "VersionId": VERSION}, ExpiresIn=min(seconds, 900))


@pytest.mark.parametrize("operation", ["put", "delete", "signed_url", "check_private_versioned_bucket"])
def test_other_sdk_failures_are_redacted(store: S3Store, operation: str) -> None:
    failure = EndpointConnectionError(endpoint_url="https://synthetic-private.invalid/credential")
    getattr(store.client, {"put": "put_object", "delete": "delete_object", "signed_url": "generate_presigned_url", "check_private_versioned_bucket": "get_bucket_versioning"}[operation]).side_effect = failure
    with pytest.raises(DomainError) as error:
        if operation == "put":
            store.put(KEY, BytesIO(b"abc"), "audio/wav", 10)
        elif operation == "delete":
            store.delete(KEY, VERSION)
        elif operation == "signed_url":
            store.signed_url(KEY, VERSION, 60)
        else:
            store.check_private_versioned_bucket()
    assert error.value.code == "storage_unavailable"
    assert "synthetic-private.invalid" not in "".join(traceback.format_exception(error.value))


@pytest.mark.parametrize(("versioning", "grants", "policy", "code"), [
    ({}, [], {"IsPublic": False}, "storage_versioning_required"),
    ({"Status": "Enabled"}, [{"Grantee": {"URI": "http://acs.amazonaws.com/groups/global/AllUsers"}}], {"IsPublic": False}, "public_bucket_denied"),
    ({"Status": "Enabled"}, [], {"IsPublic": True}, "bucket_privacy_unverified"),
    ({"Status": "Enabled"}, [], {}, "bucket_privacy_unverified"),
])
def test_bucket_preflight_remains_fail_closed(store: S3Store, versioning: dict[str, Any], grants: list[dict[str, Any]], policy: dict[str, Any], code: str) -> None:
    store.client.get_bucket_versioning.return_value = versioning
    store.client.get_bucket_acl.return_value = {"Grants": grants}
    store.client.get_bucket_policy_status.return_value = {"PolicyStatus": policy}
    with pytest.raises(DomainError) as error:
        store.check_private_versioned_bucket()
    assert error.value.code == code
