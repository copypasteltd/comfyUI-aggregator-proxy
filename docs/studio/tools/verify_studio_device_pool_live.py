"""Read-only native host, public HTTP and real Admin contract checks for 137."""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT.parent / '生图生视API封装/app/.local/deploy-ssh-venv/Lib/site-packages'))
from studio_deploy import run  # noqa: E402
from studio_live import Admin  # noqa: E402

EVIDENCE = ROOT / 'aggregator-service/docs/evidence/studio-release-137'
RELEASE = 'linux-20261030.137.1'
PRIVATE = ROOT / '.local/studio-cloud-build'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    target = EVIDENCE / 'live-delivery-1371.json'
    assert not target.exists()
    build = read(PRIVATE / (RELEASE + '-build.json'))
    manifest = read(PRIVATE / RELEASE / 'linux-deployment.json')
    sys.path.insert(0, str(ROOT / 'aggregator-service/app/scripts'))
    from release_source import source_snapshot, source_revision
    sources = source_snapshot(ROOT / 'aggregator-service/app')
    assert sources == manifest['current_source_sha256']
    assert source_revision(ROOT / 'aggregator-service/app', sources) == build['application_source_revision']
    script = """set -euo pipefail
/opt/comfyui-aggregator/current/venv/bin/python -B - <<'PY'
import hashlib,importlib.util,json,subprocess,zipfile
from pathlib import Path
base=Path('/opt/comfyui-aggregator')
release=(base/'current').resolve()
assert release.name=='linux-20261030.137.1'
m=json.loads((release/'linux-deployment.json').read_text())
checked=0
for item in m['files']:
    path=(release/item['path']).resolve()
    assert path.is_relative_to(release) and path.is_file()
    assert path.stat().st_size==item['bytes'] and hashlib.sha256(path.read_bytes()).hexdigest()==item['sha256']
    checked+=1
site=next((release/'venv/lib').glob('python*/site-packages'))
payload=0
for wheel in (release/'wheels').glob('*.whl'):
    with zipfile.ZipFile(wheel) as archive:
        for name in archive.namelist():
            if '.dist-info/' not in name and not name.endswith('/'):
                assert (site/name).read_bytes()==archive.read(name)
                payload+=1
spec=importlib.util.spec_from_file_location('owned137',Path('/var/lib/comfyui-aggregator-deploy/studio137/studio137-host.py'))
host=importlib.util.module_from_spec(spec);spec.loader.exec_module(host)
names=['comfy-aggregator-api.service','comfy-aggregator-runner.service','comfy-aggregator-http-worker.service','comfy-aggregator-edge.service']
value={'backend':release.name,'studio':(base/'studio-current').resolve().name,'services':{name:subprocess.check_output(['systemctl','is-active',name],text=True).strip() for name in names},'worker_pid':int(subprocess.check_output(['systemctl','show','--value','-p','MainPID','comfy-aggregator-http-worker.service'],text=True)), 'database':host.probe(),'manifest_sha256':hashlib.sha256((release/'linux-deployment.json').read_bytes()).hexdigest(),'source_revision':m['application_source_revision'],'files_verified':checked,'wheel_payload_files_verified':payload}
print(json.dumps(value))
PY
"""
    raw = run(script)
    (ROOT / '.local/studio-ops/live137-host-private.json').write_text(json.dumps(raw, indent=2) + '\n', encoding='utf-8')
    if raw['exit_code']:
        raise RuntimeError('Native verification failed; details retained privately')
    host = json.loads(raw['stdout'])
    assert host['source_revision'] == build['application_source_revision']
    assert host['manifest_sha256'] == sha(PRIVATE / RELEASE / 'linux-deployment.json')
    assert host['worker_pid'] == 803588 and host['studio'] == 'studio-20261030.136.1'
    assert host['database']['database_head'] == '04d137a00001'
    assert host['database']['balanced_models'] == host['database']['balanced_jobs'] == 0
    assert all(state == 'active' for state in host['services'].values())
    responses, cors = [], []
    static = read(PRIVATE / 'static-build.json')
    static_manifest = read(PRIVATE / (static['release_id'] + '.json'))
    studio_index = next(item['sha256'] for item in static_manifest['files'] if item['path'] == 'studio/index.html')
    admin_build = read(sorted(EVIDENCE.glob('admin-build-*.json'))[-1])
    origin = 'http://43.165.167.179:39443'
    with httpx.Client(base_url=origin, trust_env=False, timeout=35) as client:
        for path, expected in (('/studio/', 200), ('/studio/models', 200), ('/admin/', 200), ('/health/ready', 200), ('/v1/models', 401), ('/admin/api/v1/public-models', 401)):
            response = client.get(path)
            assert response.status_code == expected
            digest = hashlib.sha256(response.content).hexdigest()
            if path.startswith('/studio/'):
                assert digest == studio_index
            if path == '/admin/':
                assert digest == admin_build['dist_sha256']['index.html']
            responses.append({'path': path, 'status': response.status_code, 'body_sha256': digest})
        for requested in ('http://device-pool-validation.invalid', 'https://another-origin.invalid'):
            response = client.options('/v1/jobs/lookup', headers={'Origin': requested, 'Access-Control-Request-Method': 'POST', 'Access-Control-Request-Headers': 'authorization,idempotency-key,x-expected-release-digest,content-type'})
            allowed = response.headers.get('Access-Control-Allow-Origin')
            assert response.status_code == 200 and allowed in ('*', requested)
            cors.append({'requested_origin': requested, 'allow_origin': allowed, 'status': response.status_code})
    admin = Admin()
    checks = []
    try:
        catalog = admin.call('GET', 'public-models')
        models = catalog['data']
        assert len(models) == host['database']['models'] == 13
        for model in models:
            assert model['routing'] == {'policy': 'first_ready', 'weights': {}}
            pool = admin.call('GET', 'public-models/' + model['id'] + '/device-pool')
            assert pool == {'model_id': model['id'], 'model_revision': model['revision'], 'policy': 'first_ready', 'pool_id': None, 'sequence': 0, 'members': []}
            checks.append({'model_id': model['id'], 'policy': pool['policy'], 'revision': pool['model_revision'], 'empty_legacy_pool': True})
        queues = admin.call('GET', 'queues/snapshot')
        assert isinstance(queues, dict)
    finally:
        admin.close()
    report = {'passed': True, 'at': datetime.now(UTC).isoformat(), 'scope': 'read-only real native deployment, public HTTP and all 13 legacy Admin model/pool projections; no balanced physical GPU execution',
              **{key: value for key, value in host.items() if key != 'worker_pid'}, 'original_worker_pid_unchanged': True,
              'runtime_source_files': sources, 'runtime_source_files_count': len(sources), 'git_inputs_rechecked': True,
              'responses': responses, 'cors': cors, 'admin_model_checks': checks, 'admin_queue_read': True, 'admin_session_closed': True,
              'generation_posts': 0, 'production_model_or_key_mutations': 0, 'static_pointer_changed': False, 'virtualization_used': False, 'full_acceptance': 'HOLD', 'new_feature_acceptance': 'pending-test'}
    with target.open('x', encoding='utf-8') as output:
        output.write(json.dumps(report, indent=2) + '\n')
    print(json.dumps({key: value for key, value in report.items() if key not in {'runtime_source_files', 'admin_model_checks'}}))


if __name__ == '__main__':
    main()
