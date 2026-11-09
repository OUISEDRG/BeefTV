"""Migrate existing video protocols only, after installing the reviewed plugin.

Default: credential-free dry-run summary. --install requires BeefTV to be closed.
Never regenerates model profiles, changes defaults, creates tasks or calls Dreama.
"""
import argparse
import base64
import copy
import datetime
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent
TARGETS = {
    'dreama-local-preflight': ('http://127.0.0.1:18765', 'dreama-videos-async'),
    'dreama-video-extension': ('http://127.0.0.1:18765/extension', 'dreama-extension-async'),
}


def merge(document, token):
    result = copy.deepcopy(document)
    channels = result['config']['channels']
    for channel_id, (base_url, protocol) in TARGETS.items():
        matches = [c for c in channels if c.get('id') == channel_id]
        if len(matches) != 1:
            raise ValueError(f'目标剧梦渠道缺失或不唯一：{channel_id}')
        channel = matches[0]
        if channel.get('baseUrl') != base_url or channel.get('apiKey') != token:
            raise ValueError(f'目标渠道不属于当前本机剧梦桥接：{channel_id}')
        profiles = channel.get('modelProfiles')
        if not profiles or any(p.get('capability') != 'video' for p in profiles):
            raise ValueError(f'目标渠道含未知模型类型：{channel_id}')
        if channel.get('interfaceType') not in ('seedance-videos-compatible', protocol):
            raise ValueError(f'目标渠道使用未知协议：{channel_id}')
        if any(p.get('protocol') not in ('seedance-videos-compatible', protocol) for p in profiles):
            raise ValueError(f'目标模型使用未知协议：{channel_id}')
        channel['interfaceType'] = protocol
        for profile in profiles:
            profile['protocol'] = protocol
    return result


def summary(before, after):
    changes = []
    for old, new in zip(before['config']['channels'], after['config']['channels']):
        if old != new:
            changes.append({'channelId': old['id'], 'protocol': new['interfaceType'],
                            'modelsPreserved': old['models'] == new['models'], 'modelCount': len(new['models'])})
    return {'changedChannels': changes, 'videoDefaultPreserved': before['config'].get('videoModel') == after['config'].get('videoModel'),
            'resolutionPreserved': before['config'].get('vquality') == after['config'].get('vquality'),
            'paidTestsSubmitted': 0}


def run(install=False):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from install_channel import decode
    pointer = Path(os.environ['APPDATA']) / 'BeefTV/storage-location.json'
    data = Path(json.loads(pointer.read_text('utf-8-sig'))['dataDir']).resolve()
    target = data / 'local-model-config.json'
    original = target.read_bytes()
    key = (data / '.settings-key').read_bytes()
    token = (ROOT / 'state/local-api-key').read_text('utf-8').strip()
    before = decode(original, key)
    after = merge(before, token)
    receipt = summary(before, after)
    receipt['dryRun'] = not install
    if not install or after == before:
        print(json.dumps(receipt, ensure_ascii=False))
        return
    processes = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq BeefTV.exe', '/FO', 'CSV', '/NH'], capture_output=True, check=True)
    if b'beeftv.exe' in processes.stdout.lower():
        raise RuntimeError('请先关闭 BeefTV，再写入协议配置；插件应已安装。')
    after['revision'] += 1
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    backup_dir = ROOT / 'backups'
    backup_dir.mkdir(exist_ok=True)
    backup = backup_dir / ('before-image-voice-protocols-' + stamp + '.json.enc')
    backup.write_bytes(original)
    nonce = os.urandom(12)
    encrypted = nonce + AESGCM(key).encrypt(nonce, json.dumps(after, ensure_ascii=False).encode('utf-8'), None)
    output = json.dumps({'schemaVersion': 2, 'encryptedConfig': 'enc:v1:' + base64.b64encode(encrypted).decode('ascii').rstrip('=')}).encode('utf-8')
    assert decode(output, key) == after
    if target.read_bytes() != original:
        raise RuntimeError('配置已发生并发变化，未写入，请关闭 BeefTV 后重试。')
    temporary = data / ('.dreama-voice-protocols-' + stamp + '.tmp')
    with temporary.open('xb') as handle:
        handle.write(output)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, target)
    assert decode(target.read_bytes(), key) == after
    receipt.update(installed=True, revision=after['revision'], backup=str(backup))
    print(json.dumps(receipt, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--install', action='store_true')
    run(parser.parse_args().install)
