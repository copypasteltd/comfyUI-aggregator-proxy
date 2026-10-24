from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import math
import re
from contextlib import AsyncExitStack
from decimal import Decimal
from typing import Any, BinaryIO
from urllib.parse import urlsplit

from fastapi import Request
from generation_application.ports import BusinessIdentity, GenerationService
from generation_contracts.compatibility import ImageEdit, VideoGeneration
from generation_contracts.validation import JobCreate
from generation_domain.errors import DomainError
from generation_domain.values import parse_id
from generation_infrastructure.content import ContentRepository, validate_image
from starlette.datastructures import UploadFile


def strict_object(raw: bytes | str) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name, value in items:
            if name in result:
                raise ValueError("Duplicate JSON property")
            result[name] = value
        return result

    def constant(_: str) -> None:
        raise ValueError("Nonfinite JSON number")

    def number(text: str) -> float:
        result = float(text)
        if not math.isfinite(result) or Decimal(str(result)) != Decimal(text):
            raise ValueError("JSON number loses precision")
        return result

    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="strict")
    result = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant, parse_float=number)
    if not isinstance(result, dict):
        raise ValueError("Object required")
    pending = [(result, 0)]
    count = 0
    while pending:
        value, depth = pending.pop()
        count += 1
        if depth > 32 or count > 65536:
            raise ValueError("JSON structure exceeds limit")
        if isinstance(value, dict):
            for key in value:
                key.encode("utf-8", errors="strict")
            pending.extend((v, depth + 1) for v in value.values())
        elif isinstance(value, list):
            pending.extend((v, depth + 1) for v in value)
        elif isinstance(value, str):
            value.encode("utf-8", errors="strict")
    return result


async def finished_io(function: Any, *args: Any, **kwargs: Any) -> Any:
    # Keep an upload's source and capacity slot alive until its thread has stopped.
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await task
        finally:
            raise


