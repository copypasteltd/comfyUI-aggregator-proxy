from generation_domain.errors import DomainError
from starlette.requests import Request


def upload_token(request: Request, *, required: bool = False) -> str | None:
    values = request.headers.getlist("idempotency-key")
    if not values:
        if required:
            raise DomainError("idempotency_required", "网关上传必须提供 Idempotency-Key", 400)
        return None
    if len(values) != 1 or not 1 <= len(values[0]) <= 128 or any(not 33 <= ord(c) <= 126 for c in values[0]):
        raise DomainError("invalid_idempotency_key", "上传幂等键格式无效", 400)
    return values[0]
