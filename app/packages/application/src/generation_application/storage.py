from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import BinaryIO, Protocol


@dataclass(frozen=True)
class StoredObject:
    key: str
    version: str
    bytes: int
    sha256: str
    media_type: str


class ObjectStore(Protocol):
    def put(self, key: str, source: BinaryIO, media_type: str, maximum: int) -> StoredObject: ...
    def read(self, key: str, version: str, start: int, end: int) -> Iterator[bytes]: ...
    def delete(self, key: str, version: str) -> None: ...
    def signed_url(self, key: str, version: str, seconds: int) -> str: ...
