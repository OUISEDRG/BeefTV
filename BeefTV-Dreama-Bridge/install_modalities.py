"""Install Dreama modality profiles without changing video selections or other channels.

Run only after the reviewed dreama-modalities protocol package is installed in BeefTV.
The CLI default builds/prints a credential-free package; --install updates encrypted config.
"""
import argparse
import base64
import copy
import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parent
PORT = 18765
PLUGIN_ID = 'dreama-modalities'
CHANNEL_IDS = ('dreama-image-generation', 'dreama-text-generation', 'dreama-audio-generation', 'dreama-audio-voices')


def image_profile(spec):
    resolutions = list(spec['resolutions'])
    ratios = spec['aspectRatios']
    default_res = spec.get('defaultResolution')
    if default_res not in resolutions:
        default_res = resolutions[0]
    return {
        'model': spec['id'], 'displayName': spec['label'], 'capability': 'image',
        'description': '选择分辨率和比例后提交；支持画布参考图，完成后回传。',
        'protocol': 'dreama-images-async', 'referenceMediaTransport': {'image': 'inline'},
        'imageCapabilitiesVersion': 'dreama-image-v2-workbench',
        'capabilityConfig': {'version': 1, 'image': {
            'references': {'promptMaxChars': spec['promptMaxChars'], 'maxImages': spec['maxImages'],
                           'maxImageBytes': int(min(30, spec.get('limits', {}).get('imageSizeMax', 10)) * 1024 * 1024), 'maskSupported': False},
            'size': {'parameter': 'aspect_ratio', 'values': list(ratios),
                     'default': '1:1' if '1:1' in ratios else ratios[0], 'allowCustom': False},
            'quality': {'supported': True, 'values': resolutions, 'default': default_res},
            'transparentBackground': {'supported': False, 'default': False},
            'responseFormat': {'supported': False}, 'outputFormat': {'supported': False},
            'maxOutputs': spec['maxOutputs'],
        }},
    }


def text_profile(spec):
    return {
        'model': spec['id'], 'displayName': spec['label'], 'capability': 'text',
        'description': '剧梦文本生成，保留提示词与对话上下文；不提供 Agent 工具调用。',
        'protocol': 'dreama-text-async',
        'capabilityConfig': {'version': 1, 'text': {
            'streaming': False, 'thinking': False,
            'references': {'promptMaxChars': spec.get('maxChars', 32000),
                           'maxImages': 0, 'maxImageBytes': 0, 'maxVideos': 0, 'maxVideoBytes': 0},
        }},
    }


def audio_profile(spec):
    from audio_flow import voices
    frozen_voice = bool(spec.get('voiceId'))
    adjustable = spec.get('taskType') == 'voiceover-generate-doubao'
    voice_values = [] if frozen_voice else [{'value':key,'label':row['name']} for key,row in voices().items()]
    return {
        'model': spec['id'], 'displayName': spec['label'], 'capability': 'audio',
        'description': '按已绑定音色与明确参数生成，输出 MP3。',
        'protocol': 'dreama-audio-async', 'referenceMediaTransport': {'audio': 'inline'},
        'audioCapabilitiesVersion': 'dreama-audio-v2-workbench',
        'capabilityConfig': {'version':1,'audio': {
            'voices': {'supported':not frozen_voice,'values':voice_values,'default':spec.get('voiceId',''),'allowCustom':False},
            'formats': {'supported':True,'values':['mp3'],'default':'mp3'},
            'speed': {'supported':adjustable,'min':0.5,'max':2,'step':0.05,'default':1},
            'pitch': {'supported':adjustable,'min':-12,'max':12,'step':1,'default':0},
            'volume': {'supported':adjustable,'min':0.5,'max':2,'step':0.05,'default':1},
            'instructions': {'supported':False},
            'references': {'promptMaxChars':spec.get('maxChars',3000)},
        }},
        'mediaCapabilitiesVersion':'dreama-audio-v2-workbench',
        'mediaCapabilities': {'audio': {'operations':['generate'],'options':[],
            'referenceKinds':[] if frozen_voice else ['audio'],'maxOutputs':1,'promptMaxChars':spec.get('maxChars',3000)}},
    }


def _channel(token, channel_id, name, kind, protocol, profiles):
    return {'id': channel_id, 'name': name, 'scope': 'user', 'enabled': True, 'pinned': False,
            'baseUrl': f'http://127.0.0.1:{PORT}/{kind}', 'apiFormat': 'openai',
            'interfaceType': protocol, 'apiKey': token, 'headers': [], 'concurrencyLimit': 1,
            'models': [p['model'] for p in profiles], 'modelProfiles': profiles}


def channels(token, image_specs=None, text_specs=None, audio_specs=None, voice_specs=None):
    if image_specs is None:
        from image_flow import image_models
        image_specs = image_models()
    if text_specs is None:
        from text_flow import text_models
        text_specs = text_models()
    if audio_specs is None or voice_specs is None:
        from audio_flow import audio_models, voices, resolve_audio_model
        if audio_specs is None:
            audio_specs = audio_models()
        if voice_specs is None:
            voice_specs = {f'dreama-voice-clone-{vid}': resolve_audio_model(f'dreama-voice-clone-{vid}') for vid in voices()}
    result = []
    groups = (
        (CHANNEL_IDS[0], '剧梦图片生成', 'image', 'dreama-images-async', image_specs, image_profile),
        (CHANNEL_IDS[1], '剧梦文本生成', 'text', 'dreama-text-async', text_specs, text_profile),
        (CHANNEL_IDS[2], '剧梦音频生成', 'audio', 'dreama-audio-async', audio_specs, audio_profile),
        (CHANNEL_IDS[3], '剧梦文本配音（音色）', 'audio', 'dreama-audio-async', voice_specs, audio_profile),
    )
    for channel_id, name, kind, protocol, specs, make_profile in groups:
        profiles = [make_profile(s) for s in specs.values() if s.get('enabled', True)]
        if profiles:
            result.append(_channel(token, channel_id, name, kind, protocol, profiles))
    return result


