"""Verify a deployed control plane using disposable, audited API resources."""
from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import ssl
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
from PIL import Image


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--access-file", type=Path, required=True, help="Private JSON containing url, username, password")
    parser.add_argument("--ca-file", type=Path)
    parser.add_argument("--expect-any-origin", action="store_true")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    access = json.loads(args.access_file.read_text(encoding="utf-8"))
    origin = access["url"].removesuffix("/admin/")
    calling_origin = "https://arbitrary-client.invalid" if args.expect_any_origin else origin
    checks: list[dict[str, object]] = []
    keys: list[str] = []
    asset: str | None = None
    owner_token: str | None = None
    authenticated = False
    csrf = ""
    passed = False

    def check(name: str, condition: bool, **details: object) -> None:
        checks.append({"check": name, "passed": condition, **details})
        if not condition:
            raise RuntimeError(f"Deployment check failed: {name}")

    def write_headers(**extra: str) -> dict[str, str]:
        return {"Origin": calling_origin, "X-CSRF-Token": csrf, "Idempotency-Key": str(uuid4()), **extra}

    verify = ssl.create_default_context(cafile=str(args.ca_file)) if args.ca_file else True
    with httpx.Client(base_url=origin, verify=verify, trust_env=False, timeout=20) as client:
        try:
            for route in ("/health/live", "/health/ready", "/admin/"):
                response = client.get(route)
                check("public_entry_" + route, response.status_code == 200, status=response.status_code)
            live = client.get("/health/live").json()
            check("production_with_real_s3_adapter", live.get("environment") == "production" and live.get("storage_backend") == "s3")
            for route, expected in (("/internal/v1/heartbeat", 404), ("/internal/monitoring/v1/metrics", 404), ("/v1/models", 401), ("/admin/api/v1/session", 401)):
                response = client.get(route)
                check("unauthenticated_boundary_" + route, response.status_code == expected, status=response.status_code)
            if args.expect_any_origin:
                for arbitrary in ("https://arbitrary-client.invalid", "http://different.invalid:8090", "null"):
                    response = client.options("/v1/assets", headers={"Origin": arbitrary, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization,x-arbitrary-header,idempotency-key"})
                    check("arbitrary_origin_preflight_" + arbitrary, response.status_code == 200 and response.headers.get("access-control-allow-origin") == arbitrary and response.headers.get("access-control-allow-credentials") == "true" and "x-arbitrary-header" in response.headers.get("access-control-allow-headers", "").lower(), status=response.status_code)
                response = client.get("/v1/models", headers={"Origin": calling_origin})
                check("cors_on_authentication_failure", response.status_code == 401 and response.headers.get("access-control-allow-origin") == calling_origin, status=response.status_code)
            else:
                response = client.post("/admin/api/v1/session", headers={"Origin": "https://invalid.example"}, json={"username": access["username"], "password": access["password"]})
                check("wrong_login_origin_denied", response.status_code == 403, status=response.status_code)
            response = client.post("/admin/api/v1/session", headers={"Origin": calling_origin}, json={"username": access["username"], "password": access["password"]})
            check("administrator_login", response.status_code == 200, status=response.status_code)
            authenticated = True
            info = response.json()
            csrf = info["csrf_token"]
            cookie = response.headers.get("set-cookie", "").casefold()
            check("cookie_matches_public_transport", ("secure" in cookie) == origin.startswith("https://") and "httponly" in cookie)
            if args.expect_any_origin:
                response = client.post("/admin/api/v1/session/activity", headers=write_headers(Origin="https://second-client.invalid"), json={"activity": "interactive"})
                check("admin_write_from_another_origin", response.status_code == 200, status=response.status_code)
            scope = info["scopes"][0]
            parameters = {k: scope[k] for k in ("tenant_id", "project_id")}
            routes = ("worker-nodes", "channels", "workflows", "public-models", "model-artifacts", "queues/snapshot", "queues/statistics", "queues/jobs", "api-keys", "content", "tenants", "projects", "admin-users", "operations", "alerts", "audit-events", "audit-retention")
            for route in routes:
                response = client.get("/admin/api/v1/" + route)
                check("admin_read_" + route, response.status_code == 200, status=response.status_code)
            policy = client.get("/admin/api/v1/api-keys").json()["default_policy"]
            policy["scopes"] = ["models:read", "assets:upload", "assets:read", "content:read", "content:download", "content:delete"]
            request = {**parameters, "name": "deployment-verification-" + uuid4().hex[:10], "policy": policy}
            response = client.post("/admin/api/v1/api-keys", headers={"Origin": origin, "Idempotency-Key": str(uuid4())}, json=request)
            check("management_write_without_csrf_denied", response.status_code == 403, status=response.status_code)
            tokens = []
            for number in range(2):
                request["name"] = "deployment-verification-" + str(number) + "-" + uuid4().hex[:8]
                response = client.post("/admin/api/v1/api-keys", headers=write_headers(), json=request)
                check("create_disposable_business_key_" + str(number), response.status_code == 201, status=response.status_code)
                value = response.json()
                keys.append(value["key"]["id"])
                tokens.append(value["secret"])
            owner_token = tokens[0]
            auth = {"Authorization": "Bearer " + owner_token}
            with httpx.Client(base_url=origin, verify=verify, trust_env=False, timeout=20, headers=auth) as business:
                response = business.get("/admin/api/v1/session")
                check("business_key_cannot_open_admin_session", response.status_code == 401, status=response.status_code)
                response = business.get("/v1/models")
                check("unapproved_models_not_published", response.status_code == 200 and response.json().get("data") == [], status=response.status_code)
                image = io.BytesIO()
                Image.new("RGB", (16, 16), (190, 244, 60)).save(image, format="PNG")
                raw = image.getvalue()
                response = business.post("/v1/assets", headers={"Idempotency-Key": str(uuid4())}, files={"file": ("deployment-probe.png", raw, "image/png")})
                check("real_s3_asset_upload", response.status_code == 201, status=response.status_code)
                uploaded = response.json()
                asset = uploaded["id"]
                check("uploaded_hash_matches", uploaded["sha256"] == hashlib.sha256(raw).hexdigest())
                route = f"/v1/assets/{asset}/content"
                response = business.get(route)
                check("sensitive_input_scope_required", response.status_code == 403, status=response.status_code)
                policy["scopes"].append("content:sensitive")
                for number, identifier in enumerate(keys):
                    record = client.get("/admin/api/v1/api-keys/" + identifier).json()
                    revision = record["revision"]
                    response = client.patch("/admin/api/v1/api-keys/" + identifier, headers=write_headers(**{"If-Match": f'"{revision}"'}), json={"expected_revision": revision, "policy": policy})
                    check("explicitly_grant_input_download_" + str(number), response.status_code == 200, status=response.status_code)
                response = business.get(route)
                check("authenticated_asset_download", response.status_code == 200 and response.content == raw, status=response.status_code)
                response = business.get(route, headers={"Range": "bytes=0-7"})
                check("authenticated_range_download", response.status_code == 206 and response.content == raw[:8], status=response.status_code)
                response = business.head(route)
                check("authenticated_head", response.status_code == 200 and not response.content, status=response.status_code)
                response = client.get(route)
                check("admin_cookie_cannot_replace_business_key", response.status_code == 401, status=response.status_code)
                response = client.get(route, headers={"Authorization": "Bearer " + tokens[1]})
                check("second_key_cannot_read_owner_asset", response.status_code in (403, 404), status=response.status_code)
                second = client.get("/admin/api/v1/api-keys/" + keys[1]).json()
                restricted = copy.deepcopy(policy)
                restricted["allowed_cidrs"] = {"mode": "allowlist", "values": ["192.0.2.1/32"]}
                response = client.patch("/admin/api/v1/api-keys/" + keys[1], headers=write_headers(**{"If-Match": f'"{second["revision"]}"'}), json={"expected_revision": second["revision"], "policy": restricted})
                check("set_ip_restriction", response.status_code == 200, status=response.status_code)
                response = client.get("/v1/models", headers={"Authorization": "Bearer " + tokens[1], "X-Forwarded-For": "192.0.2.1"})
                check("client_cannot_spoof_forwarded_ip", response.status_code == 403, status=response.status_code)
                response = business.delete(f"/v1/assets/{asset}")
                check("asset_delete_accepted", response.status_code == 202, status=response.status_code)
                response = business.get(route)
                check("deleted_asset_immediately_denied", response.status_code in (403, 404, 410), status=response.status_code)
                asset = None
            passed = True
        finally:
            try:
                if asset and owner_token:
                    response = client.delete(f"/v1/assets/{asset}", headers={"Authorization": "Bearer " + owner_token})
                    check("cleanup_probe_asset", response.status_code == 202, status=response.status_code)
                for identifier in keys:
                    record = client.get("/admin/api/v1/api-keys/" + identifier).json()
                    revision = record["revision"]
                    response = client.delete("/admin/api/v1/api-keys/" + identifier, params={"revision": revision}, headers=write_headers(**{"If-Match": f'"{revision}"'}))
                    check("cleanup_probe_key", response.status_code == 200, status=response.status_code)
                if authenticated:
                    response = client.delete("/admin/api/v1/session", headers=write_headers())
                    check("administrator_logout", response.status_code == 204, status=response.status_code)
                    check("session_revoked", client.get("/admin/api/v1/session").status_code == 401)
            finally:
                args.report.parent.mkdir(parents=True, exist_ok=True)
                args.report.write_text(json.dumps({"at": datetime.now(UTC).isoformat(), "kind": "real_linux_public_http" if origin.startswith("http://") else "real_linux_public_https", "any_origin": args.expect_any_origin, "passed": passed and all(c["passed"] for c in checks), "checks": checks, "real_workflows_verified": 0, "generation_acceptance": False}, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": passed, "checks": len(checks)}))


if __name__ == "__main__":
    main()
