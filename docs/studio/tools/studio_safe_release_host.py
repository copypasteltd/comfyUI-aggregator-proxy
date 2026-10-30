"""Prepare/switch only independent static releases, with explicit v5 recovery."""
import fcntl
import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from studio_release_guard import CONTRACT, ReleaseRefused, compatible, entry_map, merge_entries, read_release, require, sha, unpack_verified

BASE = Path('/opt/infinite-canvas')
TASK = Path('/var/lib/infinite-canvas-deploy/146-safe-release')
CURRENT = BASE / 'current'
ORIGINAL, NEW, RECOVERY = ['canvas-20261030.' + value for value in ['145.1', '146.1', '146.0']]


def write(name, value):
    with (TASK / name).open('x') as output: output.write(json.dumps(value, indent=2) + '\n')


def snapshots():
    sys.path.insert(0, '/var/lib/infinite-canvas-deploy/1392-static-route')
    from studio_independent_host import api_snapshot, run, CONFIG, UNIT
    return {'api': api_snapshot(), 'static': {'current': str(CURRENT.resolve()), 'config_sha256': sha(CONFIG.read_bytes()), 'unit_sha256': sha(UNIT.read_bytes()), 'service': run('systemctl', 'show', 'infinite-canvas-static', '--property=MainPID', '--property=ActiveState')}}


def verify_boundary(before, after, pointer):
    require(before['api'] == after['api'], 'Original API boundary changed; inspect external state')
    require({key: value for key, value in before['static'].items() if key != 'current'} == {key: value for key, value in after['static'].items() if key != 'current'}, 'Static service or configuration changed')
    require(after['static']['current'] == str(pointer), 'Unexpected static pointer')


def owned_release(name):
    require(name in {ORIGINAL, NEW, RECOVERY}, 'Unknown release selection')
    directory = BASE / 'releases' / name
    require(directory.parent.resolve() == BASE / 'releases' and directory.is_dir() and not directory.is_symlink(), 'Release path is not an owned directory')
    return directory


def health(manifest):
    files = entry_map(manifest['files'])
    for name in ['studio/index.html', 'studio/maintenance.html']:
        with urllib.request.urlopen('http://127.0.0.1:39445/' + name) as response:
            require(response.status == 200 and sha(response.read()) == files[name]['sha256'], 'Static HTTP entry differs from selected release')


def prepare():
    require(not (TASK / 'prepared.json').exists(), 'Preparation already recorded; inspect status')
    require(CURRENT.is_symlink() and CURRENT.resolve() == BASE / 'releases' / ORIGINAL, 'Current release changed before preparation')
    before = snapshots(); write('prepare-before.json', before)
    old_dir = owned_release(ORIGINAL)
    old, old_files = read_release(old_dir, require_contract=False)
    manifests, directories, packages = {}, {}, {}
    for name in [NEW, RECOVERY]:
        package = json.loads((TASK / (name + '.package.json')).read_text())
        directory = BASE / 'releases' / name
        manifest, files = unpack_verified(TASK / (name + '.tar.gz'), package, directory)
        manifests[name], directories[name], packages[name] = manifest, directory, package
    recovery = manifests[RECOVERY]
    require(recovery['recovery_source']['release_id'] == ORIGINAL and recovery['recovery_source']['manifest_sha256'] == sha((old_dir / 'canvas-deployment.json').read_bytes()), 'Recovery provenance does not match actual previous release')
    require(recovery['source_revision'] == old['source_revision'] and recovery['source_sha256'] == old['source_sha256'] and entry_map(recovery['files']) == entry_map(old['files']), 'Recovery application differs from the verified source release')
    compatible(recovery, manifests[NEW])
    union = dict(old_files)
    sources = {name: old_dir for name in old_files}
    for release, manifest in manifests.items():
        files = entry_map(manifest['files']); union = merge_entries(union, {name: item for name, item in files.items() if name.startswith('studio/assets/')})
        sources.update({name: directories[release] for name in files if name.startswith('studio/assets/')})
    for release, manifest in manifests.items():
        declared = entry_map(manifest['files'])
        retained = entry_map([entry for name, entry in union.items() if name.startswith('studio/assets/') and name not in declared], assets_only=True)
        for name, entry in retained.items():
            target = directories[release] / name
            require(not target.exists() and target.resolve().is_relative_to(directories[release]), 'Retained target already exists or escapes release')
            target.parent.mkdir(parents=True, exist_ok=True)
            data = (sources[name] / name).read_bytes()
            require(len(data) == entry['bytes'] and sha(data) == entry['sha256'], 'Retained source changed')
            target.write_bytes(data); target.chmod(0o644)
        with (directories[release] / 'retained-previous-assets.json').open('x') as output:
            output.write(json.dumps({'release_id': ORIGINAL, 'files': list(retained.values())}, indent=2) + '\n')
        read_release(directories[release])
    after = snapshots(); verify_boundary(before, after, old_dir)
    result = {'passed': True, 'at': datetime.now(UTC).isoformat(), 'original': ORIGINAL, 'new': NEW, 'recovery': RECOVERY, 'storage_contract': CONTRACT, 'manifest_sha256': {name: sha((directory / 'canvas-deployment.json').read_bytes()) for name, directory in {ORIGINAL: old_dir, **directories}.items()}, 'retained_inventory_sha256': {name: sha((directory / 'retained-previous-assets.json').read_bytes()) for name, directory in {ORIGINAL: old_dir, **directories}.items()}, 'packages': packages, 'before': before, 'after': after, 'api_unchanged': True, 'static_service_restarted': False, 'current_pointer_changed': False, 'recovery_application_bytes_identical_to_source': True}
    write('prepared.json', result); print(json.dumps(result))


