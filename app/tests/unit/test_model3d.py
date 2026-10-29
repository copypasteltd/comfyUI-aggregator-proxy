from __future__ import annotations

import json
import struct
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from generation_contracts.defaults import default_key_policy
from generation_contracts.validation import JobCreate, KeyPolicy
from generation_contracts.workflows import WorkflowManifest
from generation_domain.errors import DomainError
from generation_domain.media_types import MODEL3D_MIME, output_mime, task_media_kind
from generation_domain.policies import enforce_limit
from generation_domain.workflows import compile_graph, validate_manifest
from generation_infrastructure.media import inspect_media
from generation_infrastructure.model_usage import model_usage
from jsonschema import Draft202012Validator
from pydantic import ValidationError


def rewrite(path: Path, mutation: Any) -> None:
    data = path.read_bytes()
    length = struct.unpack_from("<I", data, 12)[0]
    document = json.loads(data[20 : 20 + length])
    binary = data[28 + length :]
    mutation(document)
    header = json.dumps(document).encode()
    header += b" " * (-len(header) % 4)
    path.write_bytes(
        struct.pack("<4sII", b"glTF", 2, 28 + len(header) + len(binary))
        + struct.pack("<II", len(header), 0x4E4F534A)
        + header
        + struct.pack("<II", len(binary), 0x004E4942)
        + binary
    )


def test_single_image_glb_contract_and_usage_match(model3d_manifest: dict[str, Any]) -> None:
    WorkflowManifest.model_validate(model3d_manifest)
    validate_manifest(model3d_manifest, "image_to_3d")
    execution = {
        "input": {"source_image": {"asset_id": "asset_synthetic"}},
        "parameters": {"quality": "standard", "seed": 7, "n": 1},
    }
    graph = compile_graph(
        model3d_manifest, execution, {"source_image": "inputs/source.png"}, "results/model"
    )
    assert graph["1"]["inputs"]["image"] == "inputs/source.png"
    assert graph["2"]["inputs"]["seed"] == 7
    assert graph["3"]["inputs"]["filename_prefix"] == "results/model"
    usage = model_usage(
        "synthetic-model3d", "image_to_3d", model3d_manifest, {"quality": "standard", "n": 1}, {}
    )
    assert "prompt" not in usage["example_request"]["input"]
    assert usage["references"]["slots"][0]["path"] == "input.source_image"
    assert "meshes:create" in usage["required_scopes"]
    assert usage["output"]["media_types"] == [MODEL3D_MIME]
    JobCreate.model_validate(usage["example_request"])
    Draft202012Validator(usage["request_schema"]).validate(usage["example_request"])


