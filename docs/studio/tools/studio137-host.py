"""Fixed native 137 preparation/promotion with quiescence and verified backup."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

TASK = Path('/var/lib/comfyui-aggregator-deploy/studio137')
spec = importlib.util.spec_from_file_location('studio137_core', TASK / 'studio131-host.py')
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)
core.TASK = TASK
core.UPLOAD = Path('/home/ubuntu/.comfy-aggregator-upload/studio137')
core.RELEASE_ID = 'linux-20261030.137.1'
core.RELEASE = core.BASE / 'releases' / core.RELEASE_ID
core.OLD = core.BASE / 'releases/linux-20261030.132.2'
SERVICES = ('comfy-aggregator-api.service', 'comfy-aggregator-runner.service', 'comfy-aggregator-gc.service')


def probe():
    sys.path[:0] = [str(value) for value in (core.BASE / 'current/venv/lib').glob('python*/site-packages')]
    from generation_infrastructure.database import make_sessions
    from generation_infrastructure.settings import Settings
    from sqlalchemy import text
    sessions = make_sessions(Settings(_env_file=Path('/etc/comfyui-aggregator/cloud.env')))
    try:
        with sessions() as session:
            values = {key: session.scalar(text(query)) for key, query in {
                'database_head': 'SELECT version_num FROM alembic_version', 'fsync': 'SHOW fsync', 'synchronous_commit': 'SHOW synchronous_commit',
                'unfinished_jobs': "SELECT count(*) FROM jobs WHERE state NOT IN ('succeeded','failed','cancelled','expired')",
                'gpu_occupancies': 'SELECT count(*) FROM gpu_occupancies', 'management_holds': 'SELECT count(*) FROM gpu_resources WHERE management_hold IS NOT NULL',
                'jobs': 'SELECT count(*) FROM jobs', 'content': 'SELECT (SELECT count(*) FROM artifacts)+(SELECT count(*) FROM assets)',
                'models': 'SELECT count(*) FROM public_models',
            }.items()}
            if values['database_head'] == '04d137a00001':
                values['balanced_models'] = session.scalar(text("SELECT count(*) FROM public_models WHERE routing->>'policy'='balanced_ready'"))
                values['balanced_jobs'] = session.scalar(text('SELECT count(*) FROM jobs WHERE device_pool_id IS NOT NULL'))
            else:
                values.update(balanced_models=0, balanced_jobs=0)
    finally:
        sessions.kw['bind'].dispose()
    assert values['database_head'] in ('03d128a00001', '04d137a00001')
    assert values['fsync'] == values['synchronous_commit'] == 'on'
    assert values['unfinished_jobs'] == values['gpu_occupancies'] == values['management_holds'] == 0
    return values


def migrate():
    source = """from pathlib import Path
from alembic import command
from alembic.config import Config
from generation_infrastructure.settings import Settings
config=Config(str(Path.cwd()/'alembic.ini'))
config.attributes['settings']=Settings(_env_file=Path('/etc/comfyui-aggregator/cloud.env'))
config.set_main_option('script_location',str(Path.cwd()/'migrations'))
command.upgrade(config,'head')
"""
    subprocess.run([str(core.RELEASE / 'venv/bin/python'), '-B', '-c', source], cwd=core.RELEASE, check=True, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})


def promote():
    assert (TASK / 'prepare.state').read_text().strip() == 'prepared'
    assert (core.BASE / 'current').resolve() == core.OLD
    before = probe()
    assert before['database_head'] == '03d128a00001'
    worker = core.command('systemctl', 'show', '--property=MainPID', '--value', 'comfy-aggregator-http-worker.service')
    gc_timer = subprocess.run(['systemctl', 'is-active', '--quiet', 'comfy-aggregator-gc.timer'], check=False).returncode == 0
    if gc_timer:
        core.command('systemctl', 'stop', 'comfy-aggregator-gc.timer')
    core.command('systemctl', 'stop', *SERVICES)
    switched = False
    try:
        stopped = probe()
        assert stopped['database_head'] == '03d128a00001'
        core.command('systemctl', 'start', 'comfy-aggregator-backup.service')
        backup = core.check_backup()
        manifest = Path('/var/lib/comfyui-aggregator/backups') / backup['manifest']
        assert 0 <= datetime.now(UTC).timestamp() - manifest.stat().st_mtime < 300
        migrate()
        migrated = probe()
        assert migrated['database_head'] == '04d137a00001'
        assert all(migrated[key] == stopped[key] for key in ('jobs', 'content', 'models'))
        assert migrated['balanced_models'] == migrated['balanced_jobs'] == 0
        core.symlink(core.RELEASE, core.BASE / 'current'); switched = True
        core.command('systemctl', 'start', *SERVICES)
        core.command('curl', '--fail', '--silent', '--retry', '20', '--retry-delay', '1', '--retry-connrefused', '--max-time', '5', '-H', 'Host: 43.165.167.179', 'http://127.0.0.1:32880/health/ready')
        after = probe()
        assert all(after[key] == stopped[key] for key in ('jobs', 'content', 'models'))
        assert worker == core.command('systemctl', 'show', '--property=MainPID', '--value', 'comfy-aggregator-http-worker.service')
        result = {'passed': True, 'at': datetime.now(UTC).isoformat(), 'release_id': core.RELEASE_ID, 'backup': backup, 'before': before, 'stopped': stopped, 'after': after,
                  'database_migration_executed': True, 'legacy_models_first_ready': True, 'worker_pid_unchanged': True, 'worker_restarted': False, 'studio_static_pointer_changed': False,
                  'schema_downgraded': False, 'scope': 'native migration and API/Admin delivery; balanced physical GPU fleet not accepted'}
        with (TASK / 'promote.json').open('x') as output:
            output.write(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result))
    except BaseException:
        # The old API tolerates additive columns. Never erase used pool history.
        current = probe()
        if current['balanced_models'] == current['balanced_jobs'] == 0:
            if switched:
                core.command('systemctl', 'stop', *SERVICES)
                core.symlink(core.OLD, core.BASE / 'current')
            core.command('systemctl', 'start', *SERVICES)
        raise
    finally:
        if gc_timer:
            core.command('systemctl', 'start', 'comfy-aggregator-gc.timer')


if __name__ == '__main__':
    action = sys.argv[1]
    if action == 'probe':
        print(json.dumps(probe()))
    elif action == 'extract':
        build = json.loads((TASK / 'cloud-build.json').read_text())
        assert (core.BASE / 'current').resolve() == core.OLD and build['release_id'] == core.RELEASE_ID
        manifest = core.unpack(core.RELEASE_ID + '.tar.gz', core.RELEASE, 'linux-deployment.json', build['archive_sha256'])
        assert manifest['database_schema_changed'] and not manifest['worker_protocol_changed'] and manifest['database_head'] == '04d137a00001'
        print(json.dumps({'archive_verified': True, 'release_id': core.RELEASE_ID}))
    elif action == 'verify-installed':
        core.verify_installed()
    elif action == 'promote':
        promote()
    else:
        raise SystemExit('Unknown fixed 137 action')
