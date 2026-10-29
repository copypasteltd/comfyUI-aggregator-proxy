"""Live negative checks use dedicated, revocable test identities only."""
from __future__ import annotations

import json
import sys
import time
from copy import deepcopy
from datetime import UTC, datetime
from uuid import uuid4

import httpx
from jsonschema import Draft202012Validator, ValidationError

from studio_live import Admin, PRIVATE, ROOT


def main():
    action = sys.argv[1]
    source = json.loads((PRIVATE / "validation-key.json").read_text(encoding="utf-8"))
    report_file = ROOT / "docs/research/studio-implementation/live-api-131.json"
    report = json.loads(report_file.read_text(encoding="utf-8")) if report_file.exists() else {"scope": "actual public HTTP identity, release, CORS and reference validation; not full fault matrix", "checks": []}
    report["passed"] = False
    admin = Admin()
    clients = []

    def business(secret):
        class PacedClient(httpx.Client):
            def request(self, *args, **kwargs):
                # Share the original server's published poll interval with this QA
                # client, leaving room for the concurrently running UI observer.
                time.sleep(2)
                response = super().request(*args, **kwargs)
                while response.status_code in {429, 503} and response.headers.get("Retry-After"):
                    time.sleep(float(response.headers["Retry-After"]))
                    response = super().request(*args, **kwargs)
                return response
        client = PacedClient(base_url=source["origin"], trust_env=False, timeout=35, headers={"Authorization": "Bearer " + secret} if secret else {})
        clients.append(client); return client

    def check(name, detail):
        report["checks"].append({"name": name, "passed": True, "detail": detail})
        print(json.dumps({"passed": name, "detail": detail}), flush=True)

    def test_response(client, method, path, expected, **kwargs):
        response = client.request(method, path, **kwargs)
        if method == "POST" and path == "/v1/jobs" and response.status_code == 202:
            job = response.json(); (PRIVATE / "unexpected-negative-job.json").write_text(json.dumps(job), encoding="utf-8")
            client.post("/v1/jobs/" + job["id"] + "/cancel")
        assert response.status_code == expected, f"{method} {path}: {response.status_code} expected {expected}"
        return response

    try:
        if action == "base":
            key_file = PRIVATE / "restricted-validation-key.json"
            if not key_file.exists():
                plan_file = PRIVATE / "restricted-validation-key-plan.json"
                if not plan_file.exists():
                    policy = deepcopy(source["key"]["policy"])
                    policy["scopes"] = ["models:read", "capabilities:read", "jobs:read", "content:read", "content:download", "assets:read", "artifacts:read"]
                    body = {"tenant_id": source["key"]["tenant_id"], "project_id": source["key"]["project_id"], "name": "Studio fusion isolation (temporary)", "policy": policy}
                    plan_file.write_text(json.dumps({"token": "studio131-key-" + uuid4().hex, "body": body}), encoding="utf-8")
                plan = json.loads(plan_file.read_text(encoding="utf-8"))
                key = admin.call("POST", "api-keys", plan["body"], token=plan["token"])
                assert key.get("secret"), "Original one-time secret must be recovered before further key creation"
                key_file.write_text(json.dumps(key, indent=2), encoding="utf-8")
            restricted = json.loads(key_file.read_text(encoding="utf-8"))
            a, b, anonymous = business(source["secret"]), business(restricted["secret"]), business(None)
            for client in (anonymous, business("invalid-studio131-business-key")):
                test_response(client, "GET", "/v1/models", 401)
            test_response(admin.client, "GET", "/v1/models", 401)
            test_response(a, "GET", "/admin/api/v1/api-keys", 401)
            check("business/Admin identity realms remain independent", {"missing_or_invalid_business_key": 401, "admin_cookie_only_for_business": 401, "business_key_for_admin": 401})
            ui = json.loads((PRIVATE / "ui-image-job.json").read_text(encoding="utf-8"))
            test_response(b, "GET", "/v1/jobs/" + ui["job"]["id"], 404)
            assert b.get("/v1/jobs").json()["data"] == []
            check("second Key cannot read first Key task or history", {"key_a": source["key"]["id"], "key_b": restricted["key"]["id"], "cross_task": 404})
            response = a.get("/v1/access-policy", headers={"Origin": "http://studio-origin-test.invalid"}); response.raise_for_status()
            assert response.headers["X-Studio-Contract"] == "1" and response.headers["Access-Control-Allow-Origin"] in {"*", "http://studio-origin-test.invalid"}
            assert "X-Studio-Contract" in response.headers["Access-Control-Expose-Headers"]
            options = anonymous.options("/v1/jobs", headers={"Origin": "http://studio-origin-test.invalid", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "Authorization,Content-Type,Idempotency-Key,X-Expected-Release-Digest"})
            assert options.status_code == 200 and options.headers["Access-Control-Allow-Origin"] in {"*", "http://studio-origin-test.invalid"}
            check("arbitrary Origin and Studio release header work through public edge", {"allow_origin": response.headers["Access-Control-Allow-Origin"], "studio_contract": "1", "preflight": 200, "origin_reflection_permits_arbitrary_origins": True})
            catalog = a.get("/v1/capabilities").json()["data"]; models = {m["id"]: m for m in catalog}
            before = {job["id"] for job in a.get("/v1/jobs").json()["data"]}
            tests = []
            image = models["comfy-3080-image-lustify-krea2"]
            tests.append(("release mismatch", deepcopy(image["usage"]["example_request"]), "0" * 64, 409))
            wrong = deepcopy(image["usage"]["example_request"]); wrong["parameters"]["size"] = "721x1080"
            tests.append(("undeclared size", wrong, image["release"]["digest"], 422))
            edit = models["comfy-3080-image-edit-wai"]; wrong = deepcopy(edit["usage"]["example_request"]); wrong["input"].pop("source_image")
            tests.append(("missing edit source", wrong, edit["release"]["digest"], 422))
            refs = models["comfy-5070-video-h3-refs"]; wrong = deepcopy(refs["usage"]["example_request"]); wrong["input"]["references"] = wrong["input"]["references"][:1]
            tests.append(("one image cannot substitute for multi-reference workflow", wrong, refs["release"]["digest"], 422))
            plans = [{"name": name, "body": body, "release_digest": digest, "expected": status, "token": "studio131-negative-" + uuid4().hex} for name, body, digest, status in tests]
            (PRIVATE / ("negative-plans-" + uuid4().hex + ".json")).write_text(json.dumps(plans, indent=2), encoding="utf-8")
            for plan in plans:
                response = test_response(a, "POST", "/v1/jobs", plan["expected"], json=plan["body"], headers={"Idempotency-Key": plan["token"], "X-Expected-Release-Digest": plan["release_digest"]})
                check(plan["name"], {"status": response.status_code, "code": response.json()["error"]["code"]})
            test_response(b, "POST", "/v1/jobs", 403, json=image["usage"]["example_request"], headers={"Idempotency-Key": "studio131-readonly-" + uuid4().hex, "X-Expected-Release-Digest": image["release"]["digest"]})
            after = {job["id"] for job in a.get("/v1/jobs").json()["data"]}
            assert before == after
            check("negative requests create no additional task", {"task_ids_unchanged": True, "read_only_key_generation": 403})
        elif action == "catalog-scope":
            restricted = json.loads((PRIVATE / "restricted-validation-key.json").read_text(encoding="utf-8"))
            identifier = restricted["key"]["id"]
            assert identifier != source["key"]["id"]
            current = admin.call("GET", "api-keys/" + identifier)
            chosen = next(model for model in admin.call("GET", "public-models")["data"] if model["alias"] == "comfy-3080-image-lustify-krea2")
            selection = {"mode": "selected", "selected_ids": [chosen["alias"]]}
            if current["policy"]["models"] != selection:
                policy = deepcopy(current["policy"]); policy["models"] = selection
                plan = {"expected_revision": current["revision"], "policy": policy}
                (PRIVATE / "restricted-key-catalog-plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
                admin.call("PATCH", "api-keys/" + identifier, plan)
            a, b = business(source["secret"]), business(restricted["secret"])
            all_models = {item["id"] for item in a.get("/v1/models").json()["data"]}
            selected_models = b.get("/v1/models").json()["data"]
            selected_capabilities = b.get("/v1/capabilities").json()["data"]
            assert [item["id"] for item in selected_models] == [chosen["alias"]]
            assert [item["id"] for item in selected_capabilities] == [chosen["alias"]]
            excluded = next(alias for alias in sorted(all_models) if alias != chosen["alias"])
            test_response(b, "GET", "/v1/models/" + excluded, 404)
            assert b.get("/v1/access-policy").json()["scopes"] == current["policy"]["scopes"]
            check("different Key model selections agree across discovery endpoints", {"full_key_models": len(all_models), "restricted_key_models": 1, "restricted_key_capabilities": 1, "unselected_model_detail": 404, "read_only_scopes_preserved": True, "only_temporary_key_policy_changed": True})
        elif action == "reference-guards":
            a = business(source["secret"])
            state = json.loads((PRIVATE / "live-media-state.json").read_text(encoding="utf-8"))
            models = {item["id"]: item for item in a.get("/v1/capabilities").json()["data"]}
            before = {job["id"] for job in a.get("/v1/jobs").json()["data"]}
            plans = []
            for mode in ("references-image", "first-frame-video", "references-video"):
                operation = state["operations"][mode]
                model = models[operation["model"]]
                for kind in ("invalid role", "undeclared weight"):
                    body = json.loads(operation["body"])
                    if kind == "invalid role":
                        body["input"]["references"][0]["role"] = "reference_0" if mode == "first-frame-video" else "INVALID ROLE"
                    else:
                        body["input"]["references"][0]["weight"] = 1
                    try:
                        Draft202012Validator(model["usage"]["request_schema"]).validate(body)
                    except ValidationError:
                        pass
                    else:
                        raise AssertionError("QA mutation must violate the actual published schema")
                    plans.append({"name": mode + ": " + kind, "body": body, "digest": model["release"]["digest"], "token": "studio131-reference-negative-" + uuid4().hex})
            (PRIVATE / ("reference-negative-plans-" + uuid4().hex + ".json")).write_text(json.dumps(plans, indent=2), encoding="utf-8")
            for plan in plans:
                response = test_response(a, "POST", "/v1/jobs", 422, json=plan["body"], headers={"Idempotency-Key": plan["token"], "X-Expected-Release-Digest": plan["digest"]})
                check(plan["name"], {"status": response.status_code, "code": response.json()["error"]["code"]})
            after = {job["id"] for job in a.get("/v1/jobs").json()["data"]}
            assert before == after
            check("reference role/weight guards create no GPU task", {"task_ids_unchanged": True, "rejections": len(plans), "multi_reference_role_is_semantic_label_order_is_array_position": True})
        elif action == "content":
            restricted = json.loads((PRIVATE / "restricted-validation-key.json").read_text(encoding="utf-8"))
            a, b = business(source["secret"]), business(restricted["secret"])
            ui = json.loads((PRIVATE / "ui-image-job.json").read_text(encoding="utf-8")); job = a.get("/v1/jobs/" + ui["job"]["id"]).json()
            assert job["status"] == "succeeded"
            content = job["artifacts"][0]["id"]
            for path in ("/v1/content/" + content, "/v1/content/" + content + "/download"):
                test_response(b, "GET", path, 404)
            download = "/v1/content/" + content + "/download"
            test_response(b, "HEAD", download, 404)
            test_response(b, "GET", download, 404, headers={"Range": "bytes=0-15"})
            check("second Key cannot read or download first Key original", {"cross_metadata": 404, "cross_download": 404, "cross_head": 404, "cross_range": 404})
            metadata = test_response(a, "GET", "/v1/content/" + content, 200).json()
            head = test_response(a, "HEAD", download, 200)
            assert int(head.headers["Content-Length"]) == metadata["bytes"] and not head.content
            first = test_response(a, "GET", download, 206, headers={"Range": "bytes=0-15"})
            original = (ROOT / ".local/studio-live-media/image.png").read_bytes()
            assert first.content == original[:16] and first.headers["Content-Range"] == f"bytes 0-15/{metadata['bytes']}"
            check("authorized HEAD and byte Range match the actual original", {"head": 200, "range": 206, "bytes": metadata["bytes"]})
        elif action == "cleanup":
            for name in ("restricted-validation-key.json", "validation-key.json"):
                path = PRIVATE / name
                if not path.exists(): continue
                key = json.loads(path.read_text(encoding="utf-8"))["key"]
                rows = admin.call("GET", "api-keys")["data"]; current = next(row for row in rows if row["id"] == key["id"])
                if current["enabled"]:
                    admin.call("POST", "api-keys/" + key["id"] + "/actions", {"action": "disable", "expected_revision": current["revision"]})
                secret = json.loads(path.read_text(encoding="utf-8"))["secret"]
                test_response(business(secret), "GET", "/v1/models", 401)
                check("temporary validation Key disabled", {"id": key["id"], "revoked_access": 401})
        else:
            raise SystemExit("Choose base/catalog-scope/reference-guards/content/cleanup")
        report["passed"] = True
    finally:
        report["at"] = datetime.now(UTC).isoformat()
        report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for client in clients: client.close()
        admin.close()


if __name__ == "__main__":
    main()
