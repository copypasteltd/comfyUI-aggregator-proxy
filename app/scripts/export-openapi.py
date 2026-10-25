"""Export the implemented API only; the design route catalog is not an implementation."""
from __future__ import annotations

import argparse
import hashlib
import json
import secrets
from pathlib import Path

from control_api.main import create_app
from cryptography.fernet import Fernet
from generation_infrastructure.settings import Settings
from pydantic import SecretStr

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--check", action="store_true", help="Reject drift without writing OpenAPI or its digest")
arguments = parser.parse_args()
settings = Settings(_env_file=None, environment="test", database_url=SecretStr("postgresql+psycopg://schema@127.0.0.1:1/schema_only"), credential_pepper=SecretStr(secrets.token_urlsafe(48)), encryption_key=SecretStr(Fernet.generate_key().decode()), public_origin="https://127.0.0.1", storage_backend="local_development", local_storage_root=root / ".local/schema-export/objects", spool_root=root / ".local/schema-export/spool")
schema = create_app(settings).openapi()
data = (json.dumps(schema, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
if arguments.check:
    schema_file = root / "config/openapi.json"
    digest_file = root / "config/openapi.sha256"
    passed = schema_file.is_file() and schema_file.read_bytes() == data and digest_file.is_file() and digest_file.read_text(encoding="ascii") == hashlib.sha256(data).hexdigest() + "\n"
    print(json.dumps({"passed": passed, "openapi_paths": len(schema["paths"]), "generated_files_unchanged": True}))
    raise SystemExit(0 if passed else 1)
(root / "config/openapi.json").write_bytes(data)
(root / "config/openapi.sha256").write_text(hashlib.sha256(data).hexdigest() + "\n", encoding="ascii")
print(f"Exported {len(schema['paths'])} implemented OpenAPI paths")
