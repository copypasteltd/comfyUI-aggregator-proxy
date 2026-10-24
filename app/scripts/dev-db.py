"""Own native PostgreSQL instance only; never discovers or touches other instances."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import secrets
import socket
import subprocess
from pathlib import Path

import psycopg
from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / ".local/toolchains/postgres-18.6/pgsql/bin"
DATA = ROOT / ".local/postgres/data"
PORT = 55438
FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def native_root() -> Path:
    """A DOS path alias avoids PostgreSQL's ANSI path conversion on Windows.

    No files move: the alias points at this app directory. No virtual machine is used.
    """
    if os.name != "nt" or str(ROOT).isascii():
        return ROOT
    owner_file = ROOT / ".local/postgres-drive.json"
    for letter in "RSTUVWXYZ":
        drive = letter + ":"
        buffer = ctypes.create_unicode_buffer(32768)
        exists = ctypes.windll.kernel32.QueryDosDeviceW(drive, buffer, len(buffer))
        target = "\\??\\" + str(ROOT)
        if exists and buffer.value.casefold() == target.casefold():
            return Path(drive + "\\")
        if exists:
            continue
        subprocess.run([str(Path(os.environ["SystemRoot"]) / "System32/subst.exe"), drive, str(ROOT)], check=True, creationflags=FLAGS)  # noqa: S603 -- fixed OS executable and validated project path
        owner_file.write_text(json.dumps({"drive": drive, "target": str(ROOT)}), encoding="utf-8")
        return Path(drive + "\\")
    raise SystemExit("No free drive alias for the native PostgreSQL Unicode path workaround")


def command(*args: str) -> None:
    alias = native_root()
    def translated(value: str) -> str:
        return value.replace(str(ROOT), str(alias).rstrip("\\"))
    output_path = ROOT / ".local/postgres-command.log"
    # A detached PostgreSQL server inherits pipe handles on Windows. A file keeps
    # pg_ctl's completion independent of the lifetime of the running server.
    with output_path.open("wb") as output:
        result = subprocess.run([translated(str(BIN / args[0])), *(translated(a) for a in args[1:])], stdout=output, stderr=subprocess.STDOUT, creationflags=FLAGS, cwd=alias)  # noqa: S603 -- fixed project-native executable and controlled arguments
    print(output_path.read_text(encoding="utf-8", errors="replace"), end="", flush=True)
    result.check_returncode()


def initialize() -> None:
    if DATA.exists():
        raise SystemExit("Existing project data directory: initialization refused")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", PORT))
    password = secrets.token_urlsafe(32)
    password_file = ROOT / ".local/postgres-password.secret"
    password_file.write_text(password, encoding="utf-8")
    command("initdb.exe", "-D", str(DATA), "-U", "gen_dev", "--encoding=UTF8", "--locale=C", "--auth=scram-sha-256", "--pwfile=" + str(password_file))
    password_file.unlink()
    env = ROOT / ".env"
    if env.exists():
        raise SystemExit("Existing .env retained; connect configuration manually before starting")
    env.write_text("\n".join([
        "GEN_ENVIRONMENT=development",
        f"GEN_DATABASE_URL=postgresql+psycopg://gen_dev:{password}@127.0.0.1:{PORT}/render_control",
        "GEN_CREDENTIAL_PEPPER=" + secrets.token_urlsafe(48),
        "GEN_ENCRYPTION_KEY=" + Fernet.generate_key().decode(),
        "GEN_PUBLIC_ORIGIN=https://127.0.0.1:8443",
        "GEN_STORAGE_BACKEND=local_development",
        "",
    ]), encoding="utf-8")
    (ROOT / ".local/postgres-owner.json").write_text(json.dumps({"data": str(DATA), "port": PORT, "binary": str(BIN / "postgres.exe")}), encoding="utf-8")
    start()
    with psycopg.connect(host="127.0.0.1", port=PORT, user="gen_dev", password=password, dbname="postgres", autocommit=True) as conn:
        conn.execute("CREATE DATABASE render_control")
        conn.execute("CREATE DATABASE render_control_test")
    print("Created isolated native development and test databases; credentials are in ignored .env")


def owner_check() -> None:
    owner = json.loads((ROOT / ".local/postgres-owner.json").read_text(encoding="utf-8"))
    if Path(owner["data"]).resolve() != DATA.resolve() or owner["port"] != PORT:
        raise SystemExit("Native database owner record mismatch")
    if ROOT not in DATA.resolve().parents:
        raise SystemExit("Refusing a data directory outside app")


def release_alias() -> None:
    owner_file = ROOT / ".local/postgres-drive.json"
    if os.name != "nt" or not owner_file.exists():
        return
    owner = json.loads(owner_file.read_text(encoding="utf-8"))
    if owner.get("target") != str(ROOT) or owner.get("drive") not in [letter + ":" for letter in "RSTUVWXYZ"]:
        raise SystemExit("Native path alias owner mismatch; retained")
    buffer = ctypes.create_unicode_buffer(32768)
    exists = ctypes.windll.kernel32.QueryDosDeviceW(owner["drive"], buffer, len(buffer))
    if exists and buffer.value.casefold() == ("\\??\\" + str(ROOT)).casefold():
        subprocess.run([str(Path(os.environ["SystemRoot"]) / "System32/subst.exe"), owner["drive"], "/d"], check=True, creationflags=FLAGS)  # noqa: S603 -- remove only this project's verified DOS path alias
        owner_file.unlink()


def start() -> None:
    owner_check()
    command("pg_ctl.exe", "-D", str(DATA), "-l", str(ROOT / ".local/postgres/server.log"), "-o", f"-h 127.0.0.1 -p {PORT} -c max_connections=80", "-w", "start")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["init", "start", "stop", "status"])
    action = parser.parse_args().action
    if action == "init":
        initialize()
    elif action == "start":
        start()
    else:
        owner_check()
        command("pg_ctl.exe", "-D", str(DATA), *(["-m", "fast", "-w", "stop"] if action == "stop" else ["status"]))
        if action == "stop":
            release_alias()


if __name__ == "__main__":
    main()
