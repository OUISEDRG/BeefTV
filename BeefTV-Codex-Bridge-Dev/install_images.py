"""Install the Codex image plugin and encrypted channel while BeefTV is closed.

Preview and package creation never read credentials. Installation does not submit
generation requests or modify the plugin database; BeefTV registers the package
from its executable's plugin-packages directory at the next start.
"""
import argparse
import copy
import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import zipfile

from install_codex import (DEFAULT_DATA, DEFAULT_PORT, DEFAULT_ROOT,
                           assert_beeftv_closed, decode, encode)

DEFAULT_APP = Path('D:/BeefTv')
PLUGIN_SOURCE = Path(__file__).resolve().parent / 'plugin/codex-images'
PLUGIN_ID = 'codex-images'
PROVIDER_ID = MODEL_ID = 'codex-image'
CHANNEL_ID = 'codex-local-image'
MODEL_KEY = CHANNEL_ID + '::' + MODEL_ID
PACKAGE_NAME = PLUGIN_ID + '.beeftv-plugin'
AUTHOR = 'Local Codex Bridge'
RATIOS = ['auto', '9:16', '16:9', '1:1', '3:4', '4:3', '2:3', '3:2', '21:9']
RESOLUTIONS = ['1k', '2k', '4k', '8k']


def build_channel(token, port=DEFAULT_PORT):
    if not isinstance(token, str) or not token.strip():
        raise ValueError('本机桥接令牌不能为空。')
    if not isinstance(port, int) or isinstance(port, bool) or not 1024 <= port <= 65535:
        raise ValueError('本机桥接端口无效。')
    return {
        'id': CHANNEL_ID, 'name': 'Codex 内置生图', 'scope': 'user',
        'enabled': True, 'pinned': False, 'baseUrl': f'http://127.0.0.1:{port}',
        'apiFormat': 'openai', 'interfaceType': PROVIDER_ID, 'apiKey': token,
        'headers': [], 'concurrencyLimit': 1, 'models': [MODEL_ID],
        'modelProfiles': [{
            'model': MODEL_ID, 'displayName': 'Codex 内置生图',
            'description': '使用已登录 Codex 的生图能力及额度；单张输出。分辨率为生成目标，实际尺寸由模型决定，不自动放大图片。',
            'capability': 'image', 'protocol': PROVIDER_ID,
            'referenceMediaTransport': {'image': 'inline'},
            'imageCapabilitiesVersion': 'codex-local-image/v3',
            'capabilityConfig': {'version': 1, 'image': {
                'references': {'promptMaxChars': 30000, 'maxImages': 4,
                               'maxImageBytes': 8 * 1024 * 1024, 'maskSupported': False},
                'size': {'parameter': 'aspect_ratio', 'values': list(RATIOS),
                         'default': 'auto', 'allowCustom': False},
                # Declared tiers use BeefTV's quality field, then become resolution targets.
                'quality': {'supported': True, 'values': list(RESOLUTIONS), 'default': '1k'},
                'transparentBackground': {'supported': True, 'default': False},
                'responseFormat': {'supported': False}, 'outputFormat': {'supported': False},
                'maxOutputs': 1, 'fixedOutputs': 1, 'generationOutputs': [1],
            }},
        }],
    }


def merge(document, token, port=DEFAULT_PORT, *, make_default=False):
    doc = copy.deepcopy(document)
    config = doc['config']
    if not isinstance(config.get('channels'), list) or any(not isinstance(c, dict) for c in config['channels']):
        raise ValueError('模型渠道配置损坏。')
    fresh = build_channel(token, port)
    indices = [i for i, c in enumerate(config['channels']) if c.get('id') == CHANNEL_ID]
    if len(indices) > 1:
        raise ValueError('Codex 生图渠道标识重复，已停止。')
    if indices:
        old = config['channels'][indices[0]]
        if (old.get('baseUrl') != fresh['baseUrl'] or old.get('apiKey') != token
                or old.get('interfaceType') != PROVIDER_ID):
            raise ValueError('同名生图渠道不属于本桥接，已停止覆盖。')
        old.update(copy.deepcopy(fresh))
    else:
        config['channels'].append(fresh)
    for field in ('models', 'imageModels'):
        items = config.get(field, [])
        if not isinstance(items, list):
            raise ValueError('模型列表配置损坏。')
        config[field] = [x for x in items if not isinstance(x, str) or not x.startswith(CHANNEL_ID + '::')]
        config[field].append(MODEL_KEY)
    if make_default:
        config['imageModel'] = MODEL_KEY
    allowed = {'channels', 'models', 'imageModels'} | ({'imageModel'} if make_default else set())
    if config.get('imageModel') == MODEL_KEY and 'quality' in config and config['quality'] not in RESOLUTIONS:
        config['quality'] = RESOLUTIONS[0]
        allowed.add('quality')
    assert {k: v for k, v in config.items() if k not in allowed} == {
        k: v for k, v in document['config'].items() if k not in allowed}
    assert [c for c in config['channels'] if c.get('id') != CHANNEL_ID] == [
        c for c in document['config']['channels'] if c.get('id') != CHANNEL_ID]
    return doc


