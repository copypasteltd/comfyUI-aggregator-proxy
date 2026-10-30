"""Prepare the explicit 137 contract increment, without rewriting older schemas."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "aggregator-service/app"
sys.path[:0] = [str(base) for folder in ("packages", "apps") for base in (APP / folder).glob("*/src")]
from generation_contracts.device_pool import DevicePoolView, DeviceRouting  # noqa: E402

contracts = APP.parent / "docs/contracts"
for name, contract in (("admin-device-routing", DeviceRouting), ("admin-device-pool", DevicePoolView)):
    schema = {"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": "https://generation.local/contracts/" + name + ".schema.json", **contract.model_json_schema()}
    (contracts / (name + ".schema.json")).write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

path = contracts / "admin-model-mapping-options.schema.json"
schema = json.loads(path.read_bytes())
for branch in schema["allOf"]:
    if branch.get("if", {}).get("properties", {}).get("kind", {}).get("const") == "channel":
        item = branch["then"]["properties"]["data"]["items"]
        item["properties"]["gpu_id"] = {"type": "string", "pattern": "^gpu_[a-f0-9]{32}$", "not": {"pattern": "[\\r\\n]"}}
        if "gpu_id" not in item["required"]:
            item["required"].append("gpu_id")
path.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
path = contracts / "endpoint-catalog.json"
catalog = json.loads(path.read_bytes())
route = {"operation_id": "get_admin_api_v1_public_models_id_device_pool", "method": "GET", "path": "/admin/api/v1/public-models/{id}/device-pool", "identity": "admin", "scopes_all": [], "permissions_all": ["models:read"], "resource_guard": "admin_visibility", "csrf_required": False, "idempotency": "none", "if_match_required": False, "work_package": "WP-08", "response_schemas": ["admin-device-pool.schema.json"]}
if not any(item["operation_id"] == route["operation_id"] for item in catalog["endpoints"]):
    catalog["endpoints"].append(route)
path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"prepared_contracts": 4, "virtualization_used": False}))
