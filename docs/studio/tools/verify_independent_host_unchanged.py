"""Read-only remote snapshot; no upload, deployment, reload or service restart."""
import hashlib
import json
import re
import sys
import tarfile
from datetime import UTC, datetime
from pathlib import Path

import httpx

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root.parent / '生图生视API封装/app/.local/deploy-ssh-venv/Lib/site-packages'))
from studio_remote import connect  # noqa: E402

evidence = root / 'docs/research/studio-implementation'
version = sys.argv[1] if len(sys.argv) == 2 else '140'
assert re.fullmatch(r'\d+(?:-preflight)?', version)
baseline = json.loads((evidence / 'independent-static-route-1392.json').read_text(encoding='utf-8'))
expected_pointer = '/opt/infinite-canvas/releases/canvas-20261030.139.2'
expected_service = baseline['static_after']
if version.startswith(('142', '143', '144', '145', '146')):
    previous_version = {'142': '141', '143': '142', '144': '143', '145': '144', '146': '145'}[version.split('-')[0]]
    previous = json.loads((root / ('.local/studio' + previous_version + '/publish-promote-' + previous_version + '1.json')).read_text(encoding='utf-8'))
    assert previous['exit_code'] == 0
    deployment = json.loads(previous['stdout'])
    assert deployment['passed'] and deployment['api_unchanged']
    expected_pointer = deployment['static_after']['current']
    expected_service = deployment['static_after']['service']
    with tarfile.open(root / ('.local/independent-canvas-package/canvas-20261030.' + previous_version + '.1/canvas-20261030.' + previous_version + '.1.tar.gz')) as archive:
        manifest = json.load(archive.extractfile('canvas-deployment.json'))
    baseline = {'api_after': deployment['api_after'], 'index_sha256': next(item['sha256'] for item in manifest['files'] if item['path'] == 'studio/index.html')}
elif version == '147':
    # Evidence-only continuation of the same verified 146.1 release; no promotion.
    names = json.loads((root / '.local/studio146/receipts.json').read_text(encoding='utf-8'))
    rollback = json.loads((evidence / names['rollback_web']).read_text(encoding='utf-8'))
    assert rollback['passed'] and rollback['restore']['passed']
    after = rollback['restore']['after']
    expected_pointer = after['static']['current']
    expected_service = after['static']['service']
    with tarfile.open(root / '.local/independent-canvas-package/canvas-20261030.146.1/canvas-20261030.146.1.tar.gz') as archive:
        manifest = json.load(archive.extractfile('canvas-deployment.json'))
    assert expected_pointer == '/opt/infinite-canvas/releases/canvas-20261030.146.1'
    baseline = {'api_after': after['api'], 'index_sha256': next(item['sha256'] for item in manifest['files'] if item['path'] == 'studio/index.html')}
code = '''import sys,json
from pathlib import Path
sys.path.insert(0,"/var/lib/infinite-canvas-deploy/1392-static-route")
from studio_independent_host import api_snapshot,run,sha,BASE,CONFIG,UNIT
print(json.dumps({"api":api_snapshot(),"static_service":run("systemctl","show","infinite-canvas-static","--property=MainPID","--property=ActiveState"),"static_pointer":str((BASE/"current").resolve()),"static_config_sha256":sha(CONFIG),"static_unit_sha256":sha(UNIT)}))
'''
client = connect()
try:
    stdin, stdout, stderr = client.exec_command('sudo -n python3 -B -', timeout=55)
    stdin.write(code)
    stdin.channel.shutdown_write()
    actual = stdout.read().decode('utf-8')
    error = stderr.read().decode('utf-8')
    assert stdout.channel.recv_exit_status() == 0, 'Read-only host inspection failed'
    value = json.loads(actual)
finally:
    client.close()
assert value['api'] == baseline['api_after']
assert value['static_service'] == expected_service
assert value['static_pointer'] == expected_pointer
assert value['static_config_sha256'] == hashlib.sha256((root / 'docs/deployment/nginx.conf').read_bytes()).hexdigest()
assert value['static_unit_sha256'] == hashlib.sha256((root / 'docs/deployment/infinite-canvas-static.service').read_bytes()).hexdigest()
with httpx.Client(trust_env=False, timeout=20) as http:
    index = http.get('http://43.165.167.179:39445/studio/')
    assert index.status_code == 200 and hashlib.sha256(index.content).hexdigest() == baseline['index_sha256']
    assert http.get('http://43.165.167.179:39443/health/ready').status_code == 200
result = {'passed': True, 'at': datetime.now(UTC).isoformat(), 'scope': 'Read-only snapshot compared with the last verified static release; no remote mutation or deployment', 'compared_static_pointer': expected_pointer,
          'api_pointer_unchanged': True, 'api_configuration_unchanged': True, 'api_services_unchanged': True,
          'static_pointer_unchanged': True, 'static_configuration_unchanged': True, 'static_service_unchanged': True,
          'static_index_sha256': baseline['index_sha256'], 'api': value['api'], 'api_healthy': True,
          'service_restarts': 0, 'api_code_written': False, 'ssh_session_closed': True}
with (evidence / ('independent-host-unchanged-' + version + '.json')).open('x', encoding='utf-8') as output:
    output.write(json.dumps(result, indent=2) + '\n')
print(json.dumps({name: result[name] for name in ['passed', 'api_pointer_unchanged', 'api_configuration_unchanged', 'api_services_unchanged', 'static_service_unchanged', 'service_restarts']}))
