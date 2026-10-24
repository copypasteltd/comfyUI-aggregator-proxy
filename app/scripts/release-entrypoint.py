"""Start an installed candidate with explicit configuration and external state."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

from release_bundle import ReleaseError, strict_json, verify_candidate, verify_payload  # noqa: E402 -- before importing the immutable local verifier


def outside(path: Path, installation: Path) -> Path:
    resolved = path.resolve()
    if resolved == installation or installation in resolved.parents:
        raise ReleaseError("runtime_path_inside_installation")
    return resolved


def load_cloud(config: Path, state: Path, payload: Path):
    from generation_infrastructure.settings import Settings

    # Configuration is explicitly selected, never inherited from the development cwd.
    for key in list(os.environ):
        if key.upper().startswith("GEN_"):
            del os.environ[key]
    settings = Settings(_env_file=config)
    installation = payload.parent
    updates: dict = {"admin_dist": payload / "admin"}
    for field in ("spool_root", "local_storage_root"):
        value = getattr(settings, field)
        updates[field] = outside(value if value.is_absolute() else state / value, installation)
    return settings.model_copy(update=updates)


def database_compatible(settings, manifest: dict) -> None:
    from sqlalchemy import create_engine, text

    engine = create_engine(settings.database_url.get_secret_value(), connect_args={"connect_timeout": 5})
    try:
        with engine.connect() as connection:
            heads = list(connection.scalars(text("SELECT version_num FROM alembic_version")))
        if heads != [manifest["database"]["head"]]:
            raise ReleaseError("database_revision_incompatible")
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=("api", "runner", "gc", "worker"))
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--validate-config", action="store_true")
    parser.add_argument("--adopt-legacy-journal", action="store_true")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--allow-network-bind", action="store_true")
    parser.add_argument("--port", type=int, default=8443)
    parser.add_argument("--tls-cert", type=Path)
    parser.add_argument("--tls-key", type=Path)
    args = parser.parse_args()
    try:
        payload = Path(__file__).resolve().parents[1]
        installation = payload.parent
        record = strict_json((installation / "install.json").read_bytes())
        if not isinstance(record, dict) or not isinstance(record.get("manifest_sha256"), str):
            raise ReleaseError("invalid_install_record")
        manifest = verify_payload(payload, record["manifest_sha256"])
        verify_candidate(payload, manifest)
        if manifest["contracts"]["version"] == "0.5.0":
            os.environ["PATH"] = str(payload / "media/bin") + os.pathsep + os.environ.get("PATH", "")
        if args.once and args.role not in ("runner", "gc"):
            raise ReleaseError("once_only_supported_for_runner_gc")
        if args.adopt_legacy_journal and args.role != "worker":
            raise ReleaseError("journal_adoption_only_supported_for_worker")
        if Path(sys.prefix).resolve() != (installation / "venv").resolve():
            raise ReleaseError("installed_interpreter_required")
        config = outside(args.config, installation).resolve(strict=True)
        state = outside(args.state_dir, installation)
        if not state.is_dir():
            raise ReleaseError("state_directory_required")
        os.chdir(state)
        if args.role == "worker":
            from windows_worker.configuration import load_settings, validate_startup
            from windows_worker.runtime import Worker

            settings = load_settings(config)
            if settings.state_root != state:
                raise ReleaseError("worker_state_directory_mismatch")
            outside(settings.credential_file, installation)
            if args.validate_config:
                validate_startup(settings)
                print(json.dumps({"role": "worker", "configuration_valid": True, "processes_started": False, "server_identity_verified": False}))
                return
            asyncio.run(Worker(settings, adopt_legacy_journal=args.adopt_legacy_journal).run())
            return
        settings = load_cloud(config, state, payload)
        if args.validate_config:
            print(json.dumps({"role": args.role, "configuration_valid": True, "processes_started": False}))
            return
        database_compatible(settings, manifest)
        if args.role == "api":
            import uvicorn
            from control_api.main import create_app

            if args.host not in ("127.0.0.1", "::1", "localhost") and not args.allow_network_bind:
                raise ReleaseError("network_bind_requires_explicit_flag")
            if not 1 <= args.port <= 65535 or args.tls_cert is None or args.tls_key is None:
                raise ReleaseError("explicit_tls_required")
            cert = outside(args.tls_cert, installation).resolve(strict=True)
            key = outside(args.tls_key, installation).resolve(strict=True)
            uvicorn.run(create_app(settings), host=args.host, port=args.port, ssl_certfile=str(cert), ssl_keyfile=str(key), access_log=False, proxy_headers=False, log_level="warning")
        else:
            if args.role == "runner":
                from control_runner.main import main as run
            else:
                from control_runner.gc import main as run
            sys.argv = [sys.argv[0], *(["--once"] if args.once else [])]
            run(settings)
    except KeyboardInterrupt:
        print(json.dumps({"event": "stopped", "durable_state_retained": True}))
    except Exception as exc:
        print(json.dumps({"error": str(exc) if isinstance(exc, ReleaseError) else "release_startup_failed"}), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
