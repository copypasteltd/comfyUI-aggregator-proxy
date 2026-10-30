"""Read-only verification of the reviewed Studio mirror and published receipts."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import UTC, datetime
from functools import lru_cache
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


@lru_cache
def frozen_sources(revision):
    """Historical evidence stays bound to its actual Git bytes as current code evolves."""
    assert re.fullmatch(r"[a-f0-9]{40}", revision)
    entries = subprocess.check_output(["git", "ls-tree", "-r", "-z", revision, "--", "app"], cwd=REPOSITORY).split(b"\0")
    result = {}
    process = subprocess.Popen(["git", "cat-file", "--batch"], cwd=REPOSITORY, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    try:
        for entry in entries:
            if not entry:
                continue
            metadata, name = entry.split(b"\t", 1)
            mode, kind, object_id = metadata.split()
            assert kind == b"blob" and mode in (b"100644", b"100755")
            process.stdin.write(object_id + b"\n"); process.stdin.flush()
            header = process.stdout.readline().split()
            assert header[0] == object_id and header[1] == b"blob"
            content = process.stdout.read(int(header[2])); assert process.stdout.read(1) == b"\n"
            result[name.decode("utf-8")] = hashlib.sha256(content).hexdigest()
    finally:
        process.stdin.close(); process.stdout.close()
        assert process.wait(timeout=30) == 0
    return result


def frozen_sha(revision, name):
    return frozen_sources(revision)[name]


def check_device_pool_delivery():
    folder = REPOSITORY / 'docs/evidence/studio-release-137'
    focused = read(folder / 'device-pool-1c1057f48daa49689f24a09a0dea7505.json')
    compatibility = read(folder / 'device-pool-ed2043ab0dd24f0eb08d87ea86b52f2f.json')
    for result, count in ((focused, 38), (compatibility, 1695)):
        assert result['passed'] and result['sources_unchanged'] and result['test_database_backup_verified']
        assert result['counts'] == {'tests': count, 'failures': 0, 'errors': 0, 'skipped': 0}
        assert not result['database_durability']['crash_durability_tested'] and not result['real_resource_acceptance']
        for name, digest in result['source_sha256'].items():
            assert sha(REPOSITORY / name) == digest
    live = read(folder / 'live-delivery-1371.json')
    build = read(folder / 'build-1371.json')
    assert live['passed'] and live['git_inputs_rechecked'] and live['source_revision'] == build['application_source_revision']
    assert len(live['runtime_source_files']) == live['runtime_source_files_count'] == build['source_files'] == 564
    for name, digest in live['runtime_source_files'].items():
        assert sha(REPOSITORY / 'app' / name) == digest == frozen_sha(live['source_revision'], 'app/' + name)
    assert live['backend'] == build['release_id'] == 'linux-20261030.137.1' and live['studio'] == 'studio-20261030.136.1'
    assert all(state == 'active' for state in live['services'].values()) and live['original_worker_pid_unchanged']
    assert live['database']['database_head'] == '04d137a00001'
    assert live['database']['fsync'] == live['database']['synchronous_commit'] == 'on'
    assert live['database']['balanced_models'] == live['database']['balanced_jobs'] == 0
    assert len(live['responses']) == 6 and len(live['cors']) == 2 and len(live['admin_model_checks']) == 13
    assert all(value['status'] == 200 and value['allow_origin'] in ('*', value['requested_origin']) for value in live['cors'])
    assert all(value['policy'] == 'first_ready' and value['empty_legacy_pool'] for value in live['admin_model_checks'])
    assert live['generation_posts'] == live['production_model_or_key_mutations'] == 0 and live['admin_session_closed']
    assert live['full_acceptance'] == 'HOLD' and live['new_feature_acceptance'] == 'pending-test'
    prepared = read(folder / 'prepared-1371.json')
    promotion = read(folder / 'promotion-1371.json')
    binding = read(folder / 'admin-binding-1371.json')
    assert prepared['passed'] and promotion['passed'] and binding['passed']
    assert prepared['source_revision'] == live['source_revision'] and prepared['wheel_payload_files_verified'] == live['wheel_payload_files_verified'] == 254
    assert promotion['database_migration_executed'] and not promotion['worker_restarted'] and not promotion['schema_downgraded']
    assert promotion['worker_pid_unchanged'] and not promotion['studio_static_pointer_changed']
    assert promotion['backup']['bytes'] > 0 and re.fullmatch('[a-f0-9]{64}', promotion['backup']['sha256'])
    for name in ('jobs', 'content', 'models'):
        assert promotion['before'][name] == promotion['stopped'][name] == promotion['after'][name] == live['database'][name]
    assert binding['configuration_fields_changed'] == ['admin_dist'] and binding['configuration_mode'] == '0600' and binding['protected_original_saved']
    assert binding['admin_path'] == '/opt/comfyui-aggregator/current/admin' and not binding['worker_restarted']
    assert not read(folder / 'live-admin-prebinding-1371.json')['passed']
    admin_build = read(folder / 'admin-build-20261030T050527743988Z.json')
    browser = read(folder / 'admin-browser-1371.json')
    public = read(folder / 'public-admin-1372.json')
    assert admin_build['passed'] and browser['passed'] and public['passed']
    assert admin_build['openapi_sha256'] == sha(REPOSITORY / 'app/config/openapi.json')
    for name, digest in admin_build['source_sha256'].items():
        assert sha(REPOSITORY / 'app/apps/admin_web' / name) == digest
    assert browser['generated_dist_verified'] and len(browser['checks']) == 5 and browser['owned_browser_closed'] and browser['owned_server_closed']
    assert len(public['checks']) == 3 and all(public['cleanup'].values()) and not public['page_errors'] and not public['blocked_mutations']
    assert not read(folder / 'public-admin-1371.json')['passed']
    assert public['generation_posts'] == public['production_model_or_key_mutations'] == 0
    for item in public['screenshots']:
        original = ROOT / item['file']
        assert original.stat().st_size == item['bytes'] and sha(original) == item['sha256'] == sha(REPOSITORY / 'docs/studio' / original.relative_to(ROOT / 'docs'))
    media = read(ROOT / 'docs/media/studio-137/media-manifest.json')
    assert media['passed'] and len(media['files']) == 5 and sha(ROOT / media['source_report']) == media['source_report_sha256']
    for item in media['files']:
        original = ROOT / 'docs/media/studio-137' / item['file']
        assert original.stat().st_size == item['bytes'] and sha(original) == item['sha256'] == sha(REPOSITORY / 'docs/studio/media/studio-137' / item['file'])
        if original.suffix in ('.mp4', '.gif'):
            assert item['fully_decoded']
    return {'current_backend_release': live['backend'], 'current_backend_compatibility_cases': 1695, 'device_pool_focused_cases': 38,
            'device_pool_synthetic_browser_groups': 5, 'device_pool_public_browser_groups': 3, 'current_backend_source_files': 564,
            'device_pool_physical_fleet_accepted': False, 'full_acceptance': 'HOLD'}


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
    # 131.9 is immutable history; bind its published package receipt, not today's source.
    assert read(evidence / "static-build-studio-20261030.131.9.json")["source_sha256"] == current["source_sha256"]
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
    assert read(evidence / final["static_build"])["browser_report_sha256"] == sha(evidence / final["browser"])
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
    latest = read(evidence / "submission-lookup-validation-notes-1321.json")
    receipts = latest["passing_package_evidence"]
    # 132.2 is immutable history; the current 133.1 source is verified below.
    assert read(evidence / receipts["static_build"])["source_sha256"] == latest["source_sha256"]
    for kind in ("units", "types", "build"):
        result = read(evidence / receipts[kind])
        assert result["passed"] and result["source_unchanged"] and result["source_after"] == latest["source_sha256"]
    unit_log = (evidence / read(evidence / receipts["units"])["log"]).read_text(encoding="utf-8")
    assert re.search(r"^# tests 136$", unit_log, re.M) and re.search(r"^# fail 0$", unit_log, re.M)
    final_ui = read(evidence / receipts["browser"])
    for name, count in (("browser", 18), ("storage", 22), ("task_backup", 25), ("submission_lookup", 15)):
        result = read(evidence / receipts[name])
        assert result["passed"] and len(result["checks"]) == count and all(result["cleanup"].values())
        assert result.get("static_artifact_unchanged", True) and result["static_artifact_sha256"] == final_ui["static_artifact_sha256"]
        assert not result.get("page_errors")
        for screenshot in result["screenshots"]:
            assert sha(ROOT / screenshot["file"]) == screenshot["sha256"]
    assert read(evidence / receipts["static_build"])["browser_report_sha256"] == sha(evidence / receipts["browser"])
    package = read(evidence / receipts["static_build"])
    promotion = read(evidence / receipts["static_promotion"])
    assert package["passed"] and package["file_count"] == len(final_ui["static_artifact_sha256"]) == 96
    assert package["source_sha256"] == promotion["source_sha256"] == latest["source_sha256"]
    assert package["archive_sha256"] == promotion["archive_sha256"]
    assert not promotion["backend_restarted"] and not promotion["old_ui_roundtrip_performed"]
    health = read(evidence / receipts["health"])
    assert health["passed"] and health["backend"] == "linux-20261030.132.2" and health["studio"] == "studio-20261030.132.2"
    assert health["original_worker_pid_unchanged"] and all(value == "active" for value in health["services"].values())
    assert len(health["responses"]) == 4 and all(item["status"] == 200 for item in health["responses"])
    assert len(health["cors"]) == 2 and all(item["status"] == 200 and item["allow_origin"] in ("*", item["requested_origin"]) for item in health["cors"])
    public = read(evidence / receipts["public_current_restart"])
    assert public["passed"] and len(public["checks"]) == 3 and all(public["cleanup"].values()) and not public["blocked_mutations"]
    assert public["checks"][0]["detail"] == {"before_version": 3, "after_version": 3, "static_release": "studio-20261030.132.2"}
    correction = read(evidence / "static-manifest-correction-1322.json")
    assert correction["passed"] and correction["actual_ui_files_identical"]
    assert correction["previous_declared_database_version"] == 2 and correction["current_declared_database_version"] == 3
    assert correction["source_sha256"] == latest["source_sha256"]
    assert all(not read(evidence / name)["passed"] for name in latest["failed_public_reports_preserved"])
    deployment = read(evidence / "production-deployment-1322.json")
    assert deployment["passed"] and deployment["prepared"]["wheel_payload_files_verified"] == 249
    assert deployment["prepared"]["source_revision"] == latest["runtime_commit"]
    deployed = deployment["promotion"]
    assert deployed["passed"] and not deployed["database_migration_executed"] and not deployed["worker_restarted"]
    assert deployed["steps"][0]["before"] == deployed["steps"][0]["after"]
    for name, count in (("backend_focused", 112), ("backend_compatibility", 1486)):
        result = read(REPOSITORY / latest[name])
        assert result["passed"] and result["sources_unchanged"] and result["python"] == {"tests": count, "failures": 0, "errors": 0, "skipped": 0}
    generated = read(REPOSITORY / "docs/evidence/studio-release-132/admin-generated-types-1322.json")
    assert generated["passed"] and generated["typecheck_passed"] and generated["build_passed"] and generated["generated_copy_matches"]
    assert generated["python_sources_unchanged_from_compatibility"] and generated["compatibility_report_sha256"] == sha(REPOSITORY / latest["backend_compatibility"])
    assert frozen_sha(latest["runtime_commit"], "app/config/openapi.json") == generated["openapi_sha256"]
    assert set(generated["source_differences_from_compatibility"]) == {"app/apps/admin_web/src/generated/api.d.ts"}
    for name, digest in result["source_sha256"].items():
        if name == "app/apps/admin_web/src/generated/api.d.ts":
            assert digest == generated["source_differences_from_compatibility"][name]["before"]
            assert frozen_sha(latest["runtime_commit"], name) == generated["generated_types_sha256"]
        else:
            assert frozen_sha(latest["runtime_commit"], name) == digest
    assert sum(item["passed_cases"] for item in result["python_tests"] if item["path"].endswith("/test_studio_submission_lookup.py")) == 30
    live = read(evidence / receipts["live_lookup"])
    assert live["passed"] and len(live["checks"]) == 5 and live["jobs_before"] == live["jobs_after"]
    assert live["lookup_response"] == {"object": "generation.submission_lookup", "outcome": "not_observed", "job": None}
    assert live["cleanup"]["temporary_key_disabled"] and live["cleanup"]["revoked_key_returns_401"] and live["cleanup"]["clients_closed"]
    assert live["cleanup"]["previous_validation_keys_reenabled"] is False
    tour = read(MEDIA / "studio-submission-lookup-manifest.json")
    assert tour["passed"] and "synthetic" in tour["scope"].lower() and sha(ROOT / tour["source_report"]) == tour["source_report_sha256"]
    for item in tour["files"]:
        assert item["fully_decoded"] and sha(MEDIA / item["file"]) == item["sha256"]
        assert sha(MEDIA / item["file"]) == sha(REPOSITORY / "docs/studio/media/studio-131" / item["file"])
    assert all(not read(REPOSITORY / name)["passed"] for name in latest["failed_backend_reports_preserved"])
    assert all(not read(REPOSITORY / name)["passed"] for name in latest["failed_documentation_reports_preserved"])
    assert latest["full_acceptance"] == "HOLD" and latest["no_new_gpu_submission"] and latest["no_disabled_key_reenabled"]
    batch_notes = read(evidence / "batch-validation-notes-1331.json")
    batch_receipts = batch_notes["passing_package_evidence"]
    # 133.1 remains immutable history; today's GLB source is checked below.
    assert read(evidence / batch_receipts["static_build"])["source_sha256"] == batch_notes["source_sha256"]
    for kind in ("units", "types", "build"):
        result = read(evidence / batch_receipts[kind])
        assert result["passed"] and result["source_unchanged"] and result["source_after"] == batch_notes["source_sha256"]
    log = (evidence / read(evidence / batch_receipts["units"])["log"]).read_text(encoding="utf-8")
    assert re.search(r"^# tests 147$", log, re.M) and re.search(r"^# fail 0$", log, re.M)
    current_ui = read(evidence / batch_receipts["browser"])
    for name, count in (("browser", 18), ("storage", 22), ("task_backup", 25), ("submission_lookup", 15), ("batch", 15)):
        result = read(evidence / batch_receipts[name])
        assert result["passed"] and len(result["checks"]) == count and all(result["cleanup"].values())
        assert result.get("static_artifact_unchanged", True) and result["static_artifact_sha256"] == current_ui["static_artifact_sha256"]
        assert not result.get("page_errors") and not result.get("handler_failure") and not result.get("route_failure")
        for screenshot in result["screenshots"]:
            assert sha(ROOT / screenshot["file"]) == screenshot["sha256"]
    assert read(evidence / batch_receipts["static_build"])["browser_report_sha256"] == sha(evidence / batch_receipts["browser"])
    batch = read(evidence / batch_receipts["batch"])
    assert batch["browser_storage_version"] == 3 and batch["generation_posts"] == 5
    assert batch["archive"]["version"] == 2 and batch["archive"]["tasks"] == 3
    assert all(item["bearer_valid"] and not item["cookie_present"] for item in batch["business_requests"])
    package = read(evidence / batch_receipts["static_build"])
    promotion = read(evidence / batch_receipts["static_promotion"])
    assert package["passed"] and package["file_count"] == len(current_ui["static_artifact_sha256"]) == 96
    assert package["source_sha256"] == promotion["source_sha256"] == batch_notes["source_sha256"]
    assert package["archive_sha256"] == promotion["archive_sha256"] and not promotion["backend_restarted"] and not promotion["old_ui_roundtrip_performed"]
    assert package["browser_report_sha256"] == sha(evidence / batch_receipts["browser"])
    health = read(evidence / batch_receipts["health"])
    assert health["passed"] and health["backend"] == "linux-20261030.132.2" and health["studio"] == "studio-20261030.133.1"
    assert health["original_worker_pid_unchanged"] and all(value == "active" for value in health["services"].values())
    assert len(health["responses"]) == 4 and all(item["status"] == 200 for item in health["responses"])
    assert len(health["cors"]) == 2 and all(item["status"] == 200 and item["allow_origin"] in ("*", item["requested_origin"]) for item in health["cors"])
    public = read(evidence / batch_receipts["public_current_restart"])
    assert public["passed"] and len(public["checks"]) == 3 and all(public["cleanup"].values()) and not public["blocked_mutations"] and not public["page_errors"]
    assert public["checks"][0]["detail"] == {"before_version": 3, "after_version": 3, "static_release": "studio-20261030.133.1"}
    for item in public["screenshots"]:
        assert sha(ROOT / item["file"]) == item["sha256"]
    runtime = read(evidence / batch_notes["backend_runtime_receipt"])
    assert sha(evidence / batch_notes["backend_runtime_receipt"]) == batch_notes["backend_runtime_receipt_sha256"]
    assert runtime["passed"] and runtime["runtime_commit"] == latest["runtime_commit"] and len(runtime["source_files"]) == 555
    for name, digest in runtime["source_files"].items():
        assert frozen_sha(runtime["runtime_commit"], "app/" + name) == digest
    for name, digest in batch_notes["report_sha256"].items():
        assert sha(evidence / batch_receipts[name]) == digest
    for name, count in (("backend_focused", 112), ("backend_compatibility", 1486)):
        result = read(REPOSITORY / batch_notes[name])
        assert result["passed"] and result["python"]["tests"] == count
    batch_tour = read(MEDIA / "studio-batch-manifest.json")
    assert batch_tour["passed"] and "synthetic" in batch_tour["scope"].lower()
    assert batch_tour["source_report"].endswith(batch_receipts["batch"]) and sha(ROOT / batch_tour["source_report"]) == batch_tour["source_report_sha256"]
    for item in batch_tour["files"]:
        file = MEDIA / item["file"]
        assert item["fully_decoded"] and file.stat().st_size == item["bytes"] and sha(file) == item["sha256"]
        assert sha(file) == sha(REPOSITORY / "docs/studio/media/studio-131" / item["file"])
    assert len(batch_notes["failed_reports_preserved"]) == 4
    assert all(not read(evidence / name)["passed"] for name in batch_notes["failed_reports_preserved"])
    assert batch_notes["full_acceptance"] == "HOLD" and batch_notes["no_new_gpu_submission"] and batch_notes["no_disabled_key_reenabled"]
    model_notes = read(evidence / "model3d-validation-notes-1341.json")
    model_receipts = model_notes["passing_package_evidence"]
    # 134.1 is immutable history; current source/dist are checked against 135.1 below.
    assert model_notes["source_sha256"] == read(evidence / model_receipts["static_build"])["source_sha256"]
    for name in ("units", "types", "build"):
        result = read(evidence / model_receipts[name])
        assert result["passed"] and result["source_unchanged"] and result["source_after"] == model_notes["source_sha256"]
    log = (evidence / read(evidence / model_receipts["units"])["log"]).read_text(encoding="utf-8")
    assert re.search(r"^# tests 159$", log, re.M) and re.search(r"^# fail 0$", log, re.M)
    current_ui = read(evidence / model_receipts["browser"])
    for name, count in (("browser", 18), ("storage", 22), ("task_backup", 25), ("submission_lookup", 15), ("batch", 15), ("model3d", 17)):
        result = read(evidence / model_receipts[name])
        assert result["passed"] and len(result["checks"]) == count and all(result["cleanup"].values())
        assert result.get("static_artifact_unchanged", True) and result["static_artifact_sha256"] == current_ui["static_artifact_sha256"]
        assert not result.get("page_errors") and not result.get("handler_failure") and not result.get("route_failure")
        for screenshot in result["screenshots"]:
            assert sha(ROOT / screenshot["file"]) == screenshot["sha256"]
    assert read(evidence / model_receipts["static_build"])["browser_report_sha256"] == sha(evidence / model_receipts["browser"])
    model = read(evidence / model_receipts["model3d"])
    assert model["browser_storage_version"] == 3 and model["generation_posts"] == 0 and not model["external_requests"]
    assert model["real_original"]["bytes"] == 76942864 and model["real_original"]["vertices"] == 1597077 and model["real_original"]["triangles"] == 3217656 and model["real_original"]["saved_original_unchanged"]
    assert len(model["closed_previews"]) == 13
    for closed in model["closed_previews"]:
        assert not closed["active_bitmaps"] and not closed["image_blob_urls"] and not closed["active_viewer_observers"] and all(context["lost"] for context in closed["contexts"])
    assert sha(ROOT / model["synthetic_fixture"]["file"]) == model["synthetic_fixture"]["sha256"]
    package = read(evidence / model_receipts["static_build"])
    promotion = read(evidence / model_receipts["static_promotion"])
    assert package["passed"] and package["file_count"] == len(current_ui["static_artifact_sha256"])
    assert package["source_sha256"] == promotion["source_sha256"] == model_notes["source_sha256"]
    assert package["archive_sha256"] == promotion["archive_sha256"] and not promotion["backend_restarted"] and not promotion["worker_restarted"] and not promotion["old_ui_roundtrip_performed"]
    assert promotion["studio_blob_connect_allowed"] and promotion["dedicated_edge_reloaded"] and package["browser_report_sha256"] == sha(evidence / model_receipts["browser"])
    health = read(evidence / model_receipts["health"])
    assert health["passed"] and health["backend"] == "linux-20261030.132.2" and health["studio"] == "studio-20261030.134.1"
    assert health["original_worker_pid_unchanged"] and all(value == "active" for value in health["services"].values())
    assert len(health["responses"]) == 4 and all(item["status"] == 200 for item in health["responses"])
    assert len(health["cors"]) == 2 and all(item["status"] == 200 and item["allow_origin"] in ("*", item["requested_origin"]) for item in health["cors"])
    for name, count in (("public_current_restart", 3), ("public_model3d", 4)):
        public = read(evidence / model_receipts[name])
        assert public["passed"] and len(public["checks"]) == count and all(public["cleanup"].values()) and not public["blocked_mutations"] and not public["page_errors"]
        for item in public["screenshots"]:
            assert sha(ROOT / item["file"]) == item["sha256"]
    public = read(evidence / model_receipts["public_current_restart"])
    assert public["checks"][0]["detail"] == {"before_version": 3, "after_version": 3, "static_release": "studio-20261030.134.1"}
    public = read(evidence / model_receipts["public_model3d"])
    assert public["static_release"] == "studio-20261030.134.1" and public["static_artifact_match"] and public["generation_posts"] == 0 and public["browser_storage_version"] == 3
    assert "connect-src http: https: blob:" in public["csp"] and all(item["lost"] and item["draws"] > 0 for item in public["closed_contexts"])
    for name, digest in public["received_static_sha256"].items():
        assert current_ui["static_artifact_sha256"][name] == digest
    runtime = read(evidence / model_notes["backend_runtime_receipt"])
    assert sha(evidence / model_notes["backend_runtime_receipt"]) == model_notes["backend_runtime_receipt_sha256"] and runtime["passed"] and runtime["runtime_commit"] == latest["runtime_commit"] and len(runtime["source_files"]) == 555
    for name, digest in runtime["source_files"].items():
        assert frozen_sha(runtime["runtime_commit"], "app/" + name) == digest
    for name, digest in model_notes["report_sha256"].items():
        assert sha(evidence / model_receipts[name]) == digest
    for name, count in (("backend_focused", 112), ("backend_compatibility", 1486)):
        result = read(REPOSITORY / model_notes[name]); assert result["passed"] and result["python"]["tests"] == count
    model_tour = read(MEDIA / "studio-model3d-manifest.json")
    assert model_tour["passed"] and model_tour["source_report"].endswith(model_receipts["model3d"]) and sha(ROOT / model_tour["source_report"]) == model_tour["source_report_sha256"]
    for item in model_tour["files"]:
        file = MEDIA / item["file"]
        assert item["fully_decoded"] and file.stat().st_size == item["bytes"] and sha(file) == item["sha256"] == sha(REPOSITORY / "docs/studio/media/studio-131" / item["file"])
    assert all(not read(evidence / name)["passed"] for name in model_notes["failed_reports_preserved"])
    assert model_notes["full_acceptance"] == "HOLD" and model_notes["no_new_gpu_submission"] and model_notes["no_disabled_key_reenabled"]
    storage_notes = read(evidence / "storage-validation-notes-1351.json")
    receipts = storage_notes["passing_package_evidence"]
    # 135.1 remains immutable history; current source and dist are bound to 136.1 below.
    assert read(evidence / receipts["static_build"])["source_sha256"] == storage_notes["source_sha256"]
    for name in ("units", "types", "build"):
        result = read(evidence / receipts[name])
        assert result["passed"] and result["source_unchanged"] and result["source_after"] == storage_notes["source_sha256"]
    log = (evidence / read(evidence / receipts["units"])["log"]).read_text(encoding="utf-8")
    assert re.search(r"^# tests 169$", log, re.M) and re.search(r"^# fail 0$", log, re.M)
    current_ui = read(evidence / receipts["browser"])
    for name, count in (("browser", 18), ("storage", 22), ("task_backup", 25), ("submission_lookup", 15), ("batch", 15), ("model3d", 17), ("upload_cache", 28)):
        result = read(evidence / receipts[name])
        assert result["passed"] and len(result["checks"]) == count and all(result["cleanup"].values())
        assert result.get("static_artifact_unchanged", True) and result["static_artifact_sha256"] == current_ui["static_artifact_sha256"]
        assert not result.get("page_errors") and not result.get("handler_failure") and not result.get("route_failure")
        for item in result["screenshots"]:
            assert sha(ROOT / item["file"]) == item["sha256"]
    cache = read(evidence / receipts["upload_cache"])
    assert cache["generation_posts"] == cache["production_mutations"] == cache["remote_delete_requests"] == 0 and cache["synthetic_upload_posts"] == 1
    assert cache["button_hover"]["foreground"] == cache["button_hover"]["border"] == "rgb(12, 12, 12)" and cache["button_hover"]["contrast"] >= 4.5
    clean = read(evidence / receipts["clean_final"])
    origin = read(evidence / receipts["clean_origin"])
    assert clean["passed"] and clean["source_before"] == clean["source_after"] == clean["copied_source_after"] == storage_notes["source_sha256"]
    assert clean["static_artifact_sha256"] == current_ui["static_artifact_sha256"] and clean["unit_tests"] == 169 and clean["unit_failures"] == 0
    assert all(stage["exit_code"] == 0 for stage in clean["stages"]) and clean["canonical_lock_unchanged"]
    assert origin["passed"] and any(stage["name"] == "clean-install" and stage["exit_code"] == 0 for stage in origin["stages"])
    assert clean["package_sha256"] == origin["package_sha256"] and clean["lock_sha256"] == origin["lock_sha256"] and not origin["install_scripts_disabled"] and not origin["force_or_peer_bypass"]
    assert sha(ROOT / clean["clean_install_reused"]["file"]) == clean["clean_install_reused"]["sha256"]
    package = read(evidence / receipts["static_build"])
    promotion = read(evidence / receipts["static_promotion"])
    assert package["passed"] and package["file_count"] == len(current_ui["static_artifact_sha256"]) == 104
    assert package["source_sha256"] == promotion["source_sha256"] == storage_notes["source_sha256"] and package["archive_sha256"] == promotion["archive_sha256"]
    assert not promotion["backend_restarted"] and not promotion["worker_restarted"] and not promotion["dedicated_edge_reloaded"] and not promotion["old_ui_roundtrip_performed"]
    health = read(evidence / receipts["health"])
    assert health["passed"] and health["studio"] == "studio-20261030.135.1" and health["backend"] == "linux-20261030.132.2" and health["original_worker_pid_unchanged"]
    assert all(value == "active" for value in health["services"].values()) and len(health["responses"]) == 4 and all(value["status"] == 200 for value in health["responses"])
    assert len(health["cors"]) == 2 and all(value["status"] == 200 and value["allow_origin"] in ("*", value["requested_origin"]) for value in health["cors"])
    for name, count in (("public_storage_space", 4), ("public_current_restart", 3)):
        result = read(evidence / receipts[name])
        assert result["passed"] and len(result["checks"]) == count and all(result["cleanup"].values()) and not result["blocked_mutations"] and not result["page_errors"]
        for item in result["screenshots"]:
            assert sha(ROOT / item["file"]) == item["sha256"]
    result = read(evidence / receipts["public_storage_space"])
    assert result["static_release"] == "studio-20261030.135.1" and result["static_artifact_match"] and result["business_requests"] == result["generation_posts"] == 0 and "connect-src http: https: blob:" in result["csp"]
    for name, digest in result["received_static_sha256"].items():
        assert current_ui["static_artifact_sha256"][name] == digest
    assert read(evidence / receipts["public_current_restart"])["checks"][0]["detail"] == {"before_version": 3, "after_version": 3, "static_release": "studio-20261030.135.1"}
    runtime = read(evidence / storage_notes["backend_runtime_receipt"])
    assert runtime["passed"] and len(runtime["source_files"]) == 555 and runtime["runtime_commit"] == latest["runtime_commit"] and sha(evidence / storage_notes["backend_runtime_receipt"]) == storage_notes["backend_runtime_receipt_sha256"]
    for name, digest in runtime["source_files"].items():
        assert frozen_sha(runtime["runtime_commit"], "app/" + name) == digest
    for name, digest in storage_notes["report_sha256"].items():
        assert sha(evidence / receipts[name]) == digest
    cache_tour = read(MEDIA / "studio-upload-cache-manifest.json")
    assert cache_tour["passed"] and cache_tour["source_report"].endswith(receipts["upload_cache"]) and sha(ROOT / cache_tour["source_report"]) == cache_tour["source_report_sha256"]
    for item in cache_tour["files"]:
        file = MEDIA / item["file"]
        assert item["fully_decoded"] and file.stat().st_size == item["bytes"] and sha(file) == item["sha256"] == sha(REPOSITORY / "docs/studio/media/studio-131" / item["file"])
    assert all(not read(evidence / name)["passed"] for name in storage_notes["failed_reports_preserved"])
    assert storage_notes["full_acceptance"] == "HOLD" and storage_notes["no_new_gpu_submission"] and storage_notes["no_new_business_key"] and storage_notes["no_disabled_key_reenabled"]
    access_notes = read(evidence / "task-access-validation-notes-1361.json")
    receipts = access_notes["passing_package_evidence"]
    assert access_notes["passed"] and frozen.hexdigest() == access_notes["source_sha256"] and len(source["source_files"]) == 265
    for name in ("units", "types", "build"):
        result = read(evidence / receipts[name])
        assert result["passed"] and result["source_unchanged"] and result["source_after"] == access_notes["source_sha256"]
    log = (evidence / read(evidence / receipts["units"])["log"]).read_text(encoding="utf-8")
    assert re.search(r"^# tests 179$", log, re.M) and re.search(r"^# fail 0$", log, re.M)
    current_ui = read(evidence / receipts["browser"])
    for name, count in (("browser", 18), ("storage", 22), ("task_backup", 25), ("submission_lookup", 15), ("batch", 15), ("model3d", 17), ("upload_cache", 28), ("task_access", 31)):
        result = read(evidence / receipts[name])
        assert result["passed"] and len(result["checks"]) == count and all(result["cleanup"].values())
        assert result.get("static_artifact_unchanged", True) and result["static_artifact_sha256"] == current_ui["static_artifact_sha256"]
        assert not result.get("page_errors") and not result.get("handler_failure") and not result.get("route_failure")
        for item in result["screenshots"]:
            assert sha(ROOT / item["file"]) == item["sha256"]
    assert {file.relative_to(dist).as_posix(): sha(file) for file in dist.rglob("*") if file.is_file()} == current_ui["static_artifact_sha256"]
    access = read(evidence / receipts["task_access"])
    assert access["browser_storage_version"] == 4 and access["generation_posts"] == access["production_mutations"] == 0 and access["synthetic_cancellations"] == 1
    assert all(not value["cookie"] for value in access["business_requests"])
    assert all(any(f"native v{version} to v4" in value["name"] for value in access["checks"]) for version in (1, 2, 3))
    clean = read(evidence / receipts["clean_final"])
    origin = read(evidence / receipts["clean_origin"])
    assert clean["passed"] and clean["source_before"] == clean["source_after"] == clean["copied_source_after"] == access_notes["source_sha256"]
    assert clean["static_artifact_sha256"] == current_ui["static_artifact_sha256"] and clean["unit_tests"] == 179 and clean["unit_failures"] == 0
    assert all(stage["exit_code"] == 0 for stage in clean["stages"]) and clean["canonical_lock_unchanged"]
    assert origin["passed"] and any(stage["name"] == "clean-install" and stage["exit_code"] == 0 for stage in origin["stages"])
    assert clean["package_sha256"] == origin["package_sha256"] and clean["lock_sha256"] == origin["lock_sha256"] and not origin["install_scripts_disabled"] and not origin["force_or_peer_bypass"]
    assert sha(ROOT / clean["clean_install_reused"]["file"]) == clean["clean_install_reused"]["sha256"]
    package = read(evidence / receipts["static_build"])
    promotion = read(evidence / receipts["static_promotion"])
    assert package["source_sha256"] == promotion["source_sha256"] == access_notes["source_sha256"] and package["archive_sha256"] == promotion["archive_sha256"]
    assert package["file_count"] == len(current_ui["static_artifact_sha256"]) and promotion["passed"] and promotion["steps"] == ["studio-20261030.136.1"] and not promotion["old_ui_roundtrip_performed"]
    health = read(evidence / receipts["health"])
    assert health["passed"] and health["studio"] == "studio-20261030.136.1" and health["backend"] == "linux-20261030.132.2" and health["original_worker_pid_unchanged"]
    assert len(health["responses"]) == 4 and all(value["status"] == 200 for value in health["responses"])
    assert len(health["cors"]) == 2 and all(value["status"] == 200 and value["allow_origin"] in ("*", value["requested_origin"]) for value in health["cors"])
    for name, count in (("public_storage_space", 6), ("public_preupgrade", 3), ("public_current_restart", 3)):
        result = read(evidence / receipts[name])
        assert result["passed"] and len(result["checks"]) == count and all(result["cleanup"].values()) and not result["blocked_mutations"] and not result["page_errors"]
        for item in result["screenshots"]:
            assert sha(ROOT / item["file"]) == item["sha256"]
    public = read(evidence / receipts["public_storage_space"])
    assert public["static_release"] == "studio-20261030.136.1" and public["static_artifact_match"] and public["business_requests"] == public["generation_posts"] == 0 and "connect-src http: https: blob:" in public["csp"]
    assert read(evidence / receipts["public_preupgrade"])["checks"][0]["detail"] == {"before_version": 3, "after_version": 3, "static_release": "studio-20261030.135.1"}
    assert read(evidence / receipts["public_current_restart"])["checks"][0]["detail"] == {"before_version": 3, "after_version": 4, "static_release": "studio-20261030.136.1"}
    live_rotation = read(evidence / receipts["live_key_rotation"])
    assert live_rotation["passed"] and len(live_rotation["checks"]) == 5 and all(live_rotation["cleanup"].values()) and live_rotation["generation_posts"] == 1 and live_rotation["jobs_after"] == live_rotation["jobs_before"] + 1 and live_rotation["user_keys_unchanged"]
    assert live_rotation["artifact"]["cloud_lifetime_seconds"] == 28800
    closure = read(evidence / receipts["key_closure"])
    assert closure["passed"] and closure["disabled_logical_keys"] == 5 and closure["rejected_credentials"] == len(closure["checks"]) == 6
    assert all(item["passed"] and item["status"] == 401 for item in closure["checks"]) and all(closure["cleanup"].values())
    delivery = read(evidence / receipts["actual_delivery"])
    assert delivery["passed"] and delivery["generation_posts"] == 1 and delivery["end_to_end_seconds"] == 1483
    assert delivery["completed_at"] - delivery["created_at"] == delivery["end_to_end_seconds"]
    artifact = delivery["artifact"]
    file = ROOT / artifact["file"]
    assert artifact["fully_decoded"] and artifact["media_type"] == "image/png" and (artifact["width"], artifact["height"]) == (720, 1080)
    assert artifact["bytes"] == file.stat().st_size == 747994
    assert artifact["sha256"] == sha(file) == sha(REPOSITORY / "docs/studio" / file.relative_to(ROOT / "docs"))
    for name in ("actual_permissions", "key_closure"):
        ref = delivery[name]
        assert sha(ROOT / ref["file"]) == ref["sha256"]
    runtime = read(evidence / access_notes["backend_runtime_receipt"])
    assert runtime["passed"] and runtime["git_inputs_rechecked"] and len(runtime["source_files"]) == 555 and runtime["runtime_commit"] == latest["runtime_commit"] and sha(evidence / access_notes["backend_runtime_receipt"]) == access_notes["backend_runtime_receipt_sha256"]
    for name, digest in runtime["source_files"].items():
        assert frozen_sha(runtime["runtime_commit"], "app/" + name) == digest
    for name, digest in access_notes["report_sha256"].items():
        assert sha(evidence / receipts[name]) == digest
    access_tour = read(MEDIA / "studio-task-access-manifest.json")
    assert access_tour["passed"] and access_tour["source_report"].endswith(receipts["task_access"]) and sha(ROOT / access_tour["source_report"]) == access_tour["source_report_sha256"]
    for item in access_tour["files"]:
        file = MEDIA / item["file"]
        assert item["fully_decoded"] and file.stat().st_size == item["bytes"] and sha(file) == item["sha256"] == sha(REPOSITORY / "docs/studio/media/studio-131" / item["file"])
    assert all(not read(evidence / name)["passed"] for name in access_notes["failed_reports_preserved"])
    assert access_notes["full_acceptance"] == "HOLD" and access_notes["no_preexisting_key_rotated_or_reenabled"] and access_notes["no_backend_migration"]
    device_pool = check_device_pool_delivery()
    print(json.dumps({"passed": True, "at": datetime.now(UTC).isoformat(), **device_pool,
                      "scope": "Read-only source-mirror, public media/report/screenshot hashes, eight call receipts, all46 IDs and temporary Key revocation; not full runtime/phone/fleet acceptance",
                      "source_files_verified": len(source["source_files"]), "actual_gpu_modes": 8,
                      "browser_checks": 9, "public_samples": published, "large_glb_raw_published": False, "interactive_glb_verified": True,
                      "requirements_retained": 46, "temporary_keys_disabled": 5, "rejected_validation_credentials": 6, "actual_image_end_to_end_seconds": 1483, "storage_checks": 22,
                      "current_ui_checks": 18, "task_backup_checks": 25, "submission_lookup_checks": 15, "batch_checks": 15, "model3d_checks": 17, "historical_public_model3d_checks": 4, "current_unit_tests": 179, "upload_cache_checks": 28, "task_access_checks": 31, "native_browser_checks": 171, "public_storage_space_checks": 6, "live_key_rotation_checks": 5, "clean_install_and_isolated_final_build_verified": True,
                      "historical_backend_compatibility_cases": 1486, "live_lookup_checks": 5,
                      "public_storage_upgrade_checks": 3, "current_static_release": "studio-20261030.136.1", "browser_storage_version": 4,
                      "browser_storage_downgrade_supported": False, "virtualization_used": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
