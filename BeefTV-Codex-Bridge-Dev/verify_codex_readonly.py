"""Read-only BeefTV config and bridge health verification; never starts inference."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tomllib
import urllib.request

from install_codex import (CHANNEL_ID, DEFAULT_DATA, DEFAULT_MODEL, DEFAULT_PORT,
                          DEFAULT_ROOT, catalog_models, read_catalog, valid_text_selection)


def client_read_config(data_dir=DEFAULT_DATA, codex_config=None):
    path = Path(codex_config) if codex_config else Path.home() / '.codex/config.toml'
    cfg = tomllib.loads(path.read_text('utf-8'))['mcp_servers']['beeftv']
    env = dict(os.environ)
    env.update(cfg.get('env', {}))
    env['BEEFTV_DATA_DIR'] = str(data_dir)
    args = [cfg['command'], 'business', 'call', '--tool',
            'business_get_workspace_model_config', '--params', '{}']
    result = subprocess.run(args, env=env, capture_output=True, text=True, encoding='utf-8',
                            timeout=90, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        # Avoid leaking client tokens, provider keys, or unrestricted stderr.
        raise RuntimeError('BeefTV 只读配置查询失败，退出码 ' + str(result.returncode))
    body = json.loads(result.stdout)
    for _ in range(5):
        if isinstance(body, dict) and isinstance(body.get('config'), dict):
            return body
        if isinstance(body, dict) and isinstance(body.get('data'), dict):
            body = body['data']
            continue
        if isinstance(body, dict) and isinstance(body.get('result'), dict):
            body = body['result']
            continue
        raise RuntimeError('BeefTV 配置回执结构不符合预期。')
    raise RuntimeError('BeefTV 配置回执层级无效。')


def verify_snapshot(snapshot, port=DEFAULT_PORT, model=DEFAULT_MODEL, catalog=None):
    config = snapshot['config']
    rows = catalog_models(catalog, model)
    expected_ids = [row['id'] for row in rows]
    expected_keys = [CHANNEL_ID + '::' + model_id for model_id in expected_ids]
    matches = [c for c in config.get('channels', []) if c.get('id') == CHANNEL_ID]
    if len(matches) != 1:
        raise RuntimeError('没有找到唯一的本机 Codex 渠道。')
    channel = matches[0]
    expected = {'baseUrl': f'http://127.0.0.1:{port}/v1', 'enabled': True,
                'interfaceType': 'chat-completion', 'apiFormat': 'openai'}
    if any(channel.get(k) != v for k, v in expected.items()):
        raise RuntimeError('本机 Codex 渠道连接设置不匹配。')
    if not valid_text_selection(config, config.get('textModel')) or not valid_text_selection(config, config.get('assistantModel'), assistant=True):
        raise RuntimeError('默认文本模型或助手模型选择已失效。')
    if channel.get('models') != expected_ids:
        raise RuntimeError('本机 Codex 渠道模型列表与完整目录不匹配。')
    for field in ('models', 'textModels'):
        actual = [key for key in config.get(field, []) if isinstance(key, str) and key.startswith(CHANNEL_ID + '::')]
        if actual != expected_keys:
            raise RuntimeError('本机 Codex 的完整模型目录没有进入 ' + field + ' 菜单。')
    profiles = channel.get('modelProfiles', [])
    if not isinstance(profiles, list):
        raise RuntimeError('本机 Codex 模型能力配置与目录不匹配。')
    # BeefTV may normalize profile order independently of the model menu.
    profiles_by_model = {}
    for profile in profiles:
        profile_model = profile.get('model') if isinstance(profile, dict) else None
        if not isinstance(profile_model, str) or not profile_model or profile_model in profiles_by_model:
            raise RuntimeError('本机 Codex 模型能力配置存在无效或重复模型。')
        profiles_by_model[profile_model] = profile
    if set(profiles_by_model) != set(expected_ids):
        raise RuntimeError('本机 Codex 模型能力配置与目录不匹配。')
    for row in rows:
        profile = profiles_by_model[row['id']]
        if (profile.get('capability') != 'text' or profile.get('protocol') != 'chat-completion'
                or profile.get('displayName') != '本机 Codex · ' + row['displayName']):
            raise RuntimeError('本机 Codex 的模型名称或助手协议配置不匹配。')
    if not channel.get('hasApiKey') and not channel.get('apiKey'):
        raise RuntimeError('本机 Codex 桥接令牌尚未配置。')
    return {'configured': True, 'revision': snapshot.get('revision'), 'channelId': CHANNEL_ID,
            'textModel': config['textModel'], 'assistantModel': config['assistantModel'],
            'models': expected_ids, 'modelCount': len(expected_ids), 'protocol': 'chat-completion',
            'generationRequests': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=DEFAULT_DATA)
    parser.add_argument('--codex-config', type=Path)
    parser.add_argument('--bridge-root', type=Path, default=DEFAULT_ROOT)
    parser.add_argument('--port', type=int, default=DEFAULT_PORT)
    parser.add_argument('--model', help='兼容只验证一个指定模型，不与 --catalog 同时使用')
    parser.add_argument('--catalog', type=Path, help='默认读取 bridge-root/model-catalog.json（如存在）')
    parser.add_argument('--skip-health', action='store_true')
    args = parser.parse_args()
    if args.model and args.catalog:
        parser.error('--model 与 --catalog 不能同时使用。')
    default_catalog = args.bridge_root / 'model-catalog.json'
    catalog_path = args.catalog or (default_catalog if args.model is None and default_catalog.is_file() else None)
    catalog = read_catalog(catalog_path) if catalog_path else None
    result = verify_snapshot(client_read_config(args.data_dir, args.codex_config), args.port, args.model or DEFAULT_MODEL, catalog)
    if not args.skip_health:
        with urllib.request.urlopen(f'http://127.0.0.1:{args.port}/health', timeout=15) as response:
            health = json.load(response)
        # Project only safe health fields; never echo unknown service output.
        result['bridge'] = {k: health[k] for k in ('ready', 'service', 'version', 'authMode', 'models', 'signedIn') if k in health}
        if health.get('ready') is not True or health.get('service') != 'beeftv-codex':
            raise RuntimeError('本机 Codex 桥接服务未就绪。')
        if sorted(health.get('models', [])) != sorted(result['models']):
            raise RuntimeError('运行中的桥接服务模型列表与 BeefTV 目录不匹配。')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
