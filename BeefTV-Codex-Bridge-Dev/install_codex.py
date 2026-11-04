"""Install a local Codex channel only while BeefTV is closed.

Does not call any inference or generation endpoint. Existing channels, secrets,
and unknown document fields are preserved. Uses the app's encrypted v2 envelope.
"""
import argparse
import base64
import copy
import csv
import datetime
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

CHANNEL_ID = 'codex-local'
DEFAULT_MODEL = 'gpt-6-astra'
DEFAULT_PORT = 18766
DEFAULT_ROOT = Path('D:/BeefTV-Codex-Bridge')
DEFAULT_DATA = Path('D:/BeeftvData')


def catalog_models(catalog=None, model=DEFAULT_MODEL):
    """Accept a native model/list array or the saved {models: [...]} envelope."""
    if catalog is None:
        catalog = [{'id': model, 'displayName': model, 'isDefault': True}]
    rows = catalog.get('models') if isinstance(catalog, dict) else catalog
    if not isinstance(rows, list) or not rows:
        raise ValueError('Codex 模型目录必须包含非空 models 数组。')
    result, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('Codex 模型目录条目无效。')
        model_id = row.get('id') or row.get('model')
        if not isinstance(model_id, str) or not model_id.strip() or model_id != model_id.strip() or '::' in model_id:
            raise ValueError('Codex 模型标识无效。')
        if model_id in seen:
            raise ValueError('Codex 模型目录包含重复标识。')
        seen.add(model_id)
        name = row.get('displayName') or model_id
        if not isinstance(name, str) or not name.strip():
            raise ValueError('Codex 模型名称无效。')
        result.append({**copy.deepcopy(row), 'id': model_id, 'displayName': name})
    return result


def catalog_default(catalog=None, model=DEFAULT_MODEL):
    rows = catalog_models(catalog, model)
    ids = {row['id'] for row in rows}
    declared = catalog.get('defaultModel') if isinstance(catalog, dict) else None
    if declared is not None:
        if declared not in ids:
            raise ValueError('Codex 目录默认模型不在可用列表中。')
        return declared
    default_rows = [row['id'] for row in rows if row.get('isDefault') is True]
    if len(default_rows) > 1:
        raise ValueError('Codex 目录声明了多个默认模型。')
    return default_rows[0] if default_rows else model if model in ids else rows[0]['id']


def read_catalog(path):
    catalog = json.loads(Path(path).read_text('utf-8-sig'))
    catalog_models(catalog)
    catalog_default(catalog)
    return catalog


def valid_text_selection(config, selection, *, assistant=False):
    if not isinstance(selection, str) or '::' not in selection:
        return False
    channel_id, model = selection.split('::', 1)
    channels = [c for c in config['channels'] if c.get('id') == channel_id and c.get('enabled') is True]
    if len(channels) != 1 or model not in channels[0].get('models', []):
        return False
    channel = channels[0]
    profiles = [p for p in channel.get('modelProfiles', []) if p.get('model') == model]
    if profiles:
        if len(profiles) != 1 or profiles[0].get('capability') != 'text':
            return False
        protocol = profiles[0].get('protocol') or 'chat-completion'
    else:
        if selection not in config.get('textModels', []):
            return False
        protocol = channel.get('interfaceType') or ('claude-api' if channel.get('apiFormat') == 'claude' else 'chat-completion')
    return not assistant or protocol in ('chat-completion', 'claude-api', 'openai-response', 'responses')


