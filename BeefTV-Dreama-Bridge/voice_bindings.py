"""Account scoped voice catalog and immutable picture/voice request bindings.

Contracts: create-CvFMVb6n.js, VoiceSelectorModal-B339lT2t.js and the official
CLI's read-only prompt assembler. No endpoint here starts inference.
"""
import hashlib
import json
import math
import re
import threading
import time
from pathlib import Path

import requests

from bridge import BridgeError, DreamaGateway

ROOT = Path(__file__).resolve().parent
BASE = 'https://jzzm.duanju.com/gw'


class VoiceCatalog:
    def __init__(self, gateway=None, ttl=60):
        self.gateway = gateway or DreamaGateway()
        self.ttl = ttl
        self.lock = threading.Lock()
        self._scope = None
        self._expires = 0
        self._rows = {}

    def _read(self, token, path, payload):
        response = requests.post(BASE + path, json=payload,
            headers={'Authorization': token}, timeout=(10, 30), allow_redirects=False)
        if response.status_code != 200:
            raise BridgeError('剧梦音色目录暂不可用，请稍后刷新。')
        body = response.json()
        if str(body.get('code')) != '0':
            raise BridgeError('剧梦音色目录读取失败，请核对当前账号。')
        return body.get('result', {})

    def get(self, force=False):
        from audio_flow import cdn_url
        try:
            token = json.loads(self.gateway.credentials.read_text('utf-8-sig'))['accessToken']
        except (OSError, ValueError, KeyError):
            raise BridgeError('无法读取当前 Dreama 登录状态。') from None
        scope = hashlib.sha256(token.encode()).hexdigest()
        with self.lock:
            # A different login never receives the previous account's private rows.
            if self._scope != scope:
                self._rows = {}; self._expires = 0; self._scope = scope
            if not force and self._expires > time.monotonic():
                return {key: dict(value) for key, value in self._rows.items()}
            rows = {}
            for source, path, extra in (
                ('public', '/storyboard/voiceTone/list', {'isHumanlike': True}),
                ('public', '/storyboard/voiceTone/list', {'provider': 'douge'}),
                ('personal', '/storyboard/voiceTone/listMine', {'provider': 'custom'})):
                for page in range(1, 101):
                    body = self._read(token, path, {'page': page, 'size': 100, **extra})
                    items = body.get('items', []) if isinstance(body, dict) else body
                    if not isinstance(items, list):
                        raise BridgeError('剧梦音色目录响应无效。')
                    for raw in items:
                        voice_id = str(raw.get('id', ''))
                        url = raw.get('voiceUrl') or raw.get('clipVoiceUrl')
                        if not re.fullmatch(r'[1-9][0-9]*', voice_id) or not cdn_url(url):
                            continue
                        old = rows.get(voice_id)
                        if old and old['source'] != source:
                            raise BridgeError('公共与个人音色编号冲突，未自动选择。')
                        duration = raw.get('duration', raw.get('clipDurationSec'))
                        clip_duration = raw.get('clipDurationSec')
                        rows[voice_id] = {'id': voice_id, 'name': str(raw.get('name') or voice_id),
                            'source': source, 'previewUrl': url, 'voiceUrl': url,
                            'videoUrl': raw.get('clipVoiceUrl') if cdn_url(raw.get('clipVoiceUrl')) else url,
                            'duration': duration if type(duration) in (int, float) and math.isfinite(duration) and duration > 0 else None,
                            'clipDuration': clip_duration if type(clip_duration) in (int, float) and math.isfinite(clip_duration) and clip_duration > 0 else None}
                    total = body.get('totalCount') if isinstance(body, dict) else None
                    if len(items) < 100 or type(total) is int and page * 100 >= total:
                        break
                else:
                    raise BridgeError('音色目录分页未完整返回，请稍后重试。')
            self._rows = rows; self._expires = time.monotonic() + self.ttl
            return {key: dict(value) for key, value in rows.items()}

    def resolve(self, voice_id, force=False):
        row = self.get(force=force).get(str(voice_id))
        if not row:
            raise BridgeError('所选音色已删除或不属于当前 Dreama 账号，请重新选择。')
        return row


_catalog = None
_catalog_lock = threading.Lock()


def catalog():
    global _catalog
    with _catalog_lock:
        if _catalog is None:
            _catalog = VoiceCatalog()
        return _catalog


def validate_bindings(data, image_count, *, audio=False):
    from audio_flow import decode_audio
    bindings = data.get('character_voices', [])
    if not isinstance(bindings, list) or len(bindings) > (1 if audio else image_count):
        raise BridgeError('独立配音只能选择一个绑定人物。' if audio else '人物音色绑定数量无效。')
    seen = set(); result = []
    for binding in bindings:
        if not isinstance(binding, dict) or set(binding) - {'image_index', 'name', 'voice_id', 'audio'}:
            raise BridgeError('人物音色绑定包含未知字段。')
        index, name = binding.get('image_index'), binding.get('name')
        if type(index) is not int or not 1 <= index <= (1 if audio else image_count) or index in seen:
            raise BridgeError('人物音色与参考图片编号不对应或重复。')
        if not isinstance(name, str) or not name.strip() or len(name) > 200:
            raise BridgeError('人物音色名称无效。')
        seen.add(index)
        if ('voice_id' in binding) == ('audio' in binding):
            raise BridgeError('每个人物只能选择目录音色或一个声音样本。')
        if 'voice_id' in binding:
            if not isinstance(binding['voice_id'], str) or not re.fullmatch(r'[1-9][0-9]*', binding['voice_id']):
                raise BridgeError('人物音色编号无效。')
        else:
            decode_audio(binding['audio'])
        result.append(dict(binding))
    return result


