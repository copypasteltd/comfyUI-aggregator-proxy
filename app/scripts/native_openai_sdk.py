"""Actual pinned SDK clients against this run's owned loopback Provider only."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import importlib.metadata
import json
import time
import traceback
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

PYTHON_VERSION = "3.37.0"
NODE_VERSION = "7.43.0"
ROOT = Path(__file__).resolve().parents[1]


def require(condition: bool, code: str) -> None:
    if not condition:
        raise RuntimeError("sdk_" + code)


def loopback_origin(value: str) -> str:
    parsed = urlsplit(value)
    require(parsed.scheme == "http" and parsed.hostname == "127.0.0.1" and parsed.port is not None
            and parsed.username is None and parsed.password is None and not parsed.query
            and not parsed.fragment and parsed.path in ("", "/"), "owned_loopback_origin_required")
    return value.rstrip("/")


def preflight() -> None:
    """Fail before databases/processes are created; never install dependencies."""
    require(importlib.metadata.version("openai") == PYTHON_VERSION, "python_version_mismatch")
    package = ROOT / "integrations/openai-clients/node_modules/openai/package.json"
    require(package.is_file() and json.loads(package.read_text(encoding="utf-8"))["version"] == NODE_VERSION, "node_version_mismatch")
    require((ROOT / ".local/toolchains/node-24.22.1/node-v24.22.1-win-x64/node.exe").is_file(), "native_node_missing")


def python_client(origin: str, token: str, image: bytes, root: Path) -> dict[str, Any]:
    import httpx2
    import openai

    origin = loopback_origin(origin)
    cases: list[dict[str, Any]] = []
    attempts: list[tuple[str, str, str]] = []
    last_request = 0.0
    expected_sha = hashlib.sha256(image).hexdigest()
    generation = {"model": "protocol-text-to-image", "prompt": "Synthetic Python SDK compatibility",
                  "n": 1, "size": "1024x1024", "quality": "standard", "response_format": "b64_json",
                  "user": "sdk-python-fixture"}
    idem = "sdk-python-generation-0001"

    def observe(request: Any) -> None:
        nonlocal last_request

        require(str(request.url).startswith(origin + "/v1/"), "request_left_owned_origin")
        time.sleep(max(0, 3 - (time.monotonic() - last_request)))
        last_request = time.monotonic()
        attempts.append((request.method, request.url.path, request.headers.get("idempotency-key", "")))

    def row(name: str, **facts: Any) -> None:
        cases.append({"case": name, "passed": True, **facts})

    def verify_image(value: Any) -> None:
        require(type(value.created) is int and value.created > 0 and len(value.data or []) == 1, "image_response_type")
        require(hashlib.sha256(base64.b64decode(value.data[0].b64_json, validate=True)).hexdigest() == expected_sha, "image_bytes")

    transport = httpx2.Client(trust_env=False, follow_redirects=False, timeout=70,
                              event_hooks={"request": [observe]})
    with openai.OpenAI(api_key=token, base_url=origin + "/v1", max_retries=0, timeout=70,
                       organization=None, project=None, http_client=transport, _strict_response_validation=True) as client:
        models = client.models.list()
        require({"protocol-text-to-image", "protocol-text-to-audio"} <= {model.id for model in models.data}, "model_list")
        require(client.models.retrieve("protocol-text-to-image").id == "protocol-text-to-image", "model_detail")
        row("models_list_and_retrieve", strict_response_validation=True)
        raw = client.images.with_raw_response.generate(**generation, extra_headers={"Idempotency-Key": idem})
        result = raw.parse()
        verify_image(result)
        require(raw.status_code == 200 and raw.headers.get("x-request-id") == result._request_id and bool(result._request_id), "success_request_id")
        row("generate_typed_base64", http_status=raw.status_code, created_type="integer", image_sha256=expected_sha, request_id_present=True)
        verify_image(client.images.generate(**generation, extra_headers={"Idempotency-Key": idem}))
        row("same_key_replay")

        for retries in (0, 1):
            before = len(attempts)
            try:
                client.with_options(max_retries=retries).images.generate(**{**generation, "prompt": "Synthetic changed prompt"}, extra_headers={"Idempotency-Key": idem})
            except openai.ConflictError as error:
                require(error.status_code == 409 and error.code == "task_conflict" and bool(error.request_id), "conflict_error_shape")
            else:
                raise RuntimeError("sdk_conflict_not_rejected")
            require(attempts[before:] == [("POST", "/v1/images/generations", idem)] * (retries + 1), "retry_identity_changed")
            row("conflict_with_retry_" + str(retries), http_status=409, error_code="task_conflict", attempts=retries + 1, stable_idempotency=True)

        try:
            client.images.generate(**{**generation, "n": 2}, extra_headers={"Idempotency-Key": "sdk-python-invalid-n-0001"})
        except openai.BadRequestError as error:
            require(error.code == "invalid_request" and bool(error.request_id), "unsupported_field_error")
            row("unsupported_n_rejected", http_status=400, error_code=error.code, request_id_present=True)
        else:
            raise RuntimeError("sdk_unsupported_n_accepted")

        for name, files, options, model in (
            ("edit_single_multipart", ("source.png", image, "image/png"), {"seed": 73}, "protocol-image-to-image"),
            ("edit_multiple_multipart", [("first.png", image, "image/png"), ("second.png", image, "image/png")],
             {"seed": 74, "image_roles": [{"index": 0, "role": "character"}, {"index": 1, "role": "scene"}]}, "protocol-references-to-image"),
        ):
            edit = client.images.edit(model=model, prompt="Synthetic SDK edit", image=files, response_format="b64_json",
                                      extra_body={"workflow_options": json.dumps(options)}, extra_headers={"Idempotency-Key": "sdk-python-" + name})
            verify_image(edit)
            row(name, image_sha256=expected_sha, workflow_options_encoding="JSON form text")

        asynchronous = client.post("/images/generations/async", cast_to=dict, body=generation, options={"headers": {"Idempotency-Key": idem}})
        require(asynchronous["object"] == "image.generation.task" and asynchronous["status"] == "completed", "async_extension_shape")
        row("async_extension_reuses_sync_job", http_status=202, extension=True)
        url_image = client.images.generate(**{**generation, "response_format": "url"}, extra_headers={"Idempotency-Key": idem})
        href = urlsplit(url_image.data[0].url)
        require(href.hostname == "127.0.0.1" and href.port == urlsplit(origin).port and href.path.startswith("/v1/content/"), "delivery_url_scope")
        time.sleep(3)
        with httpx.Client(base_url=origin, trust_env=False, follow_redirects=False, timeout=10) as delivery:
            owned = delivery.get(href.path, headers={"Authorization": "Bearer " + token})
            denied = delivery.get(href.path)
        require(owned.status_code == 200 and hashlib.sha256(owned.content).hexdigest() == expected_sha and denied.status_code == 401, "authorized_url_download")
        row("url_replay_authorized_download", http_status=200, anonymous_status=401, same_job=True,
            public_url_tls_frontend_verified=False, internal_provider_path_verified=True)

        async def async_replay() -> None:
            await asyncio.sleep(3)
            async with openai.AsyncOpenAI(api_key=token, base_url=origin + "/v1", max_retries=0, timeout=70,
                                          organization=None, project=None, http_client=httpx2.AsyncClient(trust_env=False, follow_redirects=False),
                                          _strict_response_validation=True) as asynchronous_client:
                verify_image(await asynchronous_client.images.generate(**generation, extra_headers={"Idempotency-Key": idem}))
        asyncio.run(async_replay())
        row("async_python_sdk_replay", strict_response_validation=True)
        from native_multimedia_sdk import python_multimedia

        multimedia_jobs = python_multimedia(client, root, image, row)
    require(transport.is_closed, "python_transport_not_closed")
    return {"sdk": "openai-python", "version": openai.__version__, "transport": "httpx2-" + httpx2.__version__,
            "passed": True, "cases": cases, "new_generation_jobs": 3 + multimedia_jobs, "client_closed": True,
            "minimum_inter_request_seconds": 3, "production_openai_called": False}


def exercise(origin: str, token: str, root: Path, upstream: Any, control: Any, outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    from native_ci import Gate, execute

    preflight()
    origin = loopback_origin(origin)
    image = (root / "comfy/output/result.png").read_bytes()

    def counts() -> tuple[int, int, int]:
        intents = upstream.execute("SELECT count(*) FROM render_control_intents").fetchone()[0]
        settlements = upstream.execute("SELECT count(*) FROM usage_logs").fetchone()[0]
        jobs = control.execute("SELECT count(*) FROM jobs WHERE is_validation=FALSE").fetchone()[0]
        upstream.commit()
        control.commit()
        return intents, settlements, jobs

    def media_counts() -> tuple[tuple[int, ...], tuple[int, ...]]:
        logged = upstream.execute("SELECT COALESCE(sum(image_count),0),COALESCE(sum(video_count),0),COALESCE(sum(video_duration_seconds),0),COALESCE(sum(audio_count),0),COALESCE(sum(audio_duration_milliseconds),0) FROM usage_logs").fetchone()
        measured = control.execute("SELECT COALESCE(sum(image_count),0),COALESCE(sum(video_milliseconds),0),COALESCE(sum(audio_milliseconds),0) FROM usage_ledger").fetchone()
        upstream.commit()
        control.commit()
        return tuple(int(value) for value in logged), tuple(int(value) for value in measured)

    before = counts()
    media_before = media_counts()
    try:
        python_report = python_client(origin, token, image, root)
    except BaseException:
        (root / "sdk-python-failure.log").write_text(traceback.format_exc(), encoding="utf-8")
        raise
    configuration = root / "sdk-client.json"
    result = root / "sdk-node-result.json"
    configuration.write_text(json.dumps({"origin": origin, "token": token, "image": str(root / "comfy/output/result.png"),
                                         "video": str(root / "comfy/output/result.mp4"), "audio": str(root / "comfy/output/result.wav"),
                                         "result": str(result)}), encoding="utf-8")
    try:
        import os

        env = {key: value for key, value in os.environ.items() if not key.startswith("OPENAI_")}
        node = ROOT / ".local/toolchains/node-24.22.1/node-v24.22.1-win-x64/node.exe"
        gate = Gate("openai_node_sdk", [str(node), str(ROOT / "integrations/openai-clients/client.mjs"), str(configuration)], ROOT, 240)
        node_run = execute(gate, root / "sdk-node.log", env, limit=2 * 1024 * 1024)
        require(node_run["status"] == "passed", "node_client_failed")
        require(result.is_file() and result.stat().st_size < 128 * 1024, "node_result_missing")
        node_report = json.loads(result.read_text(encoding="utf-8"))
        require(node_report["passed"] and node_report["version"] == NODE_VERSION and node_report["requests_finished"] and node_run["owned_process_tree_closed"], "node_result_invalid")
        from jsonschema import Draft202012Validator

        schema = json.loads((ROOT / "packages/contracts/src/generation_contracts/schemas/sdk-video-response.schema.json").read_text(encoding="utf-8"))
        samples = node_report.pop("video_validation_samples")
        require(isinstance(samples, list) and 5 <= len(samples) <= 64, "node_video_samples_missing")
        require(all(Draft202012Validator(schema).is_valid(sample) for sample in samples), "node_video_contract_mismatch")
        node_report["video_response_contract_samples"] = len(samples)
        node_report["client_closed"] = True
        node_report["client_cleanup"] = "owned process exit after response consumption"
    finally:
        configuration.unlink(missing_ok=True)

    expected = python_report["new_generation_jobs"] + node_report["new_generation_jobs"]
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        after = counts()
        if after == tuple(value + expected for value in before):
            break
        time.sleep(0.25)
    require(after == tuple(value + expected for value in before), "duplicate_job_or_settlement")
    media_after = media_counts()
    logged_delta, measured_delta = (tuple(new - old for new, old in zip(after_values, before_values, strict=True))
                                    for after_values, before_values in zip(media_after, media_before, strict=True))
    require(logged_delta == (6, 4, 20, 2, 1000) and measured_delta == (6, 20000, 1000), "multimedia_metering_changed")
    result_report = {"passed": True, "python": python_report, "node": node_report, "node_process": node_run,
                     "new_jobs": expected, "new_intents": after[0] - before[0], "new_usage_logs": after[1] - before[1],
                     "media_usage": {"images": 6, "videos": 4, "video_milliseconds": 20000, "video_billed_seconds": 20,
                                     "audio_count": 2, "audio_milliseconds": 1000, "cross_media_contamination": False},
                     "real_comfyui": False, "real_redis": False, "real_s3": False, "full_acceptance": "pending"}
    outcomes.append({"case": "actual_python_and_node_sdk_clients", "passed": True, "new_jobs": expected,
                     "sdk_cases": len(python_report["cases"]) + len(node_report["cases"])})
    return result_report
