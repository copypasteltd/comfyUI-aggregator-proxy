"""Public HTTP lookup checks with a new jobs:read-only Key, disabled in finally."""
from __future__ import annotations

import json
import time
from copy import deepcopy
from datetime import UTC, datetime
from urllib.parse import quote
from uuid import uuid4

import httpx

from studio_deploy import run
from studio_live import Admin, PRIVATE, ROOT


def probe():
    result = run("/opt/comfyui-aggregator/current/venv/bin/python -B /var/lib/comfyui-aggregator-deploy/studio132/studio132-host.py probe")
    assert result["exit_code"] == 0
    return json.loads(result["stdout"])


def keys(admin):
    page, visited = admin.call("GET", "api-keys"), set()
    while True:
        yield from page["data"]
        if not page.get("has_more"):
            return
        cursor = page["next_cursor"]
        assert isinstance(cursor, str) and cursor not in visited
        visited.add(cursor)
        page = admin.call("GET", "api-keys?cursor=" + quote(cursor, safe=""))


if __name__ == "__main__":
    target = ROOT / "docs/research/studio-implementation/live-submission-lookup-1321.json"
    credential = PRIVATE / "lookup-validation-key-1321.json"
    assert not target.exists() and not credential.exists(), "Preserve existing evidence and credentials; never silently re-enable or duplicate a Key"
    admin = Admin()
    identifier, secret, client = None, None, None
    name = "Studio read-only lookup validation " + uuid4().hex
    report = {"passed": False, "at": datetime.now(UTC).isoformat(), "scope": "Actual public high-port HTTP, current read-only business Key, CORS and auth checks; no generation, replay, upload, physical phone or lost GPU response", "checks": [], "cleanup": {}}

    def check(name):
        report["checks"].append({"name": name, "passed": True})

    def call(method, path, **kwargs):
        # Reuse the existing QA poll cadence, without changing product budgets.
        time.sleep(2)
        return client.request(method, path, **kwargs)

    try:
        source = next(key for key in keys(admin) if key["enabled"])
        policy = deepcopy(source["policy"])
        policy["scopes"] = ["jobs:read"]
        body = {"name": name, "tenant_id": source["tenant_id"], "project_id": source["project_id"], "policy": policy}
        plan = {"token": "studio-readonly-key-" + uuid4().hex, "body": body}
        (PRIVATE / "lookup-validation-plan-1321.json").write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
        issued = admin.call("POST", "api-keys", body, token=plan["token"])
        identifier, secret = issued["key"]["id"], issued.get("secret")
        assert secret, "Do not issue another Key to recover a lost credential"
        with credential.open("x", encoding="utf-8") as output:
            output.write(json.dumps({"origin": admin.origin, **issued}, indent=2) + "\n")
        before = probe()
        client = httpx.Client(base_url=admin.origin, trust_env=False, timeout=35, headers={"Authorization": "Bearer " + secret, "Origin": "http://external-readonly-canvas.invalid:8111"})
        policy_response = call("GET", "/v1/access-policy")
        assert policy_response.status_code == 200
        assert policy_response.json()["scopes"] == ["jobs:read"]
        assert policy_response.headers["X-Studio-Submission-Lookup"] == "1"
        assert "X-Studio-Submission-Lookup" in policy_response.headers["Access-Control-Expose-Headers"]
        assert policy_response.headers["Access-Control-Allow-Origin"] in ("*", "http://external-readonly-canvas.invalid:8111")
        check("actual service advertises and exposes read-only lookup to an arbitrary Origin")
        headers = {"Idempotency-Key": "studio-readonly-probe-" + uuid4().hex, "X-Expected-Release-Digest": "b" * 64}
        request = {"model": "synthetic-readonly-probe", "task_type": "text_to_image", "input": {"prompt": "Synthetic read-only protocol validation; do not generate"}, "client_request_id": "live-readonly-1321"}
        lookup = call("POST", "/v1/jobs/lookup", headers=headers, json=request)
        assert lookup.status_code == 200 and lookup.json() == {"object": "generation.submission_lookup", "outcome": "not_observed", "job": None}
        assert "Location" not in lookup.headers and "Retry-After" not in lookup.headers
        report["lookup_response"] = lookup.json()
        check("jobs:read-only Key receives explicit not-observed without a published model or admission")
        assert call("POST", "/v1/jobs", headers=headers, json=request).status_code == 403
        check("same read-only Key cannot submit a generation request")
        assert call("POST", "/v1/jobs/lookup", json=request).status_code == 422
        assert call("POST", "/v1/jobs/lookup", headers={**headers, "Idempotency-Key": "short"}, json=request).status_code == 400
        assert call("POST", "/v1/jobs/lookup", headers={**headers, "X-Expected-Release-Digest": "bad"}, json=request).status_code == 422
        check("required original token and existing token/digest validation are enforced by public HTTP")
        after = probe()
        assert before["jobs"] == after["jobs"]
        assert after["unfinished_jobs"] == after["gpu_occupancies"] == after["management_holds"] == 0
        report["jobs_before"], report["jobs_after"] = before["jobs"], after["jobs"]
        check("production job count and GPU occupancy stay unchanged throughout all lookup checks")
        report["passed"] = True
    except Exception as error:
        report["failure_type"] = type(error).__name__
        raise
    finally:
        try:
            # A lost create response is also reconciled by the unique test name.
            if identifier is None:
                matches = [key for key in keys(admin) if key["name"] == name]
                assert len(matches) <= 1
                identifier = matches[0]["id"] if matches else None
            if identifier:
                current = admin.call("GET", "api-keys/" + identifier)
                if current["enabled"]:
                    admin.call("POST", "api-keys/" + identifier + "/actions", {"action": "disable", "expected_revision": current["revision"]})
                report["cleanup"]["temporary_key_disabled"] = not admin.call("GET", "api-keys/" + identifier)["enabled"]
                if client:
                    report["cleanup"]["revoked_key_returns_401"] = call("POST", "/v1/jobs/lookup", headers=headers, json=request).status_code == 401
            report["cleanup"]["previous_validation_keys_reenabled"] = False
        finally:
            if client:
                client.close()
            admin.close()
            report["cleanup"]["clients_closed"] = True
            report["passed"] = report["passed"] and report["cleanup"].get("temporary_key_disabled", False) and report["cleanup"].get("revoked_key_returns_401", False)
            with target.open("x", encoding="utf-8") as output:
                output.write(json.dumps(report, indent=2) + "\n")
            print(json.dumps(report))