async def media_request(request: Request, identity: BusinessIdentity, jobs: GenerationService, content: ContentRepository, public_origin: str, *, video: bool) -> tuple[JobCreate, str]:
    identity.require("jobs:create")
    identity.require("videos:create" if video else "images:create")
    if request.headers.get("content-encoding", "identity") != "identity":
        raise DomainError("unsupported_media_type", "不支持压缩的兼容请求", 415)
    async with AsyncExitStack() as stack:
        bindings: list[tuple[dict[str, Any], BinaryIO]] = []

        async def inline(source: BinaryIO) -> dict[str, Any]:
            identity.require("assets:upload")
            await finished_io(validate_image, source)
            value = {"asset_id": "asset_inline_placeholder"}
            bindings.append((value, source))
            return value

        async def source(value: Any) -> dict[str, Any]:
            if not isinstance(value, dict) or set(value) not in ({"asset_id"}, {"image_url"}):
                raise ValueError("Invalid image source")
            if "asset_id" in value:
                identifier = value["asset_id"]
            else:
                url = value["image_url"]
                if not isinstance(url, str):
                    raise ValueError("Invalid image URL")
                if url.startswith("data:"):
                    match = re.fullmatch(r"data:(image/(?:png|jpeg|webp));base64,([A-Za-z0-9+/]*={0,2})", url)
                    if not match or len(match[2]) > 4 * ((20 * 1024 * 1024 + 2) // 3):
                        raise ValueError("Invalid data URL")
                    file = stack.enter_context(io.BytesIO(base64.b64decode(match[2], validate=True)))
                    metadata = await finished_io(validate_image, file)
                    if metadata["media_type"] != match[1]:
                        raise ValueError("Image MIME mismatch")
                    return await inline(file)
                parsed, origin = urlsplit(url), urlsplit(public_origin)
                if (parsed.scheme, parsed.netloc) != (origin.scheme, origin.netloc) or parsed.query or parsed.fragment or "?" in url or "#" in url or "%" in parsed.path:
                    raise ValueError("Untrusted image URL")
                match = re.fullmatch(r"/v1/assets/(asset_[A-Za-z0-9_-]{6,80})/content", parsed.path)
                if not match:
                    raise ValueError("Invalid asset URL")
                identifier = match[1]
            await finished_io(content.input_asset, identity, parse_id("asset", identifier))
            return {"asset_id": identifier}

        root: dict[str, Any]
        uploads: list[UploadFile] = []
        mask = None
        legacy_roles = None
        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type == "application/json":
            root = strict_object(await request.body())
        elif content_type == "multipart/form-data":
            form = await stack.enter_async_context(request.form(max_files=5, max_fields=16, max_part_size=65536))
            root = {}
            image_field = ""
            for name, value in form.multi_items():
                if isinstance(value, UploadFile):
                    if value.headers.get("content-transfer-encoding"):
                        raise ValueError("Encoded multipart part")
                    if video:
                        if name != "input_reference" or uploads:
                            raise ValueError("Invalid video upload")
                        uploads.append(value)
                    elif name in ("image", "image[]") and (not image_field or image_field == name) and len(uploads) < 4:
                        image_field = name
                        uploads.append(value)
                    elif name == "mask" and mask is None:
                        mask = value
                    else:
                        raise ValueError("Invalid image upload")
                elif name in root or name == "reference_roles" and legacy_roles is not None or len(value.encode("utf-8")) > 65536:
                    raise ValueError("Duplicate or oversized form field")
                elif name == "workflow_options":
                    root[name] = strict_object(value)
                elif name == "reference_roles" and not video:
                    legacy_roles = json.loads(value)
                elif name in ({"model", "prompt", "size", "quality", "seconds"} if video else {"model", "prompt", "n", "size", "quality", "response_format", "user"}):
                    if name == "n" and not re.fullmatch(r"1(?:\.0+)?", value):
                        raise ValueError("Invalid image count")
                    root[name] = 1 if name == "n" else value
                else:
                    raise ValueError("Unsupported form field")
            if video:
                if uploads:
                    root["input_reference"] = await inline(uploads[0].file)
            else:
                if not uploads:
                    raise ValueError("Image required")
                root["images"] = [await inline(upload.file) for upload in uploads]
                if mask:
                    root["mask"] = await inline(mask.file)
        else:
            raise DomainError("unsupported_media_type", "使用 application/json 或 multipart/form-data", 415)

        options = root.get("workflow_options", {})
        if not isinstance(options, dict):
            raise ValueError("Workflow options must be an object")
        if video:
            if "input_reference" in root:
                value = root["input_reference"]
                if not any(value is binding for binding, _ in bindings):
                    root["input_reference"] = await source(value)
            response_format = "b64_json"
        else:
            if "images" in root:
                if "source_image" in root or "references" in root or not isinstance(root["images"], list) or not 1 <= len(root["images"]) <= 4:
                    raise ValueError("Conflicting image styles")
                images = [value if any(value is binding for binding, _ in bindings) else await source(value) for value in root.pop("images")]
                if len(images) == 1:
                    if "image_roles" in options or legacy_roles is not None:
                        raise ValueError("Single image has no role array")
                    root["source_image"] = images[0]
                else:
                    if "mask" in root or "strength" in options:
                        raise ValueError("Multiple references cannot use mask or strength")
                    roles = options.pop("image_roles", None)
                    if legacy_roles is not None:
                        if roles is not None or not isinstance(legacy_roles, list):
                            raise ValueError("Conflicting role styles")
                        roles = [{"index": i, "role": role} for i, role in enumerate(legacy_roles)]
                    if not isinstance(roles, list) or len(roles) != len(images):
                        raise ValueError("Explicit reference roles required")
                    indexed = {}
                    for role in roles:
                        if not isinstance(role, dict) or set(role) != {"index", "role"} or type(role["index"]) is not int or role["index"] not in range(len(images)) or role["index"] in indexed:
                            raise ValueError("Invalid role index")
                        indexed[role["index"]] = role["role"]
                    root["references"] = [{**image, "role": indexed[i]} for i, image in enumerate(images)]
                    # The reference objects must retain their inline binding identities.
                    for original, reference in zip(images, root["references"], strict=True):
                        for index, (binding, file) in enumerate(bindings):
                            if binding is original:
                                bindings[index] = (reference, file)
            else:
                for name in ("source_image", "mask"):
                    if name in root:
                        root[name] = await source(root[name])
            if "mask" in root and not any(root["mask"] is binding for binding, _ in bindings):
                root["mask"] = await source(root["mask"])
            response_format = root.get("response_format", "b64_json")

        def factory() -> JobCreate:
            return VideoGeneration.model_validate(root).job() if video else ImageEdit.model_validate(root).edit_job()

        preview = factory()  # Fully validate placeholders before the first persistent upload.
        await finished_io(jobs.preflight_inputs, identity, preview.model_dump(exclude_unset=True))
        for binding, file in bindings:
            file.seek(0)
            digest = hashlib.sha256()
            while chunk := file.read(65536):
                digest.update(chunk)
            sha = digest.hexdigest()
            file.seek(0)
            token = "inline:" + hashlib.sha256((str(root["model"]) + "\0" + sha).encode()).hexdigest()
            asset = await finished_io(content.upload_asset, identity, file, request.state.request_id, token)
            binding["asset_id"] = asset["id"]
        return factory(), response_format


def media_request_schema(video: bool) -> dict[str, Any]:
    schema = (VideoGeneration if video else ImageEdit).model_json_schema()
    if not video:
        schema["properties"]["images"] = {"type": "array", "minItems": 1, "maxItems": 4, "items": {"type": "object", "additionalProperties": False, "required": ["image_url"], "properties": {"image_url": {"type": "string"}}}}
    properties = {name: value for name, value in schema["properties"].items() if name not in {"source_image", "mask", "references", "input_reference", "images", "workflow_options"}}
    properties["workflow_options"] = {"type": "string", "description": "Project extension: strict JSON object"}
    if video:
        properties["input_reference"] = {"type": "string", "format": "binary"}
    else:
        properties["image"] = {"type": "array", "minItems": 1, "maxItems": 4, "items": {"type": "string", "format": "binary"}}
        properties["mask"] = {"type": "string", "format": "binary"}
    multipart = {"type": "object", "additionalProperties": False, "required": ["model", "prompt"] + ([] if video else ["image"]), "properties": properties}
    return {"requestBody": {"required": True, "content": {"application/json": {"schema": schema}, "multipart/form-data": {"schema": multipart}}}}