def build_channel(token, port=DEFAULT_PORT, model=DEFAULT_MODEL, catalog=None):
    if not isinstance(token, str) or not token.strip():
        raise ValueError('本机桥接令牌不能为空。')
    if not isinstance(port, int) or not 1024 <= port <= 65535:
        raise ValueError('本机桥接端口无效。')
    rows = catalog_models(catalog, model)
    catalog_default(catalog, model)
    return {
        'id': CHANNEL_ID, 'name': '本机 Codex', 'scope': 'user',
        'enabled': True, 'pinned': False,
        'baseUrl': f'http://127.0.0.1:{port}/v1', 'apiFormat': 'openai',
        'interfaceType': 'chat-completion', 'apiKey': token,
        'headers': [], 'concurrencyLimit': 1, 'models': [row['id'] for row in rows],
        'modelProfiles': [{
            'model': row['id'], 'displayName': '本机 Codex · ' + row['displayName'],
            'description': '使用本机 Codex 登录账号；支持文本生成与助手工具调用、画布操作。',
            'capability': 'text', 'protocol': 'chat-completion',
            'referenceMediaTransport': {'image': 'inline'},
        } for row in rows],
    }


def merge(document, token, port=DEFAULT_PORT, model=DEFAULT_MODEL, catalog=None):
    """Return a copy; preserve all unrelated preferences and whole other channels."""
    doc = copy.deepcopy(document)
    config = doc['config']
    if not isinstance(config.get('channels'), list):
        raise ValueError('模型渠道配置损坏。')
    fresh = build_channel(token, port, model, catalog)
    indices = [i for i, c in enumerate(config['channels']) if c.get('id') == CHANNEL_ID]
    if len(indices) > 1:
        raise ValueError('本机 Codex 渠道标识重复，已停止。')
    if indices:
        old = config['channels'][indices[0]]
        if old.get('baseUrl') != fresh['baseUrl'] or old.get('apiKey') != token:
            raise ValueError('同名 Codex 渠道不属于本桥接，已停止覆盖。')
        # Keep user-added metadata, replace only our channel's functional fields.
        old.update(copy.deepcopy(fresh))
    else:
        config['channels'].append(fresh)
    keys = [CHANNEL_ID + '::' + item for item in fresh['models']]
    for field in ('models', 'textModels'):
        items = config.get(field, [])
        if not isinstance(items, list):
            raise ValueError('模型列表配置损坏。')
        config[field] = [x for x in items if not isinstance(x, str) or not x.startswith(CHANNEL_ID + '::')]
        config[field].extend(keys)
    fallback = CHANNEL_ID + '::' + catalog_default(catalog, model)
    if not valid_text_selection(config, config.get('textModel')):
        config['textModel'] = fallback
    if not valid_text_selection(config, config.get('assistantModel'), assistant=True):
        config['assistantModel'] = fallback
    allowed = {'channels', 'models', 'textModels', 'textModel', 'assistantModel'}
    assert {k: v for k, v in config.items() if k not in allowed} == {
        k: v for k, v in document['config'].items() if k not in allowed}
    assert [c for c in config['channels'] if c.get('id') != CHANNEL_ID] == [
        c for c in document['config']['channels'] if c.get('id') != CHANNEL_ID]
    return doc


def decode(raw, key):
    envelope = json.loads(raw)
    if envelope.get('schemaVersion') != 2 or not isinstance(envelope.get('encryptedConfig'), str):
        raise ValueError('不支持的 BeefTV 配置格式。')
    cipher = envelope['encryptedConfig']
    if not cipher.startswith('enc:v1:'):
        raise ValueError('不支持的 BeefTV 配置加密版本。')
    packed = base64.b64decode(cipher[7:] + '===')
    document = json.loads(AESGCM(key).decrypt(packed[:12], packed[12:], None))
    if document.get('schemaVersion') != 1 or not isinstance(document.get('config'), dict):
        raise ValueError('BeefTV 配置内容损坏。')
    if not isinstance(document.get('revision'), int):
        raise ValueError('BeefTV 配置版本号损坏。')
    return document


