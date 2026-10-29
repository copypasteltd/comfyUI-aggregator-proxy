"""Use the installed remote-3080 client, keeping this task's records in its root.

The user's current workspace requirement overrides the skill's old default
controller output directory. Device configuration and credentials stay intact.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path("C:/Users/carzy/.codex/skills/remote-3080/scripts/windows_mcp.py")
spec = importlib.util.spec_from_file_location("studio_remote3080_client", SOURCE)
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)
original = client.read_json


def local_output_config(path):
    value = original(path)
    if Path(path).resolve() == SOURCE.parent.parent / "device.json":
        value["controller_root"] = str(ROOT / ".local/remote3080")
    return value


client.read_json = local_output_config
if __name__ == "__main__":
    raise SystemExit(client.main())
