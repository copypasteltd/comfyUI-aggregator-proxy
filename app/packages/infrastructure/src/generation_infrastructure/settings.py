from __future__ import annotations

from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from cryptography.fernet import Fernet
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
        if self.environment == "production" and origin.scheme != "https":
            raise ValueError("Production requires HTTPS")
        if self.storage_backend == "local_development":
            if self.environment == "production" or origin.hostname not in ("localhost", "127.0.0.1", "::1"):
                raise ValueError("Local storage adapter is restricted to loopback development/test")
        elif not all((self.s3_bucket, self.s3_access_key, self.s3_secret_key)):
            raise ValueError("Private S3 credentials and bucket are required")
        return self