@pytest.mark.parametrize(
    "input",
    [
        {},
        {"prompt": "ignored"},
        {"source_image": {"asset_id": "asset_synthetic"}, "prompt": "ignored"},
        {"source_image": {"asset_id": "asset_synthetic"}, "negative_prompt": "ignored"},
    ],
)
def test_model3d_rejects_missing_image_and_ignored_prompt(input: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        JobCreate(model="mesh", task_type="image_to_3d", input=input)


@pytest.mark.parametrize(
    "parameters", [{"size": "512x512"}, {"seconds": 1}, {"fps": 30}, {"strength": 0.5}, {"n": 2}]
)
def test_model3d_rejects_nonmesh_parameters(parameters: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        JobCreate(
            model="mesh",
            task_type="image_to_3d",
            input={"source_image": {"asset_id": "asset_synthetic"}},
            parameters=parameters,
        )


def test_old_policies_never_inherit_mesh_permission_or_quota() -> None:
    policy = default_key_policy()
    policy["limits"].pop("daily_mesh_count")
    assert "meshes:create" not in policy["scopes"]
    assert KeyPolicy.model_validate(policy).limits.daily_mesh_count.value == 0
    with pytest.raises(DomainError, match="daily_mesh_count"):
        enforce_limit(policy["limits"], "daily_mesh_count", 0, 1)


@pytest.mark.parametrize(
    "kind", ["adapter", "history", "mime", "budget", "preset", "binding", "supported", "version"]
)
def test_release_cannot_claim_unimplemented_mesh_capabilities(
    model3d_manifest: dict[str, Any], kind: str
) -> None:
    manifest = deepcopy(model3d_manifest)
    if kind == "adapter":
        manifest["output"]["adapter"] = "save-image-v1"
    elif kind == "history":
        manifest["output"]["history_field"] = "images"
    elif kind == "mime":
        manifest["output"]["mime_types"] = ["model/obj"]
    elif kind == "budget":
        manifest["output"]["max_bytes"] = 512 * 1024**2 + 1
    elif kind == "preset":
        manifest["presets"][0]["size"] = "512x512"
    elif kind == "binding":
        manifest["bindings"][0]["source"] = "prompt"
    elif kind == "supported":
        manifest["supported_parameters"].append("size")
    else:
        manifest["format_version"] = "1.0"
    with pytest.raises(DomainError):
        validate_manifest(manifest, "image_to_3d")


def test_glb_is_validated_as_geometry_not_video(model3d_glb: Path) -> None:
    assert inspect_media(model3d_glb, MODEL3D_MIME) == {
        "media_type": MODEL3D_MIME,
        "format": "glb",
        "mesh_count": 1,
        "vertex_count": 3,
        "triangle_count": 1,
        "material_count": 0,
        "texture_count": 0,
        "has_textures": False,
    }
    assert output_mime(".GLB") == MODEL3D_MIME and task_media_kind("image_to_3d") == "model"


@pytest.mark.parametrize("limited", [False, True])
def test_position_accessor_used_as_custom_attribute_is_still_counted_and_bounded(
    model3d_glb: Path, monkeypatch: pytest.MonkeyPatch, limited: bool
) -> None:
    def mutate(document: dict[str, Any]) -> None:
        document["accessors"].append(deepcopy(document["accessors"][0]))
        document["bufferViews"][0]["byteStride"] = 12
        document["meshes"][0]["primitives"][0]["attributes"]["_SECOND_SHAPE"] = 2
        document["meshes"].append({"primitives": [{"attributes": {"POSITION": 2}, "indices": 1}]})
        document["nodes"].append({"mesh": 1})
        document["scenes"][0]["nodes"].append(1)

    rewrite(model3d_glb, mutate)
    if limited:
        monkeypatch.setattr("generation_infrastructure.model3d_media.MAX_MODEL3D_VERTICES", 5)
        with pytest.raises(DomainError) as failure:
            inspect_media(model3d_glb, MODEL3D_MIME)
        assert failure.value.code == "invalid_model3d"
    else:
        metadata = inspect_media(model3d_glb, MODEL3D_MIME)
        assert metadata["vertex_count"] == 6
        assert metadata["mesh_count"] == metadata["triangle_count"] == 2


@pytest.mark.parametrize("variant", ["disconnected_cycle", "scene_root_has_parent", "other_scene_root_has_parent"])
def test_glb_rejects_invalid_hierarchy_outside_the_default_scene(
    model3d_glb: Path, variant: str
) -> None:
    def mutate(document: dict[str, Any]) -> None:
        if variant == "disconnected_cycle":
            document["nodes"].extend([{"children": [2]}, {"children": [1]}])
        elif variant == "scene_root_has_parent":
            document["nodes"].append({"children": [0]})
        else:
            document["nodes"].extend([{"children": [2]}, {"mesh": 0}])
            document["scenes"].append({"nodes": [2]})

    rewrite(model3d_glb, mutate)
    with pytest.raises(DomainError) as failure:
        inspect_media(model3d_glb, MODEL3D_MIME)
    assert failure.value.code == "invalid_model3d"


def test_glb_accepts_a_valid_unselected_scene(model3d_glb: Path) -> None:
    def mutate(document: dict[str, Any]) -> None:
        document["nodes"].append({"mesh": 0})
        document["scenes"].append({"nodes": [1]})

    rewrite(model3d_glb, mutate)
    assert inspect_media(model3d_glb, MODEL3D_MIME)["vertex_count"] == 3


@pytest.mark.parametrize(
    "kind", ["empty", "header", "version", "truncated", "extra", "nan", "index"]
)
def test_glb_rejects_corruption_and_invalid_geometry(model3d_glb: Path, kind: str) -> None:
    data = bytearray(model3d_glb.read_bytes())
    length = struct.unpack_from("<I", data, 12)[0]
    if kind == "empty":
        data = bytearray()
    elif kind == "header":
        data[:4] = b"RIFF"
    elif kind == "version":
        struct.pack_into("<I", data, 4, 1)
    elif kind == "truncated":
        data = data[:-4]
    elif kind == "extra":
        data.extend(b"evil")
    elif kind == "nan":
        struct.pack_into("<f", data, 28 + length, float("nan"))
    else:
        struct.pack_into("<H", data, 28 + length + 36, 3)
    model3d_glb.write_bytes(data)
    with pytest.raises(DomainError) as error:
        inspect_media(model3d_glb, MODEL3D_MIME)
    assert error.value.code == "invalid_model3d"


@pytest.mark.parametrize(
    "kind",
    [
        "external",
        "data_uri",
        "no_geometry",
        "offset",
        "stride",
        "sparse",
        "compressed",
        "animation",
        "cycle",
        "invisible",
        "component",
        "count",
        "bool_buffer",
    ],
)
def test_glb_rejects_unsupported_or_unbounded_references(model3d_glb: Path, kind: str) -> None:
    def mutate(document: dict[str, Any]) -> None:
        if kind == "external":
            document["buffers"][0]["uri"] = "http://127.0.0.1/secret"
        elif kind == "data_uri":
            document["buffers"][0]["uri"] = "data:application/octet-stream;base64,AAAA"
        elif kind == "no_geometry":
            document["meshes"] = []
        elif kind == "offset":
            document["accessors"][0]["byteOffset"] = 4
        elif kind == "stride":
            document["bufferViews"][0]["byteStride"] = 1
        elif kind == "sparse":
            document["accessors"][0]["sparse"] = {"count": 1}
        elif kind == "compressed":
            document["extensionsRequired"] = ["KHR_draco_mesh_compression"]
        elif kind == "animation":
            document["animations"] = [{"channels": []}]
        elif kind == "cycle":
            document["nodes"][0]["children"] = [0]
        elif kind == "invisible":
            document["nodes"] = [{}]
        elif kind == "component":
            document["accessors"][0]["componentType"] = 0
        elif kind == "count":
            document["accessors"][0]["count"] = 1000000000
        else:
            document["bufferViews"][0]["buffer"] = False

    rewrite(model3d_glb, mutate)
    with pytest.raises(DomainError) as error:
        inspect_media(model3d_glb, MODEL3D_MIME)
    assert error.value.code == "invalid_model3d"
