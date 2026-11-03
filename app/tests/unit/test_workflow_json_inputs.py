from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

import pytest
from generation_domain.errors import DomainError
from generation_domain.workflow_json_inputs import document_target, replace_scalar
from generation_domain.workflows import compile_graph, validate_manifest

from tests.unit.test_workflow_inputs import request


def test_independent_nested_fields_receive_owned_media_and_literal_text(
    multimodal_manifest: dict[str, Any],
) -> None:
    manifest = deepcopy(multimodal_manifest)
    manifest["workflow_api"]["5"]["inputs"]["script"] = json.dumps(
        {"speakers": [{"transcript": ""}], "fixed": "operator value", "file": ""}
    )
    script = next(b for b in manifest["bindings"] if b["source"] == "text:script")
    script["pointer"] = "/speakers/0/transcript"
    media = next(b for b in manifest["bindings"] if b["source"] == "media:voice")
    media.update(node_id="5", input_name="script", pointer="/file")
    # The audio loader is still reachable, but its old unbound value is never executed.
    original = deepcopy(manifest)
    validate_manifest(manifest, manifest["task_type"])
    execution = request(manifest)
    execution["input"]["texts"]["script"] = 'Literal "quotes", \\slashes and {"file":"untrusted"}'
    paths = {"media:" + s["name"]: "controlled/" + s["name"] for s in manifest["input_slots"]}
    graph = compile_graph(manifest, execution, paths, "render-control/synthetic/result")
    data = json.loads(graph["5"]["inputs"]["script"])
    assert data == {
        "speakers": [{"transcript": execution["input"]["texts"]["script"]}],
        "fixed": "operator value",
        "file": paths["media:voice"],
    }
    assert original == manifest


@pytest.mark.parametrize(
    "raw,pointer",
    [
        ("{}", "/missing"),
        ('{"x":{}}', "/x"),
        ('{"x":1,"x":2}', "/x"),
        ('{"x":NaN}', "/x"),
        ('{"x":1e999}', "/x"),
        ('{"x":[0]}', "/x/-"),
        ('{"x":[0]}', "/x/00"),
        ('{"x":0}', "/x~2"),
        ('{"x":0}', "x"),
        ('{"x":0}', "/"),
        ('{"x":0}', "/x/y"),
        ("x" * (1024 * 1024 + 1), "/x"),
    ],
    ids=[
        "missing",
        "collection",
        "duplicate",
        "nan",
        "infinity",
        "append",
        "index",
        "escape",
        "no-slash",
        "empty",
        "scalar-parent",
        "oversized",
    ],
)
def test_json_binding_rejects_creation_collections_ambiguity_and_nonfinite_numbers(
    raw: str, pointer: str
) -> None:
    with pytest.raises(DomainError) as error:
        document_target(raw, pointer)
    assert error.value.code == "invalid_json_binding"


def test_pointer_escaping_and_request_text_are_data() -> None:
    raw = '{"a/b":{"~key":0}}'
    assert (
        json.loads(replace_scalar(raw, "/a~1b/~0key", "../not-a-path"))["a/b"]["~key"]
        == "../not-a-path"
    )


@pytest.mark.parametrize("variant", ["whole_overlap", "duplicate", "legacy"])
def test_conflicting_or_legacy_pointer_bindings_are_rejected(
    multimodal_manifest: dict[str, Any], variant: str
) -> None:
    manifest = deepcopy(multimodal_manifest)
    target = manifest["workflow_api"]["5"]["inputs"]
    target["script"] = '{"text":""}'
    binding = next(b for b in manifest["bindings"] if b["source"] == "text:script")
    binding["pointer"] = "/text"
    if variant == "legacy":
        manifest["format_version"] = "1.0"
    else:
        duplicate = deepcopy(binding)
        if variant == "whole_overlap":
            duplicate.pop("pointer")
        manifest["bindings"].append(duplicate)
    with pytest.raises(DomainError):
        validate_manifest(manifest, manifest["task_type"])
