"""Append 137 provenance; preserve historical reports and rebind only changed inputs."""
import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / 'aggregator-service'
REGISTRY = ROOT / 'docs/contracts/development-evidence.json'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    registry = read(REGISTRY)
    baseline_path = ROOT / registry['baseline_index']['path']
    assert digest(baseline_path) == registry['baseline_index']['sha256']
    baseline = deepcopy(read(baseline_path))
    candidates = [(path, read(path)) for path in (ROOT / 'docs/evidence/studio-release-137').glob('device-pool-*.json')]
    source_path, source = max(((p, r) for p, r in candidates if r['passed'] and r['mode'] == 'focused'), key=lambda item: item[1]['finished_at'])
    assert source['sources_unchanged'] and source['counts']['tests'] == 38
    component = {**source, 'python': source['counts'], 'junit_sha256': source['results.xml_sha256'], 'python_log_sha256': source['raw.log_sha256'],
                 'derived_from': {'path': source_path.relative_to(ROOT).as_posix(), 'sha256': digest(source_path)}}
    component_path = ROOT / 'docs/evidence/studio-release-137/native-component-1371.json'
    with component_path.open('x', encoding='utf-8') as output:
        output.write(json.dumps(component, indent=2) + '\n')
    baseline['reports']['studio137'] = {'path': component_path.relative_to(ROOT).as_posix(), 'sha256': digest(component_path), 'layer': 'local_component'}
    baseline.setdefault('python_runs', []).append('studio137')
    baseline.update(at=datetime.now(UTC).isoformat(), scope='Immutable history plus 38 native 137 device-pool cases. Physical fleet and full acceptance remain pending.')
    indexes = {'ci86': read(ROOT / baseline['reports']['ci86']['path'])['source_sha256'], 'admin87': baseline['admin_sources']['source_sha256']}
    for key in baseline.get('browser_runs', []):
        indexes[key] = read(ROOT / baseline['reports'][key]['path'])['source_sha256']
    indexes['go80'] = {'app/integrations/sub2api/overlay/backend/' + name: value for name, value in read(ROOT / baseline['reports']['go80']['path'])['overlay_files_sha256'].items()}
    for key in baseline['python_runs']:
        indexes[key] = component['source_sha256'] if key == 'studio137' else read(ROOT / baseline['reports'][key]['path'])['source_sha256']
    changed = set()
    for requirement in registry['requirements']:
        for category in ('implementation', 'tests'):
            for ref in requirement[category]:
                if ref['path'] not in component['source_sha256']:
                    continue
                current = digest(ROOT / ref['path'])
                assert current == component['source_sha256'][ref['path']]
                previous = ref.get('baseline', 'ci86')
                if indexes.get(previous, {}).get(ref['path']) != current:
                    ref['baseline'] = 'studio137'; changed.add(ref['path'])
                    if 'studio137' not in requirement['evidence']:
                        requirement['evidence'].append('studio137')
    extra = {
        'JOB-02': ('app/packages/infrastructure/src/generation_infrastructure/device_pools.py', 'test_global_assignment_persists_despite_wrong_worker_polling'),
        'JOB-03': ('app/packages/domain/src/generation_domain/device_pool.py', 'test_concurrent_claim_replay_and_rollback_charge_once'),
        'NODE-04': ('app/packages/infrastructure/src/generation_infrastructure/device_pools.py', 'test_native_processes_accept_one_pool_assignment'),
        'ADMIN-06': ('app/packages/contracts/src/generation_contracts/device_pool.py', 'test_http_pool_is_read_only_admin_scope'),
    }
    for requirement in registry['requirements']:
        if requirement['id'] not in extra:
            continue
        implementation, symbol = extra[requirement['id']]
        requirement['implementation'].append({'path': implementation, 'baseline': 'studio137'})
        requirement['tests'].append({'path': 'app/tests/integration/test_device_pool_transactions.py', 'symbol': symbol, 'language': 'python', 'baseline': 'studio137'})
        if 'studio137' not in requirement['evidence']:
            requirement['evidence'].append('studio137')
        requirement['limitation'] += '；137设备均衡为原生控制层验证，真实同release逐卡批准、10～20台容量及人工验收仍待完成。'
    target = ROOT / 'docs/evidence/2026-10-30/development-trace-baselines-137-device-pool.json'
    with target.open('x', encoding='utf-8') as output:
        output.write(json.dumps(baseline, ensure_ascii=False, indent=2) + '\n')
    registry['increment'] = 137
    registry['baseline_index'] = {'path': target.relative_to(ROOT).as_posix(), 'sha256': digest(target)}
    REGISTRY.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'passed': True, 'rebound_changed_sources': sorted(changed), 'added_pool_requirement_bindings': len(extra), 'historical_reports_rewritten': False}))


if __name__ == '__main__':
    main()
