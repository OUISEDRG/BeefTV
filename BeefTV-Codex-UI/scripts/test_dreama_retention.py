"""Retention tests create and delete temporary fixtures only."""
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import dreama_release as release
import dreama_retention as retention


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.tools = self.root / 'BeefTV-Codex-UI-tools'
        self.tools.mkdir()
        self.app = self.root / 'installed'
        self.app.mkdir()
        self.data = self.root / 'data'
        self.data.mkdir()
        self.policy = {'schemaVersion': 1, 'toolsRoot': str(self.tools), 'app': str(self.app),
                       'protectedPaths': [str(self.data)], 'cacheMaxAgeHours': 24}
        release.write_json(self.tools / 'dreama-retention.json', self.policy)

    def package(self, name, created, installed=False):
        path = self.tools / 'releases' / name
        file = path / 'app/BeefTV.exe'
        file.parent.mkdir(parents=True)
        file.write_bytes(name.encode())
        manifest = {'schemaVersion': 1, 'createdAt': created, 'sourceCommit': name,
                    'files': [{'area': 'app', 'path': 'BeefTV.exe', 'sha256': release.digest(file)}]}
        release.write_json(path / 'release.json', manifest)
        if installed:
            release.write_json(self.app / 'dreama-release.json', manifest)
        return path

    def backup(self, name, workspace=False, complete=True):
        path = self.tools / 'backups' / name
        area = 'data' if workspace else 'app'
        file = path / area / 'fixture.txt'
        file.parent.mkdir(parents=True)
        file.write_bytes(b'fixture')
        entry = {'area': area, 'path': 'fixture.txt', 'sha256': release.digest(file)}
        if not workspace:
            entry.update(existed=True, previousSha256=entry['sha256'])
        record = {'completed': complete, 'files': [entry]}
        if workspace:
            record.update(databases=[], restorePolicy='Never restore stale databases')
        else:
            record.update(schemaVersion=1, roots={'app': str(self.app), 'bridge': str(self.root/'bridge'),
                          'codex': str(self.root/'codex')}, data=str(self.data),
                          previousRelease={}, newRelease={})
        release.write_json(path / 'record.json', record)
        return path

    def execute(self, apply=False, busy=False):
        return retention.maintain(self.tools, apply=apply, process_probe=lambda: busy)

    def test_latest_final_and_installed_are_kept_preview_does_not_delete(self):
        old = self.package('BeefTV-old', '2026-11-01T00:00:00Z')
        current = self.package('BeefTV-current', '2026-11-02T00:00:00Z', installed=True)
        newest = self.package('BeefTV-new-final', '2026-11-03T00:00:00Z')
        plan = self.execute()
        self.assertEqual({row['path'] for row in plan['delete']}, {str(old)})
        self.assertTrue(old.exists())
        self.execute(apply=True)
        self.assertFalse(old.exists())
        self.assertTrue(current.exists())
        self.assertTrue(newest.exists())

    def test_final_wins_timestamp_tie_and_mtime_is_ignored(self):
        old = self.package('BeefTV-a', '2026-11-01T00:00:00Z')
        final = self.package('BeefTV-a-final', '2026-11-01T00:00:00Z', installed=True)
        os.utime(old, (time.time()+10000, time.time()+10000))
        self.execute(apply=True)
        self.assertFalse(old.exists())
        self.assertTrue(final.exists())

    def test_newest_complete_backup_each_kind_and_unknown_are_preserved(self):
        old = self.backup('dreama-20261101T000000-000001Z')
        newest = self.backup('dreama-20261102T000000-000001Z')
        workspace_old = self.backup('current-workspace-20261101T000000Z', workspace=True)
        workspace = self.backup('current-workspace-20261102T000000Z', workspace=True)
        incomplete = self.backup('dreama-20261103T000000-000001Z', complete=False)
        unknown = self.backup('manual-backup')
        self.execute(apply=True)
        self.assertFalse(old.exists())
        self.assertFalse(workspace_old.exists())
        self.assertTrue(all(p.exists() for p in (newest, workspace, incomplete, unknown)))

    def test_missing_installed_manifest_or_incomplete_new_package_never_prunes_releases(self):
        old = self.package('BeefTV-old', '2026-11-01T00:00:00Z')
        broken = self.package('BeefTV-broken', '2026-11-03T00:00:00Z')
        (broken/'app/BeefTV.exe').unlink()
        self.execute(apply=True)
        self.assertTrue(old.exists())
        self.assertTrue(broken.exists())

    def test_protected_path_and_git_tree_cannot_be_deleted(self):
        old = self.backup('dreama-20261101T000000-000001Z')
        self.backup('dreama-20261102T000000-000001Z')
        (old/'.git').write_text('gitdir: external')
        self.execute(apply=True)
        self.assertTrue(old.exists())
        (old/'.git').unlink()
        self.policy['protectedPaths'].append(str(old/'app'))
        release.write_json(self.tools/'dreama-retention.json', self.policy)
        self.execute(apply=True)
        self.assertTrue(old.exists())

    def test_manifest_escape_and_unknown_directory_are_preserved(self):
        old = self.package('BeefTV-old', '2026-11-01T00:00:00Z')
        self.package('BeefTV-new', '2026-11-03T00:00:00Z', installed=True)
        manifest = json.loads((old/'release.json').read_text())
        manifest['files'][0]['path'] = '../../data/fixture.txt'
        release.write_json(old/'release.json', manifest)
        self.execute(apply=True)
        self.assertTrue(old.exists())
        self.assertTrue(self.data.exists())

    def test_reparse_tree_is_preserved(self):
        old = self.backup('dreama-20261101T000000-000001Z')
        self.backup('dreama-20261102T000000-000001Z')
        original = retention.is_reparse
        with patch.object(retention, 'is_reparse', side_effect=lambda p: Path(p) == old/'app' or original(p)):
            self.execute(apply=True)
        self.assertTrue(old.exists())

    def test_busy_build_skips_all_pruning_and_cache_only_prunes_old_hash_files(self):
        cache = self.tools/'go-cache/00'
        cache.mkdir(parents=True)
        stale = cache/('0'*64+'-a')
        fresh = cache/('0'*63+'1-d')
        unknown = cache/'notes.txt'
        for file in (stale, fresh, unknown): file.write_bytes(b'cache')
        os.utime(stale, (time.time()-172800, time.time()-172800))
        self.execute(apply=True, busy=True)
        self.assertTrue(stale.exists())
        self.execute(apply=True)
        self.assertFalse(stale.exists())
        self.assertTrue(fresh.exists())
        self.assertTrue(unknown.exists())

    def test_wrong_tools_root_refused(self):
        self.policy['toolsRoot'] = str(self.root)
        release.write_json(self.tools/'dreama-retention.json', self.policy)
        with self.assertRaises(ValueError): self.execute(apply=True)

    def test_unlisted_payload_file_is_preserved(self):
        old = self.package('BeefTV-old', '2026-11-01T00:00:00Z')
        self.package('BeefTV-new', '2026-11-03T00:00:00Z', installed=True)
        (old/'app/uncommitted.py').write_text('preserve me')
        self.execute(apply=True)
        self.assertTrue(old.exists())

    def test_release_evidence_requires_exact_hash_inventory(self):
        old = self.package('BeefTV-old', '2026-11-01T00:00:00Z')
        self.package('BeefTV-new', '2026-11-03T00:00:00Z', installed=True)
        file = old/'evidence/verified.json'
        file.parent.mkdir(); file.write_text('{}')
        self.execute(apply=True)
        self.assertTrue(old.exists())
        self.policy['releaseExtraFiles'] = {old.name: {'evidence/verified.json': release.digest(file)}}
        release.write_json(self.tools/'dreama-retention.json', self.policy)
        self.execute(apply=True)
        self.assertFalse(old.exists())

    def test_workspace_database_must_exist_and_match_hash(self):
        old = self.backup('current-workspace-20261101T000000Z', workspace=True)
        new = self.backup('current-workspace-20261102T000000Z', workspace=True)
        record = json.loads((new/'record.json').read_text())
        record['databases'] = [{'area': 'data', 'path': 'missing.db', 'sha256': '0'*64}]
        release.write_json(new/'record.json', record)
        self.execute(apply=True)
        self.assertTrue(old.exists())
        self.assertTrue(new.exists())

    def test_workspace_database_sidecars_need_narrow_hash_enrollment(self):
        old = self.backup('current-workspace-20261101T000000Z', workspace=True)
        self.backup('current-workspace-20261102T000000Z', workspace=True)
        database = old/'data/fixture.db'; database.write_bytes(b'database fixture')
        sidecar = old/'data/fixture.db-wal'; sidecar.write_bytes(b'journal fixture')
        record = json.loads((old/'record.json').read_text())
        record['databases'] = [{'area': 'data', 'path': 'fixture.db', 'sha256': release.digest(database)}]
        release.write_json(old/'record.json', record)
        self.execute(apply=True)
        self.assertTrue(old.exists())
        self.policy['backupExtraFiles'] = {old.name: {'data/fixture.db-wal': release.digest(sidecar)}}
        release.write_json(self.tools/'dreama-retention.json', self.policy)
        self.execute(apply=True)
        self.assertFalse(old.exists())

    def test_legacy_backup_ancillary_files_need_explicit_inventory(self):
        old = self.backup('dreama-20261101T000000-000001Z')
        self.backup('dreama-20261102T000000-000001Z')
        file = old/'settings-key'; file.write_bytes(b'fixture')
        self.execute(apply=True)
        self.assertTrue(old.exists())
        self.policy['backupExtraFiles'] = {old.name: {'settings-key': release.digest(file)}}
        release.write_json(self.tools/'dreama-retention.json', self.policy)
        self.execute(apply=True)
        self.assertFalse(old.exists())

    def test_malformed_record_shapes_are_preserved_without_aborting(self):
        path = self.package('BeefTV-invalid', '2026-11-01T00:00:00Z')
        backup = self.backup('dreama-20261101T000000-000001Z')
        manifest = json.loads((path/'release.json').read_text())
        for value in ([], None, {**manifest, 'createdAt': 123}, {**manifest, 'files': [None]}):
            release.write_json(path/'release.json', value)
            release.write_json(backup/'record.json', value)
            self.execute(apply=True)
            self.assertTrue(path.exists())
            self.assertTrue(backup.exists())

    def test_mutation_after_planning_is_preserved(self):
        old = self.package('BeefTV-old', '2026-11-01T00:00:00Z')
        self.package('BeefTV-new', '2026-11-03T00:00:00Z', installed=True)
        plan = retention.plan_retention
        def changed(policy):
            result = plan(policy)
            (old/'app/new-work.py').write_text('preserve')
            return result
        with patch.object(retention, 'plan_retention', side_effect=changed): self.execute(apply=True)
        self.assertTrue(old.exists())

    @unittest.skipUnless(os.name == 'nt', 'Windows build lock interoperability')
    def test_powershell_build_lock_conflicts_with_maintenance_lock(self):
        environment = os.environ.copy()
        environment['RETENTION_TEST_LOCK'] = str(self.tools/retention.LOCK)
        command = "try { $s=[IO.File]::Open($env:RETENTION_TEST_LOCK,[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None); $s.Dispose(); exit 2 } catch { exit 0 }"
        with retention.operation_lock(self.tools):
            result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
                                    env=environment, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(result.returncode, 0)

    @unittest.skipUnless(os.name == 'nt', 'Windows junction protection')
    def test_real_junction_preserves_external_files(self):
        old = self.backup('dreama-20261101T000000-000001Z')
        self.backup('dreama-20261102T000000-000001Z')
        external = self.root/'external'; external.mkdir()
        (external/'keep.txt').write_text('unchanged')
        junction = old/'app/external'
        environment = os.environ.copy()
        environment.update(RETENTION_TEST_JUNCTION=str(junction), RETENTION_TEST_TARGET=str(external))
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
                                 "$ErrorActionPreference='Stop'; New-Item -ItemType Junction -Path $env:RETENTION_TEST_JUNCTION -Target $env:RETENTION_TEST_TARGET | Out-Null"],
                                env=environment, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
        try:
            self.assertTrue(retention.is_reparse(junction))
            self.execute(apply=True)
            self.assertTrue(old.exists())
            self.assertEqual((external/'keep.txt').read_text(), 'unchanged')
        finally:
            os.rmdir(junction)

    @unittest.skipUnless(os.name == 'nt', 'Windows build lock interoperability')
    def test_maintenance_refuses_powershell_held_build_lock(self):
        environment = os.environ.copy()
        environment['RETENTION_TEST_LOCK'] = str(self.tools/retention.LOCK)
        command = "$ErrorActionPreference='Stop'; $s=[IO.File]::Open($env:RETENTION_TEST_LOCK,[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None); try { Write-Output 'locked'; [Console]::ReadLine() | Out-Null } finally { $s.Dispose() }"
        with subprocess.Popen(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
                              env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, creationflags=subprocess.CREATE_NO_WINDOW) as process:
            try:
                self.assertEqual(process.stdout.readline().strip(), 'locked')
                with self.assertRaises(OSError): self.execute(apply=True)
            finally:
                process.communicate('\n', timeout=5)

    def test_failed_install_does_not_call_retention(self):
        with patch.object(release, 'retention_after_success') as hook:
            with self.assertRaises(FileNotFoundError):
                release.install(self.root/'missing', self.app, self.data, self.root/'bridge',
                                self.root/'codex', self.tools/'backups', process_check=lambda **_: None)
        hook.assert_not_called()

    def test_install_copy_failure_restores_and_does_not_prune(self):
        from test_dreama_release import ReleaseTests
        package, app, data, bridge, codex, _ = ReleaseTests().desktop_fixture(self.root)
        copy = shutil.copy2
        def fail_copy(source, target):
            if Path(source).is_relative_to(package): raise OSError('fixture copy failure')
            return copy(source, target)
        with patch.object(release, 'retention_after_success') as hook, patch.object(release.shutil, 'copy2', side_effect=fail_copy):
            with self.assertRaisesRegex(OSError, 'fixture copy failure'):
                release.install(package, app, data, bridge, codex, self.tools/'backups', process_check=lambda **_: None)
        hook.assert_not_called()
        self.assertEqual((app/'BeefTV.exe').read_bytes(), b'old-desktop')

    def test_successful_install_invokes_retention_after_completed_record(self):
        from test_dreama_release import ReleaseTests
        package, app, data, bridge, codex, _ = ReleaseTests().desktop_fixture(self.root)
        completed = []
        def inspect(directory, category):
            completed.extend(json.loads(file.read_text())['completed'] for file in Path(directory).glob('*/record.json'))
        with patch.object(release, 'retention_after_success', side_effect=inspect):
            backup = release.install(package, app, data, bridge, codex, self.tools/'backups', process_check=lambda **_: None)
        self.assertEqual(completed, [True])
        self.assertIn('supplementalHashes', json.loads((backup/'record.json').read_text()))


if __name__ == '__main__': unittest.main()
