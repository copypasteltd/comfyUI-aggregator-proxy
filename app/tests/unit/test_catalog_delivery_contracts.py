from __future__ import annotations

from copy import deepcopy

import pytest
from generation_contracts.validation import JobCreate

from scripts.workflow_catalog_metadata import describe_media_slots


@pytest.mark.parametrize("seconds", [0.001, 5.167, 14.792, 34.157, 119.999])
def test_decimal_millisecond_durations_are_accepted_exactly(seconds: float) -> None:
    request = JobCreate.model_validate({
        "model": "synthetic-timeline", "task_type": "text_to_video",
        "input": {"prompt": "Synthetic timeline"}, "parameters": {"seconds": seconds},
    })
    assert request.parameters["seconds"] == seconds


@pytest.mark.parametrize("seconds", [0.0001, 5.1667, 34.1571, 120.001, float("inf")])
def test_submillisecond_nonfinite_and_overlimit_durations_are_rejected(seconds: float) -> None:
    with pytest.raises(ValueError):
        JobCreate.model_validate({
            "model": "synthetic-timeline", "task_type": "text_to_video",
            "input": {"prompt": "Synthetic timeline"}, "parameters": {"seconds": seconds},
        })


def test_performance_can_supply_named_media_without_an_unused_prompt() -> None:
    request = JobCreate.model_validate({
        "model": "synthetic-performance", "task_type": "references_to_video",
        "input": {"media": [{"slot": "source_video", "asset_id": "asset_video0001"}]},
    })
    assert "prompt" not in request.input


def test_roles_follow_transforms_and_keep_shared_anchors_and_bindings() -> None:
    item = {
        "output_kind": "video",
        "workflow_api": {
            "1": {"class_type": "LoadImage", "inputs": {"image": ""}},
            "2": {"class_type": "ImageScale", "inputs": {"image": ["1", 0]}},
            "3": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {"first_frame": ["2", 0], "last_frame": ["2", 0]}},
            "4": {"class_type": "LoadAudio", "inputs": {"audio": ""}},
            "5": {"class_type": "SoulXPodcastInputParser", "inputs": {"S2_prompt_audio": ["4", 0]}},
        },
        "input_slots": [{"name": "opaque_image", "kind": "image"}, {"name": "opaque_audio", "kind": "audio"}],
        "bindings": [{"source": "media:opaque_image", "node_id": "1", "input_name": "image"}, {"source": "media:opaque_audio", "node_id": "4", "input_name": "audio"}],
    }
    graph = deepcopy(item["workflow_api"])
    describe_media_slots(item)
    assert [slot["name"] for slot in item["input_slots"]] == ["first_last_frame", "speaker_2"]
    assert [binding["source"] for binding in item["bindings"]] == ["media:first_last_frame", "media:speaker_2"]
    assert item["workflow_api"] == graph
    assert "transcript" in item["input_slots"][1]["description"]
