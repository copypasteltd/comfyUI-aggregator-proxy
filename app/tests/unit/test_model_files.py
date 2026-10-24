from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

import pytest
from generation_contracts.model_files import ModelFileCatalog, ModelFileRegistration
from generation_domain.errors import DomainError
from generation_domain.model_files import model_file_path
from generation_domain.values import fingerprint
from pydantic import ValidationError
from windows_worker import model_files
from windows_worker.model_files import discover_models, inspect_model, readonly_model


def synthetic_file(root: Path, relative: str = "models/checkpoints/synthetic.safetensors") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"Synthetic opaque bytes; this is not a model.\n" * 100)
    return path


def payload(root: Path) -> dict[str, Any]:
    catalog = discover_models(root)
    return {**catalog["entries"][0], "catalog_digest": catalog["catalog_digest"]}


@pytest.mark.parametrize(
    "name",
    [
        "../outside.pt",
        "models/../outside.pt",
        "C:/models/file.pt",
        "models/checkpoints/CON.pt",
        "models/checkpoints/.hidden.pt",
        "models/checkpoints/a?.pt",
        "models/checkpoints/a.pt ",
        "models/checkpoints/a.py",
        "models/checkpoints/a\u202e.pt",
        "models/checkpoints/e\u0301.pt",
    ],
)
def test_model_path_rejects_ambiguous_or_outside_names(name: str) -> None:
    with pytest.raises(DomainError):
        model_file_path(name)


def test_directory_discovers_metadata_without_opening_weight_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = synthetic_file(tmp_path)
    synthetic_file(tmp_path, "models/loras/a.GGUF")
    (tmp_path / "models/.private").mkdir()
    (tmp_path / "models/README.txt").write_text("not a weight")

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Discovery must not read model contents")

    monkeypatch.setattr(model_files, "readonly_model", forbidden)
    result = ModelFileCatalog.model_validate(discover_models(tmp_path))
    assert result.status == "ready" and result.skipped_entries == 2
    assert [e.kind for e in result.entries] == ["checkpoint", "lora"]
    assert result.entries[0].bytes == path.stat().st_size
    assert result.catalog_digest == fingerprint(result.model_dump(exclude={"catalog_digest"}))


@pytest.mark.parametrize(
    "limits,reason",
    [
        ({"maximum_entries": 1}, "entry_limit"),
        ({"maximum_files": 1}, "file_limit"),
        ({"maximum_depth": 0}, "depth_limit"),
        ({"seconds": -1}, "time_budget"),
    ],
)
def test_bounded_scan_reports_partial_instead_of_empty_complete(
    tmp_path: Path, limits: dict[str, Any], reason: str
) -> None:
    synthetic_file(tmp_path)
    synthetic_file(tmp_path, "models/vae/second.bin")
    result = discover_models(tmp_path, **limits)
    assert result["status"] == "partial" and result["reason"] == reason


def test_missing_model_directory_is_unavailable(tmp_path: Path) -> None:
    result = discover_models(tmp_path)
    assert result["status"] == "unavailable" and result["reason"] == "not_found"


def test_selected_hash_is_streamed_and_matches_exact_opaque_bytes(tmp_path: Path) -> None:
    path = synthetic_file(tmp_path)
    request = payload(tmp_path)
    result = inspect_model(tmp_path, request, maximum_bytes=path.stat().st_size, seconds=10)
    assert result["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert (
        result["bytes"] == request["bytes"]
        and result["catalog_digest"] == request["catalog_digest"]
    )


@pytest.mark.parametrize("change", ["size", "mtime", "removed", "hardlink"])
def test_selected_file_change_or_hardlink_is_rejected(tmp_path: Path, change: str) -> None:
    path = synthetic_file(tmp_path)
    request = payload(tmp_path)
    if change == "size":
        path.write_bytes(b"changed")
    elif change == "mtime":
        info = path.stat()
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 10_000_000))
    elif change == "hardlink":
        os.link(path, tmp_path / "alias.bin")
        result = discover_models(tmp_path)
        assert result["status"] == "partial" and not result["entries"]
    else:
        path.unlink()
    with pytest.raises(DomainError) as error:
        inspect_model(tmp_path, request, maximum_bytes=10**6, seconds=10)
    assert error.value.code in ("model_file_changed", "model_file_not_allowed")


@pytest.mark.parametrize("maximum,seconds", [(1, 10), (10**6, -1)])
def test_hash_size_and_time_budgets_are_enforced(
    tmp_path: Path, maximum: int, seconds: int
) -> None:
    synthetic_file(tmp_path)
    with pytest.raises(DomainError) as error:
        inspect_model(tmp_path, payload(tmp_path), maximum_bytes=maximum, seconds=seconds)
    assert error.value.code == "model_hash_budget_exceeded"


@pytest.mark.skipif(os.name != "nt", reason="Windows native sharing semantics")
def test_native_handle_denies_write_and_rename_until_closed(tmp_path: Path) -> None:
    path = synthetic_file(tmp_path)
    with readonly_model(path) as stream:
        assert stream.read(9) == b"Synthetic"
        with pytest.raises(OSError):
            path.write_bytes(b"unsafe overwrite")
        with pytest.raises(OSError):
            path.rename(path.with_name("replacement.bin"))
    path.rename(path.with_name("replacement.bin"))


def test_tampered_catalog_digest_and_duplicate_paths_rejected(tmp_path: Path) -> None:
    synthetic_file(tmp_path)
    result = discover_models(tmp_path)
    with pytest.raises(ValidationError):
        ModelFileCatalog.model_validate({**result, "catalog_digest": "0" * 64})
    duplicate = {**result, "entries": result["entries"] * 2, "scanned_entries": 20}
    duplicate["catalog_digest"] = fingerprint(
        {k: v for k, v in duplicate.items() if k != "catalog_digest"}
    )
    with pytest.raises(ValidationError):
        ModelFileCatalog.model_validate(duplicate)


def test_browser_cannot_supply_proof_or_scope_in_registration() -> None:
    with pytest.raises(ValidationError):
        ModelFileRegistration.model_validate(
            {"name": "forged", "kind": "checkpoint", "sha256": "1" * 64, "tenant_id": "foreign"}
        )


@pytest.mark.skipif(os.name != "nt", reason="Windows native reparse point")
def test_directory_junction_is_not_followed_or_hashable(tmp_path: Path) -> None:
    import _winapi

    root = tmp_path / "comfy"
    root.mkdir()
    external = tmp_path / "external"
    target = synthetic_file(external)
    link = root / "models"
    _winapi.CreateJunction(str(external / "models"), str(link))
    try:
        catalog = discover_models(root)
        assert catalog["status"] == "unavailable" and not catalog["entries"]
        info = target.stat()
        request = {
            "relative_path": "models/checkpoints/synthetic.safetensors",
            "bytes": info.st_size,
            "modified_at_ns": info.st_mtime_ns,
            "catalog_digest": "1" * 64,
        }
        with pytest.raises(DomainError):
            inspect_model(root, request, maximum_bytes=10**6, seconds=10)
    finally:
        link.rmdir()
    assert target.exists()


def test_hash_deadline_is_checked_after_reading_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    synthetic_file(tmp_path)
    data = payload(tmp_path)
    ticks = iter([0.0, 0.0, 2.0])
    monkeypatch.setattr(model_files, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    with pytest.raises(DomainError) as expired:
        inspect_model(tmp_path, data, maximum_bytes=10**6, seconds=1)
    assert expired.value.code == "model_hash_budget_exceeded"
