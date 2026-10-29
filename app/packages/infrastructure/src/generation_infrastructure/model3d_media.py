"""Bounded validation of self-contained, uncompressed static triangle GLB 2.0.

This delivery profile intentionally excludes external resources, sparse accessors,
animation, skinning and geometry compression. It never executes or renders files.
"""
from __future__ import annotations

import json
import math
import struct
import time
from collections.abc import Iterator
from io import BytesIO
from pathlib import Path
from typing import Any, BinaryIO

from generation_domain.errors import DomainError
from generation_domain.media_types import (
    MAX_MODEL3D_BYTES,
    MAX_MODEL3D_TRIANGLES,
    MAX_MODEL3D_VERTICES,
    MODEL3D_MIME,
)

from generation_infrastructure.content import validate_image

JSON_LIMIT = 8 * 1024 * 1024
VALIDATION_SECONDS = 30
COMPONENTS = {5120: ("b", 1), 5121: ("B", 1), 5122: ("h", 2), 5123: ("H", 2), 5125: ("I", 4), 5126: ("f", 4)}
SHAPES = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}


def _invalid() -> ValueError:
    return ValueError("Unsupported or invalid self-contained GLB mesh")


def _integer(value: Any, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise _invalid()
    return value


def _object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _invalid()
    return value


def _list(value: Any, maximum: int, *, minimum: int = 0) -> list[Any]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise _invalid()
    return value


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _invalid()
        result[key] = value
    return result


class _GLB:
    def __init__(self, source: BinaryIO, size: int) -> None:
        self.source, self.size, self.started = source, size, time.monotonic()
        self.document: dict[str, Any] = {}
        self.bin_offset = self.bin_size = 0

    def deadline(self) -> None:
        if time.monotonic() - self.started > VALIDATION_SECONDS:
            raise _invalid()

    def read(self) -> None:
        if not 28 <= self.size <= MAX_MODEL3D_BYTES or self.size % 4:
            raise _invalid()
        if struct.unpack("<4sII", self.source.read(12)) != (b"glTF", 2, self.size):
            raise _invalid()
        length, kind = struct.unpack("<II", self.source.read(8))
        if kind != 0x4E4F534A or not 4 <= length <= min(JSON_LIMIT, self.size - 28) or length % 4:
            raise _invalid()
        self.document = _object(json.loads(self.source.read(length).decode("utf-8"), object_pairs_hook=_pairs))
        self.bin_size, kind = struct.unpack("<II", self.source.read(8))
        self.bin_offset = self.source.tell()
        if kind != 0x004E4942 or self.bin_size < 4 or self.bin_size % 4 or self.bin_offset + self.bin_size != self.size:
            raise _invalid()
        # Bound structure, numbers and URI handling even in unknown/extras fields.
        levels: list[tuple[Any, int]] = [(self.document, 0)]
        count = 0
        while levels:
            value, depth = levels.pop()
            count += 1
            if depth > 48 or count > 500000:
                raise _invalid()
            if isinstance(value, dict):
                if "uri" in value or "extensionsRequired" in value and value["extensionsRequired"]:
                    raise _invalid()
                levels.extend((item, depth + 1) for item in value.values())
            elif isinstance(value, list):
                levels.extend((item, depth + 1) for item in value)
            elif isinstance(value, float) and not math.isfinite(value):
                raise _invalid()
        doc = self.document
        asset = _object(doc.get("asset"))
        if asset.get("version") != "2.0" or asset.get("minVersion", "2.0") != "2.0" or doc.get("animations") or doc.get("skins"):
            raise _invalid()
        buffers = _list(doc.get("buffers"), 1, minimum=1)
        size = _integer(_object(buffers[0]).get("byteLength"), 1, self.bin_size)
        if self.bin_size - size > 3:
            raise _invalid()
        self.buffer_size = size
        self.views = _list(doc.get("bufferViews"), 50000, minimum=1)
        self.accessors = _list(doc.get("accessors"), 50000, minimum=1)
        for view in self.views:
            self.view(view)
        self.deadline()

    def view(self, value: Any) -> tuple[int, int, int | None]:
        view = _object(value)
        if _integer(view.get("buffer"), 0, 0) != 0 or view.get("extensions"):
            raise _invalid()
        offset = _integer(view.get("byteOffset", 0), 0, self.buffer_size)
        length = _integer(view.get("byteLength"), 1, self.buffer_size - offset)
        stride = view.get("byteStride")
        if stride is not None and (_integer(stride, 4, 252) % 4):
            raise _invalid()
        return offset, length, stride

    def accessor(self, identifier: Any) -> tuple[int, dict[str, Any], int, int, struct.Struct]:
        identifier = _integer(identifier, 0, len(self.accessors) - 1)
        accessor = _object(self.accessors[identifier])
        if accessor.get("sparse") or accessor.get("extensions") or accessor.get("componentType") not in COMPONENTS or accessor.get("type") not in SHAPES:
            raise _invalid()
        component, width = COMPONENTS[accessor["componentType"]]
        shape = SHAPES[accessor["type"]]
        count = _integer(accessor.get("count"), 1, MAX_MODEL3D_TRIANGLES * 3)
        view_index = _integer(accessor.get("bufferView"), 0, len(self.views) - 1)
        offset, length, declared_stride = self.view(self.views[view_index])
        local_offset = _integer(accessor.get("byteOffset", 0), 0, length)
        element = struct.Struct("<" + component * shape)
        stride = declared_stride or element.size
        if stride < element.size or (offset + local_offset) % width or stride % width or local_offset + (count - 1) * stride + element.size > length:
            raise _invalid()
        return identifier, accessor, self.bin_offset + offset + local_offset, stride, element

    def values(self, accessor: tuple[int, dict[str, Any], int, int, struct.Struct]) -> Iterator[tuple[Any, ...]]:
        _, description, offset, stride, element = accessor
        remaining = description["count"]
        rows = max(1, 1024 * 1024 // stride)
        while remaining:
            self.deadline()
            count = min(rows, remaining)
            length = (count - 1) * stride + element.size
            self.source.seek(offset)
            data = self.source.read(length)
            if len(data) != length:
                raise _invalid()
            if stride == element.size:
                yield from element.iter_unpack(data)
            else:
                for index in range(count):
                    yield element.unpack_from(data, index * stride)
            remaining -= count
            offset += count * stride

    def geometry(self) -> dict[str, Any]:
        meshes = _list(self.document.get("meshes"), 2000, minimum=1)
        self.scene(len(meshes))
        for index in range(len(self.accessors)):
            self.accessor(index)
        vertices = triangles = primitives = 0
        checked: set[int] = set()
        checked_positions: set[int] = set()
        for mesh in meshes:
            for primitive in _list(_object(mesh).get("primitives"), 10000, minimum=1):
                self.deadline()
                primitive = _object(primitive)
                primitives += 1
                if primitives > 10000 or primitive.get("mode", 4) != 4 or primitive.get("targets") or primitive.get("extensions"):
                    raise _invalid()
                if "material" in primitive:
                    _integer(primitive["material"], 0, len(self.document.get("materials", [])) - 1)
                attributes = _object(primitive.get("attributes"))
                position = self.accessor(attributes.get("POSITION"))
                identifier, description, _, _, _ = position
                if description["type"] != "VEC3" or description["componentType"] != 5126 or description.get("normalized"):
                    raise _invalid()
                count = description["count"]
                if identifier not in checked_positions:
                    vertices += count
                    if vertices > MAX_MODEL3D_VERTICES:
                        raise _invalid()
                    lower, upper = [math.inf] * 3, [-math.inf] * 3
                    for point in self.values(position):
                        for axis, value in enumerate(point):
                            if not math.isfinite(value) or abs(value) > 1e12:
                                raise _invalid()
                            lower[axis], upper[axis] = min(lower[axis], value), max(upper[axis], value)
                    if sum(upper[axis] > lower[axis] for axis in range(3)) < 2:
                        raise _invalid()
                    checked_positions.add(identifier)
                    checked.add(identifier)
                for attribute in attributes.values():
                    current = self.accessor(attribute)
                    if current[1]["count"] != count:
                        raise _invalid()
                    if current[0] not in checked:
                        if current[1]["componentType"] == 5126:
                            for values in self.values(current):
                                if any(not math.isfinite(value) for value in values):
                                    raise _invalid()
                        checked.add(current[0])
                if "indices" in primitive:
                    indices = self.accessor(primitive["indices"])
                    if indices[1]["type"] != "SCALAR" or indices[1]["componentType"] not in (5121, 5123, 5125) or indices[1].get("normalized"):
                        raise _invalid()
                    index_count = indices[1]["count"]
                    if index_count % 3:
                        raise _invalid()
                    for (index,) in self.values(indices):
                        if index >= count:
                            raise _invalid()
                else:
                    index_count = count
                    if count % 3:
                        raise _invalid()
                triangles += index_count // 3
                if triangles > MAX_MODEL3D_TRIANGLES:
                    raise _invalid()
        materials = _list(self.document.get("materials", []), 2000)
        images = _list(self.document.get("images", []), 256)
        for image in images:
            image = _object(image)
            if image.get("mimeType") not in ("image/png", "image/jpeg"):
                raise _invalid()
            index = _integer(image.get("bufferView"), 0, len(self.views) - 1)
            offset, length, stride = self.view(self.views[index])
            if stride or length > 32 * 1024 * 1024:
                raise _invalid()
            self.source.seek(self.bin_offset + offset)
            metadata = validate_image(BytesIO(self.source.read(length)), 32 * 1024 * 1024)
            if metadata["media_type"] != image["mimeType"]:
                raise _invalid()
            self.deadline()
        for texture in _list(self.document.get("textures", []), 256):
            _integer(_object(texture).get("source"), 0, len(images) - 1)
        return {"media_type": MODEL3D_MIME, "format": "glb", "mesh_count": len(meshes), "vertex_count": vertices, "triangle_count": triangles, "material_count": len(materials), "texture_count": len(images), "has_textures": bool(images)}

    def scene(self, meshes: int) -> None:
        nodes = _list(self.document.get("nodes"), 10000, minimum=1)
        scenes = _list(self.document.get("scenes"), 1000, minimum=1)
        selected = _integer(self.document.get("scene", 0), 0, len(scenes) - 1)
        children: list[list[int]] = []
        for node in nodes:
            node = _object(node)
            children.append([_integer(index, 0, len(nodes) - 1) for index in _list(node.get("children", []), 10000)])
            if "mesh" in node:
                _integer(node["mesh"], 0, meshes - 1)
            if "skin" in node or "weights" in node:
                raise _invalid()
            for key, count in (("matrix", 16), ("translation", 3), ("rotation", 4), ("scale", 3)):
                if key in node and (len(_list(node[key], count, minimum=count)) != count or any(type(value) not in (int, float) or not math.isfinite(value) for value in node[key])):
                    raise _invalid()
        # Acyclic forest, one parent per node, and a visible mesh in the default scene.
        parents: set[int] = set()
        for group in children:
            for child in group:
                if child in parents:
                    raise _invalid()
                parents.add(child)
        roots: set[int] = set()
        for scene_index, scene in enumerate(scenes):
            indices = [
                _integer(index, 0, len(nodes) - 1)
                for index in _list(_object(scene).get("nodes", []), 10000, minimum=int(scene_index == selected))
            ]
            if len(set(indices)) != len(indices) or any(index in parents for index in indices):
                raise _invalid()
            if scene_index == selected:
                roots.update(indices)
        visited: set[int] = set()
        active: set[int] = set()
        visible = False
        pending = [(index, False, 0, index in roots) for index in range(len(nodes)) if index not in parents]
        while pending:
            index, leaving, depth, in_default_scene = pending.pop()
            if leaving:
                active.remove(index)
                continue
            if index in active or depth > 128:
                raise _invalid()
            if index in visited:
                continue
            visited.add(index)
            active.add(index)
            visible |= in_default_scene and "mesh" in nodes[index]
            pending.append((index, True, depth, in_default_scene))
            pending.extend((child, False, depth + 1, in_default_scene) for child in children[index])
        if not visible or len(visited) != len(nodes):
            raise _invalid()


def inspect_glb(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as source:
            glb = _GLB(source, path.stat().st_size)
            glb.read()
            return glb.geometry()
    except (ValueError, TypeError, KeyError, IndexError, UnicodeError, RecursionError, OverflowError, struct.error) as exc:
        raise DomainError("invalid_model3d", "3D 文件未通过自包含静态三角网格 GLB 2.0 校验", 422) from exc
