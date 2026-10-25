from __future__ import annotations

import re
from decimal import Decimal

from generation_domain.errors import DomainError


def frozen_video_specification(parameters: object) -> dict[str, str]:
    """Project only known persisted specifications; never guess or round metadata."""
    invalid = DomainError("invalid_video_metadata", "视频冻结规格不可用，请联系管理员", 503)
    if not isinstance(parameters, dict):
        raise invalid
    result = {}
    if "seconds" in parameters:
        value = parameters["seconds"]
        if type(value) not in (int, float):
            raise invalid
        number = Decimal(str(value))
        if not number.is_finite() or not 0 < number <= 86400:
            raise invalid
        milliseconds = number * 1000
        if milliseconds != milliseconds.to_integral_value():
            raise invalid
        whole, remainder = divmod(int(milliseconds), 1000)
        result["seconds"] = str(whole) + (f".{remainder:03d}".rstrip("0") if remainder else "")
    if "size" in parameters:
        size = parameters["size"]
        if not isinstance(size, str) or not re.fullmatch(r"[1-9][0-9]{1,4}x[1-9][0-9]{1,4}", size):
            raise invalid
        result["size"] = size
    return result
