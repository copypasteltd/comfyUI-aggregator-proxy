from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import stat
import subprocess
import warnings
import zipfile
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("native_candidate_installer", ROOT / "scripts/release_bundle.py")
assert SPEC and SPEC.loader
bundle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bundle)
FIXTURES = json.loads((ROOT.parent / "docs/contracts/fixtures.json").read_text(encoding="utf-8"))["cases"]
CASES = [case for case in FIXTURES if case["schema"] == "release-manifest.schema.json"]


def candidate(files: dict[str, bytes] | None = None) -> tuple[dict, dict[str, bytes]]:
    data = copy.deepcopy(next(case["data"] for case in CASES if case["valid"]))
    content = files or {"wheels/example.whl": b"synthetic wheel, not installable"}
    data["files"] = [{"path": name, "bytes": len(value), "sha256": hashlib.sha256(value).hexdigest()} for name, value in content.items()]
    return data, content


def archive(tmp_path: Path, files: dict[str, bytes] | None = None, *, mutate=None, extra=None) -> tuple[Path, str]:
    manifest, content = candidate(files)
    if mutate:
        mutate(manifest)
    target = tmp_path / "candidate.zip"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as output:
            output.writestr(bundle.MANIFEST, json.dumps(manifest))
            for name, data in content.items():
                output.writestr(name, data)
            if extra:
                for name, data in extra:
                    output.writestr(name, data)
    return target, bundle.digest_file(target)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_stdlib_manifest_validation_matches_frozen_contract(case: dict[str, Any]) -> None:
    if case["valid"]:
        assert bundle.validate_manifest(case["data"]) == case["data"]
    else:
        with pytest.raises(bundle.ReleaseError):
            bundle.validate_manifest(case["data"])


@pytest.mark.parametrize("name", ["/x", "C:/x", "x\\y", "../x", "a/../x", "a/./x", "a//x", "a/", "x:y", "NUL", "con.txt", "COM1.txt", "Lpt9", "a/aux.json", "x ", " x", "x.", "a/ b/x", "x\x00", "x\n", "x?", "x*", 'x"', "x|", "é", "a" * 241])
def test_windows_unsafe_members_are_rejected(name: str) -> None:
    with pytest.raises(bundle.ReleaseError, match="unsafe_member"):
        bundle.safe_member(name)


@pytest.mark.parametrize("name", ["admin/index.html", "wheels/pydantic-2.12.5-py3-none-any.whl", "migrations/versions/00_a.py", "gateway/LICENSE"])
def test_windows_regular_artifact_names_are_accepted(name: str) -> None:
    assert bundle.safe_member(name) == name


@pytest.mark.parametrize("text", ['{"x":1,"x":2}', '{"a":{"x":1,"x":2}}', '{"x":NaN}', '{"x":Infinity}', '{', '"\ud800"'])
def test_duplicate_or_nonfinite_json_is_rejected(text: str) -> None:
    if text == '"\ud800"':
        with pytest.raises(UnicodeError):
            bundle.strict_json(text.encode("utf-8"))
    else:
        with pytest.raises(bundle.ReleaseError):
            bundle.strict_json(text)


def test_complete_archive_verifies_without_creating_files(tmp_path: Path) -> None:
    source, digest = archive(tmp_path)
    before = set(tmp_path.iterdir())
    assert len(bundle.verify_archive(source, digest)["files"]) == 1
    assert set(tmp_path.iterdir()) == before


