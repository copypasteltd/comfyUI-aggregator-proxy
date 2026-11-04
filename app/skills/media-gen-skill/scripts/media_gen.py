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
UPLOAD_LIMITS = {"image": UPLOAD_LIMIT, "video": 512 * 1024**2, "audio": 256 * 1024**2, "model": 512 * 1024**2}
MIME_SUFFIXES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "video/mp4": ".mp4", "video/webm": ".webm", "audio/wav": ".wav", "audio/flac": ".flac", "audio/mpeg": ".mp3", "audio/ogg": ".opus", "model/gltf-binary": ".glb"}
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
                                     headers={"Authorization": "Bearer " + self.key, "Accept": "application/json", "User-Agent": "media-gen-skill/1.3.0", **(headers or {})})
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

    def upload(self, path, kind="image", token=None):
        path = Path(path)
        if kind not in UPLOAD_LIMITS:
            raise ClientError("Unknown upload kind")
        with path.open("rb") as source:
            size = os.fstat(source.fileno()).st_size
            if not 0 < size <= UPLOAD_LIMITS[kind]:
                raise ClientError("Upload exceeds the declared " + kind + " byte bound")
            prefix = source.read(64)
            source.seek(0)
            mime = upload_mime(prefix, kind)
            boundary = "media-gen-" + uuid4().hex
            # Fixed fields and filenames prevent path/header injection. The server
            # independently decodes the complete media and enforces the slot type.
            head = ("--" + boundary + '\r\nContent-Disposition: form-data; name="kind"\r\n\r\n' + kind + "\r\n--" + boundary + '\r\nContent-Disposition: form-data; name="file"; filename="input' + MIME_SUFFIXES[mime] + '"\r\nContent-Type: ' + mime + "\r\n\r\n").encode()
            tail = ("\r\n--" + boundary + "--\r\n").encode()
            headers = {"Content-Type": "multipart/form-data; boundary=" + boundary, "Content-Length": str(len(head) + size + len(tail)), "Idempotency-Key": token or uuid4().hex}
            with self.open("POST", "/v1/assets", multipart_chunks(source, size, head, tail), headers) as response:
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


def upload_mime(prefix, kind):
    formats = {
        "image": [(prefix.startswith(b"\x89PNG\r\n\x1a\n"), "image/png"), (prefix.startswith(b"\xff\xd8\xff"), "image/jpeg"), (prefix[:4] == b"RIFF" and prefix[8:12] == b"WEBP", "image/webp")],
        "video": [(prefix[4:8] == b"ftyp", "video/mp4"), (prefix.startswith(b"\x1aE\xdf\xa3"), "video/webm")],
        "audio": [(prefix[:4] == b"RIFF" and prefix[8:12] == b"WAVE", "audio/wav"), (prefix.startswith(b"fLaC"), "audio/flac"), (prefix.startswith(b"OggS"), "audio/ogg"), (prefix.startswith(b"ID3") or len(prefix) >= 2 and prefix[0] == 255 and prefix[1] & 224 == 224, "audio/mpeg")],
        "model": [(prefix.startswith(b"glTF\x02\x00\x00\x00"), "model/gltf-binary")],
    }
    mime = next((mime for matches, mime in formats[kind] if matches), None)
    if mime is None:
        raise ClientError("File signature does not match upload kind " + kind)
    return mime


def multipart_chunks(source, size, head, tail):
    yield head
    remaining = size
    while remaining:
        block = source.read(min(65536, remaining))
        if not block:
            raise ClientError("Upload file changed while reading")
        remaining -= len(block)
        yield block
    yield tail


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
                    ext = MIME_SUFFIXES.get(meta.get("media_type")) or mimetypes.guess_extension(meta.get("media_type", "")) or ".bin"
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
    properties = usage["request_schema"]["properties"]
    input_properties = properties["input"]["properties"]
    prompt = Path(args.prompt_file).read_text("utf-8") if args.prompt_file else args.prompt
    if "prompt" not in input_properties:
        if prompt is not None or args.negative_prompt is not None:
            raise ClientError("This workflow accepts its declared media inputs (legacy 3D: image only); omit undeclared text prompts")
        body["input"].pop("prompt", None)
    else:
        if not prompt or len(prompt) > 8000:
            raise ClientError("Provide a prompt of 1–8000 characters, or a structured prompt file")
        body["input"]["prompt"] = prompt
    if args.negative_prompt is not None:
        if "negative_prompt" not in input_properties:
            raise ClientError("This model does not accept negative_prompt")
        body["input"]["negative_prompt"] = args.negative_prompt
    named_inputs(api, args, body, usage)
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


