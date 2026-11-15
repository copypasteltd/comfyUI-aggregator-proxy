#!/usr/bin/env python3
"""Direct, authenticated ComfyUI workflows through /origin. Python 3.10+, stdlib."""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.cookies import SimpleCookie
from pathlib import Path
from uuid import uuid4

VERSION = "1.4.1"
DEFAULT_BASE = "https://gen.sub2share.xyz"
JSON_LIMIT = 16 * 1024**2
GRAPH_LIMIT = 8 * 1024**2
UPLOAD_LIMIT = 127 * 1024**2
DOWNLOAD_LIMIT = 2 * 1024**3
COOKIE = "comfy_origin_session"
SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".mp4", ".webm", ".mov", ".mkv", ".wav", ".mp3", ".flac", ".ogg", ".m4a", ".glb", ".obj", ".ply"}


class OriginError(Exception):
    pass


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def read_json(path, maximum=JSON_LIMIT):
    with Path(path).open("rb") as source:
        raw = source.read(maximum + 1)
    if len(raw) > maximum:
        raise OriginError("Local JSON exceeds its byte limit")
    return json.loads(raw)


def private_write(path, value, exclusive=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
            stream.write(canonical(value) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        if exclusive:
            os.link(temporary, path)
        else:
            os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def origin(value):
    value = value.rstrip("/")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment or any(c.isspace() for c in value):
        raise OriginError("Base URL must be an HTTP(S) origin without credentials or a path")
    return value


def component(value, label):
    if not isinstance(value, str) or not value or len(value) > 255 or value in {".", ".."} or any(c in value for c in "/\\:%?#\r\n") or any(ord(c) < 32 for c in value):
        raise OriginError("Invalid " + label)
    return urllib.parse.quote(value, safe="")


def native_path(slug, path=""):
    if not isinstance(slug, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,159}", slug):
        raise OriginError("Use a device slug returned by devices")
    parsed = urllib.parse.urlsplit(path)
    if parsed.scheme or parsed.netloc or parsed.fragment or parsed.path.startswith("/"):
        raise OriginError("Use a relative ComfyUI path, never a device address or URL")
    encoded = "/".join(component(part, "native path") for part in parsed.path.split("/")) if parsed.path else ""
    query = urllib.parse.urlencode(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    return "/origin/" + slug + "/" + encoded + ("?" + query if query else "")


def descriptor(value):
    if not isinstance(value, dict):
        raise OriginError("Expected a ComfyUI file descriptor")
    name, folder, kind = value.get("filename", value.get("name")), value.get("subfolder", ""), value.get("type", "output")
    component(name, "filename")
    if not isinstance(folder, str) or len(folder) > 1024 or folder.startswith(("/", "\\")) or "\\" in folder:
        raise OriginError("Invalid file subfolder")
    for part in folder.split("/") if folder else []:
        component(part, "file subfolder")
    if kind not in {"input", "output", "temp"}:
        raise OriginError("Invalid ComfyUI file type")
    return {"filename": name, "subfolder": folder, "type": kind}


def output_files(value):
    pending, result, seen, visited = [value], [], set(), 0
    while pending and visited < 20000 and len(result) < 1000:
        item = pending.pop()
        visited += 1
        if isinstance(item, dict):
            if "filename" in item:
                try:
                    file = descriptor(item)
                except OriginError:
                    pass
                else:
                    key = tuple(file[k] for k in ("filename", "subfolder", "type"))
                    if key not in seen:
                        seen.add(key)
                        result.append(file)
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return result


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise OriginError("Redirect refused; native credentials stay on the configured origin")


class Origin:
    def __init__(self, base=None, session_file=".comfy-origin-session.json"):
        self.base = origin(base or os.environ.get("MEDIA_GEN_BASE_URL") or DEFAULT_BASE)
        self.session_file = Path(session_file)
        self.session = read_json(self.session_file, 16384) if self.session_file.exists() else None
        if self.session and (self.session.get("schema_version") != 1 or self.session.get("base_url") != self.base):
            raise OriginError("Session belongs to another origin or version; choose another session file")
        self.opener = urllib.request.build_opener(NoRedirect())

    def open(self, method, path, body=None, headers=None, authenticated=True):
        if not path.startswith("/origin/") or any(c in path for c in "\r\n\\") or any(part in {".", ".."} for part in urllib.parse.unquote(path).split("/")):
            raise OriginError("Only this origin's /origin/ routes are allowed")
        wire = {"Accept": "application/json", "Accept-Encoding": "identity", "User-Agent": "media-gen-skill/" + VERSION + " origin", **(headers or {})}
        if authenticated:
            if not self.session:
                raise OriginError("Run login first using an explicitly device-authorized API Key")
            token, csrf = self.session.get("cookie", ""), self.session.get("csrf_token", "")
            if not token or not csrf or any(c in token + csrf for c in "\r\n;"):
                raise OriginError("Invalid private session file")
            wire["Cookie"] = COOKIE + "=" + token
            if method not in {"GET", "HEAD"}:
                wire["X-Origin-CSRF"] = csrf
        request = urllib.request.Request(self.base + path, data=body, headers=wire, method=method)  # noqa: S310 -- checked HTTP(S) origin, fixed /origin/ prefix, no redirects
        try:
            return self.opener.open(request, timeout=45)
        except urllib.error.HTTPError as exc:
            code = ""
            try:
                data = json.loads(exc.read(16384))
                candidate = data.get("error", {}).get("code", "")
                if isinstance(candidate, str) and re.fullmatch("[a-z0-9_]{1,96}", candidate):
                    code = " (" + candidate + ")"
            except (ValueError, AttributeError, OSError):
                pass
            finally:
                exc.close()
            raise OriginError("HTTP " + str(exc.code) + code + "; preserve saved submission state; inspect authorization, reservation and device status") from None
        except (OSError, TimeoutError, http.client.HTTPException) as exc:
            raise OriginError("Transport outcome unknown (" + type(exc).__name__ + "); preserve the exact saved state and never create a replacement submission") from None

    def request(self, method, path, value=None, headers=None, authenticated=True):
        wire = {"Content-Type": "application/json", **(headers or {})} if value is not None else headers
        with self.open(method, path, canonical(value) if value is not None else None, wire, authenticated) as response:
            raw = response.read(JSON_LIMIT + 1)
        if len(raw) > JSON_LIMIT:
            raise OriginError("Response exceeds 16 MiB")
        return json.loads(raw) if raw else {}

    def login(self, key=None):
        key = key or os.environ.get("MEDIA_GEN_API_KEY", "")
        if not key or any(c in key for c in "\r\n"):
            raise OriginError("Set MEDIA_GEN_API_KEY to your device-authorized business API Key")
        with self.open("POST", "/origin/api/session", canonical({"api_key": key}), {"Content-Type": "application/json"}, authenticated=False) as response:
            result = json.loads(response.read(16384))
            cookies = SimpleCookie()
            for header in response.headers.get_all("Set-Cookie", []):
                cookies.load(header)
        if COOKIE not in cookies or not isinstance(result.get("csrf_token"), str):
            raise OriginError("Server did not return a native session")
        self.session = {"schema_version": 1, "base_url": self.base, "cookie": cookies[COOKIE].value, "csrf_token": result["csrf_token"], "expires_at": result.get("expires_at"), "key_fingerprint": hashlib.sha256(key.encode()).hexdigest()}
        private_write(self.session_file, self.session)
        return {"session_file": str(self.session_file.resolve()), "expires_at": self.session["expires_at"], "native_access": "explicit device grants; trusted operator"}

    def logout(self):
        self.request("DELETE", "/origin/api/session")
        self.session_file.unlink(missing_ok=True)
        self.session = None
        return {"logged_out": True, "tasks_cancelled": False}

    def upload(self, slug, path, original=None):
        path = Path(path)
        suffix = path.suffix.lower()
        if suffix not in SUFFIXES:
            raise OriginError("File extension is not supported by the native upload route")
        boundary = "origin-" + uuid4().hex
        fields = [] if original is None else [("original_ref", json.dumps(descriptor(original)))]
        head = b"".join(("--" + boundary + '\r\nContent-Disposition: form-data; name="' + key + '"\r\n\r\n' + value + "\r\n").encode() for key, value in fields)
        head += ("--" + boundary + '\r\nContent-Disposition: form-data; name="image"; filename="input' + suffix + '"\r\nContent-Type: application/octet-stream\r\n\r\n').encode()
        tail = ("\r\n--" + boundary + "--\r\n").encode()
        with path.open("rb") as stream:
            size = os.fstat(stream.fileno()).st_size
            if not 0 < size <= UPLOAD_LIMIT:
                raise OriginError("Native upload must be 1 byte to 127 MiB")

            def chunks():
                yield head
                remaining = size
                while remaining:
                    block = stream.read(min(65536, remaining))
                    if not block:
                        raise OriginError("Upload file changed during streaming")
                    remaining -= len(block)
                    yield block
                yield tail

            route = "upload/image" if original is None else "upload/mask"
            with self.open("POST", native_path(slug, route), chunks(), {"Content-Type": "multipart/form-data; boundary=" + boundary, "Content-Length": str(len(head) + size + len(tail))}) as response:
                raw = response.read(65537)
        if len(raw) > 65536:
            raise OriginError("Upload response exceeds its bound")
        result = json.loads(raw)
        return {**result, "descriptor": descriptor({**result, "type": result.get("type", "input")})}

    def download(self, slug, file, destination, expected_sha256=None):
        file = descriptor(file)
        if expected_sha256 and not re.fullmatch("[a-f0-9]{64}", expected_sha256):
            raise OriginError("Expected SHA-256 must be 64 lowercase hexadecimal characters")
        path = native_path(slug, "view") + "?" + urllib.parse.urlencode(file)
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise OriginError("Output already exists; choose another path")
        temporary = destination.with_name(destination.name + "." + uuid4().hex + ".part")
        checksum, count, started, expected_length = hashlib.sha256(), 0, time.monotonic(), None
        try:
            with self.open("GET", path) as response, temporary.open("xb") as stream:
                length = response.headers.get("Content-Length")
                if length is not None:
                    if not length.isdigit() or not 0 < int(length) <= DOWNLOAD_LIMIT:
                        raise OriginError("Invalid or excessive download length")
                    expected_length = int(length)
                while chunk := response.read(65536):
                    count += len(chunk)
                    if count > DOWNLOAD_LIMIT or time.monotonic() - started > 1800:
                        raise OriginError("Download exceeds 2 GiB or 30 minutes")
                    checksum.update(chunk)
                    stream.write(chunk)
                stream.flush()
                os.fsync(stream.fileno())
            if not count or expected_length is not None and count != expected_length or expected_sha256 and checksum.hexdigest() != expected_sha256:
                raise OriginError("Download failed length or supplied SHA-256 verification")
            try:
                os.link(temporary, destination)
            except FileExistsError:
                raise OriginError("Output appeared during download; refusing overwrite") from None
            except OSError:
                with destination.open("xb") as stream, temporary.open("rb") as source:
                    while chunk := source.read(65536):
                        stream.write(chunk)
            return {"path": str(destination.resolve()), "bytes": count, "sha256": checksum.hexdigest(), "source_checksum_verified": expected_sha256 is not None, "content_length_verified": expected_length is not None}
        finally:
            temporary.unlink(missing_ok=True)


def submit(api, slug, body, path):
    native_path(slug)
    if not isinstance(body, dict) or "nodes" in body:
        raise OriginError("Use an API-format graph, not a UI nodes/links export")
    body = json.loads(canonical(body))
    if "prompt" not in body:
        body = {"prompt": body}
    graph = body.get("prompt")
    if not isinstance(graph, dict) or not graph or len(graph) > 3000 or not all(isinstance(node, dict) and isinstance(node.get("class_type"), str) and isinstance(node.get("inputs"), dict) for node in graph.values()):
        raise OriginError("Provide 1-3000 API graph nodes with class_type and inputs")
    body.setdefault("client_id", "skill-" + uuid4().hex)
    if len(canonical(body)) > GRAPH_LIMIT:
        raise OriginError("API graph exceeds 8 MiB")
    if not api.session:
        raise OriginError("Login before submitting")
    state = {"schema_version": 1, "base_url": api.base, "device": slug, "key_fingerprint": api.session["key_fingerprint"], "idempotency_key": uuid4().hex, "request": body, "request_sha256": hashlib.sha256(canonical(body)).hexdigest(), "prompt_id": None}
    private_write(path, state, exclusive=True)
    return resume(api, path)


def saved_state(api, path):
    state = read_json(path)
    if state.get("schema_version") != 1 or state.get("base_url") != api.base:
        raise OriginError("Submission state belongs to another origin or version")
    native_path(state.get("device"))
    if hashlib.sha256(canonical(state.get("request"))).hexdigest() != state.get("request_sha256"):
        raise OriginError("Saved graph changed; restore the original submission state")
    if not isinstance(state.get("idempotency_key"), str) or not re.fullmatch("[a-zA-Z0-9_-]{16,128}", state["idempotency_key"]):
        raise OriginError("Invalid saved submission identity")
    return state


def prompt_path(slug, prompt_id):
    return native_path(slug, "history/" + component(prompt_id, "prompt ID"))


def resume(api, path):
    state = saved_state(api, path)
    if state.get("prompt_id"):
        return api.request("GET", prompt_path(state["device"], state["prompt_id"]))
    if not api.session or state["key_fingerprint"] != api.session["key_fingerprint"]:
        raise OriginError("Unknown acceptance requires a session from the original API Key")
    receipt = api.request("POST", native_path(state["device"], "prompt"), state["request"], {"Idempotency-Key": state["idempotency_key"]})
    component(receipt.get("prompt_id"), "prompt ID")
    state["prompt_id"] = receipt["prompt_id"]
    private_write(path, state)
    return receipt


def wait(api, path, timeout, output=None):
    state = saved_state(api, path)
    if not state.get("prompt_id"):
        raise OriginError("Acceptance is unknown; use resume with the same state, then wait")
    deadline, heartbeat = time.monotonic() + timeout, 0.0
    while True:
        if time.monotonic() >= heartbeat:
            api.request("GET", "/origin/api/devices")
            heartbeat = time.monotonic() + 60
        history = api.request("GET", prompt_path(state["device"], state["prompt_id"]))
        row = history.get(state["prompt_id"], {})
        status = row.get("status", {})
        if status.get("status_str") == "error":
            raise OriginError("ComfyUI execution failed; inspect the owned history and correct a new explicit request")
        if status.get("completed") is True and status.get("status_str") == "success":
            files = output_files(row.get("outputs", {}))
            result = {"prompt_id": state["prompt_id"], "status": "completed", "files": files}
            if output is not None:
                result["downloads"] = []
                for index, file in enumerate(files):
                    suffix = Path(file["filename"]).suffix.lower()
                    suffix = suffix if re.fullmatch(r"\.[a-z0-9]{1,10}", suffix) else ".bin"
                    destination = Path(output) / (state["prompt_id"] + "-" + str(index).zfill(3) + suffix)
                    result["downloads"].append(api.download(state["device"], file, destination))
            return result
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise OriginError("Polling timed out; the task and reservation remain. Continue wait with the same state")
        time.sleep(min(2, remaining))


def workflow_path(slug, name):
    if not name.endswith(".json"):
        raise OriginError("Personal workflow name must end in .json")
    return native_path(slug, "userdata/workflows/" + name)


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--base-url")
    root.add_argument("--session-file", default=".comfy-origin-session.json")
    commands = root.add_subparsers(dest="command", required=True)
    for command in ("login", "devices", "logout"):
        commands.add_parser(command)
    for command in ("acquire", "release", "interrupt"):
        commands.add_parser(command).add_argument("device")
    get = commands.add_parser("get", help="Read device JSON: object_info, models, queue, history, userdata, settings")
    get.add_argument("device")
    get.add_argument("path")
    upload = commands.add_parser("upload")
    upload.add_argument("device")
    upload.add_argument("file")
    upload.add_argument("--original-ref", help="Owned file descriptor JSON for an actual /upload/mask request")
    create = commands.add_parser("submit")
    create.add_argument("device")
    create.add_argument("request")
    create.add_argument("--state-file", required=True)
    recover = commands.add_parser("resume")
    recover.add_argument("state_file")
    poll = commands.add_parser("wait")
    poll.add_argument("state_file")
    poll.add_argument("--timeout", type=int, default=900)
    poll.add_argument("--output-dir")
    download = commands.add_parser("download")
    download.add_argument("device")
    download.add_argument("descriptor")
    download.add_argument("--output", required=True)
    download.add_argument("--sha256")
    for command in ("save-workflow", "delete-workflow"):
        item = commands.add_parser(command)
        item.add_argument("device")
        item.add_argument("name")
        if command == "save-workflow":
            item.add_argument("file")
            item.add_argument("--overwrite", action="store_true")
    cancel = commands.add_parser("cancel")
    cancel.add_argument("device")
    cancel.add_argument("prompt_id")
    return root


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = parser().parse_args(argv)
    if hasattr(args, "timeout") and not 1 <= args.timeout <= 86400:
        raise OriginError("Timeout must be 1-86400 seconds")
    api = Origin(args.base_url, args.session_file)
    if args.command == "login":
        result = api.login()
    elif args.command == "devices":
        result = api.request("GET", "/origin/api/devices")
    elif args.command in {"acquire", "release"}:
        result = api.request("POST", "/origin/api/devices/" + component(args.device, "device") + "/" + args.command)
    elif args.command == "logout":
        result = api.logout()
    elif args.command == "get":
        result = api.request("GET", native_path(args.device, args.path))
    elif args.command == "upload":
        result = api.upload(args.device, args.file, read_json(args.original_ref) if args.original_ref else None)
    elif args.command == "submit":
        print("State: " + str(Path(args.state_file).resolve()), file=sys.stderr, flush=True)
        result = submit(api, args.device, read_json(args.request, GRAPH_LIMIT), args.state_file)
    elif args.command == "resume":
        result = resume(api, args.state_file)
    elif args.command == "wait":
        result = wait(api, args.state_file, args.timeout, args.output_dir)
    elif args.command == "download":
        result = api.download(args.device, read_json(args.descriptor), args.output, args.sha256)
    elif args.command == "save-workflow":
        result = api.request("POST", workflow_path(args.device, args.name) + "?overwrite=" + str(args.overwrite).lower(), read_json(args.file, GRAPH_LIMIT))
    elif args.command == "delete-workflow":
        result = api.request("DELETE", workflow_path(args.device, args.name))
    elif args.command == "cancel":
        component(args.prompt_id, "prompt ID")
        result = api.request("POST", native_path(args.device, "queue"), {"delete": [args.prompt_id]})
    elif args.command == "interrupt":
        result = api.request("POST", native_path(args.device, "interrupt"), {})
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OriginError, OSError, ValueError, KeyError, TypeError, http.client.HTTPException) as error:
        print("ComfyOrigin: " + str(error) if isinstance(error, OriginError) else "ComfyOrigin: local input or response error (" + type(error).__name__ + ")", file=sys.stderr)
        raise SystemExit(2) from None