def package_manifest(data):
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if archive.namelist().count('manifest.json') != 1:
                raise ValueError('插件包缺少唯一 manifest.json。')
            return json.loads(archive.read('manifest.json'))
    except (zipfile.BadZipFile, KeyError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError('插件包无效。') from exc


def validate_owned_package(data):
    manifest = package_manifest(data)
    providers = manifest.get('contributes', {}).get('providers', [])
    if (manifest.get('apiVersion') != 'beeftv.plugin/v2' or manifest.get('id') != PLUGIN_ID
            or manifest.get('author') != AUTHOR or len(providers) != 1
            or providers[0].get('id') != PROVIDER_ID):
        raise ValueError('同名插件不属于本机 Codex 生图集成，已停止覆盖。')
    return manifest


def pack(source=PLUGIN_SOURCE):
    """Produce a reproducible archive without credentials or build timestamps."""
    source = Path(source)
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name in ('manifest.json', 'README.md'):
            entry = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o644 << 16
            archive.writestr(entry, (source / name).read_bytes())
    result = output.getvalue()
    validate_owned_package(result)
    return result


def digest(data):
    return hashlib.sha256(data).hexdigest() if data is not None else None


def read_optional(path):
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def stage(directory, data, prefix):
    with tempfile.NamedTemporaryFile(mode='wb', prefix=prefix, suffix='.tmp',
                                     dir=directory, delete=False) as file:
        path = Path(file.name)
        try:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        except BaseException:
            file.close()
            path.unlink(missing_ok=True)
            raise
    return path


def backup(path, data):
    with path.open('xb') as file:
        file.write(data)
        file.flush()
        os.fsync(file.fileno())


def check_conflicts(package_dir, own_target):
    for path in package_dir.glob('*.beeftv-plugin'):
        if path == own_target:
            continue
        manifest = package_manifest(path.read_bytes())
        providers = manifest.get('contributes', {}).get('providers', [])
        if manifest.get('id') == PLUGIN_ID or any(p.get('id') == PROVIDER_ID for p in providers):
            raise ValueError('另一个已安装插件占用 Codex 生图标识，已停止安装。')


def install(data_dir=DEFAULT_DATA, bridge_root=DEFAULT_ROOT, app_dir=DEFAULT_APP,
            token_file=None, port=DEFAULT_PORT, *, make_default=False, source=PLUGIN_SOURCE):
    assert_beeftv_closed()
    data_dir = Path(data_dir).resolve(strict=True)
    bridge_root = Path(bridge_root).resolve(strict=True)
    app_dir = Path(app_dir).resolve(strict=True)
    if not (app_dir / 'BeefTV.exe').is_file():
        raise ValueError('安装目录中没有 BeefTV.exe。')
    package_dir = (app_dir / 'plugin-packages').resolve(strict=True)
    if package_dir.parent != app_dir:
        raise ValueError('插件目录不能指向应用目录以外。')
    target = data_dir / 'local-model-config.json'
    key_path = data_dir / '.settings-key'
    token_path = Path(token_file) if token_file else bridge_root / 'state/local-api-key'
    package_target = package_dir / PACKAGE_NAME
    original = target.read_bytes()
    key = key_path.read_bytes()
    token_bytes = token_path.read_bytes()
    token = token_bytes.decode('utf-8').strip()
    original_package = read_optional(package_target)
    if original_package is not None:
        validate_owned_package(original_package)
    check_conflicts(package_dir, package_target)
    package_data = pack(source)
    before = decode(original, key)
    document = merge(before, token, port, make_default=make_default)
    config_changed, package_changed = document != before, original_package != package_data
    result = {'installed': True, 'changed': config_changed or package_changed,
              'configChanged': config_changed, 'packageChanged': package_changed,
              'channelId': CHANNEL_ID, 'providerId': PROVIDER_ID, 'model': MODEL_KEY,
              'madeDefault': make_default, 'package': str(package_target),
              'revision': before['revision'], 'paidTestsSubmitted': 0}
    if not result['changed']:
        return result
    if config_changed:
        document['revision'] += 1
    output = encode(document, key) if config_changed else original
    if decode(output, key) != document:
        raise RuntimeError('加密配置预检失败。')
    backup_dir = bridge_root / 'backups'
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    config_backup = backup_dir / ('image-model-config-' + stamp + '.json.enc')
    backup(config_backup, original)
    result['backup'] = str(config_backup)
    if package_changed and original_package is not None:
        package_backup = backup_dir / ('codex-images-' + stamp + '.beeftv-plugin')
        backup(package_backup, original_package)
        result['packageBackup'] = str(package_backup)

    def recheck(*, package_value=original_package):
        assert_beeftv_closed()
        if (digest(target.read_bytes()) != digest(original)
                or digest(key_path.read_bytes()) != digest(key)
                or digest(token_path.read_bytes()) != digest(token_bytes)
                or digest(read_optional(package_target)) != digest(package_value)):
            raise RuntimeError('配置、密钥或插件文件已被其他进程修改，已停止覆盖。')
        check_conflicts(package_dir, package_target)

    staged_package = staged_config = None
    package_written = False
    try:
        if package_changed:
            staged_package = stage(package_dir, package_data, '.codex-images-')
        if config_changed:
            staged_config = stage(data_dir, output, '.codex-image-config-')
        recheck()
        if staged_package is not None:
            os.replace(staged_package, package_target)
            staged_package = None
            package_written = True
        if staged_config is not None:
            recheck(package_value=package_data)
            os.replace(staged_config, target)
            staged_config = None
        if digest(package_target.read_bytes()) != digest(package_data) or decode(target.read_bytes(), key) != document:
            raise RuntimeError('安装后的插件或加密配置核验失败。')
    except BaseException:
        # Never roll back a concurrent edit or files an already-running app may use.
        if package_written and digest(target.read_bytes()) == digest(original):
            assert_beeftv_closed()
            if digest(read_optional(package_target)) == digest(package_data):
                if original_package is None:
                    package_target.unlink()
                else:
                    restored = stage(package_dir, original_package, '.codex-images-restore-')
                    try:
                        os.replace(restored, package_target)
                    finally:
                        restored.unlink(missing_ok=True)
        raise
    finally:
        for path in (staged_package, staged_config):
            if path is not None:
                path.unlink(missing_ok=True)
    result['revision'] = document['revision']
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--install', action='store_true', help='安装插件和加密渠道配置，要求 BeefTV 已退出')
    action.add_argument('--pack', type=Path, help='只生成 .beeftv-plugin，不读取账号或配置')
    parser.add_argument('--make-default', action='store_true', help='将默认生图模型设为 Codex 内置生图')
    parser.add_argument('--data-dir', type=Path, default=DEFAULT_DATA)
    parser.add_argument('--bridge-root', type=Path, default=DEFAULT_ROOT)
    parser.add_argument('--app-dir', type=Path, default=DEFAULT_APP)
    parser.add_argument('--token-file', type=Path)
    parser.add_argument('--port', type=int, default=DEFAULT_PORT)
    args = parser.parse_args()
    if args.pack:
        data = pack()
        args.pack.parent.mkdir(parents=True, exist_ok=True)
        with args.pack.open('xb') as file:
            file.write(data)
        result = {'package': str(args.pack), 'sha256': digest(data), 'paidTestsSubmitted': 0}
    elif args.install:
        result = install(args.data_dir, args.bridge_root, args.app_dir, args.token_file,
                         args.port, make_default=args.make_default)
    else:
        result = {'mode': 'preview-only', 'channel': build_channel('[local-bridge-token]', args.port),
                  'makeDefault': args.make_default, 'pluginId': PLUGIN_ID, 'paidTestsSubmitted': 0}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