def assignments(values):
    result = {}
    for item in values or []:
        name, separator, value = item.partition("=")
        if not separator or not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", name) or not value or name in result:
            raise ClientError("Use each declared slot once as NAME=VALUE")
        result[name] = value
    return result


def named_inputs(api, args, body, usage):
    media = assignments(args.media)
    texts, text_files = assignments(args.text), assignments(args.text_file)
    if texts.keys() & text_files.keys():
        raise ClientError("A text slot cannot have both inline text and a text file")
    declared_media = {slot["name"]: slot for slot in usage.get("input_slots", [])}
    declared_text = {slot["name"]: slot for slot in usage.get("text_slots", [])}
    for supplied, declared in ((media, declared_media), ({**texts, **text_files}, declared_text)):
        if supplied.keys() - declared.keys():
            raise ClientError("Unknown named input slot; inspect describe")
        if any(slot["required"] and name not in supplied for name, slot in declared.items()):
            raise ClientError("Missing required named input slot; inspect describe")
    for name, path in text_files.items():
        file = Path(path)
        if file.stat().st_size > 128000:
            raise ClientError("Text input file exceeds bound")
        texts[name] = file.read_text("utf-8")
    if sum(len(value) for value in texts.values()) > 32000 or any(not value.strip() or len(value) > declared_text[name]["max_length"] for name, value in texts.items()):
        raise ClientError("Text input is empty or exceeds its declared bound")
    context = usage.get("context") or {}
    if args.parent_job:
        if not context.get("required"):
            raise ClientError("This model does not consume a parent context")
        body["input"]["context"] = {"job_id": identifier(args.parent_job, "job")}
    elif context.get("required"):
        raise ClientError("This continuation model requires --parent-job from the same Key")
    else:
        body["input"].pop("context", None)
    for name, path in media.items():
        if Path(path).stat().st_size > declared_media[name]["max_bytes"]:
            raise ClientError("File exceeds the named slot's byte bound")
    if declared_text or texts:
        body["input"]["texts"] = texts
    else:
        body["input"].pop("texts", None)
    if declared_media or media:
        body["input"]["media"] = [{"slot": name, "asset_id": api.upload(path, kind=declared_media[name]["kind"])["id"]} for name, path in media.items()]
    else:
        body["input"].pop("media", None)


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--base-url", default=None)
    sub = root.add_subparsers(dest="command", required=True)
    sub.add_parser("models")
    sub.add_parser("describe").add_argument("model")
    sub.add_parser("capabilities").add_argument("--model")
    upload = sub.add_parser("upload")
    upload.add_argument("file")
    upload.add_argument("--kind", choices=tuple(UPLOAD_LIMITS), default="image")
    upload.add_argument("--idempotency-key")
    generate = sub.add_parser("generate")
    generate.add_argument("--model", required=True)
    prompt = generate.add_mutually_exclusive_group(required=False)
    prompt.add_argument("--prompt")
    prompt.add_argument("--prompt-file")
    generate.add_argument("--source-image")
    generate.add_argument("--reference", action="append")
    generate.add_argument("--negative-prompt")
    generate.add_argument("--media", action="append", help="Declared SLOT=FILE (image, video, audio or GLB)")
    generate.add_argument("--text", action="append", help="Declared NAME=TEXT")
    generate.add_argument("--text-file", action="append", help="Declared NAME=FILE containing UTF-8 text")
    generate.add_argument("--parent-job", help="Successful compatible parent job owned by the same API Key")
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
        result = api.upload(args.file, kind=args.kind, token=args.idempotency_key)
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
