from __future__ import annotations

from typing import Any
from unittest.mock import Mock
from uuid import uuid4

import pytest
from generation_application.ports import BusinessIdentity, GenerationPort, GenerationService
from generation_domain.errors import DomainError


@pytest.mark.parametrize("method", ["preflight_inputs", "create"])
@pytest.mark.parametrize(
    ("task_type", "permission"),
    [
        ("text_to_image", "images:create"),
        ("image_to_image", "images:create"),
        ("references_to_image", "images:create"),
        ("text_to_video", "videos:create"),
        ("references_to_video", "videos:create"),
        ("text_to_audio", "audio:create"),
        ("image_to_3d", "meshes:create"),
    ],
)
def test_generation_accepts_only_the_requested_media_permission(
    task_type: str, permission: str, method: str
) -> None:
    identity = BusinessIdentity(uuid4(), uuid4(), uuid4(), {"scopes": ["jobs:create", permission]})
    repository = Mock(spec=GenerationPort)
    service = GenerationService(repository)
    request: dict[str, Any] = {"task_type": task_type}
    if method == "create":
        service.create(identity, request, "synthetic-idempotency", "synthetic-request")
        repository.create.assert_called_once_with(identity, request, "synthetic-idempotency", "synthetic-request")
    else:
        service.preflight_inputs(identity, request)
        repository.preflight_inputs.assert_called_once_with(identity, request)


@pytest.mark.parametrize("method", ["preflight_inputs", "create"])
@pytest.mark.parametrize("scopes", [["jobs:create", "images:create"], ["meshes:create"]])
def test_mesh_permission_never_substitutes_for_jobs_or_images(scopes: list[str], method: str) -> None:
    identity = BusinessIdentity(uuid4(), uuid4(), uuid4(), {"scopes": scopes})
    repository = Mock(spec=GenerationPort)
    service = GenerationService(repository)
    with pytest.raises(DomainError) as failure:
        if method == "create":
            service.create(identity, {"task_type": "image_to_3d"}, "synthetic-idempotency", "synthetic-request")
        else:
            service.preflight_inputs(identity, {"task_type": "image_to_3d"})
    assert failure.value.code == "scope_denied"
    repository.create.assert_not_called()
    repository.preflight_inputs.assert_not_called()
