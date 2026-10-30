"""Verify actual rotation of new, owned validation identities; never rotate existing keys."""
from __future__ import annotations

import hashlib
import json
import os
import time
from copy import deepcopy
from datetime import UTC, datetime
from uuid import uuid4

import httpx
from jsonschema import Draft202012Validator

from studio_live import Admin, PRIVATE, ROOT
from verify_studio_lookup_live import keys, probe


def main():
    report_path = ROOT / "docs/research/studio-implementation/live-key-rotation-1361.json"
    state_path = PRIVATE / "key-rotation-1361.json"
    assert not report_path.exists() and not state_path.exists(), "Retain original evidence/credentials; reconcile before rerunning"
    state = {"plans": [], "issued": [], "requests": []}
    report = {"passed": False, "at": datetime.now(UTC).isoformat(), "scope": "Actual public HTTP, one newly owned image job and rotation of its newly issued validation Key; original user Keys unchanged. Not full multi-mode, phone, offline or fleet acceptance", "checks": [], "cleanup": {}}
    admin = Admin()
    clients = []

    def save():
        temporary = state_path.with_suffix(".pending")
        with temporary.open("w", encoding="utf-8") as output:
            output.write(json.dumps(state, indent=2) + "\n")
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(state_path)

    def check(name):
        report["checks"].append({"name": name, "passed": True})
        print(json.dumps({"passed": name}), flush=True)

    def business(secret):
        value = httpx.Client(base_url=admin.origin, trust_env=False, timeout=35, headers={"Authorization": "Bearer " + secret, "Origin": "http://rotation-validation.invalid:8111"})
        clients.append(value)
        return value

    def call(client, method, path, **kwargs):
        time.sleep(2)  # Existing QA cadence; no product budget or automatic mutation retry.
        response = client.request(method, path, **kwargs)
        state["requests"].append({"method": method, "path": path, "status": response.status_code})
        save()
        return response

    def create(source, role):
        body = {"name": "Studio rotation 1361 " + role + " " + uuid4().hex, "tenant_id": source["tenant_id"], "project_id": source["project_id"], "policy": deepcopy(source["policy"])}
        plan = {"body": body, "token": "studio-rotation-" + uuid4().hex}
        state["plans"].append(plan)
        save()
        issued = admin.call("POST", "api-keys", body, token=plan["token"])
        state["issued"].append(issued)
        save()
        assert issued.get("secret"), "Do not issue a replacement for a lost one-time secret"
        return issued

    old = current = None
    job_id = None
    try:
        before = probe()
        assert before["unfinished_jobs"] == before["gpu_occupancies"] == before["management_holds"] == 0
        original = list(keys(admin))
        source = next(value for value in original if value["enabled"] and {"images:create", "jobs:create", "jobs:read", "content:download"}.issubset(value["policy"]["scopes"]))
        owned = create(source, "owner")
        outsider = create(source, "unrelated")
        old, unrelated = business(owned["secret"]), business(outsider["secret"])
        response = call(old, "GET", "/v1/capabilities")
        assert response.status_code == 200
        model = next(value for value in response.json()["data"] if value["id"] == "comfy-3080-image-lustify-krea2")
        usage = model["usage"]
        request = deepcopy(usage["example_request"])
        request["input"]["prompt"] = "A small ivory ceramic robot with lime green ears, on a plain light studio background, soft daylight, no text, no logo."
        request["parameters"] = {name: value for name, value in usage["presets"][0].items() if name in usage["request_schema"]["properties"]["parameters"]["properties"]}
        request["parameters"].update(n=1, seed=7313601)
        request["client_request_id"] = "studio-rotation-1361-" + uuid4().hex
        Draft202012Validator(usage["request_schema"]).validate(request)
        state["submission"] = {"body": json.dumps(request, ensure_ascii=False, separators=(",", ":")), "token": "studio-rotation-job-" + uuid4().hex, "release_digest": model["release"]["digest"]}
        save()
        headers = {"Content-Type": "application/json", "Idempotency-Key": state["submission"]["token"], "X-Expected-Release-Digest": state["submission"]["release_digest"]}
        accepted = call(old, "POST", "/v1/jobs", content=state["submission"]["body"].encode(), headers=headers)
        assert accepted.status_code == 202, "Original submit is not automatically repeated"
        job_id = accepted.json()["id"]
        state["job_id"] = job_id
        save()
        check("one original image submission accepted and persisted before rotating the dedicated new Key")
        detail = admin.call("GET", "api-keys/" + owned["key"]["id"])
        state["rotation"] = {"token": "studio-rotation-action-" + uuid4().hex, "body": {"action": "rotate", "expected_revision": detail["revision"], "overlap_seconds": 0}}
        save()
        rotated = admin.call("POST", "api-keys/" + owned["key"]["id"] + "/actions", state["rotation"]["body"], token=state["rotation"]["token"])
        state["rotated"] = rotated
        save()
        assert rotated.get("secret")
        current = business(rotated["secret"])
        assert call(old, "GET", "/v1/jobs/" + job_id).status_code == 401
        visible = call(current, "GET", "/v1/jobs/" + job_id)
        assert visible.status_code == 200 and visible.json()["id"] == job_id and visible.json()["model"] == request["model"]
        assert visible.headers["Access-Control-Allow-Origin"] in ("*", "http://rotation-validation.invalid:8111")
        assert call(unrelated, "GET", "/v1/jobs/" + job_id).status_code == 404
        check("old secret is 401, rotated secret reads the same original job, unrelated logical Key is 404, arbitrary Origin is allowed")
        job = visible.json()
        while job["status"] not in {"succeeded", "failed", "cancelled", "expired"}:
            time.sleep(float(visible.headers.get("Retry-After", 2)))
            visible = call(current, "GET", "/v1/jobs/" + job_id)
            assert visible.status_code == 200
            job = visible.json()
            state["job"] = job
            save()
        assert job["status"] == "succeeded" and len(job["artifacts"]) == 1
        content_id = job["artifacts"][0]["id"]
        metadata = call(current, "GET", "/v1/content/" + content_id).json()
        content = call(current, "GET", "/v1/content/" + content_id + "/download")
        assert content.status_code == 200 and len(content.content) == metadata["bytes"] and hashlib.sha256(content.content).hexdigest() == metadata["sha256"]
        assert metadata["expires_at"] - job["completed_at"] == 28800
        assert call(unrelated, "GET", "/v1/content/" + content_id).status_code == 404
        assert call(old, "GET", "/v1/content/" + content_id + "/download").status_code == 401
        (PRIVATE / "key-rotation-image-1361.png").write_bytes(content.content)
        report["artifact"] = {"bytes": metadata["bytes"], "sha256": metadata["sha256"], "media_type": metadata["media_type"], "cloud_lifetime_seconds": 28800}
        check("original job completes once; rotated Key downloads exact SHA/bytes, while old and unrelated identities cannot read its content")
        detail = admin.call("GET", "api-keys/" + owned["key"]["id"])
        policy = deepcopy(detail["policy"])
        policy["scopes"] = ["jobs:read", "content:read", "content:download"]
        admin.call("PATCH", "api-keys/" + owned["key"]["id"], {"expected_revision": detail["revision"], "policy": policy})
        assert call(current, "GET", "/v1/jobs/" + job_id).status_code == 200
        assert call(current, "POST", "/v1/jobs/" + job_id + "/cancel").status_code == 403
        check("removing cancel scope still permits reads and makes the server reject cancellation with 403")
        after = probe()
        assert after["jobs"] == before["jobs"] + 1 and after["unfinished_jobs"] == after["gpu_occupancies"] == after["management_holds"] == 0
        unchanged = {value["id"]: value for value in keys(admin)}
        assert all(unchanged[value["id"]] == value for value in original)
        report.update({"jobs_before": before["jobs"], "jobs_after": after["jobs"], "generation_posts": sum(value["method"] == "POST" and value["path"] == "/v1/jobs" for value in state["requests"]), "user_keys_unchanged": True, "job_id": job_id})
        assert report["generation_posts"] == 1
        check("exactly one generation POST, no unfinished jobs/GPU occupancy, all preexisting Keys unchanged")
        report["passed"] = True
    except Exception as error:
        report["failure_type"] = type(error).__name__
        raise
    finally:
        try:
            # Reconcile even a lost create response by the unique persisted test name.
            names = {value["body"]["name"] for value in state["plans"]}
            owned_keys = [value for value in keys(admin) if value["name"] in names]
            if not job_id and old and state.get("submission"):
                submission = state["submission"]
                reconciled = call(old, "POST", "/v1/jobs/lookup", content=submission["body"].encode(), headers={"Content-Type": "application/json", "Idempotency-Key": submission["token"], "X-Expected-Release-Digest": submission["release_digest"]})
                if reconciled.status_code == 200 and reconciled.json().get("job"):
                    job_id = reconciled.json()["job"]["id"]
                    state["job_id"] = job_id
                    save()
            if job_id and not report["passed"] and (current or old):
                cleanup_client = current or old
                call(cleanup_client, "POST", "/v1/jobs/" + job_id + "/cancel")
            for value in owned_keys:
                if value["enabled"]:
                    admin.call("POST", "api-keys/" + value["id"] + "/actions", {"action": "disable", "expected_revision": value["revision"]})
            report["cleanup"]["temporary_keys_disabled"] = len(owned_keys) == len(state["plans"]) and all(not admin.call("GET", "api-keys/" + value["id"])["enabled"] for value in owned_keys)
            report["cleanup"]["rotated_secret_returns_401"] = current is not None and call(current, "GET", "/v1/access-policy").status_code == 401
            report["cleanup"]["no_preexisting_key_reenabled_or_rotated"] = True
            report["requests"] = state["requests"]
        finally:
            for client in clients:
                client.close()
            admin.close()
            report["passed"] = report["passed"] and all(report["cleanup"].values())
            with report_path.open("x", encoding="utf-8") as output:
                output.write(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"passed": report["passed"], "checks": len(report["checks"]), "report": report_path.relative_to(ROOT).as_posix(), "cleanup": report["cleanup"]}))


if __name__ == "__main__":
    main()