@pytest.mark.parametrize("change", ["none", "too_many", "digest"])
def test_candidate_checks_full_contract_set_and_rejects_overflow_or_tampering(tmp_path: Path, change: str) -> None:
    manifest, _ = candidate()
    contracts = json.loads((ROOT / "config/contract-hashes.json").read_text(encoding="utf-8"))
    assert 65 <= len(contracts) <= 128
    sources = {"app/synthetic.py": "a" * 64}
    files = {"provenance/uv.lock": b"synthetic lock", "runtime-requirements.txt": b"synthetic requirements", "gateway/sub2api-provider.exe": b"synthetic provider", "admin/index.html": b"synthetic admin", "gateway/LICENSE": b"synthetic license", "alembic.ini": b"synthetic migrations", "scripts/release-entrypoint.py": b"", "scripts/migrate.py": b"", "scripts/release_bundle.py": b""}
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    (tmp_path / "config").mkdir()
    (tmp_path / "config/contract-hashes.json").write_text(json.dumps(contracts), encoding="utf-8")
    (tmp_path / "provenance/source-files.json").write_text(json.dumps(sources), encoding="utf-8")
    (tmp_path / "wheels").mkdir()
    journal = b"synthetic journal source"
    with zipfile.ZipFile(tmp_path / "wheels/render_windows_worker-0.1.0-py3-none-any.whl", "w") as wheel:
        wheel.writestr("windows_worker/journal.py", journal)
    manifest["contracts"] = {"version": "0.4.0", "sha256": bundle.canonical_digest(contracts)}
    manifest["source_tree_sha256"] = bundle.canonical_digest(sources)
    manifest["runtime"]["lock_sha256"] = hashlib.sha256(files["provenance/uv.lock"]).hexdigest()
    manifest["runtime"]["requirements_sha256"] = hashlib.sha256(files["runtime-requirements.txt"]).hexdigest()
    manifest["gateway"]["provider_binary_sha256"] = hashlib.sha256(files["gateway/sub2api-provider.exe"]).hexdigest()
    manifest["database"]["migrations_sha256"] = bundle.canonical_digest({})
    manifest["worker"]["journal_source_sha256"] = hashlib.sha256(journal).hexdigest()
    if change == "too_many":
        contracts = {f"contract-{index}.json": "a" * 64 for index in range(129)}
        (tmp_path / "config/contract-hashes.json").write_text(json.dumps(contracts), encoding="utf-8")
        manifest["contracts"]["sha256"] = bundle.canonical_digest(contracts)
    elif change == "digest":
        manifest["contracts"]["sha256"] = "f" * 64
    if change == "none":
        bundle.verify_candidate(tmp_path, manifest)
    else:
        with pytest.raises(bundle.ReleaseError, match="candidate_contract_provenance"):
            bundle.verify_candidate(tmp_path, manifest)


def test_archive_digest_is_checked_before_zip_parsing(tmp_path: Path) -> None:
    source, _ = archive(tmp_path)
    with pytest.raises(bundle.ReleaseError, match="archive_digest_mismatch"):
        bundle.verify_archive(source, "f" * 64)


def test_invalid_archive_never_creates_install_target(tmp_path: Path) -> None:
    source, _ = archive(tmp_path)
    target = tmp_path / "uncreated"
    with pytest.raises(bundle.ReleaseError, match="archive_digest_mismatch"):
        bundle.install_archive(source, "f" * 64, target)
    assert not target.exists()


def test_encrypted_member_flag_is_rejected(tmp_path: Path) -> None:
    source, _ = archive(tmp_path)
    data = bytearray(source.read_bytes())
    for signature, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        position = data.index(signature) + offset
        data[position] |= 1
    source.write_bytes(data)
    with pytest.raises(bundle.ReleaseError, match="archive_special_member"):
        bundle.verify_archive(source, bundle.digest_file(source))


@pytest.mark.parametrize("extra", [[("unknown.txt", b"x")], [("wheels/EXAMPLE.whl", b"x")], [("wheels/example.whl", b"x")], [("../outside", b"x")]])
def test_unlisted_case_duplicate_and_traversal_members_fail(tmp_path: Path, extra) -> None:
    source, digest = archive(tmp_path, extra=extra)
    with pytest.raises(bundle.ReleaseError):
        bundle.verify_archive(source, digest)
    assert not (tmp_path.parent / "outside").exists()


@pytest.mark.parametrize("mutation", [lambda data: data["files"][0].update(sha256="f" * 64), lambda data: data["files"][0].update(bytes=999), lambda data: data["files"].append({"path": "missing", "bytes": 0, "sha256": "0" * 64})])
def test_hash_size_and_missing_members_fail(tmp_path: Path, mutation) -> None:
    source, digest = archive(tmp_path, mutate=mutation)
    with pytest.raises(bundle.ReleaseError):
        bundle.verify_archive(source, digest)


@pytest.mark.parametrize("mode", [stat.S_IFLNK | 0o777, stat.S_IFIFO | 0o600, stat.S_IFDIR | 0o755])
def test_links_special_and_directory_members_fail(tmp_path: Path, mode: int) -> None:
    source, _ = archive(tmp_path)
    info = zipfile.ZipInfo("special")
    info.create_system = 3
    info.external_attr = mode << 16
    with zipfile.ZipFile(source, "a") as output:
        output.writestr(info, b"target")
    with pytest.raises(bundle.ReleaseError, match="archive_special_member"):
        bundle.verify_archive(source, bundle.digest_file(source))


def test_compression_bomb_is_rejected_before_read(tmp_path: Path) -> None:
    source, digest = archive(tmp_path, {"bomb.txt": b"0" * 1000000})
    with pytest.raises(bundle.ReleaseError, match="archive_size_limit"):
        bundle.verify_archive(source, digest)


def test_member_count_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source, digest = archive(tmp_path, {"one": b"1", "two": b"2"})
    monkeypatch.setattr(bundle, "MAX_FILES", 1)
    with pytest.raises(bundle.ReleaseError, match="archive_file_count"):
        bundle.verify_archive(source, digest)


