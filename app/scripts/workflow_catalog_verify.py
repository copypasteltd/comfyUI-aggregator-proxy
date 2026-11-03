"""Verify private catalog coverage, public usage and actual graph compilation offline."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

from generation_contracts.model_usage import ModelDocumentation
from generation_contracts.workflows import WorkflowManifest
from generation_domain.workflows import compile_graph, validate_manifest
from generation_infrastructure.model_usage import model_usage


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--manifests", type=Path, required=True)
    parser.add_argument("--public-output", type=Path, required=True)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_bytes())
    models, errors, aliases = [], {}, set()
    for key in plan["models"]:
        try:
            item = json.loads((args.manifests / (key + ".json")).read_bytes())
            manifest = item["manifest"]
            WorkflowManifest.model_validate(manifest)
            validate_manifest(manifest, manifest["task_type"])
            ModelDocumentation.model_validate(item["documentation"])
            if item["alias"] in aliases:
                raise ValueError("duplicate_public_alias")
            aliases.add(item["alias"])
            usage = model_usage(
                item["alias"],
                manifest["task_type"],
                manifest,
                item["defaults"],
                item["documentation"],
            )
            request = deepcopy(usage["example_request"])
            request["parameters"].setdefault("seed", 42)
            inputs = {
                "media:" + slot["name"]: "controlled/" + slot["name"]
                for slot in manifest.get("input_slots", [])
            }
            if manifest.get("context", {}).get("load"):
                inputs["context_latent"] = "controlled/parent.safetensors"
            original = deepcopy(manifest)
            compiled = compile_graph(manifest, request, inputs, "render-control/synthetic/result")
            if manifest != original or not compiled:
                raise ValueError("compilation_changed_release_or_returned_no_graph")
            models.append(
                {
                    "key": key,
                    "alias": item["alias"],
                    "gpu": key.split("-")[0],
                    "manifest_sha256": hashlib.sha256(
                        json.dumps(
                            manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                        ).encode()
                    ).hexdigest(),
                    "source_ids": item["source_ids"],
                    "source_files": item["source_files"],
                    "state": "offline_verified_not_published",
                    "usage": usage,
                }
            )
        except Exception as error:
            errors[key] = getattr(error, "code", str(error)[:400])
    result = {
        "format_version": 1,
        "at": datetime.now(UTC).isoformat(),
        "passed": not errors,
        "scope": "Static source coverage, frozen manifest, bilingual documentation, public schema/example and literal graph compilation. No GPU execution, validation approval or publication.",
        "real_resource_acceptance": False,
        "full_acceptance": "pending",
        "virtualization_used": False,
        "candidate_count": len(models),
        "by_task_type": dict(Counter(row["usage"]["task_type"] for row in models)),
        "models": models,
        "errors": errors,
    }
    args.public_output.parent.mkdir(parents=True, exist_ok=True)
    with args.public_output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps({k: result[k] for k in ("passed", "candidate_count", "by_task_type", "errors")})
    )
    print(
        json.dumps(
            {
                "compact_public_usage_bytes": len(
                    json.dumps(
                        [r["usage"] for r in models], ensure_ascii=False, separators=(",", ":")
                    ).encode()
                )
            }
        )
    )
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
