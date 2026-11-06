"""Release safety tests use only temporary fixtures, never installed data."""
import importlib.util
import json
import sqlite3
import tempfile
import unittest
import zipfile
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('dreama_release', Path(__file__).with_name('dreama_release.py'))
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class ReleaseTests(unittest.TestCase):
    def desktop_fixture(self, root, previous_schema=28):
        package=root/'package';app=root/'app';bridge=root/'bridge';codex=root/'codex';data=root/'data'
        for path in (package/'app',app,bridge/'state',codex,data):path.mkdir(parents=True,exist_ok=True)
        (app/'BeefTV.exe').write_bytes(b'old-desktop');(package/'app/BeefTV.exe').write_bytes(b'new-desktop')
        previous={'ledgerSchemaVersion':2,'integrationVersion':1}
        if previous_schema is not None:previous['desktopSchemaVersion']=previous_schema
        release.write_json(app/'dreama-release.json',previous)
        manifest={'schemaVersion':1,'ledgerSchemaVersion':2,'desktopSchemaVersion':29,'integrationVersion':1,
                  'files':[{'area':'app','path':'BeefTV.exe','sha256':release.digest(package/'app/BeefTV.exe')}]}
        release.write_json(package/'release.json',manifest)
        with closing(sqlite3.connect(data/'open_ai_canvas.db')) as db, db:
            db.execute('create table local_schema_migrations(version integer primary key,name text,applied_at text)')
            db.execute("insert into local_schema_migrations values(28,'prior-schema','unchanged')")
            db.execute('create table tasks(id text,status text)')
        return package,app,data,bridge,codex,manifest

    def add_installed_member(self, package, root, manifest, area, name, content=b'stable-source'):
        source=package/area/name;source.parent.mkdir(parents=True,exist_ok=True);source.write_bytes(content)
        target=root/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(content)
        manifest['files'].append({'area':area,'path':name,'sha256':release.digest(source)})
        release.write_json(package/'release.json',manifest)
        return target

    def test_unchanged_bridge_can_keep_running_without_rewriting_its_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            package,app,data,bridge,codex,manifest=self.desktop_fixture(root)
            bridge_file=self.add_installed_member(package,bridge,manifest,'bridge','server.py')
            codex_file=self.add_installed_member(package,codex,manifest,'codex','server.py')
            real_copy=release.shutil.copy2
            def running_bridge_check(bridge=None):
                if bridge is not None: raise RuntimeError('Bridge is running')
            def copy_without_touching_running_sources(source,target,*args,**kwargs):
                if Path(target) in (bridge_file,codex_file):
                    self.fail('Unchanged running bridge source must not be rewritten')
                return real_copy(source,target,*args,**kwargs)
            with patch.object(release.shutil,'copy2',side_effect=copy_without_touching_running_sources):
                backup=release.install(package,app,data,bridge,codex,root/'backups',process_check=running_bridge_check)
            record=json.loads((backup/'record.json').read_text())
            self.assertTrue(record['completed'])
            self.assertEqual((app/'BeefTV.exe').read_bytes(),b'new-desktop')
            self.assertEqual(len(record['files']),3)
            self.assertEqual([item['path'] for item in record['files'] if item['changed']],['BeefTV.exe'])
            for item in record['files']:
                self.assertEqual(release.digest(backup/item['area']/item['path']),item['previousSha256'])
            self.assertTrue((backup/'open_ai_canvas.db').is_file())

    def test_changed_bridge_still_requires_stopping_before_install(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            package,app,data,bridge,codex,manifest=self.desktop_fixture(root)
            target=self.add_installed_member(package,bridge,manifest,'bridge','server.py')
            target.write_bytes(b'prior-bridge')
            def running_bridge_check(bridge=None):
                if bridge is not None: raise RuntimeError('Bridge is running')
            with self.assertRaisesRegex(RuntimeError,'Bridge is running'):
                release.install(package,app,data,bridge,codex,root/'backups',process_check=running_bridge_check)
            self.assertEqual((app/'BeefTV.exe').read_bytes(),b'old-desktop')
            self.assertEqual(target.read_bytes(),b'prior-bridge')
            self.assertFalse((root/'backups').exists())

    def test_unchanged_bridge_does_not_bypass_desktop_or_active_work_guards(self):
        for active in ('desktop','task','bridge'):
            with self.subTest(active=active), tempfile.TemporaryDirectory() as directory:
                root=Path(directory)
                package,app,data,bridge,codex,manifest=self.desktop_fixture(root)
                self.add_installed_member(package,bridge,manifest,'bridge','server.py')
                if active=='task':
                    with closing(sqlite3.connect(data/'open_ai_canvas.db')) as db, db:
                        db.execute("insert into tasks values('active','running')")
                if active=='bridge':
                    with closing(sqlite3.connect(bridge/'state/jobs.sqlite3')) as db, db:
                        db.execute('create table jobs(result text)')
                        db.execute('insert into jobs values(?)',(json.dumps({'status':'submitting'}),))
                def check_desktop(**_):
                    if active=='desktop': raise RuntimeError('Close BeefTV normally')
                with self.assertRaisesRegex((RuntimeError,ValueError),'Close BeefTV|active'):
                    release.install(package,app,data,bridge,codex,root/'backups',process_check=check_desktop)
                self.assertEqual((app/'BeefTV.exe').read_bytes(),b'old-desktop')
                self.assertFalse((root/'backups').exists())

    def test_final_verification_checks_unchanged_members_and_preserves_external_changes_on_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            package,app,data,bridge,codex,manifest=self.desktop_fixture(root)
            target=self.add_installed_member(package,codex,manifest,'codex','server.py')
            real_copy=release.shutil.copy2
            def change_untouched_member(source,destination,*args,**kwargs):
                result=real_copy(source,destination,*args,**kwargs)
                if Path(source)==package/'app/BeefTV.exe' and Path(destination)==app/'BeefTV.exe':
                    target.write_bytes(b'external-concurrent-edit')
                return result
            with patch.object(release.shutil,'copy2',side_effect=change_untouched_member):
                with self.assertRaisesRegex(ValueError,'Installed file hash mismatch'):
                    release.install(package,app,data,bridge,codex,root/'backups',process_check=lambda **_:None)
            self.assertEqual((app/'BeefTV.exe').read_bytes(),b'old-desktop')
            self.assertEqual(target.read_bytes(),b'external-concurrent-edit')
            record=json.loads(next((root/'backups').glob('*/record.json')).read_text())
            self.assertFalse(record['completed'])

    def test_install_refuses_older_or_unknown_desktop_reader_before_replacing_files(self):
        for reader in (28,None,True,'29'):
            with self.subTest(reader=reader), tempfile.TemporaryDirectory() as directory:
                root=Path(directory)
                package,app,data,bridge,codex,manifest=self.desktop_fixture(root)
                with closing(sqlite3.connect(data/'open_ai_canvas.db')) as db, db:
                    db.execute("insert into local_schema_migrations values(29,'local-admin-audit','new')")
                if reader is None:manifest.pop('desktopSchemaVersion')
                else:manifest['desktopSchemaVersion']=reader
                release.write_json(package/'release.json',manifest)
                before=release.digest(data/'open_ai_canvas.db')
                with self.assertRaisesRegex(ValueError,'desktop schema'):
                    release.install(package,app,data,bridge,codex,root/'backups',process_check=lambda **_:None)
                self.assertEqual((app/'BeefTV.exe').read_bytes(),b'old-desktop')
                self.assertEqual(release.digest(data/'open_ai_canvas.db'),before)
                self.assertFalse((root/'backups').exists())

    def test_rollback_refuses_desktop_schema_downgrade_before_replacing_files(self):
        for previous in (28,None):
            with self.subTest(previous=previous), tempfile.TemporaryDirectory() as directory:
                root=Path(directory)
                package,app,data,bridge,codex,_=self.desktop_fixture(root,previous)
                backup=release.install(package,app,data,bridge,codex,root/'backups',process_check=lambda **_:None)
                with closing(sqlite3.connect(data/'open_ai_canvas.db')) as db, db:
                    db.execute("insert into local_schema_migrations values(29,'local-admin-audit','new')")
                    db.execute("insert into tasks values('new-task','succeeded')")
                before=release.digest(data/'open_ai_canvas.db')
                with self.assertRaisesRegex(ValueError,'desktop schema'):
                    release.rollback(backup,process_check=lambda **_:None)
                self.assertEqual((app/'BeefTV.exe').read_bytes(),b'new-desktop')
                self.assertEqual(release.digest(data/'open_ai_canvas.db'),before)
                self.assertFalse((backup/'rollback.json').exists())

    def test_package_records_desktop_schema_from_its_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);repo=root/'source';bridge=root/'bridge';codex=root/'codex';build=root/'build'
            (repo/'backend/internal/database').mkdir(parents=True);bridge.mkdir();codex.mkdir()
            (repo/'backend/internal/database/local_schema.go').write_text('package database\nconst CurrentSchemaVersion int64 = 73\n')
            release.write_json(bridge/'dreama-modalities-manifest.json',{'id':'dreama-modalities','version':'1.2.0'})
            for path in (repo,bridge):
                release.git(path,'init')
                release.git(path,'add','.')
                release.git(path,'-c','user.name=Release fixture','-c','user.email=fixture@example.invalid','commit','-m','fixture')
            release.git(repo,'tag','v1.0.0')
            for name in ('BeefTV.exe','cli/beeftv.exe','agent-host/server.mjs','agent-host/runtime/node.exe','agent-host/media-runtime/ffmpeg.exe'):
                target=build/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(b'fixture')
            (build/'plugin-packages').mkdir()
            release.make_package(repo,bridge,codex,build,root/'package','v1.0.0','fixture')
            manifest=json.loads((root/'package/release.json').read_text())
            self.assertEqual(manifest.get('desktopSchemaVersion'),73)

    def test_plugin_bundle_and_activation_use_formal_business_api(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); bridge = root/'bridge'; app=root/'app'; bridge.mkdir(); app.mkdir()
            manifest={'id':'dreama-modalities','version':'1.2.0','contributes':{'providers':[{'id':'dreama-videos-async'}]}}
            (bridge/'dreama-modalities-manifest.json').write_text(json.dumps(manifest))
            plugin=release.bundle_dreama_plugin(bridge,app)
            with zipfile.ZipFile(app/plugin['path']) as archive:
                self.assertEqual(json.loads(archive.read('manifest.json')),manifest)
            backup=root/'backup';backup.mkdir()
            record={'newRelease':{'dreamaPlugin':plugin},'completed':True}
            release.write_json(backup/'record.json',record)
            release.write_json(app/'dreama-release.json',record['newRelease'])
            tools=[{'id':'list','method':'GET','path':'/plugins'}, {'id':'config','method':'GET','path':'/workspace/model-config'},
                   {'id':'ensure','method':'POST','path':'/integrations/dreama/v1/ensure'}, {'id':'refresh','method':'POST','path':'/integrations/dreama/v1/catalog/refresh'}]
            calls=[]; plugin_enabled=False
            def fake_run(args):
                calls.append(args)
                if args[1:]==['business','discover']:return json.dumps(tools)
                if args[1:3]==['business','upload']:return '{}'
                tool=args[args.index('--tool')+1]
                if tool=='list':return json.dumps({'plugins':[{'manifest':manifest,'sha256':plugin['sha256'],'status':'enabled' if plugin_enabled else 'disabled'}]})
                if tool=='config':return json.dumps({'revision':7,'config':{}})
                if tool=='ensure':return json.dumps({'schemaVersion':1,'integrationVersion':1})
                if tool=='refresh':return json.dumps({'revision':8,'updatedChannels':['dreama-test']})
                self.fail('Unexpected API')
            with self.assertRaisesRegex(ValueError,'disabled'):
                release.activate(app,backup,'dreama-test',runner=fake_run)
            self.assertFalse(json.loads((backup/'record.json').read_text()).get('activated'))
            self.assertFalse(any('refresh' in args for args in calls))
            plugin_enabled=True
            result=release.activate(app,backup,'dreama-test',runner=fake_run)
            self.assertEqual(result['catalogRevision'],8)
            self.assertTrue(json.loads((backup/'record.json').read_text())['activated'])
            refresh_call=next(args for args in calls if 'refresh' in args)
            self.assertEqual(json.loads(refresh_call[refresh_call.index('--params')+1])['body']['expectedRevision'],7)
            self.assertFalse(any('/tasks' in str(call) for call in calls))

    def test_stable_tags_ignore_prerelease_and_sort_numerically(self):
        tags = release.stable_tags('a\trefs/tags/v2.9.0\nb\trefs/tags/v2.10.0\nc\trefs/tags/v3.0.0-rc1\nd\trefs/tags/v2.10.0^{}')
        self.assertEqual(tags, ['v2.9.0', 'v2.10.0'])

    def test_manifest_rejects_tampering_and_path_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'app').mkdir()
            file = root / 'app/BeefTV.exe'; file.write_bytes(b'fixture')
            manifest = {'schemaVersion': 1, 'files': [{'area': 'app', 'path': 'BeefTV.exe', 'sha256': release.digest(file)}]}
            release.verify_package(root, manifest)
            file.write_bytes(b'changed')
            with self.assertRaises(ValueError): release.verify_package(root, manifest)
            manifest['files'][0]['path'] = '../outside'
            with self.assertRaises(ValueError): release.verify_package(root, manifest)

    def test_install_and_rollback_preserve_ledger_and_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); package = root / 'package'; app = root / 'app'; bridge = root / 'bridge'; codex = root / 'codex'; data = root / 'data'
            for path in (package / 'app', package / 'bridge', app, bridge / 'state', codex, data): path.mkdir(parents=True, exist_ok=True)
            (app / 'BeefTV.exe').write_bytes(b'old-exe'); (package / 'app/BeefTV.exe').write_bytes(b'new-exe')
            (bridge / 'server.py').write_bytes(b'old-bridge'); (package / 'bridge/server.py').write_bytes(b'new-bridge')
            (data / 'local-model-config.json').write_bytes(b'opaque-credentials')
            with closing(sqlite3.connect(bridge / 'state/jobs.sqlite3')) as db, db: db.execute('create table jobs(key text,digest text,result text)')
            with closing(sqlite3.connect(data / 'open_ai_canvas.db')) as db, db:
                db.execute('create table tasks(id text,status text)'); db.execute('create table canvas_projects(id text,payload_json text)')
            manifest={'schemaVersion':1,'customization':'test','ledgerSchemaVersion':2,'files':[{'area':area,'path':name,'sha256':release.digest(package / area / name)} for area,name in [('app','BeefTV.exe'),('bridge','server.py')]]}
            (package / 'release.json').write_text(json.dumps(manifest))
            backup=release.install(package,app,data,bridge,codex,root / 'backups',process_check=lambda **_:None)
            self.assertEqual((app / 'BeefTV.exe').read_bytes(),b'new-exe')
            self.assertEqual((data / 'local-model-config.json').read_bytes(),b'opaque-credentials')
            with closing(sqlite3.connect(bridge / 'state/jobs.sqlite3')) as db, db: db.execute('insert into jobs values(?,?,?)',('new','hash',json.dumps({'status':'succeeded','taskId':'remote','schemaVersion':2})))
            # Old unversioned bridge cannot safely take over a newly migrated ledger.
            with self.assertRaises(ValueError): release.rollback(backup,process_check=lambda **_:None)
            with closing(sqlite3.connect(bridge / 'state/jobs.sqlite3')) as db: self.assertEqual(db.execute('select count(*) from jobs').fetchone()[0],1)

    def test_active_work_blocks_install_but_paused_unknown_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); data=root/'data';bridge=root/'bridge';data.mkdir();(bridge/'state').mkdir(parents=True)
            with closing(sqlite3.connect(bridge/'state/jobs.sqlite3')) as db, db:
                db.execute('create table jobs(result text)');db.execute('insert into jobs values(?)',(json.dumps({'status':'submission_unknown'}),))
            release.require_idle(data,bridge)
            with closing(sqlite3.connect(bridge/'state/jobs.sqlite3')) as db, db:
                db.execute('insert into jobs values(?)',(json.dumps({'status':'submitting'}),))
            with self.assertRaises(ValueError):release.require_idle(data,bridge)

    def test_pending_deadline_without_quote_cannot_bypass_release_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);data=root/'data';bridge=root/'bridge';data.mkdir();(bridge/'state').mkdir(parents=True)
            with closing(sqlite3.connect(bridge/'state/jobs.sqlite3')) as db, db:
                db.execute('create table jobs(result text)');db.execute('insert into jobs values(?)',(json.dumps({'status':'prepared','autoExecuteAt':1}),))
            with self.assertRaises(ValueError):release.require_idle(data,bridge)

    def test_compatible_rollback_restores_code_but_keeps_new_task_facts(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);package=root/'package';app=root/'app';bridge=root/'bridge';codex=root/'codex';data=root/'data'
            for path in (package/'app',app,bridge/'state',codex,data):path.mkdir(parents=True,exist_ok=True)
            (app/'BeefTV.exe').write_bytes(b'old-compatible');(package/'app/BeefTV.exe').write_bytes(b'new-compatible')
            release.write_json(app/'dreama-release.json',{'ledgerSchemaVersion':2,'desktopSchemaVersion':29,'integrationVersion':1})
            release.write_json(package/'release.json',{'schemaVersion':1,'ledgerSchemaVersion':2,'desktopSchemaVersion':29,'integrationVersion':1,'files':[{'area':'app','path':'BeefTV.exe','sha256':release.digest(package/'app/BeefTV.exe')}]})
            with closing(sqlite3.connect(data/'open_ai_canvas.db')) as db, db:
                db.execute('create table local_schema_migrations(version integer primary key)')
                db.execute('insert into local_schema_migrations values(29)')
                db.execute('create table admin_audit_events(id text primary key, action text)')
            with closing(sqlite3.connect(bridge/'state/jobs.sqlite3')) as db, db:
                db.execute('create table jobs(result text)');db.execute('pragma user_version=2')
            backup=release.install(package,app,data,bridge,codex,root/'backups',process_check=lambda **_:None)
            with closing(sqlite3.connect(bridge/'state/jobs.sqlite3')) as db, db:
                db.execute('insert into jobs values(?)',(json.dumps({'status':'completed','schemaVersion':2,'taskId':'new-paid-result'}),))
            with closing(sqlite3.connect(data/'open_ai_canvas.db')) as db, db:
                db.execute("insert into admin_audit_events values('new-audit','plugin.install')")
            desktop_before=release.digest(data/'open_ai_canvas.db')
            release.rollback(backup,process_check=lambda **_:None)
            self.assertEqual((app/'BeefTV.exe').read_bytes(),b'old-compatible')
            with closing(sqlite3.connect(bridge/'state/jobs.sqlite3')) as db:
                self.assertEqual(json.loads(db.execute('select result from jobs').fetchone()[0])['taskId'],'new-paid-result')
            self.assertFalse(json.loads((backup/'rollback.json').read_text())['databaseRestored'])
            self.assertEqual(release.digest(data/'open_ai_canvas.db'),desktop_before)

if __name__=='__main__':unittest.main()