def test_file_cannot_also_be_a_directory_prefix() -> None:
    data, _ = candidate({"a": b"x", "a/b": b"y"})
    with pytest.raises(bundle.ReleaseError, match="member_parent_is_file"):
        bundle.validate_manifest(data)


def test_existing_target_is_never_modified(tmp_path: Path) -> None:
    source, digest = archive(tmp_path)
    target = tmp_path / "installed"
    target.mkdir()
    (target / "journal").write_text("unknown GPU remains occupied", encoding="utf-8")
    with pytest.raises(bundle.ReleaseError, match="destination_exists"):
        bundle.install_archive(source, digest, target)
    assert (target / "journal").read_text() == "unknown GPU remains occupied"


def test_install_failure_removes_only_its_new_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Seven explicit locked entries pass requirement validation; injected venv failure
    # occurs before pip and must not remove a sibling's durable state.
    requirements = "\n".join(f"{name}==0.1.0 --hash=sha256:{'0' * 64}" for name in sorted(bundle.PROJECTS)) + "\n"
    source, digest = archive(tmp_path, {"runtime-requirements.txt": requirements.encode()}, mutate=lambda data: data["runtime"].update(requirements_sha256=hashlib.sha256(requirements.encode()).hexdigest()))
    sibling = tmp_path / "worker-state"
    sibling.mkdir()
    (sibling / "journal").write_bytes(b"keep")

    def fail(*args, **kwargs) -> None:
        raise subprocess.SubprocessError("synthetic failure")

    monkeypatch.setattr(bundle.venv.EnvBuilder, "create", fail)
    monkeypatch.setattr(bundle, "verify_candidate", lambda *args: None)
    target = tmp_path / "installed"
    with pytest.raises(subprocess.SubprocessError):
        bundle.install_archive(source, digest, target)
    assert not target.exists()
    assert (sibling / "journal").read_bytes() == b"keep"


def test_owned_cleanup_rejects_a_different_parent(tmp_path: Path) -> None:
    target = tmp_path / "keep"
    target.mkdir()
    with pytest.raises(bundle.ReleaseError, match="cleanup_ownership_mismatch"):
        bundle.remove_created(target, tmp_path / "other")
    assert target.is_dir()


def test_payload_tampering_and_extra_files_are_detected(tmp_path: Path) -> None:
    data, files = candidate()
    for name, value in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
    (tmp_path / bundle.MANIFEST).write_text(json.dumps(data), encoding="utf-8")
    bundle.verify_payload(tmp_path)
    (tmp_path / "extra.txt").write_text("x")
    with pytest.raises(bundle.ReleaseError, match="payload_inventory_mismatch"):
        bundle.verify_payload(tmp_path)
    (tmp_path / "extra.txt").unlink()
    (tmp_path / "wheels/example.whl").write_bytes(b"changed")
    with pytest.raises(bundle.ReleaseError, match="payload_digest_mismatch"):
        bundle.verify_payload(tmp_path)


def test_installer_clears_development_and_index_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("PYTHONPATH", "GEN_DATABASE_URL", "UV_INDEX_URL", "PIP_EXTRA_INDEX_URL", "VIRTUAL_ENV"):
        monkeypatch.setenv(key, "synthetic-private-value")
    environment = bundle.clean_environment()
    assert not {"PYTHONPATH", "GEN_DATABASE_URL", "UV_INDEX_URL", "PIP_EXTRA_INDEX_URL", "VIRTUAL_ENV"} & environment.keys()
    assert environment["PIP_NO_INDEX"] == "1"


def test_audio_candidate_rejects_missing_tampered_and_extra_media_lock_entries(tmp_path: Path) -> None:
    lock = json.loads((ROOT / "config/media-tools.lock.json").read_text(encoding="utf-8"))
    for name in lock["files"]:
        path = tmp_path / "media" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic tool or license")
        lock["files"][name] = {"bytes": path.stat().st_size, "sha256": bundle.digest_file(path)}
    config = tmp_path / "config/media-tools.lock.json"
    config.parent.mkdir()
    config.write_text(json.dumps(lock), encoding="utf-8")
    assert bundle.verify_media_runtime(tmp_path) == tmp_path / "media/bin"
    decoder = tmp_path / "media/bin/ffmpeg.exe"
    decoder.write_bytes(b"tampered")
    with pytest.raises(bundle.ReleaseError, match="candidate_media_provenance"):
        bundle.verify_media_runtime(tmp_path)
    decoder.unlink()
    with pytest.raises(bundle.ReleaseError, match="candidate_media_provenance"):
        bundle.verify_media_runtime(tmp_path)
    lock["files"]["../foreign.exe"] = lock["files"]["bin/ffprobe.exe"]
    config.write_text(json.dumps(lock), encoding="utf-8")
    with pytest.raises(bundle.ReleaseError):
        bundle.verify_media_runtime(tmp_path)
