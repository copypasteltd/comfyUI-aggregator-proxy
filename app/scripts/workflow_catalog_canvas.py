"""Freeze delivered image pixels independently of an upstream reference canvas."""

from __future__ import annotations


def ensure_image_delivery(graph, final_node_id, width, height):
    final = graph[final_node_id]["inputs"]
    source = final.get("images")
    if not isinstance(source, list) or len(source) != 2 or source[0] not in graph:
        raise ValueError("image_delivery_requires_a_graph_link")
    expected = {
        "upscale_method": "lanczos",
        "width": width,
        "height": height,
        "crop": "center",
    }
    previous = graph[source[0]]
    if previous["class_type"] == "ImageScale" and all(
        previous["inputs"].get(name) == value for name, value in expected.items()
    ):
        return
    if "rc_delivery_size" in graph:
        raise ValueError("image_delivery_node_collision")
    graph["rc_delivery_size"] = {
        "class_type": "ImageScale",
        "inputs": {"image": source, **expected},
    }
    final["images"] = ["rc_delivery_size", 0]


def freeze_image_size(graph, final_node_id, scalar):
    choices = [
        node["inputs"]
        for node in graph.values()
        if node["class_type"] in ["EmptyLatentImage", "EmptySD3LatentImage", "ImageScale"]
    ]
    choices = [values for values in choices if "width" in values and "height" in values]
    if choices:
        try:
            width, height = (int(scalar(graph, choices[-1][name])) for name in ["width", "height"])
        except ValueError:
            width, height = 1024, 1024
    else:
        width, height = 1024, 1024
    # Unused fixed latents do not establish the active sampler's image dimensions.
    # This explicitly constrains delivered pixels and is disclosed in model usage.
    ensure_image_delivery(graph, final_node_id, width, height)
    return width, height
