"""Render, verify and explicitly manage fixed-role installed Windows services."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

from native_services import (  # noqa: E402 -- immutable local modules, never cwd
    ReleaseError,
    ServiceControl,
    administrator,
    load_deployment,
    operate,
    operation_lock,
    read_document,
    render,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("render", "validate", "status", "install", "start", "stop", "uninstall"))
    parser.add_argument("--spec", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--deployment", type=Path)
    parser.add_argument("--service")
    args = parser.parse_args()
    try:
        if sys.platform != "win32":
            raise ReleaseError("native_windows_required")
        if args.action in ("install", "start", "stop", "uninstall") and not administrator():
            raise ReleaseError("service_administrator_required_no_elevation")
        if args.action == "render":
            if not args.spec or not args.output or args.deployment or args.service:
                raise ReleaseError("service_render_requires_spec_and_new_output")
            result = render(read_document(args.spec), args.output.absolute())
        else:
            if not args.deployment or args.spec or args.output or args.action == "validate" and args.service:
                raise ReleaseError("service_operation_requires_deployment")
            output = args.deployment.absolute()
            if args.action == "validate":
                spec = load_deployment(output)
                result = {"valid": True, "services": len(spec["services"]), "services_registered": False, "configuration_contents_verified": False, "application_health_verified": False, "permissions_applied": False}
            else:
                with operation_lock(output):
                    spec = load_deployment(output)
                    result = operate(args.action, output, spec, args.service, ServiceControl())
        print(json.dumps(result))
    except Exception as exc:
        print(json.dumps({"error": str(exc) if isinstance(exc, ReleaseError) else "native_service_operation_failed"}), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
