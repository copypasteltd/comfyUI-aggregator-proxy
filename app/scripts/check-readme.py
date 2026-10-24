"""Validate bilingual entry points, native request examples and published demo media."""
from __future__ import annotations

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
    for directory in (MEDIA, MEDIA / "gateway-catalog"):
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
    report = {"at": datetime.now(UTC).isoformat(), "passed": not errors, "entry_documents": len(documents), "local_links_checked": links, "request_examples_validated": 3, "screenshots_verified": screenshots, "media_manifests_verified": 2, "video_and_gif_fully_decoded": decoded, "bilingual_capability_topics_present": True, "actual_lifecycle_responses_verified": 2, "catalog_responses_verified": len(catalog_responses["responses"]), "catalog_provider_receipts_are_protocol_fixtures": True, "synthetic_capture_and_cleanup_passed": True, "errors": errors, "full_python_suite_rerun": False, "runtime_protocol_changed": False}
    (ROOT / "docs/evidence/2026-10-25/repository-readme-verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
