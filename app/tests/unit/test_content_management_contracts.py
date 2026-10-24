from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from generation_contracts.content_management import ContentQuery, ContentTags
from pydantic import ValidationError

FIXTURES = json.loads((Path(__file__).resolve().parents[3] / "docs/contracts/fixtures.json").read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", [c for c in FIXTURES if c["id"].startswith("content_management_")], ids=lambda c: c["id"])
def test_content_dto_matches_frozen_fixture(case: dict[str, Any]) -> None:
    dto = ContentTags if case["schema"] == "content-metadata.schema.json" else ContentQuery
    if case["valid"]:
        value = dto.model_validate(case["data"]).model_dump(exclude_unset=True)
        assert value == case["data"]
    else:
        with pytest.raises(ValidationError):
            dto.model_validate(case["data"])


@pytest.mark.parametrize("tags", [["e\u0301"], [" leading"], ["trailing "], ["a\x7fb"], ["a\x85b"], ["same", "same"], [""], ["x" * 65], ["x"] * 17, [1]])
def test_tags_reject_ambiguous_or_unbounded_values(tags: list[Any]) -> None:
    with pytest.raises(ValidationError):
        ContentTags(expected_revision=1, tags=tags)


def test_tags_preserve_case_order_and_support_explicit_clear() -> None:
    tags = ["春季", "É", "Key", "key", "a" * 64]
    assert ContentTags(expected_revision=1, tags=tags).tags == tags
    assert ContentTags(expected_revision=2, tags=[]).tags == []
    assert ContentQuery(page="2", created_from="0", created_before="4102444800").filters() == {"kind": "output", "created_from": 0, "created_before": 4102444800}


@pytest.mark.parametrize("body", [{"expected_revision": True, "tags": []}, {"expected_revision": "1", "tags": []}, {"expected_revision": 1, "tags": [], "key_id": "unsafe"}, {"expected_revision": 0, "tags": []}])
def test_tag_mutation_is_strict_and_does_not_accept_owner_changes(body: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ContentTags.model_validate(body)