def encode(document, key):
    nonce = os.urandom(12)
    body = json.dumps(document, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    packed = nonce + AESGCM(key).encrypt(nonce, body, None)
    return json.dumps({'schemaVersion': 2, 'encryptedConfig': 'enc:v1:' +
                      base64.b64encode(packed).decode('ascii').rstrip('=')}).encode('utf-8')


def assert_beeftv_closed():
    if os.name != 'nt':
        raise RuntimeError('该安装器仅支持 Windows BeefTV。')
    result = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq BeefTV.exe', '/FO', 'CSV', '/NH'],
                            capture_output=True, check=True, timeout=15,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    text = result.stdout.decode('utf-8', errors='replace')
    if any(row and row[0].casefold() == 'beeftv.exe' for row in csv.reader(io.StringIO(text))):
        raise RuntimeError('请先正常退出 BeefTV，再写入模型配置。')


def install(data_dir=DEFAULT_DATA, bridge_root=DEFAULT_ROOT, token_file=None,
            port=DEFAULT_PORT, model=DEFAULT_MODEL, catalog=None):
    assert_beeftv_closed()
    data_dir = Path(data_dir).resolve(strict=True)
    bridge_root = Path(bridge_root).resolve(strict=True)
    token_file = Path(token_file) if token_file else bridge_root / 'state/local-api-key'
    token = token_file.read_text('utf-8').strip()
    target = data_dir / 'local-model-config.json'
    original = target.read_bytes()
    key = (data_dir / '.settings-key').read_bytes()
    before = decode(original, key)
    doc = merge(before, token, port, model, catalog)
    if before == doc:
        return {'installed': True, 'changed': False, 'revision': doc['revision'],
                'channelId': CHANNEL_ID, 'models': [row['id'] for row in catalog_models(catalog, model)], 'paidTestsSubmitted': 0}
    doc['revision'] += 1
    output = encode(doc, key)
    assert decode(output, key) == doc
    backup_dir = bridge_root / 'backups'
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    backup = backup_dir / ('local-model-config-' + stamp + '.json.enc')
    with backup.open('xb') as file:
        file.write(original)
        file.flush()
        os.fsync(file.fileno())
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='wb', prefix='.codex-model-config-',
                                         suffix='.tmp', dir=data_dir, delete=False) as file:
            temporary = Path(file.name)
            file.write(output)
            file.flush()
            os.fsync(file.fileno())
        # Recheck both the app and the original bytes immediately before replacement.
        assert_beeftv_closed()
        if target.read_bytes() != original:
            raise RuntimeError('模型配置已被其他进程修改，已停止覆盖。')
        os.replace(temporary, target)
        temporary = None
        if decode(target.read_bytes(), key) != doc:
            raise RuntimeError('安装后的加密配置核验失败。')
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return {'installed': True, 'changed': True, 'revision': doc['revision'],
            'channelId': CHANNEL_ID, 'textModel': doc['config']['textModel'],
            'assistantModel': doc['config']['assistantModel'], 'models': [row['id'] for row in catalog_models(catalog, model)], 'backup': str(backup),
            'paidTestsSubmitted': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install', action='store_true', help='明确执行加密配置安装，要求 BeefTV 已退出')
    parser.add_argument('--data-dir', type=Path, default=DEFAULT_DATA)
    parser.add_argument('--bridge-root', type=Path, default=DEFAULT_ROOT)
    parser.add_argument('--token-file', type=Path)
    parser.add_argument('--port', type=int, default=DEFAULT_PORT)
    parser.add_argument('--model', help='兼容原来的单模型安装；不与 --catalog 同时使用')
    parser.add_argument('--catalog', type=Path, help='Codex 原生模型目录 JSON；默认读取 bridge-root/model-catalog.json（如存在）')
    args = parser.parse_args()
    if args.model and args.catalog:
        parser.error('--model 与 --catalog 不能同时使用。')
    default_catalog = args.bridge_root / 'model-catalog.json'
    catalog_path = args.catalog or (default_catalog if args.model is None and default_catalog.is_file() else None)
    catalog = read_catalog(catalog_path) if catalog_path else None
    model = args.model or DEFAULT_MODEL
    if args.install:
        result = install(args.data_dir, args.bridge_root, args.token_file, args.port, model, catalog)
    else:
        result = {'mode': 'preview-only', 'channel': build_channel('[local-bridge-token]', args.port, model, catalog),
                  'preserveValidSelections': True,
                  'fallbackModel': CHANNEL_ID + '::' + catalog_default(catalog, model), 'paidTestsSubmitted': 0}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
