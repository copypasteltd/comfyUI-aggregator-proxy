"""Authorised live acceptance helpers; all credentials and raw DTOs stay in .local/."""
from __future__ import annotations

import json
from copy import deepcopy
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / ".local/studio-ops"
PRIVATE.mkdir(parents=True, exist_ok=True)


class Admin:
    def __init__(self):
        access_file = PRIVATE / "admin-access.json"
        if not access_file.exists():
            shutil.copyfile(ROOT.parent / "生图生视API封装/app/.local/deploy-20261027/admin-access.json", access_file)
        access = json.loads(access_file.read_text(encoding="utf-8-sig"))
        url = urlsplit(access["url"])
        self.origin = f"{url.scheme}://{url.netloc}"
        # Same transport policy as the existing authorised deployment client.
        self.client = httpx.Client(base_url=self.origin, trust_env=False, timeout=35, headers={"Origin": self.origin})
        response = self.client.post("/admin/api/v1/session", json={"username": access["username"], "password": access["password"]})
        response.raise_for_status()
        self.client.headers["X-CSRF-Token"] = response.json()["csrf_token"]

    def call(self, method, path, data=None, *, token=None):
        headers = {"Idempotency-Key": token or uuid4().hex} if method not in ("GET", "HEAD") else {}
        if isinstance(data, dict) and "expected_revision" in data:
            headers["If-Match"] = '"' + str(data["expected_revision"]) + '"'
        response = self.client.request(method, "/admin/api/v1/" + path, json=data, headers=headers)
        if response.is_error:
            (PRIVATE / "last-admin-error.json").write_text(response.text, encoding="utf-8")
            raise RuntimeError(f"Admin {method} {path}: HTTP {response.status_code}; detail retained privately")
        return response.json() if response.content else None

    def close(self):
        try:
            self.call("DELETE", "session")
        finally:
            self.client.close()


if __name__ == "__main__":
    if sys.argv[1:] == ["create-validation-key"]:
        key_file = PRIVATE / "validation-key.json"
        if key_file.exists():
            print(json.dumps({"existing_private_validation_key_retained": True}))
            raise SystemExit(0)
        snapshot = json.loads((PRIVATE / "live-audit.json").read_text(encoding="utf-8"))["data"]
        source = next(key for key in snapshot["api-keys"]["data"] if key["enabled"] and "meshes:create" in key["policy"]["scopes"] and "audio:create" in key["policy"]["scopes"])
        plan_file = PRIVATE / "validation-key-plan.json"
        if not plan_file.exists():
            body = {"tenant_id": source["tenant_id"], "project_id": source["project_id"], "name": "Studio fusion acceptance (temporary)", "policy": deepcopy(source["policy"])}
            plan_file.write_text(json.dumps({"token": "studio-key-" + uuid4().hex, "body": body}), encoding="utf-8")
        plan = json.loads(plan_file.read_text(encoding="utf-8"))
        admin = Admin()
        try:
            value = admin.call("POST", "api-keys", plan["body"], token=plan["token"])
            if not value.get("secret"):
                raise RuntimeError("Key creation has no recoverable secret; inspect original Admin operation, do not create a duplicate")
            key_file.write_text(json.dumps({"origin": admin.origin, **value}, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({"validation_key_created": True, "key_id": value["key"]["id"], "credentials_published": False}))
        finally:
            admin.close()
        raise SystemExit(0)
    if sys.argv[1:] == ["catalog"]:
        private_key = json.loads((PRIVATE / "validation-key.json").read_text(encoding="utf-8"))
        with httpx.Client(base_url=private_key["origin"], trust_env=False, timeout=35, headers={"Authorization": "Bearer " + private_key["secret"]}) as client:
            catalog = {}
            contract = None
            for name in ("access-policy", "models", "capabilities"):
                response = client.get("/v1/" + name)
                response.raise_for_status()
                catalog[name] = response.json()
                if name == "access-policy":
                    contract = response.headers.get("X-Studio-Contract")
            (PRIVATE / "live-catalog.json").write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(json.dumps({"model_count": len(catalog["models"]["data"]), "capability_count": len(catalog["capabilities"]["data"]), "studio_contract": contract, "models": [{"id": model["id"], "task_type": model.get("usage", {}).get("task_type")} for model in catalog["models"]["data"]]}, ensure_ascii=False))
        raise SystemExit(0)
    if sys.argv[1:] != ["audit"]:
        raise SystemExit("Choose audit, create-validation-key or catalog")
    admin = Admin()
    try:
        endpoints = ("api-keys", "public-models", "channels", "worker-nodes", "queues/snapshot")
        snapshot = {name: admin.call("GET", name) for name in endpoints}
        stamp = datetime.now(UTC).isoformat()
        (PRIVATE / "live-audit.json").write_text(json.dumps({"at": stamp, "data": snapshot}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        summary = {}
        for name, value in snapshot.items():
            rows = value.get("data", value.get("items", [])) if isinstance(value, dict) else value
            summary[name] = {"shape": list(value) if isinstance(value, dict) else "list", "rows": len(rows)}
            if name == "public-models":
                summary[name]["models"] = [{key: row.get(key) for key in ("id", "alias", "task_type", "published", "validation_status")} for row in rows]
            if name == "channels":
                summary[name]["channels"] = [{key: row.get(key) for key in ("id", "name", "enabled", "observation_status", "desired_revision", "applied_revision", "verified_revision")} for row in rows]
        print(json.dumps({"at": stamp, "origin": admin.origin, "audit": summary}, ensure_ascii=False))
    finally:
        admin.close()
