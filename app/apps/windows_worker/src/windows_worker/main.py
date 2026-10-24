from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from generation_domain.errors import DomainError

from windows_worker.configuration import load_settings, validate_startup


def main() -> None:
    parser = argparse.ArgumentParser(description="Native Windows ComfyUI Worker")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--validate-config", action="store_true")
    parser.add_argument("--adopt-legacy-journal", action="store_true", help="After verifying the old node, back up and bind an unbound legacy journal")
    arguments = parser.parse_args()
    try:
        settings = load_settings(arguments.config)
        if arguments.validate_config:
            validate_startup(settings)
            print(json.dumps({"configuration_valid": True, "processes_started": False, "server_identity_verified": False}))
            return
        import asyncio

        from windows_worker.runtime import Worker

        asyncio.run(Worker(settings, adopt_legacy_journal=arguments.adopt_legacy_journal).run())
    except KeyboardInterrupt:
        print(json.dumps({"event": "stopped", "durable_state_retained": True}))
    except Exception as exc:
        print(json.dumps({"error": exc.code if isinstance(exc, DomainError) else "worker_startup_failed"}), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
