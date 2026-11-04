"""Preview or apply local release retention; requires an explicit local policy."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import time
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path

POLICY = 'dreama-retention.json'
LOCK = '.dreama-retention.lock'
HELD_LOCKS = set()
DEPLOY_NAME = re.compile(r'dreama-(\d{8}T\d{6}-\d{6}Z)')
WORKSPACE_NAME = re.compile(r'current-workspace-(\d{8}T\d{6}Z)')
HASH_FILE = re.compile(r'[0-9a-f]{64}-[ad]')


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def is_reparse(path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def ordinary_path(path):
    path = Path(os.path.abspath(path))
    for part in (path, *path.parents):
        if part.exists() or part.is_symlink():
            if is_reparse(part):
                raise ValueError('Reparse point is outside retention authority: ' + str(part))
    return path


def load_policy(root):
    root = ordinary_path(root)
    if root.name != 'BeefTV-Codex-UI-tools' or not root.is_dir():
        raise ValueError('Retention requires the dedicated BeefTV-Codex-UI-tools directory')
    policy = read_json(ordinary_path(root / POLICY))
    if not isinstance(policy, dict) or policy.get('schemaVersion') != 1 or not isinstance(policy.get('toolsRoot'), str):
        raise ValueError('Invalid retention policy')
    if ordinary_path(policy['toolsRoot']) != root:
        raise ValueError('Retention policy toolsRoot does not match the requested directory')
    if not isinstance(policy.get('app'), str) or not Path(policy['app']).is_absolute():
        raise ValueError('Retention policy requires an absolute installed app path')
    if not isinstance(policy.get('protectedPaths'), list) or not policy['protectedPaths']:
        raise ValueError('Retention policy must explicitly protect live data/source paths')
    protected = [policy['app'], *policy['protectedPaths']]
    if any(not isinstance(path, str) or not Path(path).is_absolute() for path in protected):
        raise ValueError('Protected paths must be absolute')
    hours = policy.get('cacheMaxAgeHours', 24)
    if type(hours) is not int or hours < 1:
        raise ValueError('Cache expiry must be at least one hour')
    if any(not isinstance(policy.get(name, {}), dict) for name in ('releaseExtraFiles', 'backupExtraFiles')):
        raise ValueError('Invalid release metadata inventory')
    policy['protected'] = [Path(path).resolve() for path in protected]
    policy['root'] = root
    return policy


def exact_inventory(path, areas, expected):
    actual = {file.relative_to(path).as_posix() for area in areas
              for file in (path / area).rglob('*') if file.is_file()}
    if actual != expected:
        raise ValueError('Artifact has missing or unlisted payload files')


def safe_tree(path, policy):
    path = ordinary_path(path)
    root = policy['root']
    if path == root or not path.is_relative_to(root):
        raise ValueError('Deletion target escaped tools root')
    relative = path.relative_to(root)
    if relative.parts[0] not in {'releases', 'backups', 'go-cache'} or len(relative.parts) < 2:
        raise ValueError('Deletion target is not a retained artifact')
    for protected in policy['protected']:
        if path == protected or path.is_relative_to(protected) or protected.is_relative_to(path):
            raise ValueError('Deletion target overlaps a protected live path')
    if path.is_dir():
        for directory, folders, files in os.walk(path, followlinks=False):
            for name in [*folders, *files]:
                child = Path(directory) / name
                if name.casefold() in {'.git', '.hg', '.svn'} or is_reparse(child):
                    raise ValueError('Artifact contains a repository or reparse point')
    return path


def processes_busy():
    """Fail closed if the OS cannot establish compiler/build process state."""
    if os.name != 'nt':
        return True
    script = "$ErrorActionPreference='Stop'; @(Get-Process | Where-Object { $_.ProcessName -in @('go','compile','link','gcc','g++','cc1','cc1plus','collect2','ld','wails','bun') }).Count"
    try:
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                                capture_output=True, text=True, timeout=20,
                                creationflags=subprocess.CREATE_NO_WINDOW, check=True)
        return int(result.stdout.strip()) != 0
    except (OSError, ValueError, subprocess.SubprocessError):
        return True


@contextmanager
def operation_lock(root):
    root = ordinary_path(root)
    if root in HELD_LOCKS:
        yield
        return
    path = ordinary_path(root / LOCK)
    with path.open('a+b') as stream:
        if stream.tell() == 0:
            stream.write(b'0'); stream.flush()
        stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        HELD_LOCKS.add(root)
        try:
            yield
        finally:
            HELD_LOCKS.remove(root)
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def release_record(path, policy):
    from dreama_release import digest, member, verify_package
    if not path.name.startswith('BeefTV-'):
        raise ValueError('Unrecognized release directory')
    if any(child.name not in {'app', 'bridge', 'codex', 'release.json', 'evidence', '\u66f4\u65b0\u4ea4\u4ed8\u8bb0\u5f55.md'} for child in path.iterdir()):
        raise ValueError('Release contains unrecognized contents')
    manifest = read_json(path / 'release.json')
    if not isinstance(manifest, dict) or not isinstance(manifest.get('files'), list):
        raise ValueError('Invalid release record')
    if any(not isinstance(entry, dict) for entry in manifest['files']):
        raise ValueError('Invalid release member')
    if not isinstance(manifest.get('createdAt'), str):
        raise ValueError('Invalid release timestamp')
    created = datetime.fromisoformat(manifest['createdAt'].replace('Z', '+00:00'))
    if created.tzinfo is None:
        raise ValueError('Release timestamp has no timezone')
    verify_package(path, manifest)
    exact_inventory(path, {'app', 'bridge', 'codex'},
                    {entry['area'] + '/' + entry['path'] for entry in manifest['files']})
    extras = policy.get('releaseExtraFiles', {}).get(path.name, {})
    if not isinstance(extras, dict): raise ValueError('Invalid release metadata inventory')
    for name, expected in extras.items():
        if not isinstance(name, str) or not (name.startswith('evidence/') or name == '\u66f4\u65b0\u4ea4\u4ed8\u8bb0\u5f55.md'):
            raise ValueError('Unrecognized release metadata')
        file = member(path, name)
        if not file.is_file() or digest(file) != expected:
            raise ValueError('Release metadata is missing or has changed')
    actual_extras = {file.relative_to(path).as_posix() for file in path.rglob('*')
                     if file.is_file() and file.relative_to(path).parts[0] not in {'app', 'bridge', 'codex', 'release.json'}}
    if actual_extras != set(extras):
        raise ValueError('Release metadata must be explicitly inventoried in releaseExtraFiles')
    return (created.timestamp(), path.name.endswith('-final'), path.name), manifest


def backup_record(path, policy):
    from dreama_release import digest, member
    deploy = DEPLOY_NAME.fullmatch(path.name)
    workspace = WORKSPACE_NAME.fullmatch(path.name)
    if not deploy and not workspace:
        raise ValueError('Unrecognized backup directory')
    record = read_json(path / 'record.json')
    if not isinstance(record, dict) or record.get('completed') is not True or not isinstance(record.get('files'), list) or not record['files']:
        raise ValueError('Incomplete backup record')
    if deploy:
        if record.get('schemaVersion') != 1 or not isinstance(record.get('roots'), dict) or set(record['roots']) != {'app', 'bridge', 'codex'}:
            raise ValueError('Unrecognized deployment backup record')
        areas = {'app', 'bridge', 'codex'}
        allowed = areas | {'record.json', 'rollback.json', 'previous-release.json', 'open_ai_canvas.db',
                           'open_ai_canvas.db-shm', 'open_ai_canvas.db-wal', 'jobs.sqlite3',
                           'local-model-config.json.enc', 'settings-key', 'dreama-integration.json'}
        key = datetime.strptime(deploy[1], '%Y%m%dT%H%M%S-%fZ')
    else:
        if not isinstance(record.get('databases'), list) or not isinstance(record.get('restorePolicy'), str):
            raise ValueError('Unrecognized workspace backup record')
        areas = {'data', 'dreama-root', 'dreama-state', 'codex', 'provenance'}
        allowed = areas | {'record.json'}
        key = datetime.strptime(workspace[1], '%Y%m%dT%H%M%SZ')
    if any(child.name not in allowed for child in path.iterdir()):
        raise ValueError('Backup contains unrecognized contents')
    seen, expected_files = set(), set()
    entries = [*record['files'], *(record.get('databases', []) if workspace else [])]
    for entry in entries:
        if not isinstance(entry, dict): raise ValueError('Invalid backup member')
        area, name = entry.get('area'), entry.get('path')
        if area not in areas or not isinstance(name, str) or (area, name.casefold()) in seen:
            raise ValueError('Invalid backup member')
        seen.add((area, name.casefold()))
        file = member(path / area, name)
        if deploy and entry.get('existed') is False:
            continue
        expected_files.add(area + '/' + name)
        expected = entry.get('previousSha256') if deploy else entry.get('sha256')
        if not file.is_file() or digest(file) != expected:
            raise ValueError('Backup is incomplete or has changed')
    if workspace:
        extras = policy.get('backupExtraFiles', {}).get(path.name, {})
        if not isinstance(extras, dict): raise ValueError('Invalid workspace backup metadata')
        databases = {entry['area'] + '/' + entry['path'] for entry in record['databases']}
        for name, expected in extras.items():
            if not isinstance(name, str) or not name.endswith(('-shm', '-wal')) or name[:-4] not in databases:
                raise ValueError('Only declared database sidecars may be enrolled for workspace backups')
            file = member(path, name)
            if not file.is_file() or digest(file) != expected:
                raise ValueError('Workspace database sidecar is missing or has changed')
            expected_files.add(name)
    exact_inventory(path, areas, expected_files)
    if deploy:
        if not isinstance(record.get('previousRelease'), dict) or not isinstance(record.get('newRelease'), dict):
            raise ValueError('Invalid deployment release identity')
        if record['previousRelease'].get('schemaVersion') == 1 and read_json(path / 'previous-release.json') != record['previousRelease']:
            raise ValueError('Previous release record is missing or has changed')
        if record.get('desktopSchemaBefore', 0) > 0 and not (path / 'open_ai_canvas.db').is_file():
            raise ValueError('Missing desktop database backup')
        if not isinstance(record.get('ledgerBefore', {}), dict):
            raise ValueError('Invalid backup ledger record')
        if record.get('ledgerBefore', {}).get('version', 0) > 0 and not (path / 'jobs.sqlite3').is_file():
            raise ValueError('Missing bridge database backup')
        for name in ('open_ai_canvas.db', 'jobs.sqlite3'):
            file = path / name
            if file.exists():
                with closing(sqlite3.connect(file.as_uri() + '?mode=ro&immutable=1', uri=True)) as db:
                    if db.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                        raise ValueError('Corrupt database backup')
        hashes = record.get('supplementalHashes', policy.get('backupExtraFiles', {}).get(path.name, {}))
        if not isinstance(hashes, dict) or any(not isinstance(name, str) or '/' in name or '\\' in name for name in hashes):
            raise ValueError('Invalid supplemental backup manifest')
        actual = {file.name for file in path.iterdir() if file.is_file() and file.name not in {'record.json', 'rollback.json'}}
        if actual != set(hashes): raise ValueError('Unlisted or missing supplemental backup file; legacy backups need backupExtraFiles')
        for name, expected in hashes.items():
            if digest(path / name) != expected: raise ValueError('Supplemental backup file has changed')
    return 'deployment' if deploy else 'workspace', key


def plan_retention(policy, now=None):
    from dreama_release import digest
    now = time.time() if now is None else now
    root = policy['root']
    result = {'delete': [], 'keep': [], 'preserve': [], 'applied': False}
    groups = {'release': [], 'deployment': [], 'workspace': []}
    manifests = {}
    installed = None
    try:
        installed = read_json(Path(policy['app']) / 'dreama-release.json')
        if not isinstance(installed, dict) or installed.get('schemaVersion') != 1 or not installed.get('files'):
            installed = None
    except (OSError, ValueError):
        pass
    for category in ('releases', 'backups'):
        parent = ordinary_path(root / category)
        if not parent.exists(): continue
        for path in sorted(parent.iterdir()):
            try:
                safe_tree(path, policy)
                if not path.is_dir(): raise ValueError('Unrecognized non-directory')
                if category == 'releases':
                    key, manifest = release_record(path, policy)
                    groups['release'].append((key, path))
                    manifests[path] = manifest
                else:
                    kind, key = backup_record(path, policy)
                    groups[kind].append((key, path))
            except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
                result['preserve'].append({'path': str(path), 'reason': str(error)})
    for kind, members in groups.items():
        members.sort(reverse=True)
        for index, (_, path) in enumerate(members):
            reason = None
            if index == 0: reason = 'latest complete ' + kind
            if kind == 'release' and installed is None: reason = 'installed release identity unavailable'
            if kind == 'release' and manifests[path] == installed: reason = 'installed release'
            result['keep' if reason else 'delete'].append({'path': str(path), 'kind': kind,
                                                         'recordSha256': digest(path / ('release.json' if kind == 'release' else 'record.json')),
                                                         'reason': reason or 'superseded complete artifact'})
    cache = ordinary_path(root / 'go-cache')
    if cache.exists():
        expiry = now - policy.get('cacheMaxAgeHours', 24) * 3600
        for bucket in sorted(cache.iterdir()):
            if not re.fullmatch(r'[0-9a-f]{2}', bucket.name) or not bucket.is_dir(): continue
            try:
                safe_tree(bucket, policy)
                for path in bucket.iterdir():
                    if not HASH_FILE.fullmatch(path.name) or not path.name.startswith(bucket.name) or not path.is_file(): continue
                    if path.stat().st_mtime < expiry:
                        result['delete'].append({'path': str(path), 'kind': 'cache', 'reason': 'expired Go build cache'})
            except (OSError, ValueError) as error:
                result['preserve'].append({'path': str(bucket), 'reason': str(error)})
    return result


def maintain(tools_root, apply=False, process_probe=processes_busy):
    from dreama_release import digest
    policy = load_policy(tools_root)
    # Preview does not create even a lock file.
    if not apply:
        return plan_retention(policy)
    with operation_lock(policy['root']):
        if process_probe():
            return {'delete': [], 'keep': [], 'preserve': [], 'applied': False, 'skipped': 'build active or process state unavailable'}
        result = plan_retention(policy)
        deleted = []
        for row in result['delete']:
            # Revalidate targets after planning, before every destructive operation.
            if row['kind'] != 'cache' or len(deleted) % 256 == 0:
                if process_probe():
                    result['skipped'] = 'build started during maintenance'
                    break
            path = safe_tree(Path(row['path']), policy)
            if row['kind'] == 'cache':
                if path.stat().st_mtime >= time.time() - policy.get('cacheMaxAgeHours', 24) * 3600: continue
                path.unlink()
            else:
                try:
                    record_path = path / ('release.json' if row['kind'] == 'release' else 'record.json')
                    if digest(record_path) != row['recordSha256']:
                        raise ValueError('Artifact record changed after planning')
                    if row['kind'] == 'release': release_record(path, policy)
                    else: backup_record(path, policy)
                    shutil.rmtree(path)
                except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
                    result['preserve'].append({'path': str(path), 'reason': str(error)})
                    continue
            deleted.append(row)
        result['deleted'] = deleted
        result['applied'] = True
        return result


def configured_root(directory, category):
    directory = Path(directory).resolve()
    if directory.name != category or not (directory.parent / POLICY).is_file():
        return None
    return load_policy(directory.parent)['root']


@contextmanager
def release_operation(directory, category):
    root = configured_root(directory, category)
    if root is None:
        yield
    else:
        with operation_lock(root): yield


def after_success(directory, category):
    root = configured_root(directory, category)
    if root is None: return None
    return maintain(root, apply=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tools-root', required=True, type=Path)
    parser.add_argument('--apply', action='store_true', help='Delete only the planned recognized artifacts')
    parser.add_argument('--check-policy', action='store_true', help='Validate the local policy and lock path only')
    args = parser.parse_args()
    try:
        if args.check_policy:
            policy = load_policy(args.tools_root)
            ordinary_path(policy['root'] / LOCK)
            result = {'policyValid': True}
        else:
            result = maintain(args.tools_root, apply=args.apply)
    except (OSError, ValueError) as error:
        parser.exit(1, str(error) + '\n')
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == '__main__': main()
