from __future__ import annotations

import re
from ipaddress import ip_network
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from cryptography.fernet import Fernet
from generation_contracts.monitoring import MonitoringThresholds
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GEN_", env_file=".env", extra="forbid")
    environment: Literal["development", "test", "production"] = "production"
    database_url: SecretStr
    credential_pepper: SecretStr = Field(min_length=43)
    credential_pepper_version: int = Field(default=1, ge=1)
    encryption_key: SecretStr
    public_origin: str
    allow_http: bool = False
    allow_any_origin: bool = False
    session_seconds: int = Field(default=28800, ge=60, le=86400)
    session_idle_seconds: int = Field(default=1800, ge=60, le=86400)
    storage_backend: Literal["s3", "local_development"] = "s3"
    local_storage_root: Path = Path(".local/objects")
    spool_root: Path = Path(".local/spool")
    s3_endpoint: str | None = None
    s3_bucket: str | None = None
    s3_region: str = "us-east-1"
    s3_access_key: SecretStr | None = None
    s3_secret_key: SecretStr | None = None
    admin_dist: Path = Path("apps/admin_web/dist")
    access_log_retention_days: int = Field(default=30, ge=1, le=365)
    audit_retention_days: int | None = Field(default=None, ge=1, le=3650)
    monitoring_token_sha256: list[SecretStr] = Field(default_factory=list, max_length=2)
    monitoring_allowed_networks: list[str] = Field(default_factory=lambda: ["127.0.0.1/32", "::1/128"], min_length=1, max_length=32)
    monitoring_thresholds: MonitoringThresholds = Field(default_factory=MonitoringThresholds)

    @field_validator("monitoring_token_sha256")
    @classmethod
    def monitoring_hashes(cls, values: list[SecretStr]) -> list[SecretStr]:
        hashes = [value.get_secret_value() for value in values]
        if any(not re.fullmatch(r"[0-9a-f]{64}", value) for value in hashes) or len(set(hashes)) != len(hashes):
            raise ValueError("Monitoring requires distinct SHA-256 digests")
        return values

    @field_validator("monitoring_allowed_networks")
    @classmethod
    def monitoring_networks(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(str(ip_network(value, strict=False)) for value in values))

    @field_validator("audit_retention_days", mode="before")
    @classmethod
    def explicit_retention_days(cls, value: Any) -> Any:
        if isinstance(value, (bool, float)):
            raise ValueError("Audit retention requires whole days or null")
        return value

    @model_validator(mode="after")
    def validate_environment(self) -> Settings:
        if not self.database_url.get_secret_value().startswith("postgresql+psycopg://"):
            raise ValueError("Cloud storage requires native PostgreSQL/psycopg")
        Fernet(self.encryption_key.get_secret_value().encode())
        origin = urlparse(self.public_origin)
        if origin.path not in ("", "/") or origin.query or origin.fragment or origin.username:
            raise ValueError("public_origin must be a plain origin")
        if self.environment == "production" and origin.scheme != "https" and not (self.allow_http and origin.scheme == "http"):
            raise ValueError("Production requires HTTPS")
        if self.storage_backend == "local_development":
            if self.environment == "production" or origin.hostname not in ("localhost", "127.0.0.1", "::1"):
                raise ValueError("Local storage adapter is restricted to loopback development/test")
        elif not all((self.s3_bucket, self.s3_access_key, self.s3_secret_key)):
            raise ValueError("Private S3 credentials and bucket are required")
        return self

    def accepts_admin_origin(self, origin: str | None) -> bool:
        return self.allow_any_origin or origin == self.public_origin

    @property
    def secure_cookies(self) -> bool:
        return urlparse(self.public_origin).scheme == "https"

    @property
    def cookie_same_site(self) -> Literal["lax", "none"]:
        return "none" if self.allow_any_origin and self.secure_cookies else "lax"
