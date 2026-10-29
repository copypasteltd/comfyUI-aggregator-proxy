"""Own native PostgreSQL test cluster; all writable data remains under 无限画布/."""
from pathlib import Path
import json
import os
import secrets
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / ".local/native-postgres"
DATA = PRIVATE / "data"
ALIAS = ROOT.parent / ".comfy-studio-native"
if ALIAS.resolve() != (ROOT / ".local").resolve():
    raise RuntimeError("native_path_alias_is_not_owned")
TOOLS = ALIAS / "pg-tools/bin"
NATIVE_DATA = ALIAS / "native-postgres/data"
APP = ROOT / "aggregator-service/app"
PORT = 55438

def run(arguments, output):
    subprocess.run([str(item) for item in arguments], check=True, stdout=output, stderr=output, creationflags=subprocess.CREATE_NO_WINDOW)

if sys.argv[1:] == ["start"]:
    if DATA.exists():
        raise SystemExit("Refusing to overwrite an existing test cluster")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", PORT))
    PRIVATE.mkdir(parents=True, exist_ok=True)
    password = secrets.token_urlsafe(40)
    password_file = PRIVATE / "bootstrap-password.txt"
    password_file.write_text(password, encoding="ascii")
    with (PRIVATE / f"bootstrap-{time.time_ns()}.log").open("w", encoding="utf-8") as output:
        run([TOOLS / "initdb.exe", "-D", NATIVE_DATA, "--username=postgres", "--encoding=UTF8", "--locale=C", "--auth=scram-sha-256", "--pwfile", ALIAS / "native-postgres/bootstrap-password.txt"], output)
        run([TOOLS / "pg_ctl.exe", "-D", NATIVE_DATA, "-l", ALIAS / "native-postgres/server.log", "-o", f"-h 127.0.0.1 -p {PORT}", "-w", "start"], output)
    import psycopg
    from psycopg import sql
    from urllib.parse import quote
    user_password = secrets.token_urlsafe(40)
    with psycopg.connect(host="127.0.0.1", port=PORT, user="postgres", password=password, dbname="postgres", autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(sql.Identifier("studio_test"), sql.Literal(user_password)))
        connection.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier("render_control_test"), sql.Identifier("studio_test")))
    environment = APP / ".env"
    text = environment.read_text(encoding="utf-8-sig")
    lines = [line for line in text.splitlines() if not line.startswith("GEN_DATABASE_URL=")]
    lines.append(f"GEN_DATABASE_URL=postgresql+psycopg://studio_test:{quote(user_password, safe='')}@127.0.0.1:{PORT}/render_control_test")
    environment.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (PRIVATE / "ownership.json").write_text(json.dumps({"port": PORT, "data": str(DATA), "purpose": "isolated Studio regression", "virtualization_used": False}), encoding="utf-8")
    print(json.dumps({"started": True, "port": PORT, "all_writable_paths_in_project": True, "virtualization_used": False}))
elif sys.argv[1:] == ["stop"]:
    ownership = json.loads((PRIVATE / "ownership.json").read_text(encoding="utf-8"))
    if Path(ownership["data"]).resolve() != DATA.resolve() or not DATA.resolve().is_relative_to(ROOT.resolve()):
        raise SystemExit("Refusing to stop a cluster without the exact ownership receipt")
    with (PRIVATE / "shutdown.log").open("w", encoding="utf-8") as output:
        run([TOOLS / "pg_ctl.exe", "-D", NATIVE_DATA, "-m", "fast", "-w", "stop"], output)
    print(json.dumps({"stopped_owned_test_cluster": True}))
else:
    raise SystemExit("Choose start or stop")
