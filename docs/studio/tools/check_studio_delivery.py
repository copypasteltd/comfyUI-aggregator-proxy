"""Read-only verification of the reviewed Studio mirror and published receipts."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPOSITORY = ROOT / "aggregator-service"
DEST = REPOSITORY / "app/apps/studio_web"
MEDIA = ROOT / "docs/media/studio-131"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def main():
    source = read(DEST / "delivery-source.json")
    for name, digest in source["source_files"].items():
        assert sha(DEST / name) == sha(ROOT / "infinite-canvas" / name) == digest
    progress = (ROOT / "docs/10-开发进度与交付记录.md").read_text(encoding="utf-8")
    assert set(re.findall(r"^\| (F-\d{2}) \|", progress, re.M)) == {f"F-{n:02}" for n in range(1, 47)}
    media = read(MEDIA / "media-manifest.json")
    assert media["passed"] and len(media["artifacts"]) == 8
    published = 0
    for artifact in media["artifacts"]:
        assert artifact["browser_download_verified"] and artifact["cloud_lifetime_seconds"] == 28800
        if artifact["file"]:
            file = MEDIA / artifact["file"]
            assert file.stat().st_size == artifact["sample_bytes"] and sha(file) == artifact["sample_sha256"]
            published += 1
        else:
            assert artifact["metadata"]["media_type"] == "model/gltf-binary"
    assert published == 7
    for ref in media["reports"]:
        assert sha(ROOT / ref["file"]) == ref["sha256"]
    browser = read(ROOT / media["reports"][1]["file"])
    assert browser["passed"] and len(browser["checks"]) == 9 and all(browser["cleanup"].values())
    assert len(browser["artifacts"]) == 8 and all(item["download_verified"] for item in browser["artifacts"])
    for screenshot in browser["screenshots"]:
        assert sha(ROOT / screenshot["file"]) == screenshot["sha256"]
    tour = read(MEDIA / "studio-focused-tour-manifest.json")
    assert tour["passed"] and sha(ROOT / tour["source_report"]) == tour["source_report_sha256"]
    for item in tour["files"]:
        file = MEDIA / item["file"]
        assert item["fully_decoded"] and file.stat().st_size == item["bytes"] and sha(file) == item["sha256"]
    calls = read(MEDIA / "api-calls.json")["calls"]
    assert len(calls) == 8
    for call in calls:
        assert call["accepted_status"] == 202 and call["terminal_job"]["status"] == "succeeded"
        assert call["authentication"] == "Bearer <business-key: omitted>"
        artifact = next(item for item in media["artifacts"] if item["mode"] == call["mode"])
        assert call["content_metadata"] == artifact["metadata"]
    api = read(ROOT / "docs/research/studio-implementation/live-api-131.json")
    revoked = [item for item in api["checks"] if item["name"] == "temporary validation Key disabled"]
    assert api["passed"] and len(revoked) == 2 and all(item["detail"]["revoked_access"] == 401 for item in revoked)
    for name in ("README.md", "README.en.md", "media-manifest.json", "api-calls.json"):
        assert sha(MEDIA / name) == sha(REPOSITORY / "docs/studio/media/studio-131" / name)
    evidence = ROOT / "docs/research/studio-implementation"
    notes = read(evidence / "storage-validation-notes-1317.json")
    receipts = notes["passing_package_evidence"]
    for name in ("units", "types", "build"):
        result = read(evidence / receipts[name])
        assert result["passed"] and result["source_unchanged"] and result["source_after"] == notes["source_sha256"]
    ui = read(evidence / receipts["browser"])
    storage = read(evidence / receipts["storage"])
    recording = read(evidence / receipts["final_recording"])
    assert ui["passed"] and len(ui["checks"]) == 18 and ui["static_artifact_unchanged"]
    for result in (storage, recording):
        assert result["passed"] and len(result["checks"]) == 22 and all(result["cleanup"].values())
        assert result["static_artifact_sha256"] == ui["static_artifact_sha256"]
        for screenshot in result["screenshots"]:
            assert sha(ROOT / screenshot["file"]) == screenshot["sha256"]
    upgraded = read(evidence / receipts["public_upgrade"])
    assert upgraded["passed"] and len(upgraded["checks"]) == 3 and all(upgraded["cleanup"].values()) and not upgraded["blocked_mutations"]
    health = read(evidence / receipts["health"])
    assert health["passed"] and health["studio"] == "studio-20261030.131.7" and health["original_worker_pid_unchanged"]
    assert len(health["cors"]) == 2 and all(item["status"] == 200 and item["allow_origin"] in ("*", item["requested_origin"]) for item in health["cors"])
    recovery = read(MEDIA / "studio-storage-recovery-verified-manifest.json")
    assert recovery["passed"] and sha(ROOT / recovery["source_report"]) == recovery["source_report_sha256"]
    for item in recovery["files"]:
        assert item["fully_decoded"] and sha(MEDIA / item["file"]) == item["sha256"] and (MEDIA / item["file"]).stat().st_size == item["bytes"]
        assert sha(MEDIA / item["file"]) == sha(REPOSITORY / "docs/studio/media/studio-131" / item["file"])
    assert all(not read(evidence / name)["passed"] for name in notes["failed_reports_preserved"])
    # Keep the 131.7 evidence above as history; verify the final 131.9 package separately.
    current = read(evidence / "task-backup-validation-notes-1319.json")
    final = current["passing_package_evidence"]
    web = ROOT / "infinite-canvas/web"
    inputs = ["package.json", "package-lock.json", "tsconfig.json", "vite.config.ts", "index.html", "../VERSION", "../CHANGELOG.md"]
    inputs.extend(file.relative_to(web).as_posix() for folder in ("src", "tests", "public") for file in (web / folder).rglob("*") if file.is_file())
    frozen = hashlib.sha256()
    for name in sorted(inputs):
        frozen.update(name.encode()); frozen.update(b"\0"); frozen.update((web / name).read_bytes())
    assert frozen.hexdigest() == current["source_sha256"]
    for name in ("units", "types", "build"):
        result = read(evidence / final[name])
        assert result["passed"] and result["source_unchanged"] and result["source_after"] == current["source_sha256"]
    unit_log = (evidence / read(evidence / final["units"])["log"]).read_text(encoding="utf-8")
    assert re.search(r"^# tests 126$", unit_log, re.M) and re.search(r"^# fail 0$", unit_log, re.M)
    current_ui = read(evidence / final["browser"])
    current_storage = read(evidence / final["storage"])
    journal = read(evidence / final["task_backup"])
    for result, count in ((current_ui, 18), (current_storage, 22), (journal, 25)):
        assert result["passed"] and len(result["checks"]) == count and all(result["cleanup"].values())
        assert result.get("static_artifact_unchanged", True) and result["static_artifact_sha256"] == current_ui["static_artifact_sha256"]
        assert not result.get("page_errors")
        for screenshot in result["screenshots"]:
            assert sha(ROOT / screenshot["file"]) == screenshot["sha256"]
    dist = ROOT / "infinite-canvas/web/dist"
    assert {file.relative_to(dist).as_posix(): sha(file) for file in dist.rglob("*") if file.is_file()} == current_ui["static_artifact_sha256"]
    assert journal["browser_storage_version"] == 3 and all(item["method"] == "GET" for item in journal["business_requests"])
    for name, version, release in (("public_v2_v3_upgrade", 2, "studio-20261030.131.8"), ("public_current_restart", 3, "studio-20261030.131.9")):
        result = read(evidence / final[name])
        assert result["passed"] and len(result["checks"]) == 3 and all(result["cleanup"].values())
        assert result["preexisting_database_version"] == version and not result["blocked_mutations"] and not result["page_errors"]
        assert result["checks"][0]["detail"]["after_version"] == 3 and result["checks"][0]["detail"]["static_release"] == release
        for screenshot in result["screenshots"]:
            assert sha(ROOT / screenshot["file"]) == screenshot["sha256"]
    current_health = read(evidence / final["health"])
    assert current_health["passed"] and current_health["studio"] == "studio-20261030.131.9" and current_health["original_worker_pid_unchanged"]
    assert all(status == "active" for status in current_health["services"].values())
    assert len(current_health["cors"]) == 2 and all(item["status"] == 200 and item["allow_origin"] in ("*", item["requested_origin"]) for item in current_health["cors"])
    package = read(evidence / final["static_build"])
    promotion = read(evidence / final["static_promotion"])
    assert package["passed"] and package["file_count"] == len(current_ui["static_artifact_sha256"]) == 96
    assert package["source_sha256"] == promotion["source_sha256"] == current["source_sha256"]
    assert package["archive_sha256"] == promotion["archive_sha256"] and not promotion["backend_restarted"] and not promotion["old_ui_roundtrip_performed"]
    backup_tour = read(MEDIA / "studio-task-backup-verified-manifest.json")
    assert backup_tour["passed"] and backup_tour["source_report"].endswith(final["task_backup"]) and sha(ROOT / backup_tour["source_report"]) == backup_tour["source_report_sha256"]
    for item in backup_tour["files"]:
        file = MEDIA / item["file"]
        assert item["fully_decoded"] and file.stat().st_size == item["bytes"] and sha(file) == item["sha256"]
        assert sha(file) == sha(REPOSITORY / "docs/studio/media/studio-131" / item["file"])
    assert all(not read(evidence / name)["passed"] for name in current["failed_reports_preserved"])
    assert current["full_acceptance"] == "HOLD" and current["no_new_gpu_submission"] and current["no_disabled_key_reenabled"]
    print(json.dumps({"passed": True, "at": datetime.now(UTC).isoformat(),
                      "scope": "Read-only source-mirror, public media/report/screenshot hashes, eight call receipts, all46 IDs and temporary Key revocation; not full runtime/phone/fleet acceptance",
                      "source_files_verified": len(source["source_files"]), "actual_gpu_modes": 8,
                      "browser_checks": 9, "public_samples": published, "large_glb_metadata_only": True,
                      "requirements_retained": 46, "temporary_keys_disabled": 2, "storage_checks": 22,
                      "current_ui_checks": 18, "task_backup_checks": 25, "current_unit_tests": 126,
                      "public_storage_upgrade_checks": 3, "current_static_release": "studio-20261030.131.9", "browser_storage_version": 3,
                      "browser_storage_downgrade_supported": False, "virtualization_used": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
