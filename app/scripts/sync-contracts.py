"""Copy frozen documentation contracts into installable runtime packages."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

app = Path(__file__).resolve().parents[1]
source = app.parent / "docs" / "contracts"
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--check", action="store_true", help="Reject drift without writing generated files")
arguments = parser.parse_args()
targets = {
    "endpoint-catalog.json": app / "packages/contracts/src/generation_contracts/endpoint-catalog.json",
    "job-state-machine.json": app / "packages/domain/src/generation_domain/job-state-machine.json",
    **{name + ".schema.json": app / "packages/contracts/src/generation_contracts/schemas" / (name + ".schema.json") for name in ("job-create", "key-policy", "worker-event", "gateway-request-identity", "model-discovery", "collection-page", "workflow-source-receipt", "workflow-catalog-receipt", "worker-capacity", "model-file-receipt", "spec-isolation", "admin-queue-query", "admin-content-query", "content-metadata", "release-manifest", "job-retry", "dispatch-policy", "resource-limits", "key-policy-options-query", "worker-node-command", "admin-statistics-query", "admin-statistics", "admin-job-timing", "recovery-checkpoint", "admin-audit-query", "admin-session", "admin-audit-retention", "admin-channel-query", "admin-channel-deletion", "worker-command-reconciliation", "workflow-sync-retry", "admin-job-cancellations", "admin-alert-query", "admin-alert-failure", "admin-model-catalog-query", "admin-model-mapping-query", "admin-model-mapping-options", "key-generation-options-query", "key-generation-options", "admin-workflow-catalog-query", "admin-workflow-catalog-page", "admin-workflow-sync-options-query", "admin-workflow-sync-options", "admin-workflow-history-query", "admin-workflow-history-page", "admin-workflow-diff-query", "admin-workflow-diff", "admin-workflow-draft-restore", "admin-workflow-draft-restored", "admin-workflow-lifecycle", "admin-workflow-lifecycle-result")},
}
hashes = {}
drift = []
for name in ("gateway-catalog-publication", "gateway-catalog-replay", "gateway-catalog-receipt", "gateway-catalog-stamp", "gateway-catalog-configuration"):
    targets[name + ".schema.json"] = app / "packages/contracts/src/generation_contracts/schemas" / (name + ".schema.json")
for name, target in targets.items():
    data = (source / name).read_bytes()
    if arguments.check:
        if not target.is_file() or target.read_bytes() != data:
            drift.append(name)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    hashes[name] = hashlib.sha256(data).hexdigest()
expected = json.dumps(hashes, indent=2) + "\n"
manifest = app / "config/contract-hashes.json"
if arguments.check:
    if not manifest.is_file() or manifest.read_text(encoding="utf-8") != expected:
        drift.append("contract-hashes.json")
    print(json.dumps({"passed": not drift, "contracts": len(targets), "drift": drift}))
    raise SystemExit(1 if drift else 0)
manifest.write_text(expected, encoding="utf-8")
print(f"Synchronized {len(targets)} frozen contracts")
