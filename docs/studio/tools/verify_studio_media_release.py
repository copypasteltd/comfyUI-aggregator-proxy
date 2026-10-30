"""Read-only verification of every new/retained static byte and the API boundary."""
import hashlib
import json
import sys
import tarfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT.parent / '生图生视API封装/app/.local/deploy-ssh-venv/Lib/site-packages'))
from studio_remote import connect

PRIVATE = ROOT / '.local/studio149'
RELEASE = 'canvas-20261031.149.1'
prepared = json.loads(json.loads((PRIVATE / 'remote-prepare.json').read_text(encoding='utf-8'))['stdout'])
promoted = json.loads(json.loads((PRIVATE / 'remote-promote.json').read_text(encoding='utf-8'))['stdout'])
assert prepared['passed'] and promoted['passed'] and promoted['to'] == RELEASE
package = json.loads((ROOT / '.local/independent-canvas-package' / RELEASE / 'package.json').read_text(encoding='utf-8'))
with tarfile.open(ROOT / '.local/independent-canvas-package' / RELEASE / (RELEASE + '.tar.gz')) as archive:
    manifest_bytes = archive.extractfile('canvas-deployment.json').read()
    manifest = json.loads(manifest_bytes)
sha = lambda value: hashlib.sha256(value).hexdigest()
assert prepared['manifest_sha256'][RELEASE] == sha(manifest_bytes) == promoted['target_manifest_sha256']
client = connect()
try:
    code = '''import sys,json,hashlib
from pathlib import Path
sys.path.insert(0,'/var/lib/infinite-canvas-deploy/1392-static-route')
from studio_independent_host import api_snapshot,run,CONFIG,UNIT
base=Path('/opt/infinite-canvas')
selected=base/'releases/canvas-20261031.149.1'
print(json.dumps({'api':api_snapshot(),'static':{'current':str((base/'current').resolve()),'config_sha256':hashlib.sha256(CONFIG.read_bytes()).hexdigest(),'unit_sha256':hashlib.sha256(UNIT.read_bytes()).hexdigest(),'service':run('systemctl','show','infinite-canvas-static','--property=MainPID','--property=ActiveState')},'manifest_sha256':hashlib.sha256((selected/'canvas-deployment.json').read_bytes()).hexdigest(),'retained':json.loads((selected/'retained-previous-assets.json').read_text()),'retained_sha256':hashlib.sha256((selected/'retained-previous-assets.json').read_bytes()).hexdigest()}))
'''
    stdin, stdout, stderr = client.exec_command('sudo -n python3 -B -', timeout=55)
    stdin.write(code); stdin.channel.shutdown_write()
    raw, error = stdout.read(), stderr.read()
    assert stdout.channel.recv_exit_status() == 0, 'Read-only inspection failed; inspect private diagnostics'
    actual = json.loads(raw)
finally:
    client.close()
assert actual['api'] == prepared['before']['api'] == promoted['after']['api']
assert actual['static'] == promoted['after']['static']
assert actual['manifest_sha256'] == sha(manifest_bytes)
assert actual['retained_sha256'] == prepared['retained_inventory_sha256'][RELEASE]
assert manifest['storage_contract'] == prepared['storage_contract'] == promoted['storage_contract']
origin, api = 'http://43.165.167.179:39445', 'http://43.165.167.179:39443'
with httpx.Client(trust_env=False, timeout=20, follow_redirects=False) as http:
    def inspect(item):
        response = http.get(origin + '/' + item['path'])
        assert response.status_code == 200 and len(response.content) == item['bytes'] and sha(response.content) == item['sha256'], item['path']
        if item['path'].endswith('.js'): assert response.headers['content-type'].split(';')[0] == 'application/javascript'
        if '/schema-worker-' in item['path']: assert "'unsafe-eval'" in response.headers.get('content-security-policy', '')
        return item['path']
    with ThreadPoolExecutor(max_workers=4) as workers:
        checked = list(workers.map(inspect, manifest['files']))
        retained = list(workers.map(inspect, actual['retained']['files']))
    routes = {}
    for route, expected in [('/',302),('/studio/',200),('/studio/models',200),('/studio/library',200),('/studio/tasks',200),('/studio/storage',200),('/studio/canvas',200),('/studio/connection',200),('/studio/maintenance.html',200),('/v1/models',404),('/admin/',404),('/internal/v1/register',404),('/api/agents',404),('/canvas-deployment.json',404),('/retained-previous-assets.json',404),('/studio/assets',200),('/studio/assets/',200),('/studio/config',200),('/studio/task-access',200),('/studio/canvas/owned-route-probe',200)]:
        response = http.get(origin + route); routes[route] = response.status_code
        assert response.status_code == expected, (route, response.status_code)
    cors = []
    for allowed in [origin, 'http://independent-origin.example:18080']:
        response = http.get(api + '/v1/models', headers={'Origin': allowed})
        assert response.status_code == 401 and response.headers.get('access-control-allow-origin') in {'*', allowed}
        cors.append({'origin': allowed, 'unauthenticated_status': 401, 'allow_origin': response.headers['access-control-allow-origin']})
    response = http.options(api + '/v1/models', headers={'Origin': origin, 'Access-Control-Request-Method': 'GET', 'Access-Control-Request-Headers': 'Authorization'})
    assert response.status_code in {200, 204} and response.headers.get('access-control-allow-origin') in {'*', origin}
    assert http.get(api + '/health/ready').status_code == 200
value = {'passed': True, 'at': datetime.now(UTC).isoformat(), 'release_id': RELEASE, 'origin': origin, 'api_origin': api,
         'source_revision': package['source_revision'], 'source_sha256': package['source_sha256'], 'package_sha256': package['archive_sha256'],
         'static_files_verified': len(checked), 'retained_previous_assets_verified': len(retained), 'retained_paths': retained,
         'routes': routes, 'cors': cors, 'api_healthy': True, 'api_pointer_unchanged': True, 'api_configuration_unchanged': True,
         'api_services_unchanged': True, 'static_configuration_unchanged': True, 'static_service_unchanged': True,
         'storage_contract': manifest['storage_contract'], 'ssh_session_closed': True, 'api_workspaces_written': False,
         'service_restarts': 0, 'generation_posts': 0, 'virtualization_used': False,
         'scope': 'Exact public static bytes and routes, existing API CORS/unauthenticated/health plus original service/configuration snapshots; not generation, browser codec or full acceptance'}
target = ROOT / 'docs/research/studio-implementation/independent-delivery-1491.json'
with target.open('x', encoding='utf-8') as output: output.write(json.dumps(value, indent=2) + '\n')
print(json.dumps({key: value[key] for key in ['passed', 'release_id', 'static_files_verified', 'retained_previous_assets_verified', 'api_services_unchanged']}))
