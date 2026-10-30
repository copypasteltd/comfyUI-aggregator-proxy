"""One owned temporary business Key for current canvas main-flow acceptance; original service is not changed."""
from copy import deepcopy
from datetime import UTC, datetime
import json
from pathlib import Path
import sys
from uuid import uuid4

import httpx

from studio_live import Admin

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / '.local/studio150'
PRIVATE.mkdir(parents=True, exist_ok=True)
KEY = PRIVATE / 'live-business-key.json'
action = sys.argv[1] if len(sys.argv) == 2 else ''
assert action in {'prepare', 'catalog', 'status', 'disable'}

if action == 'prepare':
    if KEY.exists():
        raise SystemExit('Owned Key already exists; inspect catalog/status instead of creating another')
    admin = Admin()
    try:
        rows = admin.call('GET', 'api-keys')['data']
        source = next(item for item in rows if item['enabled'] and 'images:create' in item['policy']['scopes'] and 'jobs:read' in item['policy']['scopes'])
        plan_file = PRIVATE / 'live-key-plan.json'
        if not plan_file.exists():
            with plan_file.open('x', encoding='utf-8') as output:
                output.write(json.dumps({'token': 'canvas150-' + uuid4().hex, 'body': {'tenant_id': source['tenant_id'], 'project_id': source['project_id'], 'name': 'Canvas 150 main-flow acceptance (temporary)', 'policy': deepcopy(source['policy'])}}, ensure_ascii=False, indent=2) + '\n')
        plan = json.loads(plan_file.read_text(encoding='utf-8'))
        created = admin.call('POST', 'api-keys', plan['body'], token=plan['token'])
        assert created.get('secret'), 'Inspect the original Admin operation; do not issue a duplicate Key'
        with KEY.open('x', encoding='utf-8') as output:
            output.write(json.dumps({'origin': admin.origin, **created}, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps({'passed': True, 'owned_temporary_key_created': True, 'source_key_modified': False, 'credentials_published': False, 'api_source_configuration_services_changed': False}))
    finally:
        admin.close()
elif action == 'disable':
    owned = json.loads(KEY.read_text(encoding='utf-8'))
    admin = Admin()
    try:
        current = admin.call('GET', 'api-keys/' + owned['key']['id'])
        if current['enabled']:
            admin.call('POST', 'api-keys/' + current['id'] + '/actions', {'action': 'disable', 'expected_revision': current['revision']}, token='canvas150-disable-' + current['id'])
        with httpx.Client(trust_env=False, timeout=35) as client:
            response = client.get(owned['origin'] + '/v1/models', headers={'Authorization': 'Bearer ' + owned['secret']})
        assert response.status_code == 401
        result = {'passed': True, 'at': datetime.now(UTC).isoformat(), 'owned_temporary_key_disabled': True, 'revoked_business_access': 401, 'other_keys_modified': False, 'api_source_configuration_services_changed': False}
        with (ROOT / 'docs/research/studio-implementation/composer-live-key-cleanup-150.json').open('x', encoding='utf-8') as output:
            output.write(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result))
    finally:
        admin.close()
else:
    owned = json.loads(KEY.read_text(encoding='utf-8'))
    with httpx.Client(base_url=owned['origin'], headers={'Authorization': 'Bearer ' + owned['secret']}, trust_env=False, timeout=35) as client:
        if action == 'catalog':
            catalog = {}
            for name in ['access-policy', 'models', 'capabilities']:
                response = client.get('/v1/' + name); response.raise_for_status(); catalog[name] = response.json()
            (PRIVATE / 'live-catalog.json').write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            print(json.dumps({'passed': True, 'models': [{'id': model['id'], 'task_type': model['usage']['task_type'], 'presets': model['usage']['presets']} for model in catalog['models']['data']], 'spec_limits': catalog['access-policy'].get('spec_limits')}, ensure_ascii=False))
        else:
            response = client.get('/v1/jobs'); response.raise_for_status(); rows = response.json()['data']
            print(json.dumps({'passed': True, 'jobs': [{'id': row['id'], 'model': row['model'], 'status': row['status'], 'artifacts': row.get('artifacts', [])} for row in rows]}, ensure_ascii=False))