def video_limits(model):
    if isinstance(model.get('limits'),dict): return model['limits']
    eid = model.get('eid', model.get('id', ''))
    if eid.startswith('dreama-extend-'):
        eid = eid[len('dreama-extend-'):]
    for filename in ('catalog.json', 'extension-catalog.json'):
        for row in json.loads((ROOT / filename).read_text('utf-8')).get('items', []):
            if row.get('eid') == eid:
                return json.loads(row.get('modelLimit') or '{}')
    return {}


def validate_video_bindings(data, image_count, model):
    bindings = validate_bindings(data, image_count)
    if bindings:
        limits = video_limits(model)
        if len(bindings) > limits.get('audioMax', 0):
            raise BridgeError('该模型不支持此数量的人物音色，请减少绑定人物或切换模型。')
    return bindings


def media_info(url):
    from audio_flow import cdn_url
    if not cdn_url(url):
        raise BridgeError('音色样本地址无效。')
    response = requests.get(url + ('&' if '?' in url else '?') + 'avinfo',
                            timeout=(10, 30), allow_redirects=False)
    if response.status_code != 200 or len(response.content) > 65536:
        raise BridgeError('无法确认音色样本时长，请选择其他音色。')
    try:
        info = response.json()['format']
        duration, size = info['duration'], info['size']
        if isinstance(duration, bool) or isinstance(size, bool):
            raise ValueError()
        duration = float(duration)
        if isinstance(size, str) and not re.fullmatch(r'[0-9]+', size):
            raise ValueError()
        if type(size) not in (int, str):
            raise ValueError()
        size = int(size)
        if not math.isfinite(duration) or duration <= 0 or size <= 0:
            raise ValueError()
        return {'duration': duration, 'bytes': size}
    except (KeyError, TypeError, ValueError):
        raise BridgeError('音色样本时长或大小无效。') from None


def prepare_video_voices(bindings, model, bridge, *, standalone_audio=(), reference_images=(), reference_videos=()):
    """Runs only inside an idempotent user-created task, before any role write."""
    from audio_flow import AudioFlow, decode_audio, probe_audio, MAX_AUDIO
    limits = video_limits(model); result = {}; total = 0
    flow = None
    for binding in bindings:
        if 'voice_id' in binding:
            row = catalog().resolve(binding['voice_id'])
            url = row.get('videoUrl') or row['voiceUrl']
            info = media_info(url); duration = info['duration']
            if info['bytes'] > min(MAX_AUDIO, limits.get('audioSizeMax', 0) * 1024 * 1024):
                raise BridgeError('目录音色样本超过当前视频模型的大小限制。')
            voice = {'voiceId': int(row['id']), 'uploadVoiceUrl': '', 'voiceUrl': url,
                     'voiceName': binding['name'], 'voiceDuration': duration, 'voiceBytes': info['bytes'], 'voiceSource': row['source']}
        else:
            raw = decode_audio(binding['audio'])
            if len(raw) > min(MAX_AUDIO, limits.get('audioSizeMax', 0) * 1024 * 1024):
                raise BridgeError('人物声音样本超过当前视频模型的大小限制。')
            inputs = bridge.root / 'voice-inputs'; inputs.mkdir(exist_ok=True)
            path = inputs / (hashlib.sha256(raw).hexdigest() + '.input'); path.write_bytes(raw)
            meta = probe_audio(path); duration = meta['duration']
            voice = {'voiceId': 0, 'voiceName': binding['name'], 'voiceDuration': duration,
                     'voiceBytes': meta['bytes'], 'sha256': meta['sha256'], 'voiceSource': 'resource'}
        if not limits.get('audioDurationMin', 0) <= duration <= limits.get('audioDurationMax', 0):
            raise BridgeError('人物声音样本时长不符合当前视频模型限制。')
        total += duration
        # Each bound image becomes a distinct native character material even
        # when two characters use the same voice sample. Count those slots now,
        # before creating the material IDs used by the native editor.
        voice['voiceReferenceIndex']=binding['image_index']
        result[binding['image_index']] = voice
    from video_audio_references import validate_audio_limits, validate_total_reference_size
    validate_audio_limits(model if model.get('limits') is not None else {**model, 'limits': limits}, standalone_audio, result)
    validate_total_reference_size(model,reference_images,standalone_audio,reference_videos,result.values())
    # No sample is uploaded until every input has passed the constraints above.
    for binding in bindings:
        if 'audio' in binding:
            flow = flow or AudioFlow(bridge)
            url = flow._audio_reference(binding['audio'])
            result[binding['image_index']].update(uploadVoiceUrl=url, voiceUrl=url)
    return result
