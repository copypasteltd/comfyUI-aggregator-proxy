"""Owned SDK video/audio rehearsal; raw responses remain private to the caller."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from native_openai_sdk import require


def python_multimedia(client: Any, root: Path, image: bytes, row: Any) -> int:
    import httpx2
    import openai
    from openai._models import FinalRequestOptions

    schema = json.loads((Path(__file__).resolve().parents[1] / "packages/contracts/src/generation_contracts/schemas/sdk-video-response.schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    video_bytes = (root / "comfy/output/result.mp4").read_bytes()
    audio_bytes = (root / "comfy/output/result.wav").read_bytes()
    video_sha = hashlib.sha256(video_bytes).hexdigest()
    audio_sha = hashlib.sha256(audio_bytes).hexdigest()

    def verify_video(value: dict[str, Any]) -> None:
        require(validator.is_valid(value), "video_project_schema")
        require(value.get("seconds") == "5" and value.get("size") == "1024x1024", "video_frozen_specification")
        require(value["poll_url"] == "/v1/videos/" + value["id"] and "progress" not in value, "video_unknown_progress_or_locator")

    def terminal(identifier: str, *, video: bool) -> dict[str, Any]:
        deadline = time.monotonic() + 90
        for _ in range(25):
            value = client.get(("/videos/" if video else "/jobs/") + identifier, cast_to=dict)
            if video:
                verify_video(value)
            if value["status"] in ("completed", "failed", "cancelled", "expired", "succeeded"):
                require(value["status"] == ("completed" if video else "succeeded"), "multimedia_generation_failed")
                return value
            require(time.monotonic() < deadline, "multimedia_poll_deadline")
        raise RuntimeError("sdk_multimedia_poll_limit")

    generation = {"model": "protocol-text-to-video", "prompt": "Synthetic Python SDK video", "seconds": "5", "size": "1024x1024"}
    idem = "sdk-python-video-0001"
    raw = client.videos.with_raw_response.create(**generation, extra_headers={"Idempotency-Key": idem})
    require(raw.status_code == 202 and bool(raw.headers.get("x-request-id")), "video_create_headers")
    value = raw.http_response.json()
    verify_video(value)
    row("video_multipart_create_frozen_specification", http_status=202, seconds="5", size="1024x1024", project_schema_valid=True)
    try:
        raw.parse()
    except openai.APIResponseValidationError as error:
        fields = sorted({str(item["loc"][0]) for item in error.__cause__.errors()})
        require(fields == ["progress", "size"], "typed_video_limit_changed")
        row("official_video_type_known_incompatibility", supported=False, expected_validation_rejection=True,
            fields=fields, strict_validation_disabled=False)
    else:
        raise RuntimeError("sdk_typed_video_limit_not_observed")
    identifier = value["id"]
    replay = client.post("/videos", cast_to=dict, body=generation, options={"headers": {"Idempotency-Key": idem}})
    verify_video(replay)
    require(replay["id"] == identifier, "video_cross_encoding_replay")
    row("video_json_multipart_replay", stable_idempotency=True, same_job=True)
    terminal(identifier, video=True)
    with client.videos.with_streaming_response.download_content(identifier) as response:
        require(response.status_code == 200 and hashlib.sha256(response.read()).hexdigest() == video_sha, "video_sdk_download")
    row("video_sdk_binary_download", image_or_audio=False, sha256=video_sha, bytes=len(video_bytes), stream_closed=True)
    with client.videos.with_streaming_response.download_content(identifier, extra_headers={"Range": "bytes=0-63"}) as response:
        require(response.status_code == 206 and response.read() == video_bytes[:64], "video_sdk_range")
        require(response.headers["content-range"] == f"bytes 0-63/{len(video_bytes)}", "video_content_range")
    head = client.request(httpx2.Response, FinalRequestOptions.construct(method="HEAD", url="/videos/" + identifier + "/content"))
    require(head.status_code == 200 and not head.content and int(head.headers["content-length"]) == len(video_bytes), "video_sdk_head")
    head.close()
    row("video_range_and_head", range_status=206, head_status=200, byte_range_verified=True)

    assets = []
    for index in range(2):
        asset = client.post("/assets", cast_to=dict, files=[("file", (f"sdk-reference-{index}.png", image, "image/png"))],
                            options={"headers": {"Content-Type": "multipart/form-data", "Idempotency-Key": "sdk-python-reference-" + str(index)}})
        require(isinstance(asset.get("id"), str) and asset["id"].startswith("asset_"), "sdk_asset_upload")
        assets.append(asset["id"])
    require(assets[0] != assets[1], "sdk_reference_identity")
    row("video_reference_asset_uploads", extension=True, assets=2)
    references = [{"asset_id": assets[0], "role": "character"}, {"asset_id": assets[1], "role": "scene"}]
    reference_body = {**generation, "model": "protocol-references-to-video", "workflow_options": {"references": references}}
    reference_key = "sdk-python-reference-video-0001"
    reference = client.post("/videos", cast_to=dict, body=reference_body, options={"headers": {"Idempotency-Key": reference_key}})
    verify_video(reference)
    repeated = client.post("/videos", cast_to=dict, body=reference_body, options={"headers": {"Idempotency-Key": reference_key}})
    require(repeated["id"] == reference["id"], "reference_video_replay")
    terminal(reference["id"], video=True)
    with client.videos.with_streaming_response.download_content(reference["id"]) as response:
        require(hashlib.sha256(response.read()).hexdigest() == video_sha, "reference_video_download")
    row("multiple_reference_video_extension", extension=True, references=2, ordered_roles=["character", "scene"], same_job=True, sha256=video_sha)

    audio_body = {"model": "protocol-text-to-audio", "task_type": "text_to_audio", "input": {"prompt": "Synthetic Python SDK audio"},
                  "parameters": {"quality": "standard", "seconds": 2}}
    audio_options = {"headers": {"Idempotency-Key": "sdk-python-audio-0001"}}
    audio = client.post("/jobs", cast_to=dict, body=audio_body, options=audio_options)
    repeated = client.post("/jobs", cast_to=dict, body=audio_body, options=audio_options)
    require(audio["object"] == "job" and audio["id"] == repeated["id"], "audio_async_replay")
    row("audio_async_extension_replay", extension=True, same_job=True, speech_endpoint_called=False)
    finished = terminal(audio["id"], video=False)
    require(len(finished["artifacts"]) == 1, "audio_unique_artifact")
    content_id = finished["artifacts"][0]["id"]
    metadata = client.get("/content/" + content_id, cast_to=dict)
    require(metadata["media_type"] == "audio/wav" and metadata["sha256"] == audio_sha, "audio_content_metadata")
    response = client.get("/content/" + content_id + "/download", cast_to=httpx2.Response)
    require(response.status_code == 200 and hashlib.sha256(response.content).hexdigest() == audio_sha, "audio_sdk_download")
    response.close()
    row("audio_metadata_and_binary_download", extension=True, sha256=audio_sha, bytes=len(audio_bytes), media_type="audio/wav")
    response = client.get("/content/" + content_id + "/download", cast_to=httpx2.Response, options={"headers": {"Range": "bytes=0-3"}})
    require(response.status_code == 206 and response.content == b"RIFF", "audio_sdk_range")
    response.close()
    head = client.request(httpx2.Response, FinalRequestOptions.construct(method="HEAD", url="/content/" + content_id + "/download"))
    require(head.status_code == 200 and not head.content and int(head.headers["content-length"]) == len(audio_bytes), "audio_sdk_head")
    head.close()
    cancelled = client.post("/jobs/" + audio["id"] + "/cancel", cast_to=dict)
    require(cancelled["status"] == "succeeded" and cancelled["id"] == audio["id"], "audio_terminal_cancel_changed_result")
    row("audio_range_head_and_terminal_cancel", range_status=206, head_status=200, terminal_unchanged=True)
    return 3
