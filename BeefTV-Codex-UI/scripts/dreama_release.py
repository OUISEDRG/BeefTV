"""Local Dreama distribution maintenance. Never generates media or edits user DB rows."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

AREAS = ('app', 'bridge', 'codex')
TERMINAL = {'completed', 'succeeded', 'failed', 'cancelled', 'canceled', 'unknown', 'submission_unknown', 'download_failed', 'binding_verification_required'}

def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def write_json(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

def run(args, cwd=None, env=None):
    result = subprocess.run([str(x) for x in args], cwd=cwd, env=env, capture_output=True, text=True, encoding='utf-8', errors='replace', check=False,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    if result.returncode:
        raise RuntimeError(f'{args[0]} failed ({result.returncode}): {result.stderr[-2000:]}')
    return result.stdout.strip()

def git(repo, *args):
    return run(['git', '-C', repo, *args])

def stable_tags(output):
    tags = set()
    for line in output.splitlines():
        name = line.split()[-1] if line.split() else ''
        match = re.fullmatch(r'refs/tags/(v\d+\.\d+\.\d+)(?:\^\{\})?', name)
        if match: tags.add(match[1])
    return sorted(tags, key=lambda value: tuple(map(int, value[1:].split('.'))))

def extended_path(path):
    text = str(path)
    if os.name != 'nt' or text.startswith('\\\\?\\'):
        return path
    if text.startswith('\\\\'):
        return Path('\\\\?\\UNC\\' + text[2:])
    return Path('\\\\?\\' + text)


def member(root, name):
    relative = PurePosixPath(name)
    if not name or '\\' in name or ':' in name or relative.is_absolute() or '..' in relative.parts or str(relative) != name:
        raise ValueError('Invalid package member path')
    root = extended_path(Path(root).absolute()).resolve()
    result = root.joinpath(*relative.parts).resolve()
    if not result.is_relative_to(root) or result == root:
        raise ValueError('Package member escaped target root')
    text = str(result)
    if os.name == 'nt' and text.startswith('\\\\?\\'):
        plain = '\\\\' + text[8:] if text.startswith('\\\\?\\UNC\\') else text[4:]
        # Windows also reserves space for an appended short name when creating directories.
        if len(plain) < 248:
            return Path(plain)
    return result

def verify_package(root, manifest):
    if manifest.get('schemaVersion') != 1 or not isinstance(manifest.get('files'), list) or not manifest['files']:
        raise ValueError('Invalid release manifest')
    seen = set()
    for item in manifest['files']:
        area, name = item.get('area'), item.get('path')
        if area not in AREAS or not isinstance(name, str) or (area, name.casefold()) in seen:
            raise ValueError('Invalid or duplicate release member')
        seen.add((area, name.casefold()))
        file = member(Path(root) / area, name)
        if not file.is_file() or digest(file) != item.get('sha256'):
            raise ValueError('Release artifact hash mismatch: ' + area + '/' + name)

def package_paths(root):
    paths, aliases = set(), set()
    pending = [root]
    while pending:
        path = pending.pop()
        info = path.lstat()
        if (stat.S_ISLNK(info.st_mode)
                or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise ValueError(f'Linked release member: {path}')
        name = path.relative_to(root).as_posix()
        if name.casefold() in aliases:
            raise ValueError(f'Colliding release member: {name}')
        aliases.add(name.casefold())
        if stat.S_ISDIR(info.st_mode):
            pending.extend(path.iterdir())
        elif stat.S_ISREG(info.st_mode):
            paths.add(name)
        else:
            raise ValueError(f'Invalid release member: {name}')
    return paths

def load_verified_manifest(root, manifest_pin):
    files = package_paths(root)
    if 'release.json' not in files:
        raise ValueError('Release manifest hash mismatch')
    content = (root / 'release.json').read_bytes()
    if hashlib.sha256(content).hexdigest() != manifest_pin:
        raise ValueError('Release manifest hash mismatch')
    manifest = json.loads(content)
    verify_package(root, manifest)
    declared = {'release.json', *(f"{item['area']}/{item['path']}" for item in manifest['files'])}
    if extras := files - declared:
        raise ValueError(f'Found unexpected release member: {min(extras)}')
    return manifest

def ledger_state(bridge):
    path = Path(bridge) / 'state/jobs.sqlite3'
    if not path.exists(): return {'version': 0, 'active': {}, 'unresolved': {}}
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        version = db.execute('PRAGMA user_version').fetchone()[0]
        active, unresolved = {}, {}
        for row in db.execute('SELECT result FROM jobs'):
            job = json.loads(row[0]); version = max(version, int(job.get('schemaVersion', 0)))
            status = job.get('status', '')
            if status in {'unknown', 'submission_unknown', 'download_failed', 'binding_verification_required'}:
                unresolved[status] = unresolved.get(status, 0) + 1
            elif status == 'prepared' and (not job.get('autoExecuteAt') or job.get('autoExecuteError') or job.get('autoAttemptedQuoteId') and job.get('autoAttemptedQuoteId') == job.get('quoteId')):
                continue
            elif status not in TERMINAL:
                active[status] = active.get(status, 0) + 1
        return {'version': version, 'active': active, 'unresolved': unresolved}

def require_idle(data, bridge):
    state = ledger_state(bridge)
    if state['active']: raise ValueError('Bridge has active work; wait before replacing: ' + json.dumps(state['active']))
    path = Path(data) / 'open_ai_canvas.db'
    if path.exists():
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'tasks' in tables:
                count = db.execute("SELECT count(*) FROM tasks WHERE status NOT IN ('succeeded','failed','cancelled','canceled')").fetchone()[0]
                if count: raise ValueError('BeefTV has active tasks; wait before replacing')
    return state

def process_check(bridge=None):
    if os.name == 'nt':
        environment = os.environ.copy()
        environment['BEEFTV_RELEASE_BRIDGE'] = str(bridge or '')
        run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
             "if (Get-Process BeefTV -ErrorAction SilentlyContinue) { throw 'Close BeefTV normally before installing.' }; "
             "if ($env:BEEFTV_RELEASE_BRIDGE) { $bridgeScript = Join-Path $env:BEEFTV_RELEASE_BRIDGE 'server.py'; "
             "$supervisorScript = Join-Path $env:BEEFTV_RELEASE_BRIDGE 'supervisor.py'; "
             "if (Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^python(w)?\\.exe$' -and $_.CommandLine -and "
             "($_.CommandLine.IndexOf($bridgeScript, [StringComparison]::OrdinalIgnoreCase) -ge 0 -or "
             "$_.CommandLine.IndexOf($supervisorScript, [StringComparison]::OrdinalIgnoreCase) -ge 0) }) { throw 'Stop the idle bridge before replacing its source.' } }"], env=environment)

def backup_sqlite(source, target):
    if not source.exists(): return
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as db, closing(sqlite3.connect(target)) as copy:
        db.backup(copy)

def installed_release(app):
    path = Path(app) / 'dreama-release.json'
    return json.loads(path.read_text('utf-8')) if path.exists() else {'ledgerSchemaVersion': 0, 'integrationVersion': 0}

def require_desktop_compatible(data, manifest):
    reader = manifest.get('desktopSchemaVersion', 0)
    if type(reader) is not int or reader < 0:
        raise ValueError('Invalid desktop schema reader version in release manifest')
    path = Path(data).resolve() / 'open_ai_canvas.db'
    version = 0
    if path.exists():
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='local_schema_migrations'").fetchone():
                version = db.execute('SELECT COALESCE(MAX(version),0) FROM local_schema_migrations').fetchone()[0]
    if type(version) is not int or version < 0:
        raise ValueError('Invalid desktop schema migration ledger; preserve the database for recovery')
    if version > reader:
        raise ValueError(f'Release cannot read desktop schema {version} (supported: {reader}); preserve the database and use a compatible recovery release')
    return version

def require_codex_idle(codex, manifest):
    reader = manifest.get('codexLedgerSchemaVersion', 0)
    if type(reader) is not int or reader < 0:
        raise ValueError('Invalid Codex image ledger reader version')
    path = Path(codex) / 'state/image-jobs.sqlite3'
    if path.exists():
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version > reader:
                raise ValueError('Release cannot read the Codex image ledger; preserve it for recovery')
            active = db.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running','cancel_requested') LIMIT 1").fetchone()
            if active:
                raise ValueError('Codex has active image jobs; wait before replacing')


def install(package, app, data, bridge, codex, backup_root, process_check=process_check, *, manifest_pin=None):
    package, app, data, bridge, codex = (Path(path).resolve() for path in (package, app, data, bridge, codex))
    roots = {'app': app, 'bridge': bridge, 'codex': codex}
    for left in [*roots.values(), data]:
        for right in [*roots.values(), data]:
            if left != right and left.is_relative_to(right): raise ValueError('Installation roots must be separate')
    if len({*roots.values(), data}) != 4: raise ValueError('Installation roots must be distinct')
    if manifest_pin is not None:
        manifest = load_verified_manifest(package, manifest_pin)
    else:
        manifest = json.loads((package / 'release.json').read_text('utf-8'))
        if 'codexCommit' in manifest:
            raise ValueError('A dual-Bridge install requires an external manifest pin')
        verify_package(package, manifest)
    changed = set()
    for item in manifest['files']:
        target = member(roots[item['area']], item['path'])
        if not target.is_file() or digest(target) != item['sha256']:
            changed.add((item['area'], item['path']))
    process_check(bridge=bridge if any(area == 'bridge' for area, _ in changed) else None)
    if 'codexCommit' in manifest:
        process_check(bridge=codex if any(area == 'codex' for area, _ in changed) else None)
        require_codex_idle(codex, manifest)
    state = require_idle(data, bridge)
    if state['version'] > manifest.get('ledgerSchemaVersion', 0): raise ValueError('Release cannot read the current task ledger')
    desktop_schema = require_desktop_compatible(data, manifest)
    backup = Path(backup_root).resolve() / datetime.now(timezone.utc).strftime('dreama-%Y%m%dT%H%M%S-%fZ')
    backup.mkdir(parents=True)
    record = {'schemaVersion': 1, 'roots': {k: str(v) for k, v in roots.items()}, 'data': str(data),
              'previousRelease': installed_release(app), 'newRelease': manifest,
              'ledgerBefore': state, 'desktopSchemaBefore': desktop_schema, 'files': [], 'completed': False}
    for item in manifest['files']:
        target = member(roots[item['area']], item['path'])
        entry = {**item, 'existed': target.exists(), 'changed': (item['area'], item['path']) in changed}
        if target.exists():
            saved = member(backup / item['area'], item['path']); saved.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(target, saved)
            entry['previousSha256'] = digest(saved)
        record['files'].append(entry)
    backup_sqlite(data / 'open_ai_canvas.db', backup / 'open_ai_canvas.db')
    backup_sqlite(bridge / 'state/jobs.sqlite3', backup / 'jobs.sqlite3')
    if 'codexCommit' in manifest:
        backup_sqlite(codex / 'state/image-jobs.sqlite3', backup / 'image-jobs.sqlite3')
    for source, name in [(data / 'dreama-integration.json', 'dreama-integration.json'),
                         (app / 'dreama-release.json', 'previous-release.json')]:
        if source.exists(): shutil.copy2(source, backup / name)
    write_json(backup / 'record.json', record)
    try:
        for item in manifest['files']:
            if (item['area'], item['path']) not in changed: continue
            target = member(roots[item['area']], item['path']); target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(member(package / item['area'], item['path']), target)
        for item in manifest['files']:
            target = member(roots[item['area']], item['path'])
            if not target.is_file() or digest(target) != item['sha256']:
                raise ValueError('Installed file hash mismatch: ' + item['area'] + '/' + item['path'])
        write_json(data / 'dreama-integration.json', {'schemaVersion': 1, 'pythonExecutable': sys.executable,
                   'bridgeDirectory': str(bridge), 'origin': 'http://127.0.0.1:18765'})
        write_json(app / 'dreama-release.json', manifest)
    except BaseException:
        restore_files(backup, record, changed_only=True)
        raise
    record['completed'] = True; write_json(backup / 'record.json', record)
    return backup

def restore_files(backup, record, changed_only=False):
    roots = {key: Path(value).resolve() for key, value in record['roots'].items()}
    # Validate all restoration inputs before replacing the first file.
    entries = [item for item in record['files'] if not changed_only or item.get('changed', True)]
    for item in entries:
        if item['existed']:
            saved = member(backup / item['area'], item['path'])
            if not saved.is_file() or digest(saved) != item['previousSha256']:
                raise ValueError('Backup integrity mismatch')
    for item in entries:
        target = member(roots[item['area']], item['path'])
        if item['existed']:
            saved = member(backup / item['area'], item['path'])
            target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(saved, target)
        elif target.exists(): target.unlink()
    for backup_name, target in [('dreama-integration.json', Path(record['data']) / 'dreama-integration.json'),
                                ('previous-release.json', roots['app'] / 'dreama-release.json')]:
        saved = backup / backup_name
        if saved.exists(): shutil.copy2(saved, target)
        elif target.exists(): target.unlink()

def rollback(backup, process_check=process_check, *, recovery_package=None, recovery_pin=None):
    backup = Path(backup).resolve(); record = json.loads((backup / 'record.json').read_text('utf-8'))
    if not record['completed'] or installed_release(record['roots']['app']) != record['newRelease']:
        raise ValueError('The installed release does not match the rollback bundle')
    if (recovery_package is None) != (recovery_pin is None):
        raise ValueError('A compatible recovery package and its external manifest pin are both required')
    if recovery_package is not None:
        recovery_package = Path(recovery_package).resolve()
        recovery = load_verified_manifest(recovery_package, recovery_pin)
        current = record['newRelease']
        recovered_files = {(item['area'], item['path']): item['sha256'] for item in recovery['files']}
        if not {(item['area'], item['path']) for item in current['files']} <= recovered_files.keys():
            raise ValueError('Use a complete recovery release covering every installed release member')
        plugins = [recovery.get(plugin) for plugin in ('dreamaPlugin', 'codexPlugin')]
        if not recovery.get('codexCommit') or plugins != [current.get(plugin) for plugin in ('dreamaPlugin', 'codexPlugin')] or any(
                not plugin or recovered_files.get(('app', plugin['path'])) != plugin['sha256'] for plugin in plugins):
            raise ValueError('The recovery release must keep both registered Bridge plugins')
        roots = record['roots']
        recovery_backup = install(recovery_package, roots['app'], record['data'], roots['bridge'], roots['codex'],
                                  backup.parent, process_check=process_check, manifest_pin=recovery_pin)
        write_json(backup / 'rollback.json', {'completed': True, 'databaseRestored': False,
                                             'ledgerPreserved': True, 'recoveryBackup': str(recovery_backup)})
        return
    process_check(bridge=Path(record['roots']['bridge'])); state = require_idle(Path(record['data']), Path(record['roots']['bridge']))
    previous = record['previousRelease']
    if 'codexCommit' in record['newRelease']:
        codex = Path(record['roots']['codex'])
        process_check(bridge=codex)
        require_codex_idle(codex, previous)
    require_desktop_compatible(record['data'], previous)
    if record.get('activationStarted') and previous.get('dreamaPlugin') != record['newRelease'].get('dreamaPlugin'):
        raise ValueError('Plugin registration changed; use a compatible recovery release instead of restoring a stale registry database')
    if state['version'] > previous.get('ledgerSchemaVersion', 0):
        raise ValueError('Previous bridge cannot read the current ledger; preserve it and use a compatible recovery release')
    dbpath = Path(record['data']) / 'open_ai_canvas.db'
    if previous.get('integrationVersion', 0) < 1 and dbpath.exists():
        with closing(sqlite3.connect(dbpath.as_uri() + '?mode=ro', uri=True)) as db:
            for row in db.execute('SELECT payload_json FROM canvas_projects'):
                if json.loads(row[0]).get('integrations', {}).get('dreama'):
                    raise ValueError('Previous application does not support existing Dreama canvas bindings')
    restore_files(backup, record)
    # Never restore a stale task/canvas DB over facts created after installation.
    write_json(backup / 'rollback.json', {'completed': True, 'databaseRestored': False, 'ledgerPreserved': True})

def git_bytes(repo, *args):
    result = subprocess.run(['git', '-C', str(repo), *args], capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr.decode('utf-8', errors='replace'))
    return result.stdout


def export_sources(repo, destination, revision):
    for entry in git_bytes(repo, 'ls-tree', '-r', '-z', revision).split(b'\0'):
        if not entry:
            continue
        metadata, raw_name = entry.split(b'\t', 1)
        mode, kind, blob = metadata.decode('ascii').split()
        name = raw_name.decode('utf-8')
        if name.startswith(('tests/', 'docs/', 'state/')) or name in {'config.binding.json', 'execution-policy.json'}:
            continue
        if not name.endswith(('.py', '.json', '.js', '.html', '.ps1', '.txt')) and name != 'plugin/codex-images/README.md':
            continue
        if kind != 'blob' or mode not in {'100644', '100755'}:
            raise ValueError('Invalid committed Bridge source: ' + name)
        target = member(destination, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(git_bytes(repo, 'cat-file', 'blob', blob))

def bundle_dreama_plugin(bridge, app):
    manifest = json.loads((Path(bridge) / 'dreama-modalities-manifest.json').read_text('utf-8'))
    if manifest.get('id') != 'dreama-modalities' or not manifest.get('version'):
        raise ValueError('Invalid Dreama plugin manifest')
    path = 'dreama-integration/dreama-modalities.beeftv-plugin'
    target = member(app, path); target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        entry = zipfile.ZipInfo('manifest.json', (2026, 11, 1, 0, 0, 0)); entry.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(entry, json.dumps(manifest, ensure_ascii=False, indent=2).encode('utf-8'))
    return {'id': manifest['id'], 'version': manifest['version'], 'path': path, 'sha256': digest(target)}


def bundle_codex_plugin(codex, app):
    path = 'plugin-packages/codex-images.beeftv-plugin'
    target = member(app, path)
    if target.exists():
        raise ValueError('The desktop bundle already contains a Codex plugin')
    # Run the existing pure packer from the exported source, excluding workspace state.
    run([sys.executable, '-I', '-B', '-c',
         'from pathlib import Path; import sys; sys.path.insert(0, str(Path.cwd())); '
         'from install_images import pack; Path(sys.argv[1]).write_bytes(pack())',
         target], cwd=codex)
    with zipfile.ZipFile(target) as archive:
        manifest = json.loads(archive.read('manifest.json'))
    return {'id': manifest['id'], 'version': manifest['version'], 'path': path, 'sha256': digest(target)}

def activate(app, backup, channel_id, runner=run):
    """Run after launching the installed desktop; uses its authenticated CLI only."""
    app, backup = Path(app).resolve(), Path(backup).resolve()
    record = json.loads((backup / 'record.json').read_text('utf-8'))
    manifest = installed_release(app)
    if not record.get('completed') or record['newRelease'] != manifest:
        raise ValueError('Installation record does not match the running release')
    plugin = manifest['dreamaPlugin']; package = member(app, plugin['path'])
    if digest(package) != plugin['sha256']: raise ValueError('Installed plugin package hash mismatch')
    cli = str(app / 'cli/beeftv.exe')
    tools = json.loads(runner([cli, 'business', 'discover']))
    def call(method, path, params):
        matches = [tool for tool in tools if tool['method'] == method and tool['path'] == path]
        if len(matches) != 1: raise ValueError('Required business API unavailable: ' + method + ' ' + path)
        return json.loads(runner([cli, 'business', 'call', '--tool', matches[0]['id'], '--params', json.dumps(params),
                                  '--operation-id', 'dreama-release-' + plugin['sha256'][:20] + '-' + matches[0]['id']]))
    record['activationStarted'] = True; write_json(backup / 'record.json', record)
    runner([cli, 'business', 'upload', '--target', 'plugin', '--file', str(package), '--operation-id', 'dreama-plugin-' + plugin['sha256']])
    registered = call('GET', '/plugins', {})
    matches = [item for item in registered.get('plugins', []) if item.get('manifest', {}).get('id') == plugin['id']]
    if len(matches) != 1 or matches[0]['manifest'].get('version') != plugin['version'] or matches[0].get('sha256') != plugin['sha256']:
        raise ValueError('Registered Dreama plugin does not match the release')
    if matches[0].get('status') != 'enabled':
        raise ValueError('Dreama plugin is disabled or unavailable; enable it explicitly in the plugin center before activation')
    status = call('POST', '/integrations/dreama/v1/ensure', {'body': {'channelId': channel_id}})
    if status.get('schemaVersion') != 1: raise ValueError('Unexpected bridge integration protocol')
    config = call('GET', '/workspace/model-config', {})
    refreshed = call('POST', '/integrations/dreama/v1/catalog/refresh', {'body': {'channelId': channel_id, 'expectedRevision': config['revision']}})
    result = {'pluginVersion': plugin['version'], 'pluginSHA256': plugin['sha256'], 'catalogRevision': refreshed['revision'],
              'updatedChannels': refreshed['updatedChannels'], 'bridgeIntegrationVersion': status.get('integrationVersion')}
    record['activated'] = True; record['activation'] = result; write_json(backup / 'record.json', record)
    return result

def make_package(repo, bridge, codex, build, output, tag, customization, *, pin_codex=False, build_receipt=None):
    repo, bridge, codex, build, output = (Path(value).resolve() for value in (repo, bridge, codex, build, output))
    if output.exists(): raise ValueError('Package output must be a new directory')
    repositories = (repo, bridge, codex) if pin_codex else (repo, bridge)
    if any(git(path, 'status', '--porcelain') for path in repositories):
        raise ValueError('Commit reviewed source before packaging')
    if not re.fullmatch(r'v\d+\.\d+\.\d+', tag): raise ValueError('Use a stable upstream tag')
    upstream = git(repo, 'rev-parse', '--verify', f'refs/tags/{tag}^{{commit}}')
    source = git(repo, 'rev-parse', 'HEAD')
    bridge_source = git(bridge, 'rev-parse', 'HEAD')
    try:
        git(repo, 'merge-base', '--is-ancestor', upstream, source)
    except RuntimeError as error:
        raise ValueError('Selected upstream must be an ancestor of the desktop source') from error
    schema_source = git(repo, 'show', f'{source}:backend/internal/database/local_schema.go')
    schema_versions = re.findall(r'^const CurrentSchemaVersion int64 = ([1-9][0-9]*)\s*$', schema_source, re.MULTILINE)
    if len(schema_versions) != 1: raise ValueError('Cannot determine desktop schema reader version from source')
    ledger_source = git(bridge, 'show', f'{bridge_source}:media_delivery.py')
    ledger_versions = re.findall(r'^LEDGER_SCHEMA_VERSION = ([1-9][0-9]*)\s*$', ledger_source, re.MULTILINE)
    if len(ledger_versions) != 1: raise ValueError('Cannot determine the Dreama ledger reader version from source')
    for required in ['BeefTV.exe', 'cli/beeftv.exe', 'agent-host/server.mjs', 'agent-host/runtime/node.exe', 'agent-host/media-runtime/ffmpeg.exe', 'plugin-packages']:
        if not (build / required).exists(): raise ValueError('Incomplete app bundle: ' + required)
    output.mkdir(parents=True); shutil.copytree(build, output / 'app')
    export_sources(bridge, output / 'bridge', bridge_source)
    dreama_plugin = bundle_dreama_plugin(output / 'bridge', output / 'app')
    (output / 'codex').mkdir()
    manifest = {'schemaVersion': 1, 'integrationVersion': 1, 'ledgerSchemaVersion': int(ledger_versions[0]),
                'desktopSchemaVersion': int(schema_versions[0]),
                'customization': customization, 'upstreamTag': tag, 'upstreamCommit': upstream,
                'sourceCommit': source, 'bridgeCommit': bridge_source,
                'dreamaPlugin': dreama_plugin,
                'createdAt': datetime.now(timezone.utc).isoformat(), 'files': []}
    if build_receipt is not None:
        manifest['buildReceiptSha256'] = build_receipt
    if pin_codex:
        codex_source = git(codex, 'rev-parse', 'HEAD')
        export_sources(codex, output / 'codex', codex_source)
        ledger_source = (output / 'codex/image_ledger.py').read_text('utf-8')
        ledger_versions = re.findall(r'^SCHEMA_VERSION = ([1-9][0-9]*)\s*$', ledger_source, re.MULTILINE)
        if len(ledger_versions) != 1:
            raise ValueError('Cannot determine the Codex image ledger reader version')
        manifest['codexCommit'] = codex_source
        manifest['codexLedgerSchemaVersion'] = int(ledger_versions[0])
        manifest['codexPlugin'] = bundle_codex_plugin(output / 'codex', output / 'app')
    else:
        for path in sorted(codex.glob('*')):
            if path.is_file() and path.suffix in {'.py', '.ps1', '.html', '.js'}:
                shutil.copy2(path, output / 'codex' / path.name)
    for area in AREAS:
        for path in sorted((output / area).rglob('*')):
            if path.is_file(): manifest['files'].append({'area': area, 'path': path.relative_to(output / area).as_posix(), 'sha256': digest(path)})
    write_json(output / 'release.json', manifest); verify_package(output, manifest)
    return {'package': str(output), 'files': len(manifest['files']), 'sourceCommit': manifest['sourceCommit']}

def main():
    parser = argparse.ArgumentParser(description=__doc__); commands = parser.add_subparsers(dest='command', required=True)
    check = commands.add_parser('check-upstream'); check.add_argument('--repository', type=Path, required=True)
    upgrade = commands.add_parser('prepare-upgrade'); upgrade.add_argument('--repository', type=Path, required=True); upgrade.add_argument('--tag', required=True); upgrade.add_argument('--directory', type=Path, required=True)
    package = commands.add_parser('package')
    for name in ('repository', 'bridge', 'codex', 'build', 'output'): package.add_argument('--' + name, type=Path, required=True)
    package.add_argument('--tag', required=True); package.add_argument('--customization', required=True)
    deploy = commands.add_parser('install')
    for name in ('package', 'app', 'data', 'bridge', 'codex', 'backups'): deploy.add_argument('--' + name, type=Path, required=True)
    deploy.add_argument('--manifest-sha256', required=True)
    revert = commands.add_parser('rollback'); revert.add_argument('--backup', type=Path, required=True)
    revert.add_argument('--recovery-package', type=Path)
    revert.add_argument('--recovery-manifest-sha256')
    activation = commands.add_parser('activate'); activation.add_argument('--app', type=Path, required=True); activation.add_argument('--backup', type=Path, required=True); activation.add_argument('--channel-id', required=True)
    verify = commands.add_parser('verify'); verify.add_argument('--package', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'check-upstream':
        remote = git(args.repository, 'remote', 'get-url', 'origin'); tags = stable_tags(git(args.repository, 'ls-remote', '--tags', remote))
        result = {'upstream': remote, 'stableTags': tags, 'latestStable': tags[-1] if tags else None, 'installedSource': git(args.repository, 'rev-parse', 'HEAD')}
    elif args.command == 'prepare-upgrade':
        if not re.fullmatch(r'v\d+\.\d+\.\d+', args.tag) or args.directory.exists(): raise ValueError('Use a stable tag and a new isolated directory')
        if git(args.repository, 'status', '--porcelain'): raise ValueError('Commit or preserve source changes before integration')
        if git(args.repository, 'rev-parse', '--is-shallow-repository') == 'true': git(args.repository, 'fetch', '--unshallow', 'origin')
        git(args.repository, 'fetch', 'origin', 'tag', args.tag)
        branch = 'dreama/integrate-' + args.tag + '-' + datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')
        git(args.repository, 'worktree', 'add', '-b', branch, args.directory, 'HEAD')
        try:
            git(args.directory, 'merge', '--no-edit', args.tag); result = {'directory': str(args.directory), 'branch': branch, 'merged': True, 'verified': False, 'deployed': False}
        except RuntimeError:
            result = {'directory': str(args.directory), 'branch': branch, 'merged': False, 'conflicts': git(args.directory, 'diff', '--name-only', '--diff-filter=U').splitlines(), 'deployed': False}
    elif args.command == 'package': result = make_package(args.repository, args.bridge, args.codex, args.build, args.output, args.tag, args.customization)
    elif args.command == 'install':
        backup = install(args.package, args.app, args.data, args.bridge, args.codex, args.backups,
                         manifest_pin=args.manifest_sha256)
        result = {'backup': str(backup), 'installed': True}
    elif args.command == 'rollback':
        rollback(args.backup, recovery_package=args.recovery_package, recovery_pin=args.recovery_manifest_sha256)
        result = {'rolledBack': True, 'ledgerPreserved': True}
    elif args.command == 'activate': result = activate(args.app, args.backup, args.channel_id)
    else: verify_package(args.package, json.loads((args.package / 'release.json').read_text('utf-8'))); result = {'verified': True}
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == '__main__': main()
