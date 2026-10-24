from copy import deepcopy

import pytest
from generation_contracts.defaults import default_limits
from generation_contracts.scope_administration import (
    ProjectCreate,
    ScopeLimits,
    ScopePatch,
    ScopeQuery,
    TenantCreate,
    TenantView,
)
from pydantic import ValidationError


def test_scope_names_are_normalized_and_project_ownership_is_required() -> None:
    created = TenantCreate(name="  Cafe\u0301  ", limits=default_limits())
    assert created.name == "Café" and created.enabled
    with pytest.raises(ValidationError):
        ProjectCreate.model_validate(created.model_dump())
    with pytest.raises(ValidationError):
        ScopePatch.model_validate({"expected_revision": 1, "tenant_id": "ten_" + "a" * 32})


@pytest.mark.parametrize("name", ["", "   ", "a" * 129, "bad\x00name", "bad\u200bname", True, 123])
def test_invalid_scope_names(name: object) -> None:
    with pytest.raises(ValidationError):
        TenantCreate.model_validate({"name": name, "limits": default_limits()})


@pytest.mark.parametrize("field,value", [("rpm", 60001), ("burst", 10001), ("daily_jobs", 9007199254740992), ("generating_jobs", -1), ("unfinished_jobs", True), ("daily_image_count", 1.5)])
def test_scope_limit_write_boundaries(field: str, value: object) -> None:
    limits = default_limits()
    limits[field]["value"] = value
    with pytest.raises(ValidationError):
        ScopeLimits.model_validate(limits)


def test_limits_require_explicit_modes_and_complete_shape() -> None:
    limits = default_limits()
    limits["rpm"] = {"mode": "unlimited", "value": None}
    assert ScopeLimits.model_validate(limits).rpm.value is None
    for broken in [{**limits, "rpm": {"mode": "unlimited", "value": 10}}, {**limits, "rpm": {"mode": "bounded", "value": None}}, {k: v for k, v in limits.items() if k != "burst"}]:
        with pytest.raises(ValidationError):
            ScopeLimits.model_validate(broken)
    with pytest.raises(ValidationError):
        TenantCreate.model_validate({"name": "Missing limits"})


@pytest.mark.parametrize("body", [{"expected_revision": 0, "enabled": False}, {"expected_revision": 1}, {"expected_revision": 1, "name": None}, {"expected_revision": True, "enabled": False}])
def test_scope_patch_requires_revision_and_actual_field(body: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ScopePatch.model_validate(body)


@pytest.mark.parametrize("query", [{"page": 0}, {"page": 10001}, {"q": "a\x00b"}, {"q": "a" * 129}, {"tenant_id": "prj_" + "a" * 32}, {"unknown": "ignored?"}])
def test_scope_query_is_bounded(query: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ScopeQuery.model_validate(query)


def test_scope_query_coercion_and_legacy_read_limits() -> None:
    assert ScopeQuery.model_validate({"page": "2", "enabled": "false", "q": "  Cafe\u0301 "}).model_dump()["q"] == "Café"
    limits = deepcopy(default_limits())
    limits["rpm"]["value"] = 60001
    record = TenantView.model_validate({"id": "ten_" + "a" * 32, "name": "Legacy", "enabled": True, "limits": limits, "revision": 1, "created_at": 1, "observed_at": 1, "usage": {"period": "2026-10-24", "daily_jobs": 0, "unfinished_jobs": 0, "generating_jobs": 0, "daily_image_count": 0, "daily_video_milliseconds": 0}})
    assert record.limits.rpm.value == 60001
