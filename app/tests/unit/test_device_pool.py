from collections import Counter
from uuid import UUID

import pytest
from generation_contracts.device_pool import DeviceRouting
from generation_domain.device_pool import DeviceShare, choose_device
from generation_domain.errors import DomainError
from pydantic import ValidationError


@pytest.mark.parametrize("weights, rounds, expected", [([1, 1, 1], 100, [34, 33, 33]), ([6, 3, 1], 100, [60, 30, 10]), ([1] * 20, 100, [5] * 20)])
def test_persistent_smooth_shares_independent_of_poll_order(weights, rounds, expected):
    members = [DeviceShare(UUID(int=i + 1), weight, 0, 0) for i, weight in enumerate(weights)]
    counts = Counter()
    for sequence in range(1, rounds + 1):
        selected, credits = choose_device(list(reversed(members)) if sequence % 2 else members)
        counts[selected] += 1
        members = [DeviceShare(item.gpu_id, item.weight, credits[item.gpu_id], sequence if selected == item.gpu_id else item.last_sequence) for item in members]
    assert [counts[item.gpu_id] for item in members] == expected
    assert sum(item.credit for item in members) == 0


@pytest.mark.parametrize("bad", [0, -1, 1001, True, "2"])
def test_routing_requires_strict_positive_bounded_weight(bad):
    with pytest.raises(ValidationError):
        DeviceRouting(policy="balanced_ready", weights={"gpu_" + "1" * 32: bad})


@pytest.mark.parametrize("payload", [{"weights": {"gpu_" + "1" * 32: 1}}, {"policy": "random"}, {"policy": "balanced_ready", "weights": {"channel": 1}}, {"policy": "balanced_ready", "arbitrary": True}])
def test_unknown_strategy_and_unsafe_configuration_rejected(payload):
    with pytest.raises(ValidationError):
        DeviceRouting.model_validate(payload)


def test_legacy_default_and_overflow_fail_closed():
    assert DeviceRouting().model_dump() == {"policy": "first_ready", "weights": {}}
    assert choose_device([]) is None
    member = DeviceShare(UUID(int=1), 1, 0, 0)
    with pytest.raises(DomainError):
        choose_device([member, member])
    with pytest.raises(DomainError):
        choose_device([DeviceShare(UUID(int=1), 1, 2**63 + 10, 0), DeviceShare(UUID(int=2), 1, 0, 0)])