def switch(action, operation_id='initial'):
    prepared = json.loads((TASK / 'prepared.json').read_text())
    expected, target = {'promote': (ORIGINAL, NEW), 'rollback': (NEW, RECOVERY), 'restore': (RECOVERY, NEW)}[action]
    label = action if operation_id == 'initial' else action + '-' + operation_id
    require(not (TASK / (label + '.json')).exists() and not (TASK / (label + '-attempt.json')).exists(), 'Operation already attempted; inspect its original status')
    before = snapshots()
    require(CURRENT.is_symlink() and CURRENT.resolve() == owned_release(expected), 'Expected current pointer changed; no switch')
    require(before['api'] == prepared['before']['api'], 'Original API state changed since preparation')
    require({key: value for key, value in before['static'].items() if key != 'current'} == {key: value for key, value in prepared['before']['static'].items() if key != 'current'}, 'Static configuration or service changed since preparation')
    for name in [expected, target, RECOVERY]:
        require(sha((owned_release(name) / 'retained-previous-assets.json').read_bytes()) == prepared['retained_inventory_sha256'][name], 'Retained-resource inventory changed')
    source, _ = read_release(owned_release(expected), prepared['manifest_sha256'][expected], require_contract=expected != ORIGINAL)
    if expected == ORIGINAL:
        # The legacy manifest stays immutable. Its exact tested recovery copy attests v5.
        source = {**source, 'storage_contract': prepared['storage_contract']}
    manifest, _ = read_release(owned_release(target), prepared['manifest_sha256'][target])
    compatible(source, manifest)
    temp = BASE / ('.safe-current-' + label)
    require(not temp.exists() and not temp.is_symlink(), 'Temporary switch path exists; inspect interrupted operation')
    write(label + '-attempt.json', {'expected': expected, 'target': target, 'before': before})
    switched = False
    try:
        require(CURRENT.resolve() == owned_release(expected), 'Current pointer changed during verification')
        temp.symlink_to(owned_release(target), target_is_directory=True); temp.replace(CURRENT); switched = True
        health(manifest)
        after = snapshots(); verify_boundary(before, after, owned_release(target))
        result = {'passed': True, 'at': datetime.now(UTC).isoformat(), 'action': action, 'operation_id': operation_id, 'from': expected, 'to': target, 'storage_contract': CONTRACT, 'before': before, 'after': after, 'api_unchanged': True, 'configuration_written': False, 'static_service_restarted': False, 'browser_database_touched_by_host': False, 'source_manifest_sha256': prepared['manifest_sha256'][expected], 'target_manifest_sha256': prepared['manifest_sha256'][target]}
        write(label + '.json', result); print(json.dumps(result))
    except BaseException as error:
        # A failed v5 health check must never silently restore v4-only code.
        recovered = False
        if switched and CURRENT.resolve() == owned_release(target):
            recovery, _ = read_release(owned_release(RECOVERY), prepared['manifest_sha256'][RECOVERY])
            compatible(manifest, recovery)
            fallback = BASE / ('.safe-recovery-' + label)
            require(not fallback.exists() and not fallback.is_symlink(), 'Recovery path exists; inspect state')
            fallback.symlink_to(owned_release(RECOVERY), target_is_directory=True); fallback.replace(CURRENT)
            health(recovery); recovered = True
        write(label + '-failure.json', {'passed': False, 'error': str(error), 'compatible_recovery_selected': recovered, 'current': str(CURRENT.resolve()), 'api_modified': False})
        raise


def reject_v4(operation_id='initial'):
    before = snapshots()
    previous = BASE / 'releases/canvas-20261030.144.1/canvas-deployment.json'
    old = json.loads(previous.read_text())
    try: compatible({'storage_contract': CONTRACT}, old)
    except ReleaseRefused as error: reason = str(error)
    else: raise ReleaseRefused('Expected v4 target to be refused')
    after = snapshots(); verify_boundary(before, after, CURRENT.resolve())
    require(before == after, 'Refusal changed host state')
    result = {'passed': True, 'refused': True, 'reason': reason, 'target': old['release_id'], 'pointer_unchanged': True, 'api_unchanged': True, 'static_service_restarted': False}
    write('reject-v4' + ('' if operation_id == 'initial' else '-' + operation_id) + '.json', result); print(json.dumps(result))


def main():
    require(sys.platform == 'linux' and os.geteuid() == 0 and TASK.is_dir(), 'Run only in the prepared root-owned independent task')
    action = sys.argv[1] if len(sys.argv) in {2, 3} else ''
    operation_id = sys.argv[2] if len(sys.argv) == 3 else 'initial'
    require(re.fullmatch(r'[a-z0-9-]+', operation_id), 'Invalid operation identity')
    require(action in {'prepare', 'promote', 'rollback', 'restore', 'reject-v4', 'status'}, 'Unknown operation')
    if action == 'status':
        print(json.dumps({'passed': True, 'snapshot': snapshots(), 'records': {file.name: json.loads(file.read_text()) for file in TASK.glob('*.json') if not file.name.endswith('.package.json')}})); return
    lock = os.open(BASE / '.release-control.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if action == 'prepare': prepare()
        elif action == 'reject-v4': reject_v4(operation_id)
        else: switch(action, operation_id)
    finally: os.close(lock)


if __name__ == '__main__': main()
