"""Own negative database and recovery fixtures on the isolated project-native cluster."""
import json
import sys
from pathlib import Path

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / '.local/native-postgres'
ownership = json.loads((PRIVATE / 'ownership.json').read_text())
assert ownership['port'] == 55438 and Path(ownership['data']).resolve() == (PRIVATE / 'data').resolve()
receipt = PRIVATE / 'sentinel-137-owned.json'
with psycopg.connect(host='127.0.0.1', port=55438, user='postgres', password=(PRIVATE / 'bootstrap-password.txt').read_text().strip(), dbname='postgres', autocommit=True, sslmode='disable', gssencmode='disable') as connection:
    assert connection.execute("SELECT current_setting('port')").fetchone()[0] == '55438'
    existing = connection.execute("SELECT 1 FROM pg_database WHERE datname='render_control'").fetchone()
    if existing:
        assert receipt.exists() and json.loads(receipt.read_text())['purpose'] == 'empty read-only negative fixture'
    else:
        connection.execute(sql.SQL('CREATE DATABASE {} OWNER {}').format(sql.Identifier('render_control'), sql.Identifier('studio_test')))
        connection.execute("ALTER DATABASE render_control SET default_transaction_read_only=on")
        receipt.write_text(json.dumps({'database': 'render_control', 'port': 55438, 'purpose': 'empty read-only negative fixture', 'real_data': False}) + '\n')
    connection.execute('ALTER ROLE studio_test CREATEDB')
print(json.dumps({'passed': True, 'scope': 'owned 55438 cluster only', 'empty_readonly_guard_fixture': True, 'disposable_recovery_database_permission': True, 'production_modified': False}))
