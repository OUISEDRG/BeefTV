"""Account scoped voice catalog and immutable picture/voice request bindings.

Contracts: create-CvFMVb6n.js, VoiceSelectorModal-B339lT2t.js and the official
CLI's read-only prompt assembler. No endpoint here starts inference.
"""
import hashlib
import json
import re
from pathlib import Path


from bridge_errors import BridgeError

from dreama_compat.voice import VoiceCatalog, catalog, media_info

ROOT = Path(__file__).resolve().parent


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
