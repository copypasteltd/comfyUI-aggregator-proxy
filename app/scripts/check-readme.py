"""Validate bilingual entry points, native request examples and published demo media."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote

from jsonschema import Draft202012Validator
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
MEDIA = ROOT / "docs/media"


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, help="New report beneath docs/evidence; default is read-only verification")
    arguments = parser.parse_args()
    report_path = arguments.report.resolve() if arguments.report is not None else None
    if report_path is not None and (not report_path.is_relative_to(ROOT / "docs/evidence") or report_path.exists()):
        raise SystemExit("Choose a new report file beneath docs/evidence; existing evidence is never overwritten")
    errors: list[str] = []
    documents = [ROOT / name for name in ("README.md", "README.en.md", "CHANGELOG.md", "CONTRIBUTING.md", "docs/README.en.md", "docs/media/README.md", "docs/77-仓库整理与双语文档交付.md")]
    links = 0
    for document in documents:
        text = document.read_text(encoding="utf-8-sig")
        for target in re.findall(r"!?\[[^\]]*\]\(([^\s)]+)(?:\s+[^)]*)?\)", text):
            if target.startswith(("https:", "http:", "mailto:", "#")):
                continue
            path = (document.parent / unquote(target.split("#", 1)[0]).strip("<>")).resolve()
            links += 1
            if not path.is_relative_to(ROOT) or not path.is_file():
                errors.append("missing_link:" + document.relative_to(ROOT).as_posix() + ":" + target)
        for block in re.findall(r"```json\s*\n(.*?)\n```", text, re.S):
            json.loads(block)
    for name in ("README.md", "README.en.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        for token in ("text_to_image", "image_to_image", "references_to_image", "text_to_video", "references_to_video", "text_to_audio", "Idempotency-Key", "GEN_PUBLIC_ORIGIN", "10", "20", "ComfyUI", "Sub2API", "CHANGELOG.md"):
            if token not in text:
                errors.append("missing_bilingual_topic:" + name + ":" + token)
    schema = json.loads((ROOT / "docs/contracts/job-create.schema.json").read_text(encoding="utf-8"))
    for name in ("create-image.json", "create-video.json", "create-audio.json"):
        request = json.loads((ROOT / "docs/examples" / name).read_text(encoding="utf-8"))
        Draft202012Validator(schema).validate(request)
    decoded = []
    screenshots = 0
    ffmpeg = ROOT / "app/.local/toolchains/ffmpeg-8.1/bin/ffmpeg.exe"
    lock = json.loads((ROOT / "app/config/media-tools.lock.json").read_text(encoding="utf-8"))
    assert sha(ffmpeg) == lock["files"]["bin/ffmpeg.exe"]["sha256"]
    directories = (MEDIA, MEDIA / "gateway-catalog", MEDIA / "confirmed-retry", MEDIA / "wait-estimates", MEDIA / "audit-retention")
    for directory in directories:
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["passed"] and manifest["synthetic_data"] and not manifest["real_gpu_generation"]
        for item in manifest["files"]:
            path = (directory / item["path"]).resolve()
            if not path.is_relative_to(directory.resolve()):
                raise ValueError("media_manifest_path_escape")
            if path.stat().st_size != item["bytes"] or sha(path) != item["sha256"]:
                errors.append("media_hash:" + path.name)
            if path.suffix == ".png":
                screenshots += 1
                with Image.open(path) as image:
                    image.verify()
            else:
                result = subprocess.run([str(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin", "-protocol_whitelist", "file", "-i", str(path), "-map", "0:v:0", "-threads", "1", "-f", "null", "-"], capture_output=True, timeout=120, creationflags=subprocess.CREATE_NO_WINDOW)  # noqa: S603 -- fixed native executable and manifest-owned file
                if result.returncode:
                    errors.append("media_decode:" + path.name)
                else:
                    decoded.append(path.relative_to(MEDIA).as_posix())
    audit_manifest = json.loads((MEDIA / "audit-retention/manifest.json").read_text(encoding="utf-8"))
    audit_source = (ROOT / audit_manifest["source_report"]["path"]).resolve()
    assert audit_source.is_relative_to(ROOT / "docs/evidence") and sha(audit_source) == audit_manifest["source_report"]["sha256"]
    audit_capture = json.loads(audit_source.read_text(encoding="utf-8"))
    assert audit_capture["passed"] and audit_capture["browser_closed"] and audit_capture["closed_contexts"] == 3
    assert audit_capture["sources_unchanged"] and all(audit_capture["cleanup"].values()) and audit_capture["browser_process"]["owned_process_tree_closed"]
    assert not audit_capture["real_resource_acceptance"] and audit_capture["full_acceptance"] == "pending"
    assert {(item["viewport"], item["state"]) for item in audit_capture["observations"]} == {(width, state) for width in (1360, 390, 320) for state in ("disabled", "enabled", "stale")}
    assert all(item["transform"] == "none" and item["opacity"] == 1 and not item["page_overflow"] for item in audit_capture["observations"])
    for item in audit_capture["screenshots"]:
        assert sha(MEDIA / "audit-retention" / item["path"]) == item["sha256"]
    wait_manifest = json.loads((MEDIA / "wait-estimates/manifest.json").read_text(encoding="utf-8"))
    wait_source = (ROOT / wait_manifest["source_report"]["path"]).resolve()
    assert wait_source.is_relative_to(ROOT / "docs/evidence") and sha(wait_source) == wait_manifest["source_report"]["sha256"]
    wait_capture = json.loads(wait_source.read_text(encoding="utf-8"))
    assert wait_capture["passed"] and wait_capture["browser_closed"] and wait_capture["closed_contexts"] == 3 and all(wait_capture["cleanup"].values())
    assert wait_capture["synthetic_data"] and not wait_capture["real_resource_acceptance"] and wait_capture["full_acceptance"] == "pending"
    wait_schema = json.loads((ROOT / "docs/contracts/admin-wait-estimate.schema.json").read_text(encoding="utf-8"))
    assert len(wait_capture["responses"]) == 3
    for response in wait_capture["responses"]:
        Draft202012Validator(wait_schema).validate(response)
    capture = json.loads((MEDIA / "capture.json").read_text(encoding="utf-8"))
    cleanup = json.loads((ROOT / "docs/evidence/2026-10-25/readme-media-capture.json").read_text(encoding="utf-8"))
    assert capture["passed"] and not capture["errors"] and capture["closed_contexts"] == 3 and capture["browser_closed"]
    assert cleanup["passed"] and all(cleanup["cleanup"].values())
    responses = json.loads((MEDIA / "api-responses.json").read_text(encoding="utf-8"))
    assert responses["authentication_headers_and_cookies_omitted"] and len(responses["responses"]) == 2
    for record in responses["responses"]:
        request, response = record["request"], record["response"]
        assert record["http_status"] == 200 and request["archived"] == response["archived"]
        assert request["expected_revision"] == response["previous_revision"] == response["revision"] - 1
    catalog_capture = json.loads((MEDIA / "gateway-catalog/capture.json").read_text(encoding="utf-8"))
    catalog_cleanup = json.loads((ROOT / "docs/evidence/2026-10-25/gateway-catalog-browser.json").read_text(encoding="utf-8"))
    assert catalog_capture["passed"] and not catalog_capture["errors"] and catalog_capture["closed_contexts"] == 4 and catalog_capture["browser_closed"]
    assert catalog_cleanup["passed"] and all(catalog_cleanup["cleanup"].values())
    catalog_responses = json.loads((MEDIA / "gateway-catalog/api-responses.json").read_text(encoding="utf-8"))
    assert catalog_responses["authentication_headers_and_cookies_omitted"] and catalog_responses["provider_receipts_are_protocol_fixtures"]
    assert sum(item["operation"] == "publish_six_types" and item["http_status"] == 201 for item in catalog_responses["responses"]) == 1
    assert sum(item["operation"] == "simulated_provider_receipt" and item["http_status"] == 200 for item in catalog_responses["responses"]) == 4
    retry_capture = json.loads((MEDIA / "confirmed-retry/capture.json").read_text(encoding="utf-8"))
    retry_cleanup = json.loads((ROOT / "docs/evidence/2026-10-25/confirmed-retry-browser-fixture.json").read_text(encoding="utf-8"))
    assert retry_capture["passed"] and not retry_capture["errors"] and retry_capture["closed_contexts"] == 2 and retry_capture["browser_closed"]
    assert retry_cleanup["passed"] and all(retry_cleanup["cleanup"].values())
    retry_responses = json.loads((MEDIA / "confirmed-retry/api-responses.json").read_text(encoding="utf-8"))
    assert retry_responses["synthetic_data"] and retry_responses["authentication_headers_and_cookies_omitted"]
    assert len(retry_responses["responses"]) == 3 and all(item["http_status"] == 200 for item in retry_responses["responses"])
    for item in retry_responses["responses"]:
        response = item["response"]
        assert response["retry"]["last_error"] == "comfy_oom" and response["retry"]["count"] == 1 and response["retry"]["limit"] == 2
        assert response["status"] == ("cancelled" if item["operation"] == "mobile_cancel_result" else "retry_wait")
        assert (response["retry"]["next_at"] is None) == (response["status"] == "cancelled")
    report = {"at": datetime.now(UTC).isoformat(), "passed": not errors, "entry_documents": len(documents), "local_links_checked": links, "request_examples_validated": 3, "screenshots_verified": screenshots, "media_manifests_verified": len(directories), "video_and_gif_fully_decoded": decoded, "bilingual_capability_topics_present": True, "actual_lifecycle_responses_verified": 2, "catalog_responses_verified": len(catalog_responses["responses"]), "catalog_provider_receipts_are_protocol_fixtures": True, "confirmed_retry_responses_verified": len(retry_responses["responses"]), "wait_estimate_responses_verified": len(wait_capture["responses"]), "synthetic_capture_and_cleanup_passed": True, "errors": errors, "full_python_suite_rerun": False, "runtime_protocol_changed": False}
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with report_path.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
