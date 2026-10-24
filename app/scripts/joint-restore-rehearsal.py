"""Restore a project-development snapshot, original-version bytes and runtime secrets in isolation."""
from __future__ import annotations

import argparse
import base64
import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import psycopg
from generation_infrastructure.restore_verification import (
    create_checkpoint,
    load_checkpoint,
    load_recovery_key,
    verify_checkpoint,
    write_exclusive,
)
from generation_infrastructure.settings import Settings
from generation_infrastructure.storage import LocalDevelopmentStore
from psycopg import sql
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]


def registered_process_running() -> bool:
    record = ROOT / '.local/dev-processes.json'
    if not record.exists():
        return False
    kernel = ctypes.windll.kernel32
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    for entry in json.loads(record.read_text(encoding='utf-8-sig')):
        handle = kernel.OpenProcess(0x1000, False, int(entry['Pid']))
        if handle:
            try:
                status = ctypes.c_ulong()
                if not kernel.GetExitCodeProcess(handle, ctypes.byref(status)) or status.value == 259:
                    return True
            finally:
                kernel.CloseHandle(handle)
        elif kernel.GetLastError() == 5:
            return True
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--recovery-key-file', type=Path, required=True)
    args = parser.parse_args()
    for name in list(os.environ):
        if name.upper().startswith('GEN_'):
            del os.environ[name]
    settings = Settings(_env_file=ROOT / '.env')
    settings = settings.model_copy(update={'local_storage_root': (ROOT / settings.local_storage_root).resolve()})
    url = make_url(settings.database_url.get_secret_value())
    if os.name != 'nt' or settings.environment != 'development' or settings.storage_backend != 'local_development' or (url.host, url.port, url.database) != ('127.0.0.1', 55438, 'render_control') or registered_process_running():
        raise SystemExit('joint_rehearsal_requires_stopped_project_development_services')
    key_file = args.recovery_key_file.resolve(strict=True)
    if ROOT not in key_file.parents:
        raise SystemExit('joint_rehearsal_key_must_be_in_project')
    recovery_key = load_recovery_key(key_file)
    started = time.monotonic()
    run_id = uuid4().hex[:12]
    work = ROOT / '.local/joint-restore' / run_id
    work.mkdir(parents=True, exist_ok=False)
    backup_root = ROOT / '.local/backups'
    backup_root.mkdir(exist_ok=True)
    stamp = datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')
    archive = backup_root / (stamp + '-joint.dump')
    checkpoint_file = archive.with_suffix('.checkpoint.json')
    database = 'render_joint_restore_' + run_id
    options = {'host': url.host, 'port': url.port, 'user': url.username, 'password': url.password, 'dbname': url.database, 'connect_timeout': 5, **url.query}
    tools = ROOT / '.local/toolchains/postgres-18.6/pgsql/bin'
    environment = {**os.environ, 'PGPASSWORD': url.password or ''}
    dsn = {k: v for k, v in options.items() if k != 'password' and v is not None}
    report = {'at': datetime.now(UTC).isoformat(), 'passed': False, 'virtualization_used': False, 'production_recovery_accepted': False, 'worker_journals_restored': False, 'gateway_ledger_restored': False, 'temporary_database_removed': False, 'private_copies_removed': False}
    created = False
    try:
        with psycopg.connect(**{**options, 'dbname': 'postgres'}, autocommit=True) as admin:
            if not admin.execute('SELECT pg_try_advisory_lock(634720182)').fetchone()[0]:
                raise RuntimeError('joint_rehearsal_lock_busy')
            try:
                if admin.execute('SELECT count(*) FROM pg_stat_activity WHERE datname=%s', (url.database,)).fetchone()[0] != 0:
                    raise RuntimeError('joint_rehearsal_source_connections_present')
                source_store = LocalDevelopmentStore(settings.local_storage_root)
                with psycopg.connect(**options) as source:
                    source.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
                    snapshot = source.execute('SELECT pg_export_snapshot()').fetchone()[0]
                    checkpoint = create_checkpoint(source, settings, source_store, recovery_key)
                    subprocess.run([str(tools / 'pg_dump.exe'), '--format=custom', '--snapshot=' + snapshot, '--file=' + str(archive), '--dbname=' + psycopg.conninfo.make_conninfo(**dsn), '--no-password'], env=environment, check=True, timeout=300, capture_output=True)  # noqa: S603 -- fixed native PostgreSQL tool and exact source guard
                    subprocess.run([str(tools / 'pg_restore.exe'), '--list', str(archive)], check=True, timeout=30, capture_output=True)  # noqa: S603 -- verify owned custom archive before creating any restore database
                    write_exclusive(checkpoint_file, (json.dumps(checkpoint.model_dump(), sort_keys=True, indent=2) + '\n').encode())
                    restored_store = LocalDevelopmentStore(work / 'objects')
                    copied = 0
                    for key, version, expected_size, expected_hash in source.execute('SELECT object_key,version_id,bytes,sha256 FROM object_records WHERE deleted_at IS NULL ORDER BY id'):
                        destination = restored_store._path(key, version)
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        digest, size = hashlib.sha256(), 0
                        with source_store._path(key, version).open('rb') as original, destination.open('xb') as target:
                            while block := original.read(1024 * 1024):
                                size += len(block)
                                if size > expected_size or time.monotonic() - started > 600:
                                    raise RuntimeError('joint_rehearsal_copy_limit')
                                digest.update(block)
                                target.write(block)
                            target.flush()
                            os.fsync(target.fileno())
                        if size != expected_size or digest.hexdigest() != expected_hash:
                            raise RuntimeError('joint_rehearsal_copy_mismatch')
                        copied += 1
                admin.execute(sql.SQL('CREATE DATABASE {} TEMPLATE template0').format(sql.Identifier(database)))
                created = True
                subprocess.run([str(tools / 'pg_restore.exe'), '--no-owner', '--no-privileges', '--single-transaction', '--exit-on-error', '--dbname=' + psycopg.conninfo.make_conninfo(**{**dsn, 'dbname': database}), '--no-password', str(archive)], env=environment, check=True, timeout=300, capture_output=True)  # noqa: S603 -- restore into only the freshly created tracked database
                config = work / 'restored.env'
                values = {'GEN_ENVIRONMENT': 'test', 'GEN_DATABASE_URL': url.set(database=database).render_as_string(hide_password=False), 'GEN_CREDENTIAL_PEPPER': settings.credential_pepper.get_secret_value(), 'GEN_CREDENTIAL_PEPPER_VERSION': str(settings.credential_pepper_version), 'GEN_ENCRYPTION_KEY': settings.encryption_key.get_secret_value(), 'GEN_PUBLIC_ORIGIN': settings.public_origin, 'GEN_STORAGE_BACKEND': 'local_development', 'GEN_LOCAL_STORAGE_ROOT': (work / 'objects').as_posix(), 'GEN_SPOOL_ROOT': (work / 'spool').as_posix()}
                write_exclusive(config, ''.join(name + '=' + json.dumps(value, ensure_ascii=False) + '\n' for name, value in values.items()).encode('utf-8'))
                copied_key = work / 'restored-recovery.key'
                write_exclusive(copied_key, base64.urlsafe_b64encode(recovery_key) + b'\n')
                restored_settings = Settings(_env_file=config)
                reloaded_key = load_recovery_key(copied_key)
                authenticated = load_checkpoint(checkpoint_file, reloaded_key)
                with psycopg.connect(**{**options, 'dbname': database}) as restored:
                    verification = verify_checkpoint(restored, restored_settings, restored_store, reloaded_key, authenticated)
                with archive.open('rb') as archive_source:
                    archive_sha256 = hashlib.file_digest(archive_source, 'sha256').hexdigest()
                report.update({'passed': True, 'schema_revision': checkpoint.database_revision, 'consistent_exported_snapshot': True, 'archive_list_verified': True, 'backup_sha256': archive_sha256, 'checkpoint_sha256': hashlib.sha256(checkpoint_file.read_bytes()).hexdigest(), 'original_versions_copied': copied, 'runtime_secrets_reloaded_from_file': True, 'independent_recovery_key_reloaded': True, 'verification': verification, 's3_restore_validated': False})
            finally:
                if created:
                    admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(database)))
                    report['temporary_database_removed'] = True
                admin.execute('SELECT pg_advisory_unlock(634720182)')
    except Exception:
        report['failure'] = 'joint_recovery_failed'
        raise SystemExit('joint_recovery_failed') from None
    finally:
        if work.resolve().parent != (ROOT / '.local/joint-restore').resolve() or work.is_symlink():
            raise RuntimeError('joint_recovery_cleanup_path_invalid')
        try:
            shutil.rmtree(work)
            report['private_copies_removed'] = True
        finally:
            report['passed'] = report['passed'] and report['temporary_database_removed'] and report['private_copies_removed']
            report['duration_seconds'] = round(time.monotonic() - started, 2)
            evidence = ROOT.parent / 'docs/evidence' / datetime.now().date().isoformat()
            evidence.mkdir(parents=True, exist_ok=True)
            (evidence / 'joint-recovery-development.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'passed': True, 'tables': verification['tables'], 'rows': verification['rows'], 'objects': verification['objects'], 'temporary_database_removed': report['temporary_database_removed'], 'private_copies_removed': report['private_copies_removed']}))


if __name__ == '__main__':
    main()
