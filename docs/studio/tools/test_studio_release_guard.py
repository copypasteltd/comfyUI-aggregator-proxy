"""Exercise real archive/hash/path validation with disposable workspace files."""
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

from studio_release_guard import CONTRACT, ReleaseRefused, compatible, entry_map, merge_entries, read_release, sha, unpack_verified

ROOT = Path(__file__).resolve().parents[2]


class ReleaseGuardTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT / '.local/studio146')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.release = self.root / 'canvas-20261030.146.1'

    def archive(self, corrupt=False, extra=None):
        payload = {'studio/index.html': b'<title>new</title>', 'studio/maintenance.html': b'<title>maintenance</title>', 'studio/licenses/infinite-canvas-MIT.txt': b'MIT'}
        manifest = {'format_version': 1, 'release_id': self.release.name, 'source_revision': 'a' * 40, 'source_sha256': 'b' * 64, 'api_modified': False, 'application_backend': False, 'virtualization_used': False, 'base': '/studio/', 'mode': 'managed', 'api_origin': 'http://43.165.167.179:39443', 'storage_contract': CONTRACT, 'files': [{'path': name, 'bytes': len(data), 'sha256': sha(data)} for name, data in payload.items()]}
        payload['canvas-deployment.json'] = json.dumps(manifest).encode()
        if corrupt: payload['studio/index.html'] = b'changed'
        archive = self.root / 'release.tar.gz'
        with tarfile.open(archive, 'w:gz') as output:
            for name, data in payload.items():
                member = tarfile.TarInfo(name); member.size = len(data); output.addfile(member, io.BytesIO(data))
            if extra: output.addfile(extra)
        package = {'passed': True, 'api_files': 0, 'api_modified': False, 'release_id': self.release.name, 'archive_sha256': sha(archive.read_bytes())}
        return archive, package

    def test_source_contract_and_runtime_database_share_the_verified_contract(self):
        source = json.loads((ROOT / 'infinite-canvas/web/src/services/studio-storage-contract.json').read_text(encoding='utf-8'))
        self.assertEqual(source, CONTRACT)
        runtime = (ROOT / 'infinite-canvas/web/src/services/studio-journal-db.ts').read_text(encoding='utf-8')
        self.assertIn('journalDatabaseVersion = storageContract.journal.version', runtime)

    def test_exact_contract_is_compatible(self):
        self.assertTrue(compatible({'storage_contract': CONTRACT}, {'storage_contract': CONTRACT}))

    def test_missing_or_old_contract_refuses(self):
        for old in [{}, {'storage_contract': {'journal': {'version': 4}}}]:
            with self.assertRaises(ReleaseRefused): compatible({'storage_contract': CONTRACT}, old)

    def test_same_database_version_with_unknown_record_version_refuses(self):
        for field in ['task_record', 'document_record', 'controlled_media_record', 'editor_record']:
            changed = {**CONTRACT, field: 2}
            with self.assertRaises(ReleaseRefused): compatible({'storage_contract': CONTRACT}, {'storage_contract': changed})

    def test_boolean_cannot_impersonate_a_numeric_record_version(self):
        with self.assertRaises(ReleaseRefused): compatible({'storage_contract': CONTRACT}, {'storage_contract': {**CONTRACT, 'task_record': True}})

    def test_absolute_traversal_backslash_and_noncanonical_paths_refuse(self):
        for name in ['/studio/a.js', 'studio/../api/a.js', 'studio\\a.js', 'studio//a.js', 'studio/./a.js', '../api/key']:
            with self.assertRaises(ReleaseRefused): entry_map([{'path': name, 'bytes': 0, 'sha256': 'a' * 64}])

    def test_duplicate_files_refuse(self):
        item = {'path': 'studio/index.html', 'bytes': 1, 'sha256': 'a' * 64}
        with self.assertRaises(ReleaseRefused): entry_map([item, item])

    def test_retention_cannot_replace_entry_html_or_unknown_resource(self):
        for name in ['studio/index.html', 'studio/assets/unhashed.js', 'studio/assets/new.bin']:
            with self.assertRaises(ReleaseRefused): entry_map([{'path': name, 'bytes': 1, 'sha256': 'a' * 64}], assets_only=True)

    def test_same_path_conflicting_content_refuses(self):
        with self.assertRaises(ReleaseRefused): merge_entries({'same': {'bytes': 1}}, {'same': {'bytes': 2}})

    def test_corrupt_member_is_rejected_before_release_creation(self):
        archive, package = self.archive(corrupt=True)
        with self.assertRaises(ReleaseRefused): unpack_verified(archive, package, self.release)
        self.assertFalse(self.release.exists())

    def test_symlink_archive_is_rejected_before_release_creation(self):
        member = tarfile.TarInfo('studio/linked'); member.type = tarfile.SYMTYPE; member.linkname = '/etc/passwd'
        archive, package = self.archive(extra=member)
        with self.assertRaises(ReleaseRefused): unpack_verified(archive, package, self.release)
        self.assertFalse(self.release.exists())

    def test_full_unpack_and_later_file_tampering(self):
        archive, package = self.archive()
        manifest, files = unpack_verified(archive, package, self.release)
        self.assertEqual(read_release(self.release)[1], files)
        (self.release / 'studio/index.html').write_bytes(b'tampered')
        with self.assertRaises(ReleaseRefused): read_release(self.release)


if __name__ == '__main__':
    unittest.main()
