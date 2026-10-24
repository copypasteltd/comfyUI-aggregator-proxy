"""Compare route inventories without claiming runtime acceptance."""
from __future__ import annotations

import json
import re
from pathlib import Path

root = Path(__file__).resolve().parents[1]
schema = json.loads((root / "config/openapi.json").read_text(encoding="utf-8"))
catalog = json.loads((root.parent / "docs/contracts/endpoint-catalog.json").read_text(encoding="utf-8"))


def normalized(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "{id}", path)


actual = {(method.upper(), normalized(path)) for path, operations in schema["paths"].items() for method in operations if method.lower() in {"get", "post", "put", "patch", "delete", "head"}}
designed = {(entry["method"], normalized(entry["path"])) for entry in catalog["endpoints"]}
report = {"scope": "Route registration only; not permission, behavior, hardware or gateway acceptance", "design_endpoint_count": len(designed), "implemented_method_path_count": len(actual), "matched_design_count": len(actual & designed), "missing_design": [f"{method} {path}" for method, path in sorted(designed - actual)], "additional_implemented": [f"{method} {path}" for method, path in sorted(actual - designed)]}
(root / "config/implementation-inventory.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({k: v for k, v in report.items() if not isinstance(v, list)}, ensure_ascii=False))
