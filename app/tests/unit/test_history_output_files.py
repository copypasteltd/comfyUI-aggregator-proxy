from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from generation_domain.errors import DomainError
from windows_worker.comfy_json import output_files


@pytest.mark.parametrize("field", ["images", "gifs", "videos", "audio", "3d"])
@pytest.mark.parametrize("subfolder", ["omitted", "", "nested/folder"])
def test_declared_fields_keep_standard_files_and_custom_extras(field: str, subfolder: str) -> None:
    entry: dict[str, Any] = {"filename": "synthetic.bin", "type": "output", "format": "custom-node-metadata"}
    if subfolder != "omitted":
        entry["subfolder"] = subfolder
    value = {"outputs": {"chosen": {field: [entry], "unselected": "ignore arbitrary custom metadata"}, "unselected": ["unrelated malformed output"]}}
    original = deepcopy(value)
    assert output_files(value, ["chosen"], field) == [entry] and value == original


@pytest.mark.parametrize("value", [{"outputs": {}}, {"outputs": {"chosen": {}}}, {"outputs": {"chosen": {"audio": []}}}])
def test_missing_declared_nodes_or_fields_still_mean_no_files(value: dict[str, Any]) -> None:
    assert output_files(value, ["chosen"], "audio") == []


def test_selected_nodes_keep_order_without_guessing_other_fields() -> None:
    first = {"filename": "first.mp4", "type": "temp"}
    second = {"filename": "second.mp4", "type": "output"}
    value = {"outputs": {"first": {"gifs": [first], "videos": [second]}, "second": {"gifs": [second]}, "third": {"gifs": [first]}}}
    assert output_files(value, ["second", "first"], "gifs") == [second, first]


@pytest.mark.parametrize("value", [{}, {"outputs": None}, {"outputs": []}, {"outputs": "synthetic private metadata"}])
def test_non_object_output_collection_is_a_safe_fixed_error(value: dict[str, Any]) -> None:
    with pytest.raises(DomainError) as failure:
        output_files(value, ["chosen"], "images")
    assert failure.value.code == "invalid_comfy_response" and failure.value.message == "ComfyUI 元数据格式无效"
