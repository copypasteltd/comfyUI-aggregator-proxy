"""Request-owned database failure metadata, without formatting driver errors."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import Lock
from typing import Literal

from sqlalchemy.exc import DBAPIError

Operation = Literal["gateway_auth", "audit_snapshot", "http_request"]
REASONS = {
    "55P03": "lock_unavailable",
    "57014": "query_cancelled",
    "40P01": "deadlock",
    "40001": "serialization_failure",
    **dict.fromkeys(("08000", "08001", "08003", "08004", "08006", "08007", "08P01"), "connection_failure"),
    **dict.fromkeys(("53100", "53200", "53300", "53400"), "resource_exhausted"),
    **dict.fromkeys(("57P01", "57P02", "57P03"), "database_unavailable"),
    "25006": "read_only",
    "42501": "permission_denied",
    "42P01": "undefined_relation",
}


@dataclass
class DatabaseFailureCapture:
    value: dict[str, str | bool | None] | None = None
    _lock: Lock = field(default_factory=Lock, repr=False)

    def record(self, operation: Operation, error: DBAPIError) -> None:
        with self._lock:
            if self.value is None:
                try:
                    state = getattr(error.orig, "sqlstate", None)
                except Exception:
                    state = None
                state = state if type(state) is str and state in REASONS else None
                self.value = {"operation": operation, "reason": REASONS[state] if state else "other", "sqlstate": state, "connection_invalidated": error.connection_invalidated is True}


_current: ContextVar[DatabaseFailureCapture | None] = ContextVar("database_failure_capture", default=None)


@contextmanager
def capture_database_failure() -> Iterator[DatabaseFailureCapture]:
    capture = DatabaseFailureCapture()
    token = _current.set(capture)
    try:
        yield capture
    finally:
        _current.reset(token)


def record_database_failure(operation: Operation, error: DBAPIError) -> None:
    if (capture := _current.get()) is not None:
        capture.record(operation, error)


@contextmanager
def database_operation(operation: Operation) -> Iterator[None]:
    try:
        yield
    except DBAPIError as error:
        record_database_failure(operation, error)
        raise
