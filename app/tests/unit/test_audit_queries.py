from __future__ import annotations

import pytest
from generation_contracts.audit_queries import AuditQuery
from generation_infrastructure.admin_audit import safe_detail


@pytest.mark.parametrize("data", [{"page": True}, {"page": 2.5}, {"page": "1.0"}, {"created_from": False}, {"created_from": -1}, {"created_before": "-1"}, {"created_from": 100, "created_before": 100}, {"created_from": 200, "created_before": 100}, {"actor": "system\n"}, {"action": "key.created\n"}, {"request_id": "ok\x7f"}, {"tenant_id": "self"}])
def test_audit_query_rejects_unsafe_or_ambiguous_values(data: dict) -> None:
    with pytest.raises(ValueError):
        AuditQuery.model_validate(data)


def test_http_numeric_strings_and_public_resource_normalize() -> None:
    value = AuditQuery(page="2", created_from="1", created_before="4102444800", resource="ch_" + "a" * 32)  # type: ignore[arg-type]
    assert value.page == 2 and value.created_from == 1
    assert value.resource_uuid() == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    assert "page" not in value.filters()


@pytest.mark.parametrize("detail", [None, [], "secret", {str(i): i for i in range(65)}, {"authorization": "Bearer PRIVATE", "password": "PRIVATE", "encrypted_auth": "PRIVATE", "workflow": {"prompt": "PRIVATE"}}, {"revision": True, "enabled": 1, "changed_fields": ["authorization", "PRIVATE"], "operation_id": "op_PRIVATE", "evidence_id": "https://private?signature=PRIVATE", "role": "administrator\n", "count": 2**60}])
def test_arbitrary_history_or_wrong_types_are_not_projected(detail: object) -> None:
    assert safe_detail(detail) == ({}, True)


def test_typed_audit_projection_keeps_versions_ids_counts_and_dispatch() -> None:
    detail = {"revision_before": None, "revision_after": 1, "enabled": True, "operation_id": "op_" + "b" * 32, "changed_fields": ["authorization", "base_url"], "source_digest": "f" * 64, "dispatch": {"policy_id": "tenant-service-aging-v1", "cost_source": "audio_baseline", "estimated_service_milliseconds": 1000, "effective_priority": -5, "selected_at": 100, "sequence": 2}}
    assert safe_detail(detail) == (detail, False)
    filtered, redacted = safe_detail({**detail, "secret": "PRIVATE"})
    assert filtered == detail and redacted


def test_nested_dispatch_drops_unknown_values_without_returning_json_prefix() -> None:
    detail, redacted = safe_detail({"dispatch": {"policy_id": "private", "cost_source": "private", "sequence": True, "credential": "PRIVATE"}})
    assert detail == {"dispatch": {}} and redacted
