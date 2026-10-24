from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from generation_application.ports import BusinessIdentity
from generation_domain.errors import DomainError
from generation_domain.values import canonical_json, fingerprint, utcnow


@dataclass(frozen=True)
class CursorPosition:
    through: datetime
    issued_at: int
    after_time: datetime | None = None
    after_id: UUID | None = None


class CursorCodec:
    """An authenticated continuation is a position, never an access grant."""

    def __init__(self, key: str) -> None:
        self._cipher = Fernet(key.encode("ascii"))

    @staticmethod
    def scope(identity: BusinessIdentity, purpose: str, filters: dict[str, object]) -> str:
        return fingerprint({"version": 1, "sort": "created_at:desc,id:desc", "purpose": purpose, "tenant": str(identity.tenant_id), "project": str(identity.project_id), "key": str(identity.key_id), "policy": identity.policy, "filters": filters})

    def read(self, token: str | None, scope: str, *, now: datetime | None = None) -> CursorPosition:
        current = now or utcnow()
        if token is None:
            return CursorPosition(current, int(current.timestamp()))
        try:
            if not 1 <= len(token) <= 4096 or not token.isascii():
                raise ValueError
            encoded = token.encode("ascii")
            issued = self._cipher.extract_timestamp(encoded)
            value = json.loads(self._cipher.decrypt(encoded))
            if type(value) is not dict or set(value) != {"version", "scope", "through", "after_time", "after_id"} or type(value["version"]) is not int or value["version"] != 1 or value["scope"] != scope:
                raise ValueError
            if any(type(value[key]) is not str for key in ("through", "after_time", "after_id")):
                raise ValueError
            through, after = datetime.fromisoformat(value["through"]), datetime.fromisoformat(value["after_time"])
            identifier = UUID(value["after_id"])
            if through.tzinfo is None or after.tzinfo is None or after > through or issued > int(current.timestamp()) + 60:
                raise ValueError
        except (InvalidToken, ValueError, TypeError, KeyError, UnicodeError) as exc:
            raise DomainError("invalid_cursor", "游标无效或查询范围已变化，请重新获取首屏", 400) from exc
        if int(current.timestamp()) - issued > 900:
            raise DomainError("cursor_expired", "游标已过期，请重新获取首屏", 400)
        return CursorPosition(through, issued, after, identifier)

    def next(self, position: CursorPosition, scope: str, after_time: datetime, after_id: UUID) -> str:
        value = {"version": 1, "scope": scope, "through": position.through.isoformat(), "after_time": after_time.isoformat(), "after_id": str(after_id)}
        return self._cipher.encrypt_at_time(canonical_json(value).encode(), position.issued_at).decode("ascii")
