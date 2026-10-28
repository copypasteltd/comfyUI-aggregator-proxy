#!/usr/bin/env python3
"""Portable MediaGen client. Python 3.10+, standard library only."""
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from uuid import uuid4

DEFAULT_BASE = "https://gen.sub2share.xyz"
JSON_LIMIT = 8 * 1024 * 1024
UPLOAD_LIMIT = 20 * 1024 * 1024
DOWNLOAD_LIMIT = 2 * 1024 * 1024 * 1024
TERMINAL = {"succeeded", "failed", "cancelled", "expired"}


class ClientError(Exception):
    pass


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def identifier(value, prefix):
    if not isinstance(value, str) or not re.fullmatch(prefix + r"_[a-zA-Z0-9_-]{8,128}", value):
        raise ClientError("Invalid " + prefix + " identifier")
    return value


def private_write(path, value, exclusive=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
            stream.write(canonical(value) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        if exclusive:
            os.link(tmp, path)
        else:
            os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ClientError("Redirect refused; credentials remain on the configured origin")


class Api:
    def __init__(self, base=None, key=None):
        self.base = (base or os.environ.get("MEDIA_GEN_BASE_URL") or DEFAULT_BASE).rstrip("/")
        url = urllib.parse.urlsplit(self.base)
        if url.scheme not in {"https", "http"} or not url.hostname or url.username or url.password or url.path or url.query or url.fragment:
            raise ClientError("Base URL must be an HTTP(S) origin without path or credentials")
        self.key = key or os.environ.get("MEDIA_GEN_API_KEY", "")
        if not self.key or any(c in self.key for c in "\r\n"):
            raise ClientError("Set MEDIA_GEN_API_KEY to a business API Key")
        self.key_fingerprint = hashlib.sha256(self.key.encode()).hexdigest()
        self.opener = urllib.request.build_opener(NoRedirect())

    def open(self, method, path, body=None, headers=None):
        if not path.startswith("/v1/") or path.startswith("//"):
            raise ClientError("Only the configured origin's /v1/ API is allowed")
        req = urllib.request.Request(self.base + path, data=body, method=method,  # noqa: S310 -- constructor validates HTTP(S) origin; path is restricted above.
                                     headers={"Authorization": "Bearer " + self.key, "Accept": "application/json", "User-Agent": "media-gen-skill/1.1.0", **(headers or {})})
        try:
            return self.opener.open(req, timeout=25)
        except urllib.error.HTTPError as exc:
            # Server error messages can contain submitted data: never echo raw bodies.
            retry = exc.headers.get("Retry-After", "")
            exc.close()
            suffix = "; retry after " + retry + " seconds" if retry.isdigit() else ""
            raise ClientError("HTTP " + str(exc.code) + suffix + "; check Key scopes, model usage and saved state") from None
        except (OSError, TimeoutError) as exc:
            raise ClientError("Transport outcome unknown (" + type(exc).__name__ + "); resume the saved state with the same Key") from None

    def request(self, method, path, value=None, token=None):
        headers = {}
        if value is not None:
            headers["Content-Type"] = "application/json"
        if token:
            headers["Idempotency-Key"] = token
        with self.open(method, path, canonical(value) if value is not None else None, headers) as response:
            body = response.read(JSON_LIMIT + 1)
        if len(body) > JSON_LIMIT:
            raise ClientError("JSON response exceeded 8 MiB")
        try:
            return json.loads(body)
        except (ValueError, UnicodeError):
            raise ClientError("Invalid JSON response") from None

    def describe(self, alias):
        return self.request("GET", "/v1/models/" + urllib.parse.quote(alias, safe=""))

    def upload(self, path):
        path = Path(path)
        if path.stat().st_size > UPLOAD_LIMIT:
            raise ClientError("Reference file exceeds 20 MiB")
        data = path.read_bytes()
        mime = "image/png" if data.startswith(b"\x89PNG\r\n\x1a\n") else "image/jpeg" if data.startswith(b"\xff\xd8\xff") else "image/webp" if data[:4] == b"RIFF" and data[8:12] == b"WEBP" else None
        if mime is None:
            raise ClientError("Only static PNG, JPEG or WebP references are supported")
        boundary = "media-gen-" + uuid4().hex
        # Fixed filename prevents filename/path header injection. Server validates dimensions.
        body = ("--" + boundary + '\r\nContent-Disposition: form-data; name="file"; filename="reference' + (mimetypes.guess_extension(mime) or ".bin") + '"\r\nContent-Type: ' + mime + "\r\n\r\n").encode() + data + ("\r\n--" + boundary + "--\r\n").encode()
        with self.open("POST", "/v1/assets", body, {"Content-Type": "multipart/form-data; boundary=" + boundary}) as response:
            raw = response.read(JSON_LIMIT + 1)
        if len(raw) > JSON_LIMIT:
            raise ClientError("Upload response exceeded bound")
        result = json.loads(raw)
        identifier(result.get("id"), "asset")
        return result

    def download(self, content, destination):
        content = identifier(content, "content")
        metadata = self.request("GET", "/v1/content/" + content)
        # Metadata uses size_bytes and sha256 in the platform contract.
        size = metadata.get("size_bytes", metadata.get("bytes"))
        digest = metadata.get("sha256")
        if not isinstance(size, int) or not 0 < size <= DOWNLOAD_LIMIT or not isinstance(digest, str) or not re.fullmatch("[a-f0-9]{64}", digest):
            raise ClientError("Content metadata lacks valid size or SHA-256")
        expiry = metadata.get("expires_at")
        if isinstance(expiry, (int, float)) and expiry <= time.time():
            raise ClientError("Content has expired")
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise ClientError("Output already exists; choose a different path")
        part = destination.with_name(destination.name + "." + uuid4().hex + ".part")
        checksum, count, started = hashlib.sha256(), 0, time.monotonic()
        try:
            with self.open("GET", "/v1/content/" + content + "/download") as response, part.open("xb") as stream:
                while True:
                    chunk = response.read(65536)
                    if not chunk:
                        break
                    count += len(chunk)
                    if count > size or time.monotonic() - started > 1800:
                        raise ClientError("Download exceeded expected size or time budget")
                    checksum.update(chunk)
                    stream.write(chunk)
                stream.flush()
                os.fsync(stream.fileno())
            if count != size or checksum.hexdigest() != digest:
                raise ClientError("Download failed length/SHA-256 verification")
            # Hard-link publishes atomically without replacing a user's file.
            try:
                os.link(part, destination)
            except FileExistsError:
                raise ClientError("Output appeared during download; refusing overwrite") from None
            except OSError:
                # Filesystems without hard links still use exclusive creation.
                with destination.open("xb") as stream, part.open("rb") as source:
                    while chunk := source.read(65536):
                        stream.write(chunk)
            return {"content_id": content, "path": str(destination.resolve()), "bytes": count, "sha256": digest}
        finally:
            part.unlink(missing_ok=True)


def read_json(path):
    raw = Path(path).read_bytes()
    if len(raw) > JSON_LIMIT:
        raise ClientError("Input JSON exceeded 8 MiB")
    return json.loads(raw)


def submit(api, body, path, token=None):
    path = Path(path)
    if path.exists():
        raise ClientError("State already exists; use resume")
    if not isinstance(body, dict) or not isinstance(body.get("model"), str):
        raise ClientError("Request must contain a model; inspect describe first")
    state = {"schema_version": 1, "base_url": api.base, "key_fingerprint": api.key_fingerprint,
             "idempotency_key": token or uuid4().hex, "request": body,
             "request_sha256": hashlib.sha256(canonical(body)).hexdigest(), "job_id": None}
    if not 1 <= len(state["idempotency_key"]) <= 128 or any(c in state["idempotency_key"] for c in "\r\n"):
        raise ClientError("Invalid Idempotency-Key")
    private_write(path, state, exclusive=True)
    return resume(api, path)


def resume(api, path):
    state = read_json(path)
    if state.get("schema_version") != 1 or state.get("base_url") != api.base:
        raise ClientError("State belongs to another API origin or version")
    if hashlib.sha256(canonical(state["request"])).hexdigest() != state.get("request_sha256"):
        raise ClientError("Saved request changed; refusing an unsafe retry")
    if state.get("job_id"):
        return api.request("GET", "/v1/jobs/" + identifier(state["job_id"], "job"))
    if state.get("key_fingerprint") != api.key_fingerprint:
        raise ClientError("Unknown submission requires the original API Key")
    result = api.request("POST", "/v1/jobs", state["request"], state["idempotency_key"])
    state["job_id"] = identifier(result.get("id"), "job")
    private_write(path, state)
    return result


def wait_job(api, job_id, timeout, output=None):
    identifier(job_id, "job")
    deadline = time.monotonic() + timeout
    while True:
        job = api.request("GET", "/v1/jobs/" + job_id)
        if job.get("status") in TERMINAL:
            if job["status"] != "succeeded":
                raise ClientError("Job finished with status " + job["status"] + "; inspect status")
            if output is not None:
                saved = []
                for entry in job.get("artifacts", []):
                    content = identifier(entry.get("id"), "content")
                    meta = api.request("GET", "/v1/content/" + content)
                    ext = ".glb" if meta.get("media_type") == "model/gltf-binary" else mimetypes.guess_extension(meta.get("media_type", "")) or ".bin"
                    saved.append(api.download(content, Path(output) / (content + ext)))
                if not saved:
                    raise ClientError("Succeeded without visible artifacts; check artifacts:read scope")
                job["downloads"] = saved
            return job
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ClientError("Polling timed out; task remains on the server. Use status or resume")
        time.sleep(min(2, remaining))


def generation_request(api, args):
    model = api.describe(args.model)
    usage = model.get("usage")
    if not usage:
        raise ClientError("Model has no complete usage contract; choose another model")
    body = json.loads(json.dumps(usage["example_request"]))
    prompt = Path(args.prompt_file).read_text("utf-8") if args.prompt_file else args.prompt
    if body["task_type"] == "image_to_3d":
        if prompt is not None or args.negative_prompt is not None:
            raise ClientError("This image-to-3D workflow accepts an image only; omit text prompts")
    else:
        if not prompt or len(prompt) > 8000:
            raise ClientError("Provide a prompt of 1–8000 characters, or a structured prompt file")
        body["input"]["prompt"] = prompt
    if args.negative_prompt is not None:
        body["input"]["negative_prompt"] = args.negative_prompt
    properties = usage["request_schema"]["properties"]
    input_properties = properties["input"]["properties"]
    references = usage["references"]
    if args.source_image:
        if "source_image" not in input_properties:
            raise ClientError("This model does not accept source_image")
        body["input"]["source_image"] = {"asset_id": api.upload(args.source_image)["id"]}
    elif "source_image" in body["input"]:
        raise ClientError("This model requires --source-image")
    paths = args.reference or []
    if len(paths) != references.get("exact_reference_count", 0):
        raise ClientError("Model requires exactly " + str(references.get("exact_reference_count", 0)) + " --reference files in slot order")
    for slot, file in zip(body["input"].get("references", []), paths, strict=True):
        slot["asset_id"] = api.upload(file)["id"]
    parameters = body.setdefault("parameters", {})
    declared = properties["parameters"]["properties"]
    for name in ("size", "quality", "seconds", "seed", "fps", "strength"):
        value = getattr(args, name)
        if value is not None:
            if name not in declared:
                raise ClientError("Model does not expose parameter " + name)
            rule = declared[name]
            if "enum" in rule and value not in rule["enum"]:
                raise ClientError("Unsupported " + name + "; inspect advertised presets")
            if "minimum" in rule and value < rule["minimum"] or "maximum" in rule and value > rule["maximum"]:
                raise ClientError("Parameter outside declared range: " + name)
            parameters[name] = value
    selected = {**usage.get("defaults", {}), **parameters}
    if not any(all(selected.get(k) == v for k, v in preset.items() if k in {"size", "quality", "seconds"}) for preset in usage["presets"]):
        raise ClientError("Parameters do not select an advertised preset combination")
    return body


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--base-url", default=None)
    sub = root.add_subparsers(dest="command", required=True)
    sub.add_parser("models")
    sub.add_parser("describe").add_argument("model")
    sub.add_parser("capabilities").add_argument("--model")
    sub.add_parser("upload").add_argument("file")
    generate = sub.add_parser("generate")
    generate.add_argument("--model", required=True)
    prompt = generate.add_mutually_exclusive_group(required=False)
    prompt.add_argument("--prompt")
    prompt.add_argument("--prompt-file")
    generate.add_argument("--source-image")
    generate.add_argument("--reference", action="append")
    generate.add_argument("--negative-prompt")
    for field, kind in (("size", str), ("quality", str), ("seconds", float), ("seed", int), ("fps", int), ("strength", float)):
        generate.add_argument("--" + field, type=kind)
    create = sub.add_parser("submit")
    create.add_argument("request")
    create.add_argument("--idempotency-key")
    for command in (generate, create):
        command.add_argument("--state-file")
        command.add_argument("--output-dir", default="media-gen-output")
        command.add_argument("--wait", action="store_true")
        command.add_argument("--timeout", type=int, default=900)
    recover = sub.add_parser("resume")
    recover.add_argument("state_file")
    recover.add_argument("--wait", action="store_true")
    recover.add_argument("--timeout", type=int, default=900)
    recover.add_argument("--output-dir", default="media-gen-output")
    sub.add_parser("status").add_argument("job_id")
    wait = sub.add_parser("wait")
    wait.add_argument("job_id")
    wait.add_argument("--timeout", type=int, default=900)
    wait.add_argument("--download", action="store_true")
    wait.add_argument("--output-dir", default="media-gen-output")
    download = sub.add_parser("download")
    download.add_argument("content_id")
    download.add_argument("--output", required=True)
    return root


def main(argv=None):
    # Machine-readable JSON remains UTF-8 when Windows redirects stdout.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = parser().parse_args(argv)
    if hasattr(args, "timeout") and not 1 <= args.timeout <= 86400:
        raise ClientError("Timeout must be 1–86400 seconds")
    api = Api(args.base_url)
    if args.command == "models":
        result = api.request("GET", "/v1/models")
    elif args.command == "describe":
        result = api.describe(args.model)
    elif args.command == "capabilities":
        result = api.request("GET", "/v1/capabilities" + ("?model=" + urllib.parse.quote(args.model, safe="") if args.model else ""))
    elif args.command == "upload":
        result = api.upload(args.file)
    elif args.command == "status":
        result = api.request("GET", "/v1/jobs/" + identifier(args.job_id, "job"))
    elif args.command == "download":
        result = api.download(args.content_id, args.output)
    elif args.command == "wait":
        result = wait_job(api, args.job_id, args.timeout, args.output_dir if args.download else None)
    else:
        if args.command == "resume":
            state_path = args.state_file
            result = resume(api, state_path)
        else:
            body = generation_request(api, args) if args.command == "generate" else read_json(args.request)
            state_path = args.state_file or str(Path(args.output_dir) / ("request-" + uuid4().hex + ".json"))
            # Print recovery path before network submission, without exposing request/Key.
            print("State: " + str(Path(state_path).resolve()), file=sys.stderr, flush=True)
            result = submit(api, body, state_path, getattr(args, "idempotency_key", None))
        if args.wait:
            result = wait_job(api, result["id"], args.timeout, args.output_dir)
        result = {**result, "state_file": str(Path(state_path).resolve())}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ClientError, OSError, ValueError, KeyError) as error:
        # Avoid tracebacks that could reveal request/credential state.
        print("MediaGen: " + str(error) if isinstance(error, ClientError) else "MediaGen: local input or response error (" + type(error).__name__ + ")", file=sys.stderr)
        raise SystemExit(2) from None
