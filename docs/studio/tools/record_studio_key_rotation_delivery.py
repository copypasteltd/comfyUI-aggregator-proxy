"""Read-only closure checks for all owned validation Keys and the downloaded test image."""
from __future__ import annotations

import hashlib
import json
import shutil
import time
from datetime import UTC, datetime

import httpx
from PIL import Image

from studio_live import Admin, PRIVATE, ROOT


def sha(data):
    return hashlib.sha256(data).hexdigest()


if __name__ == "__main__":
    evidence = ROOT / "docs/research/studio-implementation"
    target = evidence / "validation-key-closure-1361.json"
    delivery = evidence / "key-rotation-delivery-1361.json"
    image_target = ROOT / "docs/media/studio-131/key-rotation-1361.png"
    assert not any(path.exists() for path in (target, delivery, image_target)), "Keep immutable proof and media"
    state = json.loads((PRIVATE / "key-rotation-1361.json").read_text(encoding="utf-8"))
    live_file = evidence / "live-key-rotation-1361.json"
    live = json.loads(live_file.read_text(encoding="utf-8"))
    assert live["passed"] and all(live["cleanup"].values()) and live["generation_posts"] == 1
    credentials = []
    for name in ("validation-key.json", "restricted-validation-key.json", "lookup-validation-key-1321.json"):
        value = json.loads((PRIVATE / name).read_text(encoding="utf-8"))
        credentials.append((name.removesuffix(".json"), value["key"]["id"], value["secret"]))
    for index, value in enumerate(state["issued"]):
        credentials.append((f"rotation-test-{index + 1}", value["key"]["id"], value["secret"]))
    credentials.append(("rotated-test-credential", state["rotated"]["key"]["id"], state["rotated"]["secret"]))
    identifiers = {identifier for _, identifier, _ in credentials}
    assert len(identifiers) == 5 and len(credentials) == 6
    admin = Admin()
    closure = {"passed": False, "at": datetime.now(UTC).isoformat(), "scope": "Read-only actual Admin enabled-state and public credential rejection for all five owned validation Keys and six credentials; no enable, rotate, delete, generation or user-Key changes", "checks": [], "cleanup": {}}
    try:
        assert all(not admin.call("GET", "api-keys/" + identifier)["enabled"] for identifier in identifiers)
        with httpx.Client(base_url=admin.origin, trust_env=False, timeout=35) as client:
            for role, _, secret in credentials:
                time.sleep(2)
                response = client.get("/v1/access-policy", headers={"Authorization": "Bearer " + secret})
                assert response.status_code == 401
                closure["checks"].append({"role": role, "status": 401, "passed": True})
        closure.update({"disabled_logical_keys": 5, "rejected_credentials": 6, "passed": True})
    finally:
        admin.close()
        closure["cleanup"]["clients_and_admin_session_closed"] = True
        with target.open("x", encoding="utf-8") as output:
            output.write(json.dumps(closure, indent=2) + "\n")
    source = PRIVATE / "key-rotation-image-1361.png"
    raw = source.read_bytes()
    assert len(raw) == live["artifact"]["bytes"] and sha(raw) == live["artifact"]["sha256"]
    with Image.open(source) as image:
        image.load()
        assert image.format == "PNG" and image.size == (720, 1080)
        dimensions = {"width": image.width, "height": image.height, "mode": image.mode, "fully_decoded": True}
    request = json.loads(state["submission"]["body"])
    assert request["parameters"]["size"] == "720x1080" and request["parameters"]["n"] == 1
    job = state["job"]
    assert job["id"] == live["job_id"] and job["status"] == "succeeded"
    result = {"passed": True, "at": datetime.now(UTC).isoformat(), "scope": "One actual 3080 image and rotated-credential delivery, independently fully decoded. Single-sample end-to-end latency includes queue/loading/execution; these components and steady-state performance are not separated or accepted.", "request": request, "authorization": "Bearer <business-key omitted>", "original_idempotency_token": "retained privately; one submission, no replay", "release_digest": state["submission"]["release_digest"], "job_id": job["id"], "created_at": job["created_at"], "completed_at": job["completed_at"], "end_to_end_seconds": job["completed_at"] - job["created_at"], "generation_posts": 1, "artifact": {"file": image_target.relative_to(ROOT).as_posix(), "bytes": len(raw), "sha256": sha(raw), "media_type": "image/png", **dimensions}, "actual_permissions": {"file": live_file.relative_to(ROOT).as_posix(), "sha256": sha(live_file.read_bytes())}, "key_closure": {"file": target.relative_to(ROOT).as_posix(), "sha256": sha(target.read_bytes())}}
    shutil.copyfile(source, image_target)
    with delivery.open("x", encoding="utf-8") as output:
        output.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"passed": True, "disabled_keys": 5, "rejected_credentials": 6, "image_bytes": len(raw), "dimensions": dimensions, "end_to_end_seconds": result["end_to_end_seconds"], "reports": [path.relative_to(ROOT).as_posix() for path in (target, delivery)]}))
