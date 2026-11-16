from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from generation_domain.values import fingerprint

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import workflow_presets as presets  # noqa: E402


def record(alias: str = "comfy-3080-test") -> dict:
    graph = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "private-original.png"}},
        "2": {"class_type": "Generate", "inputs": {"image": ["1", 0], "prompt": "private original prompt", "seed": 987}},
        "3": {"class_type": "SaveImage", "inputs": {"images": ["2", 0], "filename_prefix": "private-original"}},
    }
    manifest = {"task_type": "image_to_image", "workflow_api": graph,
                "input_slots": [{"name": "source_image", "kind": "image"}],
                "bindings": [{"node_id": node, "input_name": name, "source": source} for node, name, source in [
                    ("1", "image", "media:source_image"), ("2", "prompt", "prompt"),
                    ("2", "seed", "seed"), ("3", "filename_prefix", "output_prefix")]]}
    return {"model": {"alias": alias, "published": True, "release_id": "wfr_example", "task_type": "image_to_image"},
            "release": {"id": "wfr_example", "workflow_id": "wf_example", "version": 1, "validated": True,
                        "digest": fingerprint(manifest), "manifest": manifest},
            "usage": {"display_name": "Example", "task_type": "image_to_image", "defaults": {},
                      "example_request": {"model": alias, "input": {"prompt": "A ceramic teapot."}, "parameters": {"seed": 42}}}}


def test_portable_archive_removes_customer_literals_and_preserves_execution_edges(tmp_path: Path) -> None:
    item = record()
    graph, overrides = presets.portable_graph(item)
    assert "private-original" not in json.dumps(graph)
    assert "private original prompt" not in json.dumps(graph)
    assert graph["2"]["inputs"]["image"] == ["1", 0]
    assert graph["3"]["inputs"]["images"] == ["2", 0]
    assert overrides == {}
    assert item["release"]["manifest"]["workflow_api"]["1"]["inputs"]["image"] == "private-original.png"


def test_archive_rejects_entire_batch_before_writing_bad_release(tmp_path: Path) -> None:
    rows = [record(), record("comfy-3080-other")]
    rows[1]["release"]["digest"] = "0" * 64
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps(rows), "utf-8")
    output = tmp_path / "public"
    with pytest.raises(ValueError, match="digest mismatch"):
        presets.archive(snapshot, output, tmp_path / "private")
    assert not output.exists()


def test_device_paths_are_explicit_private_overrides_not_public_literals() -> None:
    item = record()
    item["release"]["manifest"]["workflow_api"]["4"] = {
        "class_type": "NativeModel", "inputs": {"repo_root": "E:/private-project/model", "pipeline_yaml": "E:/private-project/pipeline.yaml"}}
    graph, overrides = presets.portable_graph(item)
    assert graph["4"]["inputs"]["repo_root"] == "__DEVICE_REPO_ROOT__"
    assert "private-project" not in json.dumps(graph)
    assert overrides["4/repo_root"] == "E:/private-project/model"
    graph["4"]["inputs"]["unreviewed"] = "E:/unexpected/path"
    item["release"]["manifest"]["workflow_api"] = graph
    with pytest.raises(ValueError, match="Unreviewed absolute"):
        presets.portable_graph(item)


def test_archive_cannot_put_private_frozen_source_inside_public_output(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps([record()]), "utf-8")
    with pytest.raises(ValueError, match="outside the public archive"):
        presets.archive(snapshot, tmp_path / "public", tmp_path / "public/private")
    with pytest.raises(ValueError):
        presets.archive(snapshot, tmp_path / "public", tmp_path / "private", version="../escape")


def test_sealed_archive_detects_tampering_and_keeps_release_pin(tmp_path: Path) -> None:
    item = record()
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps([item]), "utf-8")
    output = tmp_path / "public"
    index = presets.archive(snapshot, output, tmp_path / "private", version="2026-11-17")
    assert index["native_root"] == "平台预设/2026-11-17"
    folder = output / index["presets"][0]["directory"]
    (folder / "workflow.ui.json").write_text('{"nodes": []}', "utf-8")
    presets.seal(output)
    assert presets.verify(output) == 8
    pin = json.loads((folder / "release.json").read_bytes())
    assert pin["snapshot_version"] == "2026-11-17"
    assert pin["digest"] == item["release"]["digest"]
    (folder / "workflow.api.json").write_text("{}", "utf-8")
    with pytest.raises(ValueError, match="digest mismatch"):
        presets.verify(output)


def test_canvas_keeps_exact_template_and_reports_missing_runtime_nodes(tmp_path: Path) -> None:
    item = record()
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps([item]), "utf-8")
    output, private = tmp_path / "public", tmp_path / "private"
    index = presets.archive(snapshot, output, private)
    ui = {"nodes": [{"id": n, "pos": [0, 0]} for n in (1, 2, 3)], "last_node_id": 3}
    (private / "comfy-3080-test-canvas-private.json").write_text(json.dumps({"ui": ui, "differences": [{"node_id": "2", "input": "seed"}]}), "utf-8")
    (private / "rtx3080-object-info-direct-private.json").write_text('{"LoadImage": {}, "SaveImage": {}}', "utf-8")
    completed = presets.complete(output, private, private)
    entry = completed["presets"][0]
    assert entry["runtime_observation"]["missing_node_classes"] == ["Generate"]
    assert entry["runtime_observation"]["generation_executed"] is False
    folder = output / index["presets"][0]["directory"]
    canvas = json.loads((folder / "workflow.ui.json").read_bytes())
    assert canvas["extra"]["platform_preset"]["workflow_api"] == json.loads((folder / "workflow.api.json").read_bytes())
    assert "Currently missing nodes: Generate" in canvas["nodes"][-1]["widgets_values"][0]


@pytest.mark.parametrize(("alias", "task", "slots", "expected"), [
    ("comfy-3080-yue2", "text_to_audio", [], "audio/music"),
    ("comfy-3080-voice", "text_to_audio", [{"kind": "audio"}], "audio/speech"),
    ("comfy-3080-dual", "text_to_audio", [{"kind": "audio"}] * 2, "audio/dialogue"),
    ("comfy-3080-3d", "image_to_3d", [{"kind": "image"}], "3d/image-to-3d"),
    ("comfy-3080-mv", "image_to_3d", [{"kind": "image"}] * 4, "3d/multiview-to-3d"),
    ("comfy-3080-mesh", "image_to_3d", [{"kind": "model"}, {"kind": "image"}], "3d/mesh-refinement"),
])
def test_categories_follow_required_inputs(alias: str, task: str, slots: list[dict], expected: str) -> None:
    assert presets.category(alias, {"task_type": task, "input_slots": slots}) == expected