def merge(document, token, fresh_channels=None):
    doc = copy.deepcopy(document)
    config = doc['config']
    fresh_channels = channels(token) if fresh_channels is None else fresh_channels
    for fresh in fresh_channels:
        cid = fresh['id']
        if cid not in CHANNEL_IDS:
            raise RuntimeError('拒绝安装无关渠道。')
        matches = [i for i, c in enumerate(config['channels']) if c.get('id') == cid]
        if len(matches) > 1:
            raise RuntimeError('同名剧梦渠道不唯一。')
        if matches:
            old = config['channels'][matches[0]]
            if old.get('baseUrl') != fresh['baseUrl'] or old.get('apiKey') != token:
                raise RuntimeError('同名渠道不属于当前桥接，停止覆盖。')
            updated = copy.deepcopy(old)
            for field in ('name', 'models', 'modelProfiles', 'interfaceType'):
                updated[field] = fresh[field]
            config['channels'][matches[0]] = updated
        else:
            config['channels'].append(copy.deepcopy(fresh))
        kind = fresh['modelProfiles'][0]['capability']
        options = [cid + '::' + model for model in fresh['models']]
        for field in ('models', kind + 'Models'):
            config[field] = [m for m in config.get(field, []) if not m.startswith(cid + '::')] + options
        default_key = kind + 'Model'
        if not config.get(default_key) or config[default_key].startswith(cid + '::') and config[default_key] not in options:
            config[default_key] = options[0]
    # Explicitly protect all video/default assistant/canvas and unrelated preferences.
    allowed = {'channels', 'models', 'imageModels', 'textModels', 'audioModels', 'imageModel', 'textModel', 'audioModel'}
    assert {k: v for k, v in config.items() if k not in allowed} == {k: v for k, v in document['config'].items() if k not in allowed}
    return doc


def build_plugin(target=None):
    manifest = json.loads((ROOT / 'dreama-modalities-manifest.json').read_text('utf-8'))
    if manifest['id'] != PLUGIN_ID:
        raise RuntimeError('插件标识不匹配。')
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
        entry = zipfile.ZipInfo('manifest.json', (2026, 11, 1, 0, 0, 0))
        entry.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(entry, json.dumps(manifest, ensure_ascii=False, indent=2).encode('utf-8'))
    data = stream.getvalue()
    if target is not None:
        Path(target).write_bytes(data)
    return data


def install():
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from install_channel import decode
    running = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq BeefTV.exe', '/FO', 'CSV', '/NH'], capture_output=True, check=True)
    if b'beeftv.exe' in running.stdout.lower():
        raise RuntimeError('请先关闭 BeefTV，再写入加密模型配置。')
    pointer = Path(os.environ['APPDATA']) / 'BeefTV/storage-location.json'
    data = Path(json.loads(pointer.read_text('utf-8-sig'))['dataDir']).resolve()
    target = data / 'local-model-config.json'
    original = target.read_bytes()
    key = (data / '.settings-key').read_bytes()
    token = (ROOT / 'state/local-api-key').read_text('utf-8').strip()
    before = decode(original, key)
    doc = merge(before, token)
    if doc == before:
        print('Dreama modality channels already current.')
        return
    doc['revision'] += 1
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    (ROOT / 'backups').mkdir(exist_ok=True)
    backup = ROOT / 'backups' / ('before-modalities-' + stamp + '.json.enc')
    backup.write_bytes(original)
    nonce = os.urandom(12)
    payload = nonce + AESGCM(key).encrypt(nonce, json.dumps(doc, ensure_ascii=False).encode(), None)
    output = json.dumps({'schemaVersion': 2, 'encryptedConfig': 'enc:v1:' + base64.b64encode(payload).decode().rstrip('=')}).encode()
    assert decode(output, key) == doc
    if target.read_bytes() != original:
        raise RuntimeError('配置有并发修改，停止写入。')
    temporary = data / ('.dreama-modalities-' + stamp + '.tmp')
    with temporary.open('xb') as file:
        file.write(output)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, target)
    assert decode(target.read_bytes(), key) == doc
    print(json.dumps({'installed': True, 'revision': doc['revision'], 'backup': str(backup),
                      'channels': [{'id': c['id'], 'models': len(c['models'])} for c in doc['config']['channels'] if c['id'] in CHANNEL_IDS],
                      'paidTestsSubmitted': 0}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--build-plugin', action='store_true')
    parser.add_argument('--install', action='store_true')
    args = parser.parse_args()
    if args.install:
        install()
    else:
        target = ROOT / 'dreama-modalities.beeftv-plugin'
        package = build_plugin(target)
        print(json.dumps({'package': str(target), 'sha256': hashlib.sha256(package).hexdigest()}, ensure_ascii=False))
