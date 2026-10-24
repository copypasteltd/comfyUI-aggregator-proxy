"""Export the implemented API only; the design route catalog is not an implementation."""
from __future__ import annotations

import hashlib
import json
import secrets
from pathlib import Path

from control_api.main import create_app
from cryptography.fernet import Fernet
from generation_infrastructure.settings import Settings
from pydantic import SecretStr

root = Path(__file__).resolve().parents[1]
settings = Settings(_env_file=None, environment="test", database_url=SecretStr("postgresql+psycopg://schema@127.0.0.1:1/schema_only"), credential_pepper=SecretStr(secrets.token_urlsafe(48)), encryption_key=SecretStr(Fernet.generate_key().decode()), public_origin="https://127.0.0.1", storage_backend="local_development", local_storage_root=root / ".local/schema-export/objects", spool_root=root / ".local/schema-export/spool")
schema = create_app(settings).openapi()
data = (json.dumps(schema, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
(root / "config/openapi.json").write_bytes(data)
(root / "config/openapi.sha256").write_text(hashlib.sha256(data).hexdigest() + "\n", encoding="ascii")
print(f"Exported {len(schema['paths'])} implemented OpenAPI paths")
