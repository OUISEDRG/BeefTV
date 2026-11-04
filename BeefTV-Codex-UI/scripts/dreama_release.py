"""Local Dreama distribution maintenance. Never generates media or edits user DB rows."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

AREAS = ('app', 'bridge', 'codex')
LEDGER_READER = 2
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

def member(root, name):
    relative = PurePosixPath(name)
    if not name or '\\' in name or ':' in name or relative.is_absolute() or '..' in relative.parts or str(relative) != name:
        raise ValueError('Invalid package member path')
    root = Path(root).resolve()
    result = root.joinpath(*relative.parts).resolve()
    if not result.is_relative_to(root) or result == root:
        raise ValueError('Package member escaped target root')
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
             "$bridgeScript = Join-Path $env:BEEFTV_RELEASE_BRIDGE 'server.py'; "
             "if (Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^python(w)?\\.exe$' -and $_.CommandLine -and $_.CommandLine.IndexOf($bridgeScript, [StringComparison]::OrdinalIgnoreCase) -ge 0 }) { throw 'Stop the idle bridge before replacing its source.' }"], env=environment)

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

def retention_after_success(directory, category):
    from dreama_retention import after_success
    try:
        return after_success(directory, category)
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
        # A failed cleanup must not turn a completed installation into a retry.
        print('Retention skipped: ' + str(error), file=sys.stderr)
        return None

def install(package, app, data, bridge, codex, backup_root, process_check=process_check):
    from dreama_retention import release_operation
    with release_operation(backup_root, 'backups'):
        return _install(package, app, data, bridge, codex, backup_root, process_check)

def _install(package, app, data, bridge, codex, backup_root, process_check):
    package, app, data, bridge, codex = (Path(path).resolve() for path in (package, app, data, bridge, codex))
    roots = {'app': app, 'bridge': bridge, 'codex': codex}
    for left in [*roots.values(), data]:
        for right in [*roots.values(), data]:
            if left != right and left.is_relative_to(right): raise ValueError('Installation roots must be separate')
    if len({*roots.values(), data}) != 4: raise ValueError('Installation roots must be distinct')
    manifest = json.loads((package / 'release.json').read_text('utf-8'))
    verify_package(package, manifest)
    process_check(bridge=bridge); state = require_idle(data, bridge)
    if state['version'] > manifest.get('ledgerSchemaVersion', 0): raise ValueError('Release cannot read the current task ledger')
    desktop_schema = require_desktop_compatible(data, manifest)
    backup = Path(backup_root).resolve() / datetime.now(timezone.utc).strftime('dreama-%Y%m%dT%H%M%S-%fZ')
    backup.mkdir(parents=True)
    record = {'schemaVersion': 1, 'roots': {k: str(v) for k, v in roots.items()}, 'data': str(data),
              'previousRelease': installed_release(app), 'newRelease': manifest,
              'ledgerBefore': state, 'desktopSchemaBefore': desktop_schema, 'files': [], 'completed': False}
    for item in manifest['files']:
        target = member(roots[item['area']], item['path'])
        entry = {**item, 'existed': target.exists()}
        if target.exists():
            saved = member(backup / item['area'], item['path']); saved.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(target, saved)
            entry['previousSha256'] = digest(saved)
        record['files'].append(entry)
    backup_sqlite(data / 'open_ai_canvas.db', backup / 'open_ai_canvas.db')
    backup_sqlite(bridge / 'state/jobs.sqlite3', backup / 'jobs.sqlite3')
    for source, name in [(data / 'local-model-config.json', 'local-model-config.json.enc'), (data / '.settings-key', 'settings-key'),
                         (data / 'dreama-integration.json', 'dreama-integration.json'), (app / 'dreama-release.json', 'previous-release.json')]:
        if source.exists(): shutil.copy2(source, backup / name)
    record['supplementalHashes'] = {path.name: digest(path) for path in backup.iterdir() if path.is_file()}
    write_json(backup / 'record.json', record)
    try:
        for item in manifest['files']:
            target = member(roots[item['area']], item['path']); target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(member(package / item['area'], item['path']), target)
            if digest(target) != item['sha256']: raise ValueError('Installed file hash mismatch')
        write_json(data / 'dreama-integration.json', {'schemaVersion': 1, 'pythonExecutable': sys.executable,
                   'bridgeDirectory': str(bridge), 'origin': 'http://127.0.0.1:18765'})
        write_json(app / 'dreama-release.json', manifest)
    except BaseException:
        restore_files(backup, record)
        raise
    record['completed'] = True; write_json(backup / 'record.json', record)
    retention_after_success(backup_root, 'backups')
    return backup

def restore_files(backup, record):
    roots = {key: Path(value).resolve() for key, value in record['roots'].items()}
    for item in record['files']:
        target = member(roots[item['area']], item['path'])
        if item['existed']:
            saved = member(backup / item['area'], item['path'])
            if digest(saved) != item['previousSha256']: raise ValueError('Backup integrity mismatch')
            target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(saved, target)
        elif target.exists(): target.unlink()
    for backup_name, target in [('dreama-integration.json', Path(record['data']) / 'dreama-integration.json'),
                                ('previous-release.json', roots['app'] / 'dreama-release.json')]:
        saved = backup / backup_name
        if saved.exists(): shutil.copy2(saved, target)
        elif target.exists(): target.unlink()

def rollback(backup, process_check=process_check):
    from dreama_retention import release_operation
    with release_operation(Path(backup).parent, 'backups'):
        return _rollback(backup, process_check)

def _rollback(backup, process_check):
    backup = Path(backup).resolve(); record = json.loads((backup / 'record.json').read_text('utf-8'))
    process_check(bridge=Path(record['roots']['bridge'])); state = require_idle(Path(record['data']), Path(record['roots']['bridge']))
    previous = record['previousRelease']
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

def export_sources(repo, destination):
    for name in git(repo, 'ls-files').splitlines():
        if name.startswith(('tests/', 'docs/')) or name.endswith(('.md', '.cmd')): continue
        if name in {'config.binding.json', 'execution-policy.json'} or not name.endswith(('.py', '.json', '.js', '.html', '.ps1', '.txt')): continue
        source = member(repo, name); target = member(destination, name)
        target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(source, target)

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

def activate(app, backup, channel_id, runner=run):
    """Run after launching the installed desktop; uses its authenticated CLI only."""
    from dreama_retention import release_operation
    with release_operation(Path(backup).parent, 'backups'):
        return _activate(app, backup, channel_id, runner)

def _activate(app, backup, channel_id, runner):
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

def make_package(repo, bridge, codex, build, output, tag, customization):
    from dreama_retention import release_operation
    with release_operation(Path(output).parent, 'releases'):
        return _make_package(repo, bridge, codex, build, output, tag, customization)

def _make_package(repo, bridge, codex, build, output, tag, customization):
    repo, bridge, codex, build, output = (Path(value).resolve() for value in (repo, bridge, codex, build, output))
    if output.exists(): raise ValueError('Package output must be a new directory')
    if git(repo, 'status', '--porcelain') or git(bridge, 'status', '--porcelain'):
        raise ValueError('Commit reviewed source before packaging')
    if not re.fullmatch(r'v\d+\.\d+\.\d+', tag): raise ValueError('Use a stable upstream tag')
    upstream = git(repo, 'rev-parse', tag + '^{commit}')
    schema_source = (repo / 'backend/internal/database/local_schema.go').read_text('utf-8')
    schema_versions = re.findall(r'^const CurrentSchemaVersion int64 = ([1-9][0-9]*)\s*$', schema_source, re.MULTILINE)
    if len(schema_versions) != 1: raise ValueError('Cannot determine desktop schema reader version from source')
    for required in ['BeefTV.exe', 'cli/beeftv.exe', 'agent-host/server.mjs', 'agent-host/runtime/node.exe', 'agent-host/media-runtime/ffmpeg.exe', 'plugin-packages']:
        if not (build / required).exists(): raise ValueError('Incomplete app bundle: ' + required)
    output.mkdir(parents=True); shutil.copytree(build, output / 'app')
    export_sources(bridge, output / 'bridge')
    dreama_plugin = bundle_dreama_plugin(bridge, output / 'app')
    (output / 'codex').mkdir()
    for path in sorted(codex.glob('*')):
        if path.is_file() and path.suffix in {'.py', '.ps1', '.html', '.js'}: shutil.copy2(path, output / 'codex' / path.name)
    manifest = {'schemaVersion': 1, 'integrationVersion': 1, 'ledgerSchemaVersion': LEDGER_READER,
                'desktopSchemaVersion': int(schema_versions[0]),
                'customization': customization, 'upstreamTag': tag, 'upstreamCommit': upstream,
                'sourceCommit': git(repo, 'rev-parse', 'HEAD'), 'bridgeCommit': git(bridge, 'rev-parse', 'HEAD'),
                'dreamaPlugin': dreama_plugin,
                'createdAt': datetime.now(timezone.utc).isoformat(), 'files': []}
    for area in AREAS:
        for path in sorted((output / area).rglob('*')):
            if path.is_file(): manifest['files'].append({'area': area, 'path': path.relative_to(output / area).as_posix(), 'sha256': digest(path)})
    write_json(output / 'release.json', manifest); verify_package(output, manifest)
    retention_after_success(output.parent, 'releases')
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
    revert = commands.add_parser('rollback'); revert.add_argument('--backup', type=Path, required=True)
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
    elif args.command == 'install': result = {'backup': str(install(args.package, args.app, args.data, args.bridge, args.codex, args.backups)), 'installed': True}
    elif args.command == 'rollback': rollback(args.backup); result = {'rolledBack': True, 'ledgerPreserved': True}
    elif args.command == 'activate': result = activate(args.app, args.backup, args.channel_id)
    else: verify_package(args.package, json.loads((args.package / 'release.json').read_text('utf-8'))); result = {'verified': True}
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == '__main__': main()
