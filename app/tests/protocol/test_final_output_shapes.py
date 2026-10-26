from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from generation_domain.errors import DomainError
from generation_domain.workflows import TASK_TYPES

from tests.protocol.test_output_collection_resume import collector

SHAPES = ["node_null", "node_string", "node_list", "files_null", "files_string", "files_object", "item_null", "item_string", "item_list", "filename_null", "filename_number", "subfolder_null", "subfolder_number", "type_number", "filename_empty"]


def malformed(history: dict[str, Any], field: str, shape: str) -> dict[str, Any]:
    result = deepcopy(history)
    nodes = result["outputs"]
    if shape.startswith("node_"):
        nodes["2"] = {"node_null": None, "node_string": "Synthetic private node metadata", "node_list": []}[shape]
    elif shape.startswith("files_"):
        nodes["2"][field] = {"files_null": None, "files_string": "x", "files_object": {"private": "synthetic"}}[shape]
    elif shape.startswith("item_"):
        nodes["2"][field] = [{"item_null": None, "item_string": "Synthetic private filename", "item_list": []}[shape]]
    else:
        key, kind = shape.rsplit("_", 1)
        nodes["2"][field][0][key] = None if kind == "null" else "" if kind == "empty" else 17
    return result


@pytest.mark.parametrize("task_type", sorted(TASK_TYPES))
@pytest.mark.parametrize("shape", SHAPES)
async def test_six_capabilities_reject_malformed_declared_outputs_before_file_access(tmp_path: Path, task_type: str, shape: str, monkeypatch: pytest.MonkeyPatch) -> None:
    async with collector(tmp_path, task_type) as (execution, source, destination, history, payload):
        bad = malformed(history, execution.claim["workflow"]["output"]["history_field"], shape)
        snapshot = deepcopy(bad)

        def forbid_io(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("Malformed metadata must be rejected before filesystem access")

        with monkeypatch.context() as guard:
            guard.setattr("windows_worker.runtime.safe_path", forbid_io)
            with pytest.raises(DomainError) as failure:
                await execution.collect(bad, execution.worker.settings.channels[0])
        assert failure.value.code == "invalid_comfy_response"
        assert failure.value.message == "ComfyUI 元数据格式无效"
        assert bad == snapshot and source.read_bytes() == payload and not destination.exists()
        assert not execution.worker.journal.pending() and execution.worker.journal.outputs(execution.aid) is None
