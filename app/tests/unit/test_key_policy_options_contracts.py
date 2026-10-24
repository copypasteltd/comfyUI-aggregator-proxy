from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from generation_contracts.key_policy_options import KeyPolicyOptionQuery
from pydantic import ValidationError

FIXTURES = json.loads((Path(__file__).resolve().parents[3] / "docs/contracts/fixtures.json").read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", [value for value in FIXTURES if value["id"].startswith("key_policy_options_")], ids=lambda value: value["id"])
def test_directory_dto_matches_contract(case: dict[str, Any]) -> None:
    if case["valid"]:
        assert KeyPolicyOptionQuery.model_validate(case["data"]).model_dump(exclude_unset=True) == case["data"]
    else:
        with pytest.raises(ValidationError):
            KeyPolicyOptionQuery.model_validate(case["data"])


def test_search_normalizes_nfc_and_whitespace_without_widening_id_rules() -> None:
    query = KeyPolicyOptionQuery(mode="browse", kind="project", tenant_id="ten_" + "a" * 32, q="  Cafe\u0301  ")
    assert query.q == "Café"
    with pytest.raises(ValidationError):
        KeyPolicyOptionQuery(mode="resolve", kind="project", tenant_id="ten_" + "a" * 32, ids=[" prj_" + "b" * 32])


@pytest.mark.parametrize("q", ["a\x7fb", "a\x85b", "a\u200bb", "a\ud800b"])
def test_all_unicode_control_categories_are_rejected(q: str) -> None:
    with pytest.raises(ValidationError):
        KeyPolicyOptionQuery(mode="browse", kind="project", tenant_id="ten_" + "a" * 32, q=q)
