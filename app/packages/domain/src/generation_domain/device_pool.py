from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from generation_domain.errors import DomainError


@dataclass(frozen=True)
class DeviceShare:
    gpu_id: UUID
    weight: int
    credit: int
    last_sequence: int


def choose_device(members: Sequence[DeviceShare]) -> tuple[UUID, dict[UUID, int]] | None:
    """One smooth weighted round; the caller commits it only on acceptance."""
    if not members:
        return None
    if len({member.gpu_id for member in members}) != len(members) or any(not 1 <= member.weight <= 1000 for member in members):
        raise DomainError("device_pool_invalid", "设备池成员无效", 503)
    credits = {member.gpu_id: member.credit + member.weight for member in members}
    selected = min(members, key=lambda member: (-credits[member.gpu_id], member.last_sequence, member.gpu_id.int))
    credits[selected.gpu_id] -= sum(member.weight for member in members)
    if any(abs(value) > 2**63 - 1 for value in credits.values()):
        raise DomainError("device_pool_exhausted", "设备池计数已达上限", 503)
    return selected.gpu_id, credits
