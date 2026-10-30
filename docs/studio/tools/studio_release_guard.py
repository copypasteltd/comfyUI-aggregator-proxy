"""Validate independent static releases before touching a canvas-only pointer."""
import hashlib
import json
import re
import tarfile
from pathlib import Path, PurePosixPath

CONTRACT = {'format_version': 1, 'journal': {'name': 'comfy-aggregator-studio:journal', 'version': 5}, 'document_record': 1, 'task_record': 1, 'upload_record': 1, 'result_record': 1, 'controlled_media_record': 1, 'editor_record': 1, 'editor_marker_versions': [1, 2], 'project_archive': 3}
RELEASE_NAME = re.compile(r'canvas-\d{8}\.\d+\.\d+')
HEX = re.compile(r'[a-f0-9]{64}')


class ReleaseRefused(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ReleaseRefused(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def entry_map(entries, assets_only=False):
    require(isinstance(entries, list), 'Missing file inventory')
    result = {}
    for entry in entries:
        require(isinstance(entry, dict) and set(entry) == {'path', 'bytes', 'sha256'}, 'Unknown file entry')
        name = entry['path']
        require(isinstance(name, str) and '\\' not in name and '\x00' not in name, 'Invalid file path')
        path = PurePosixPath(name)
        require(not path.is_absolute() and '..' not in path.parts and path.as_posix() == name and name.startswith('studio/'), 'File escapes static canvas')
        require(type(entry['bytes']) is int and entry['bytes'] >= 0 and isinstance(entry['sha256'], str) and HEX.fullmatch(entry['sha256']), 'Invalid file digest or size')
        require(name not in result, 'Duplicate file path')
        if assets_only:
            require(re.fullmatch(r'studio/assets/[A-Za-z0-9_.-]+-[A-Za-z0-9_-]+\.(?:js|css)', name), 'Only hashed JS/CSS may be retained')
        result[name] = entry
    return result


def manifest_files(manifest):
    require(isinstance(manifest, dict) and manifest.get('format_version') == 1, 'Unknown static manifest')
    require(isinstance(manifest.get('release_id'), str) and RELEASE_NAME.fullmatch(manifest['release_id']), 'Unknown release ID')
    require(manifest.get('api_modified') is False and manifest.get('application_backend') is False and manifest.get('virtualization_used') is False, 'Release contains application/API changes')
    require(manifest.get('base') == '/studio/' and manifest.get('mode') == 'managed' and manifest.get('api_origin') == 'http://43.165.167.179:39443', 'Wrong independent deployment boundary')
    require(re.fullmatch(r'[a-f0-9]{40}', manifest.get('source_revision', '')) and HEX.fullmatch(manifest.get('source_sha256', '')), 'Missing frozen source identity')
    files = entry_map(manifest.get('files'))
    require({'studio/index.html', 'studio/maintenance.html', 'studio/licenses/infinite-canvas-MIT.txt'} <= files.keys(), 'Incomplete multi-entry static release')
    return files


def compatible(current, target):
    # Equality covers every persisted record/marker/archive contract, not just DB version.
    canonical = lambda value: json.dumps(value, sort_keys=True, separators=(',', ':'))
    require(canonical(current.get('storage_contract')) == canonical(CONTRACT), 'Current storage contract is unknown; no automatic rollback')
    require(canonical(target.get('storage_contract')) == canonical(CONTRACT), 'Target cannot preserve the complete v5 storage contract')
    return True


def read_release(directory, expected_manifest_sha256=None, require_contract=True):
    require(directory.is_dir() and not directory.is_symlink(), 'Release directory must be a real owned directory')
    file = directory / 'canvas-deployment.json'
    require(file.is_file() and not file.is_symlink(), 'Missing immutable deployment manifest')
    raw = file.read_bytes()
    if expected_manifest_sha256 is not None:
        require(sha(raw) == expected_manifest_sha256, 'Deployment manifest changed')
    manifest = json.loads(raw)
    require(directory.name == manifest['release_id'], 'Release directory identity mismatch')
    entries = manifest_files(manifest)
    if require_contract:
        compatible(manifest, manifest)
    retained = directory / 'retained-previous-assets.json'
    if retained.exists():
        require(retained.is_file() and not retained.is_symlink(), 'Invalid retained-resource inventory')
        entries = merge_entries(entries, entry_map(json.loads(retained.read_text())['files'], assets_only=True))
    for entry in entries.values():
        target = directory / entry['path']
        require(target.is_file() and not target.is_symlink() and target.resolve().is_relative_to(directory.resolve()), 'Missing/linked static file')
        require(target.stat().st_size == entry['bytes'] and sha(target.read_bytes()) == entry['sha256'], 'Static file changed: ' + entry['path'])
    return manifest, entries


def merge_entries(first, second):
    result = dict(first)
    for name, entry in second.items():
        require(name not in result or result[name] == entry, 'Same resource path has conflicting bytes')
        result[name] = entry
    return result


def unpack_verified(archive, package, directory):
    require(package.get('passed') is True and package.get('api_files') == 0 and package.get('api_modified') is False, 'Invalid static package receipt')
    require(sha(archive.read_bytes()) == package.get('archive_sha256'), 'Archive digest changed')
    require(not directory.exists() and not directory.is_symlink(), 'Target release already exists; inspect its state')
    with tarfile.open(archive) as source:
        members = source.getmembers()
        require(len({member.name for member in members}) == len(members) and all(member.isfile() for member in members), 'Archive has duplicate paths or non-files')
        manifest = json.load(source.extractfile('canvas-deployment.json'))
        files = manifest_files(manifest)
        compatible(manifest, manifest)
        require(manifest['release_id'] == package['release_id'] == directory.name, 'Archive release mismatch')
        require(set(member.name for member in members) == files.keys() | {'canvas-deployment.json'}, 'Archive has undeclared/missing members')
        payload = {member.name: source.extractfile(member).read() for member in members}
        for name, entry in files.items():
            require(len(payload[name]) == entry['bytes'] and sha(payload[name]) == entry['sha256'], 'Archive member digest changed')
    # Validate the entire archive before creating a release. Never extract symlinks.
    directory.mkdir()
    for name, data in payload.items():
        target = directory / name
        require(target.resolve().is_relative_to(directory.resolve()), 'Extraction escapes target')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(0o644)
    return manifest, files
