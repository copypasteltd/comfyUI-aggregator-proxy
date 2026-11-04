from __future__ import annotations

from copy import deepcopy

import pytest
from generation_contracts.model_usage import ModelDocumentation
from generation_contracts.validation import JobCreate

from scripts.workflow_catalog_canvas import ensure_image_delivery, freeze_image_size
from scripts.workflow_catalog_metadata import describe_media_slots, public_documentation


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


def test_unused_latent_dimensions_do_not_bypass_image_delivery_canvas() -> None:
    graph = {
        "reference": {"class_type": "LoadImage", "inputs": {"image": "synthetic.png"}},
        "encode": {"class_type": "TextEncodeQwenImage21", "inputs": {"images.image_1": ["reference", 0]}},
        "unused": {"class_type": "EmptyLatentImage", "inputs": {"width": 768, "height": 1376}},
        "decode": {"class_type": "VAEDecode", "inputs": {"samples": ["encode", 2]}},
        "save": {"class_type": "SaveImage", "inputs": {"images": ["decode", 0]}},
    }
    width, height = freeze_image_size(graph, "save", lambda _graph, value: value)
    assert (width, height) == (768, 1376)
    delivery = graph[graph["save"]["inputs"]["images"][0]]
    assert delivery["class_type"] == "ImageScale"
    assert (delivery["inputs"]["width"], delivery["inputs"]["height"]) == (768, 1376)
    assert delivery["inputs"]["image"] == ["decode", 0]


def test_existing_delivery_canvas_is_reused_without_self_cycle() -> None:
    graph = {
        "source": {"class_type": "LoadImage", "inputs": {"image": "synthetic.png"}},
        "resize": {"class_type": "ImageScale", "inputs": {"image": ["source", 0], "width": 512, "height": 512, "crop": "center", "upscale_method": "lanczos"}},
        "save": {"class_type": "SaveImage", "inputs": {"images": ["resize", 0]}},
    }
    before = deepcopy(graph)
    ensure_image_delivery(graph, "save", 512, 512)
    ensure_image_delivery(graph, "save", 512, 512)
    assert graph == before


def test_image_delivery_preserves_colliding_source_node() -> None:
    graph = {
        "source": {"class_type": "LoadImage", "inputs": {"image": "synthetic.png"}},
        "rc_delivery_size": {"class_type": "SyntheticSourceNode", "inputs": {}},
        "save": {"class_type": "SaveImage", "inputs": {"images": ["source", 0]}},
    }
    before = deepcopy(graph)
    with pytest.raises(ValueError, match="image_delivery_node_collision"):
        ensure_image_delivery(graph, "save", 512, 512)
    assert graph == before


def test_image_usage_discloses_delivery_resize_in_both_languages() -> None:
    manifest = {
        "workflow_api": {
            "1": {"class_type": "QwenImage21FastSampler", "inputs": {}},
            "2": {"class_type": "ImageScale", "inputs": {"image": ["1", 0], "width": 768, "height": 1376, "upscale_method": "lanczos", "crop": "center"}},
            "3": {"class_type": "SaveImage", "inputs": {"images": ["2", 0]}},
        },
        "task_type": "image_to_image", "presets": [{"size": "768x1376"}],
        "bindings": [{"source": "prompt"}], "output": {"final_node_ids": ["3"]},
    }
    documentation = ModelDocumentation.model_validate(public_documentation(
        {"source": "synthetic/qwen.api.json", "output_kind": "image", "gpu": "3080"}, manifest,
    ))
    assert any("交付" in note and "Lanczos" in note and "delivered canvas" in note for note in documentation.notes)


def test_image_usage_does_not_claim_an_unconnected_delivery_resize() -> None:
    manifest = {
        "workflow_api": {
            "1": {"class_type": "QwenImage21FastSampler", "inputs": {}},
            "2": {"class_type": "ImageScale", "inputs": {"image": ["1", 0], "width": 512, "height": 512, "upscale_method": "lanczos", "crop": "center"}},
            "3": {"class_type": "SaveImage", "inputs": {"images": ["1", 0]}},
        },
        "task_type": "text_to_image", "presets": [{"size": "512x512"}],
        "bindings": [{"source": "prompt"}], "output": {"final_node_ids": ["3"]},
    }
    documentation = public_documentation({"source": "synthetic/native-size.api.json", "output_kind": "image", "gpu": "3080"}, manifest)
    assert any("does not declare a final Lanczos resize" in note for note in documentation["notes"])
    assert not any("Lanczos resize and center crop are applied" in note for note in documentation["notes"])


@pytest.mark.parametrize("speakers", [("speaker_1",), ("speaker_2",), ("speaker_1", "speaker_2")])
def test_podcast_usage_names_only_speakers_with_declared_reference_slots(speakers: tuple[str, ...]) -> None:
    manifest = {
        "workflow_api": {"1": {"class_type": "SoulXPodcastInputParser", "inputs": {}}},
        "task_type": "text_to_audio", "presets": [{"seconds": 30}],
        "input_slots": [{"name": speaker, "kind": "audio"} for speaker in speakers],
        "bindings": [], "output": {},
    }
    documentation = public_documentation({"source": "synthetic/speech.api.json", "output_kind": "audio", "gpu": "3080"}, manifest)
    note = next(note for note in documentation["notes"] if "Speaker labels for this model" in note)
    assert ("[S1]" in note) == ("speaker_1" in speakers)
    assert ("[S2]" in note) == ("speaker_2" in speakers)
    assert "supplied reference recording" in note and "exact transcript" in note
    assert ("Single speaker speech" in documentation["description"]) == (len(speakers) == 1)
