"""Create/authenticate a private recovery checkpoint or verify a restored environment."""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("keygen", "snapshot", "verify"))
    parser.add_argument("--recovery-key-file", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--maximum-rows", type=int, default=1000000)
    parser.add_argument("--maximum-row-bytes", type=int, default=8 * 1024**2)
    parser.add_argument("--maximum-objects", type=int, default=10000)
    parser.add_argument("--maximum-object-bytes", type=int, default=64 * 1024**3)
    parser.add_argument("--maximum-seconds", type=int, default=300)
    arguments = parser.parse_args()
    store = None
    try:
        root = Path(__file__).resolve().parents[1]
        if (root / "release-manifest.json").exists():
            sys.path.insert(0, str(root / "scripts"))
            from release_bundle import strict_json, verify_candidate, verify_payload
            record = strict_json((root.parent / "install.json").read_bytes())
            if not isinstance(record, dict) or Path(sys.prefix).resolve() != (root.parent / "venv").resolve():
                raise ValueError
            manifest = verify_payload(root, record["manifest_sha256"])
            verify_candidate(root, manifest)
            for path in [arguments.recovery_key_file, arguments.config, arguments.checkpoint]:
                if path is not None and (path.resolve() == root.parent or root.parent in path.resolve().parents):
                    raise ValueError
        from generation_infrastructure.restore_verification import (
            RecoveryCheckError,
            RecoveryLimits,
            create_checkpoint,
            load_checkpoint,
            load_recovery_key,
            verify_checkpoint,
            write_exclusive,
        )

        if arguments.operation == "keygen":
            write_exclusive(arguments.recovery_key_file, base64.urlsafe_b64encode(os.urandom(32)) + b"\n")
            print(json.dumps({"recovery_key_created": True, "secret_displayed": False}))
            return
        if arguments.config is None or arguments.checkpoint is None:
            raise RecoveryCheckError("recovery_explicit_paths_required")
        key = load_recovery_key(arguments.recovery_key_file)
        limits = RecoveryLimits(rows=arguments.maximum_rows, row_bytes=arguments.maximum_row_bytes, objects=arguments.maximum_objects, object_bytes=arguments.maximum_object_bytes, seconds=arguments.maximum_seconds)
        expected = load_checkpoint(arguments.checkpoint, key) if arguments.operation == "verify" else None
        for name in list(os.environ):
            if name.upper().startswith("GEN_"):
                del os.environ[name]
        from generation_infrastructure.settings import Settings
        from generation_infrastructure.storage import make_store
        from sqlalchemy.engine import make_url

        config = arguments.config.resolve(strict=True)
        if config.stat().st_size > 65536:
            raise RecoveryCheckError("recovery_config_invalid")
        settings = Settings(_env_file=config)
        updates = {field: (getattr(settings, field) if getattr(settings, field).is_absolute() else config.parent / getattr(settings, field)).resolve() for field in ("local_storage_root", "spool_root")}
        settings = settings.model_copy(update=updates)
        store = make_store(settings)
        if check := getattr(store, "check_private_versioned_bucket", None):
            check()
        import psycopg

        url = make_url(settings.database_url.get_secret_value())
        with psycopg.connect(host=url.host, port=url.port, user=url.username, password=url.password, dbname=url.database, connect_timeout=5, **url.query) as connection:
            if expected is not None:
                result = verify_checkpoint(connection, settings, store, key, expected, limits)
            else:
                checkpoint = create_checkpoint(connection, settings, store, key, limits)
                write_exclusive(arguments.checkpoint, (json.dumps(checkpoint.model_dump(), sort_keys=True, indent=2) + "\n").encode())
                result = {"checkpoint_written": True, "tables": len(checkpoint.tables), "rows": sum(table.rows for table in checkpoint.tables), "objects": checkpoint.objects.records, "object_bytes": checkpoint.objects.bytes, "database_read_only": True, "production_recovery_accepted": False}
        print(json.dumps(result))
    except Exception as error:
        code = str(error) if type(error).__name__ == "RecoveryCheckError" and re.fullmatch(r"recovery_[a-z0-9_]+", str(error)) else "recovery_operation_failed"
        print(json.dumps({"error": code}), file=sys.stderr)
        raise SystemExit(1) from None
    finally:
        if store is not None and (client := getattr(store, "client", None)) is not None:
            try:
                client.close()
            except Exception:
                print(json.dumps({"event": "recovery_client_close_failed"}), file=sys.stderr)


if __name__ == "__main__":
    main()
