from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Iterator
from io import BytesIO
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from generation_contracts.recovery_checkpoint import RecoveryCheckpoint
from generation_infrastructure import restore_verification as recovery
from generation_infrastructure.settings import Settings
from generation_infrastructure.storage import LocalDevelopmentStore, S3Store

ROOT = Path(__file__).resolve().parents[3]
KEY = b"independent synthetic recovery key"[:32]


def sample() -> dict[str, Any]:
    cases = json.loads((ROOT / "docs/contracts/fixtures.json").read_text(encoding="utf-8"))["cases"]
    return next(case["data"] for case in cases if case["valid"] and case["schema"] == "recovery-checkpoint.schema.json")


def signed() -> RecoveryCheckpoint:
    checkpoint = RecoveryCheckpoint.model_validate(sample())
    return checkpoint.model_copy(update={"signature": recovery._signature(checkpoint, KEY)})


def test_authenticated_private_document_and_independent_keys(tmp_path: Path) -> None:
    checkpoint = signed()
    path = tmp_path / "checkpoint.json"
    recovery.write_exclusive(path, json.dumps(checkpoint.model_dump()).encode())
    assert recovery.load_checkpoint(path, KEY) == checkpoint
    for name in ("objects", "tables", "secrets", "nonce", "created_at_ms"):
        value = checkpoint.model_dump()
        if name == "objects":
            value[name]["bytes"] = 1
        elif name == "tables":
            value[name][0]["rows"] += 1
        elif name == "secrets":
            value[name]["pepper_version"] += 1
        elif name == "nonce":
            value[name] = "b" * 32
        else:
            value[name] += 1
        with pytest.raises(recovery.RecoveryCheckError):
            recovery.authenticate_checkpoint(value, KEY)
    with pytest.raises(recovery.RecoveryCheckError, match="recovery_signature_invalid"):
        recovery.authenticate_checkpoint(checkpoint.model_dump(), b"x" * 32)
    assert not any(secret in path.read_text() for secret in (KEY.decode(), "postgresql://", "encrypted_auth", "object_key"))


@pytest.mark.parametrize("raw", [b'{"format_version":1,"format_version":1}', b'{"x":{"y":1,"y":2}}', b"{", b"[]", b"x" * (recovery.MAX_CHECKPOINT_BYTES + 1)], ids=["duplicate-root", "duplicate-nested", "incomplete", "array", "oversized"])
def test_checkpoint_rejects_duplicate_or_invalid_or_oversized_json(tmp_path: Path, raw: bytes) -> None:
    path = tmp_path / "checkpoint.json"
    path.write_bytes(raw)
    with pytest.raises(recovery.RecoveryCheckError, match="recovery_checkpoint_invalid"):
        recovery.load_checkpoint(path, KEY)


@pytest.mark.parametrize("encoded", [b"", b"#" * 44, base64.urlsafe_b64encode(b"x" * 31), base64.urlsafe_b64encode(b"x" * 33), b"x" * 1025])
def test_recovery_key_file_is_strict_bounded_and_redacted(tmp_path: Path, encoded: bytes) -> None:
    path = tmp_path / "key"
    path.write_bytes(encoded)
    with pytest.raises(recovery.RecoveryCheckError, match="^recovery_key_invalid$"):
        recovery.load_recovery_key(path)


def test_key_file_exclusive_create_and_no_secret_replacement(tmp_path: Path) -> None:
    path = tmp_path / "key"
    recovery.write_exclusive(path, base64.urlsafe_b64encode(KEY) + b"\n")
    assert recovery.load_recovery_key(path) == KEY
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        recovery.write_exclusive(path, b"replacement")
    assert path.read_bytes() == before
    with pytest.raises(recovery.RecoveryCheckError):
        recovery.load_recovery_key(tmp_path / "missing")


