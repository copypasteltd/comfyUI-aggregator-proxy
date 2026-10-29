"""Exercise eight real media modes with durable original request receipts.

One sequential chain per existing GPU; policy, presets and polling hints come
from the service. Re-running reconciles the same token/body/release; never
creates a replacement request for an uncertain original.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "aggregator-service/app"
PRIVATE = ROOT / ".local/studio-ops"
MEDIA = ROOT / ".local/studio-live-media"
sys.path[:0] = [str(path) for folder in ("packages", "apps") for path in (APP / folder).glob("*/src")]
os.environ["PATH"] = str(ROOT.parent / "生图生视API封装/app/.local/toolchains/ffmpeg-8.1/bin") + os.pathsep + os.environ["PATH"]
from generation_infrastructure.media import inspect_media  # noqa: E402

TERMINAL = {"succeeded", "failed", "cancelled", "expired"}
EXTENSIONS = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "video/mp4": ".mp4", "video/webm": ".webm", "audio/flac": ".flac", "audio/wav": ".wav", "model/gltf-binary": ".glb"}


def durable(path, value):
    pending = path.with_suffix(path.suffix + ".pending")
    with pending.open("w", encoding="utf-8") as target:
        target.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
        target.flush()
        os.fsync(target.fileno())
    pending.replace(path)


def main():
    MEDIA.mkdir(parents=True, exist_ok=True)
    state_path = PRIVATE / "live-media-state.json"
    lock_path = PRIVATE / "live-media.lock"
    with lock_path.open("x") as lock:
        lock.write(str(os.getpid()))
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {"started_at": datetime.now(UTC).isoformat(), "operations": {}, "uploads": {}, "requests": []}
    access = json.loads((PRIVATE / "validation-key.json").read_text(encoding="utf-8"))
    fingerprint = hashlib.sha256((access["origin"] + "\0" + access["secret"]).encode()).hexdigest()
    assert state.get("fingerprint", fingerprint) == fingerprint
    state["fingerprint"] = fingerprint
    client = httpx.Client(base_url=access["origin"], trust_env=False, timeout=35, headers={"Authorization": "Bearer " + access["secret"]})
    next_turn = 0
    interval = 0
    report = {"scope": "actual native production HTTP, actual ComfyUI jobs and independently downloaded original bytes; not physical-phone or fleet capacity acceptance", "passed": False}

    def save():
        durable(state_path, state)

    def call(method, path, **kwargs):
        nonlocal next_turn
        time.sleep(max(0, next_turn - time.time()))
        response = client.request(method, path, **kwargs)
        next_turn = time.time() + interval
        state["requests"].append({"method": method, "path": path, "status": response.status_code, "retry_after": response.headers.get("Retry-After")})
        save()
        if response.is_error:
            (PRIVATE / "live-media-last-error.json").write_text(response.text, encoding="utf-8")
            raise RuntimeError(f"Original {method} {path}: HTTP {response.status_code}; inspect retained private response")
        return response

    def collect(label, job):
        operation = state["operations"][label]
        if "original" in operation:
            original = operation["original"]
            path = MEDIA / original["file"]
            assert path.stat().st_size == original["metadata"]["bytes"] and hashlib.file_digest(path.open("rb"), "sha256").hexdigest() == original["metadata"]["sha256"]
            return path, original["metadata"]
        assert job["status"] == "succeeded" and len(job["artifacts"]) == 1
        content = call("GET", "/v1/content/" + job["artifacts"][0]["id"]).json()
        path = MEDIA / (label + EXTENSIONS[content["media_type"]])
        pending = path.with_suffix(path.suffix + ".pending")
        time.sleep(max(0, next_turn - time.time()))
        with client.stream("GET", "/v1/content/" + content["id"] + "/download") as response:
            response.raise_for_status()
            assert response.status_code == 200 and response.headers["content-type"].split(";")[0] == content["media_type"]
            checksum = hashlib.sha256(); size = 0
            with pending.open("wb") as output:
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    assert size <= content["bytes"]
                    checksum.update(chunk); output.write(chunk)
                output.flush(); os.fsync(output.fileno())
            assert size == content["bytes"] and checksum.hexdigest() == content["sha256"]
        pending.replace(path)
        probe = inspect_media(path, content["media_type"])
        assert content["expires_at"] - job["completed_at"] == 8 * 3600
        operation["original"] = {"file": path.name, "metadata": content, "independent_probe": probe, "bytes_sha256_verified": True, "eight_hour_deadline_verified": True}
        save()
        print(json.dumps({"verified_original": label, "job_id": job["id"], "bytes": size, "probe": probe}, ensure_ascii=False), flush=True)
        return path, content

    def upload(label, path, metadata):
        operation = state["uploads"].get(label)
        if not operation:
            operation = {"idempotency_key": "studio131-upload-" + uuid4().hex, "sha256": metadata["sha256"], "bytes": metadata["bytes"], "media_type": metadata["media_type"]}
            state["uploads"][label] = operation; save()
        assert operation["sha256"] == metadata["sha256"]
        if "asset" not in operation:
            with path.open("rb") as source:
                value = call("POST", "/v1/assets", headers={"Idempotency-Key": operation["idempotency_key"]}, files={"file": (path.name, source, metadata["media_type"])}).json()
            assert value["sha256"] == metadata["sha256"] and value["bytes"] == metadata["bytes"] and value["status"] == "ready"
            operation["asset"] = value; save()
        return operation["asset"]["id"]

    try:
        policy_response = call("GET", "/v1/access-policy")
        assert policy_response.headers["X-Studio-Contract"] == "1"
        rpm = policy_response.json()["limits"]["rpm"]
        interval = 60 / rpm["value"] if rpm["mode"] == "bounded" else 0
        catalog = call("GET", "/v1/capabilities").json()["data"]
        models = {model["id"]: model for model in catalog}
        ui = json.loads((PRIVATE / "ui-image-job.json").read_text(encoding="utf-8"))
        if "image" not in state["operations"]:
            state["operations"]["image"] = {"job_id": ui["job"]["id"], "model": "comfy-3080-image-lustify-krea2", "submission_source": "actual deployed browser UI"}; save()
        chains = [
            [("image", "comfy-3080-image-lustify-krea2"), ("edit", "comfy-3080-image-edit-wai"), ("references-image", "comfy-3080-image-refs-wai"), ("model3d", "comfy-3080-3d-trellis2"), ("audio", "comfy-3080-music-yue2")],
            [("text-video", "comfy-5070-video-h3"), ("first-frame-video", "comfy-5070-video-h3-i2v"), ("references-video", "comfy-5070-video-h3-refs")],
        ]
        while True:
            remaining = False
            for chain in chains:
                for label, model_id in chain:
                    operation = state["operations"].get(label)
                    if operation and "original" in operation:
                        continue
                    remaining = True
                    if label not in {"image", "text-video"} and "asset" not in state["uploads"].get("base", {}):
                        break
                    if label in {"references-image", "references-video"} and "asset" not in state["uploads"].get("edited", {}):
                        break
                    model = models[model_id]; usage = model["usage"]
                    if not operation:
                        body = deepcopy(usage["example_request"])
                        body["parameters"] = {key: value for key, value in usage["presets"][0].items() if key in usage["request_schema"]["properties"]["parameters"]["properties"]}
                        body["parameters"].update(n=1, seed=7313101)
                        if "prompt" in body["input"]:
                            body["input"]["prompt"] = "A friendly ivory ceramic toy robot with orange ears on a plain studio background, gentle natural motion, fixed camera, soft light, no text, no logo."
                        if label == "edit": body["input"]["prompt"] = "The same friendly ivory ceramic toy robot with orange circular ears, wearing a small blue scarf, full body, clean studio photograph, plain pale background, no text."
                        if label == "audio": body["input"]["prompt"] = "Instrumental gentle ambient chamber music, warm acoustic strings and soft piano melody, calm welcoming studio atmosphere, natural dynamics, no vocals."
                        base_asset = state["uploads"].get("base", {}).get("asset", {}).get("id")
                        if "source_image" in body["input"]: body["input"]["source_image"] = {"asset_id": base_asset}
                        for index, reference in enumerate(body["input"].get("references", [])):
                            reference["asset_id"] = base_asset if index == 0 else state["uploads"]["edited"]["asset"]["id"]
                        Draft202012Validator(usage["request_schema"]).validate(body)
                        operation = {"model": model_id, "body": json.dumps(body, ensure_ascii=False, separators=(",", ":")), "release_digest": model["release"]["digest"], "idempotency_key": "studio131-media-" + uuid4().hex, "prepared_at": datetime.now(UTC).isoformat()}
                        state["operations"][label] = operation; save()
                    if "job_id" not in operation:
                        response = call("POST", "/v1/jobs", content=operation["body"].encode(), headers={"Content-Type": "application/json", "Idempotency-Key": operation["idempotency_key"], "X-Expected-Release-Digest": operation["release_digest"]})
                        job = response.json(); operation["job_id"] = job["id"]; operation["next_poll"] = time.time() + float(response.headers.get("Retry-After", 2)); operation["job"] = job; save()
                        print(json.dumps({"actual_submit": label, "job_id": job["id"], "model": model_id}), flush=True)
                    if time.time() < operation.get("next_poll", 0): break
                    response = call("GET", "/v1/jobs/" + operation["job_id"])
                    job = response.json(); old_status = operation.get("job", {}).get("status"); operation["job"] = job
                    operation["next_poll"] = time.time() + float(response.headers.get("Retry-After", 2)); save()
                    if job["status"] != old_status: print(json.dumps({"actual_status": label, "job_id": job["id"], "status": job["status"]}), flush=True)
                    if job["status"] in TERMINAL:
                        assert job["status"] == "succeeded", f"Original {label} ended {job['status']}: {(job.get('error') or {}).get('code')}"
                        original_path, metadata = collect(label, job)
                        if label == "image": upload("base", original_path, metadata)
                        if label == "edit": upload("edited", original_path, metadata)
                    break
            if not remaining: break
            time.sleep(2)
        report["passed"] = True
    except BaseException as error:
        report["error_type"] = type(error).__name__
        print(json.dumps({"passed": False, "error_type": type(error).__name__, "detail": str(error)}), flush=True)
        raise
    finally:
        report.update({"finished_at": datetime.now(UTC).isoformat(), "operations": [{"mode": name, "model": operation.get("model"), "job_id": operation.get("job_id"), "status": operation.get("job", {}).get("status"), "original": operation.get("original")} for name, operation in state["operations"].items()], "requests": state["requests"], "virtualization_used": False})
        durable(ROOT / "docs/research/studio-implementation/live-media-131.json", report)
        client.close(); lock_path.unlink()


if __name__ == "__main__":
    main()
