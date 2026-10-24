from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from generation_contracts.validation import StrictDTO

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Count = Annotated[int, Field(ge=0, le=10000)]


class TableSeal(StrictDTO):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,62}$")
    rows: int = Field(ge=0, le=1000000)
    hmac_sha256: Digest


class RecoveryMedia(StrictDTO):
    images: Count
    videos: Count
    audio: Count
    other: Count


class RecoveryObjects(StrictDTO):
    records: Count
    bytes: int = Field(ge=0, le=2**63 - 1)
    media: RecoveryMedia

    @model_validator(mode="after")
    def counts_match(self) -> RecoveryObjects:
        if sum(self.media.model_dump().values()) != self.records or self.bytes < self.records or (self.records == 0 and self.bytes != 0):
            raise ValueError("Object counters must describe the same inventory")
        return self


class RecoverySecrets(StrictDTO):
    master_canary: str = Field(min_length=80, max_length=512)
    pepper_canary: Digest
    pepper_version: int = Field(ge=1, le=2147483647)
    encrypted_values: int = Field(ge=0, le=1000000)


class RecoveryCheckpoint(StrictDTO):
    format_version: Literal[1]
    contract_version: Literal["0.5.0"]
    created_at_ms: int = Field(ge=1, le=2**63 - 1)
    nonce: str = Field(pattern=r"^[0-9a-f]{32}$")
    database_revision: str = Field(pattern=r"^[0-9a-f]{12}$")
    tables: list[TableSeal] = Field(min_length=1, max_length=256)
    objects: RecoveryObjects
    secrets: RecoverySecrets
    signature: Digest

    @field_validator("format_version", mode="before")
    @classmethod
    def integer_format(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("Format version must be an integer")
        return value

    @model_validator(mode="after")
    def sorted_unique_tables(self) -> RecoveryCheckpoint:
        names = [table.name for table in self.tables]
        if names != sorted(set(names)):
            raise ValueError("Table names must be unique and sorted")
        if isinstance(self.format_version, bool):
            raise ValueError("Format version must be an integer")
        return self
