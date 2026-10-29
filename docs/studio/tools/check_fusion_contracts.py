"""Exercise real Python DTOs and saved public model schemas without API/GPU calls."""

from __future__ import annotations

import copy
import hashlib
import itertools
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as SchemaError
from pydantic import ValidationError

from generation_contracts.compatibility import ImageGeneration, VideoGeneration
from generation_contracts.validation import JobCreate
from generation_domain.workflows import matching_presets

ROOT = Path(__file__).resolve().parents[2]
SERVICE = ROOT.parent / "生图生视API封装"
OUT = ROOT / "docs/research/fusion-assessment-2026-10-30"


def main() -> None:
    inputs = [
        "docs/evidence/2026-10-28/media-gen-live-catalog-final-124.json",
        "docs/examples/model3d/recheck-129/model-usage.json",
    ]
    catalog = json.loads((SERVICE / inputs[0]).read_text(encoding="utf-8"))
    models = catalog["models"] + [json.loads((SERVICE / inputs[1]).read_text(encoding="utf-8"))]
    cases: list[dict] = []

    def check(name: str, should_pass: bool, action) -> None:
        error = None
        try:
            action()
            accepted = True
        except (ValueError, ValidationError, SchemaError) as exc:
            accepted = False
            error = str(exc)[:500]
        cases.append({"id": name, "expected_valid": should_pass, "accepted": accepted, "passed": accepted == should_pass, "error": error})

    for model in models:
        alias, usage = model["id"], model["usage"]
        validator = Draft202012Validator(usage["request_schema"])
        Draft202012Validator.check_schema(usage["request_schema"])
        example = copy.deepcopy(usage["example_request"])
        body_input = example["input"]
        for index, binding in enumerate([body_input.get("source_image"), body_input.get("mask"), *body_input.get("references", [])]):
            if binding is not None:
                binding["asset_id"] = "asset_" + f"{index + 1:032x}"

        def validate(data):
            JobCreate.model_validate(data)
            validator.validate(data)
            params = {**usage["defaults"], **data.get("parameters", {})}
            if len(matching_presets({"presets": usage["presets"]}, params)) != 1:
                raise ValueError("Parameters do not select exactly one complete preset.")

        check(alias + ":example", True, lambda: validate(example))
        allowed = set(usage["request_schema"]["properties"]["parameters"]["properties"])
        for index, preset in enumerate(usage["presets"]):
            request = copy.deepcopy(example)
            request["parameters"] = {
                **{k: v for k, v in usage["defaults"].items() if k in allowed},
                **{k: v for k, v in preset.items() if k in allowed},
                "n": 1,
            }
            check(f"{alias}:preset-{index}", True, lambda request=request: validate(request))

        mutations = {
            "unknown-top-field": lambda x: x.update(output_format="png"),
            "batch-n-two": lambda x: x["parameters"].update(n=2),
            "unknown-input-field": lambda x: x["input"].update(unbound_option=True),
            "wrong-model-alias": lambda x: x.update(model="other-model"),
        }
        if "source_image" in body_input:
            mutations["missing-source-image"] = lambda x: x["input"].pop("source_image")
        if usage["task_type"] == "text_to_audio":
            mutations["tts-voice-not-workflow-input"] = lambda x: x["parameters"].update(voice="alloy")
            mutations["output-codec-not-request-param"] = lambda x: x["parameters"].update(audio_format="mp3")
        if usage["task_type"] == "image_to_3d":
            mutations["3d-rejects-prompt"] = lambda x: x["input"].update(prompt="ignored text")
            mutations["3d-rejects-size"] = lambda x: x["parameters"].update(size="720x1080")
        if body_input.get("references"):
            mutations["reference-count-minus-one"] = lambda x: x["input"]["references"].pop()
            if not usage["references"]["slots"][0].get("weight_supported", False):
                mutations["unbound-reference-weight"] = lambda x: x["input"]["references"][0].update(weight=0.5)
            if usage["task_type"] == "references_to_video" and len(body_input["references"]) == 1:
                mutations["first-frame-role-mismatch"] = lambda x: x["input"]["references"][0].update(role="reference_0")
        for name, mutate in mutations.items():
            changed = copy.deepcopy(example)
            mutate(changed)
            check(f"{alias}:{name}", False, lambda changed=changed: validate(changed))

        keys = [k for k in ["quality", "size", "seconds"] if any(k in p for p in usage["presets"])]
        options = [list(dict.fromkeys(p.get(k) for p in usage["presets"])) for k in keys]
        for values in itertools.product(*options):
            mixture = dict(zip(keys, values, strict=True))
            if any(all(p.get(k) == v for k, v in mixture.items()) for p in usage["presets"]):
                continue
            changed = copy.deepcopy(example)
            changed["parameters"].update(mixture)
            check(f"{alias}:mixed-preset-{len(cases)}", False, lambda changed=changed: validate(changed))

    check("upstream-image-output-format-rejected", False, lambda: ImageGeneration.model_validate({"model": "example", "prompt": "example", "output_format": "png"}))
    check("upstream-image-n-three-rejected", False, lambda: ImageGeneration.model_validate({"model": "example", "prompt": "example", "n": 3}))
    check("upstream-image-medium-quality-rejected", False, lambda: ImageGeneration.model_validate({"model": "example", "prompt": "example", "quality": "medium"}))
    check("upstream-video-extra-fields-rejected", False, lambda: VideoGeneration.model_validate({"model": "example", "prompt": "example", "seconds": "6", "mode": "frames", "watermark": "false", "resolution_name": "720p"}))
    report = {
        "checked_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        "scope": "offline current DTOs + historical public catalog schemas; no API, database, upload or GPU execution",
        "model_count": len(models),
        "task_types": sorted({model["usage"]["task_type"] for model in models}),
        "case_count": len(cases),
        "passed_count": sum(case["passed"] for case in cases),
        "passed": all(case["passed"] for case in cases),
        "catalog_is_live": False,
        "inputs": [{"path": name, "sha256": hashlib.sha256((SERVICE / name).read_bytes()).hexdigest()} for name in inputs],
        "cases": cases,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / "offline-contracts.json"
    if target.exists():
        raise SystemExit("Evidence exists; preserve it and choose a new run filename.")
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ["model_count", "task_types", "case_count", "passed_count", "passed"]}, ensure_ascii=False))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