def test_failed_private_file_write_removes_only_owned_partial(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(_: int) -> None:
        raise OSError("synthetic sensitive failure")
    monkeypatch.setattr(recovery.os, "fsync", fail)
    path = tmp_path / "partial"
    with pytest.raises(OSError):
        recovery.write_exclusive(path, b"synthetic")
    assert not path.exists()


@pytest.mark.parametrize("field,value", [("rows", 0), ("rows", True), ("row_bytes", 32 * 1024**2 + 1), ("objects", 10001), ("object_bytes", 1024**4 + 1), ("seconds", 3601), ("seconds", 1.5)])
def test_limits_reject_invalid_and_unbounded_work(field: str, value: object) -> None:
    with pytest.raises(recovery.RecoveryCheckError, match="recovery_limits_invalid"):
        recovery.RecoveryLimits(**{field: value})


def test_recovery_key_cannot_reuse_master_or_pepper(test_settings: Settings) -> None:
    with pytest.raises(recovery.RecoveryCheckError, match="recovery_key_must_be_independent"):
        recovery._key(base64.urlsafe_b64decode(test_settings.encryption_key.get_secret_value()), test_settings)
    from pydantic import SecretStr
    settings = test_settings.model_copy(update={"credential_pepper": SecretStr(base64.urlsafe_b64encode(KEY).decode())})
    with pytest.raises(recovery.RecoveryCheckError, match="recovery_key_must_be_independent"):
        recovery._key(KEY, settings)


def test_deadline_is_enforced_without_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    budget = recovery.Budget(recovery.RecoveryLimits())
    monkeypatch.setattr(recovery.time, "monotonic", lambda: budget.deadline + 1)
    with pytest.raises(recovery.RecoveryCheckError, match="recovery_deadline_exceeded"):
        budget.check()


@pytest.mark.parametrize("damage", ["append", "truncate", "replace", "missing"])
def test_full_local_version_rejects_bad_size_hash_and_missing_file(tmp_path: Path, damage: str) -> None:
    store = LocalDevelopmentStore(tmp_path)
    obj = store.put("outputs/synthetic", BytesIO(b"original bytes"), "audio/wav", 1024)
    path = store._path(obj.key, obj.version)
    if damage == "missing":
        path.unlink()
    else:
        path.write_bytes({"append": b"original bytes EXTRA", "truncate": b"original", "replace": b"changed! bytes"}[damage])
    with pytest.raises(recovery.RecoveryCheckError, match="recovery_object_(mismatch|unavailable)"):
        recovery._inspect_objects(store, [(obj.key, obj.version, obj.bytes, obj.sha256, obj.media_type)], recovery.Budget(recovery.RecoveryLimits()))


@pytest.mark.parametrize("version", [None, "", "null"])
def test_missing_object_version_does_not_read_storage(version: object) -> None:
    store = Mock()
    with pytest.raises(recovery.RecoveryCheckError, match="recovery_object_version_missing"):
        recovery._inspect_objects(store, [("synthetic", version, 3, "a" * 64, "audio/wav")], recovery.Budget(recovery.RecoveryLimits()))
    store.read_complete.assert_not_called()


@pytest.mark.parametrize("blocks", [[b"short"], [b"x" * (1024**2 + 1)], ["not bytes"], [b"original bytes", b"extra"]], ids=["truncated", "oversized-chunk", "wrong-type", "extra-bytes"])
def test_invalid_stream_always_closes(blocks: list[Any]) -> None:
    closed = []
    class Reader:
        def read_complete(self, key: str, version: str, size: int) -> Iterator[bytes]:
            try:
                yield from blocks
            finally:
                closed.append(True)
    with pytest.raises(recovery.RecoveryCheckError, match="recovery_object_mismatch"):
        recovery._inspect_objects(Reader(), [("synthetic", "version", 14, hashlib.sha256(b"original bytes").hexdigest(), "audio/wav")], recovery.Budget(recovery.RecoveryLimits()))
    assert closed == [True]


def test_s3_complete_read_rejects_valid_range_that_hides_extra_object_bytes() -> None:
    store = S3Store.__new__(S3Store)
    store.bucket, store.client = "synthetic", Mock()
    body = BytesIO(b"abc")
    store.client.get_object.return_value = {"Body": body, "VersionId": "original", "ContentLength": 3, "ContentRange": "bytes 0-2/4", "ResponseMetadata": {"HTTPStatusCode": 206}}
    with pytest.raises(recovery.RecoveryCheckError, match="recovery_object_unavailable"):
        recovery._inspect_objects(store, [("synthetic", "original", 3, hashlib.sha256(b"abc").hexdigest(), "audio/wav")], recovery.Budget(recovery.RecoveryLimits()))
    assert body.closed and store.client.get_object.call_count == 1
