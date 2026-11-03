from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any

from generation_domain.errors import DomainError

MAX_CONTEXT_BYTES = 512 * 1024**2
CONTEXT_MIME = "application/x-safetensors"


def inspect_context(path: Path) -> dict[str, Any]:
    """Read bounded safetensors metadata, without deserializing executable objects."""
    try:
        size = path.stat().st_size
        if not 16 < size <= MAX_CONTEXT_BYTES:
            raise ValueError
        with path.open("rb") as stream:
            prefix = stream.read(8)
            length = struct.unpack("<Q", prefix)[0]
            if not 2 <= length <= 65536 or length + 8 >= size:
                raise ValueError
            def distinct(items: list[tuple[str, Any]]) -> dict[str, Any]:
                result: dict[str, Any] = {}
                for key, value in items:
                    if key in result:
                        raise ValueError
                    result[key] = value
                return result
            header = json.loads(stream.read(length), object_pairs_hook=distinct)
        if not isinstance(header, dict) or set(header) - {"video", "audio", "__metadata__"} or not {"video", "audio"} <= header.keys():
            raise ValueError
        if "__metadata__" in header and (not isinstance(header["__metadata__"], dict) or len(json.dumps(header["__metadata__"])) > 8192 or any(not isinstance(k, str) or not isinstance(v, str) for k, v in header["__metadata__"].items())):
            raise ValueError
        end = 0
        shapes, dtypes = {}, {}
        tensors = sorted((header[name]["data_offsets"][0], name) for name in ("video", "audio"))
        for begin, name in tensors:
            tensor = header[name]
            shape, dtype, offsets = tensor["shape"], tensor["dtype"], tensor["data_offsets"]
            if set(tensor) != {"dtype", "shape", "data_offsets"} or dtype not in ("F16", "BF16", "F32") or not isinstance(shape, list) or len(shape) not in ((4, 5, 6) if name == "video" else (3, 4, 5)) or any(type(value) is not int or not 0 < value <= 4096 for value in shape) or shape[0] != 1 or not isinstance(offsets, list) or len(offsets) != 2 or any(type(value) is not int for value in offsets) or begin != end:
                raise ValueError
            elements = 1
            for value in shape:
                elements *= value
            end = offsets[1]
            if elements * (4 if dtype == "F32" else 2) != end - begin or end > size - length - 8:
                raise ValueError
            shapes[name], dtypes[name] = shape, dtype
        if end != size - length - 8:
            raise ValueError
        return {"family": "h3-av-v1", "shapes": shapes, "dtypes": dtypes}
    except (OSError, ValueError, TypeError, KeyError, IndexError, struct.error, UnicodeError) as exc:
        raise DomainError("invalid_workflow_context", "续拍上下文不是受控的 H3 AV safetensors", 422) from exc
