from __future__ import annotations

from typing import Literal

from generation_contracts.validation import StrictDTO


class DispatchPolicy(StrictDTO):
    policy_id: Literal["tenant-service-aging-v1"]
    tenant_shares: Literal["equal"]
    service_unit: Literal["estimated_gpu_milliseconds"]
    aging_step_seconds: Literal[5]
    overdue_tenant_seconds: Literal[300]
    minimum_service_milliseconds: Literal[1000]
    maximum_service_milliseconds: Literal[7200000]
    image_baseline_milliseconds: Literal[60000]
    video_baseline_milliseconds: Literal[300000]
    audio_baseline_milliseconds: Literal[300000] = 300000

    @classmethod
    def current(cls) -> DispatchPolicy:
        return cls(policy_id="tenant-service-aging-v1", tenant_shares="equal", service_unit="estimated_gpu_milliseconds", aging_step_seconds=5, overdue_tenant_seconds=300, minimum_service_milliseconds=1000, maximum_service_milliseconds=7200000, image_baseline_milliseconds=60000, video_baseline_milliseconds=300000)
