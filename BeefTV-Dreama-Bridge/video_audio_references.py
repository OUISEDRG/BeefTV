"""Standalone video audio references, matching the official free-project editor.

Audio URL mentions are distinct from character-library voice mentions. All media
inspection and conversion here is local; upload happens only after joint limits pass.
"""
import hashlib
import html
import json
import math
import re
import subprocess
from pathlib import Path
from urllib.parse import quote

from bridge import BridgeError


def audio_reference_indexes(prompt, count):
    available = {str(index + 1): index for index in range(count)}
    indexes = []
    for match in re.finditer(r'@音频([0-9]+)', prompt):
        if match[1] not in available:
            raise BridgeError('提示词里的音频编号没有对应参考音频，请检查 @音频N 编号。')
        indexes.append(available[match[1]])
    if set(indexes) != set(range(count)):
        raise BridgeError('请在原提示词需要的位置引用已连接的音频（如 @音频1）；不会自动改写提示词。')
    return indexes


def validate_reference_counts(model, image_count, audio_count, video_count=0):
    limits = model.get('limits', {})
    if not model['minImages'] <= image_count <= model['maxImages']:
        raise BridgeError(f"该模型需要 {model['minImages']}–{model['maxImages']} 张参考图。")
    maximum = limits.get('audioMax', 0)
    if audio_count > maximum:
        raise BridgeError(f'所选模型/分辨率最多支持 {maximum} 个音频参考（独立音频与人物音色合计）。')
    if audio_count < limits.get('audioMin', 0):
        raise BridgeError('所选模型需要音频参考，请按模型要求连接音频。')
    if not limits.get('videoMin', 0) <= video_count <= limits.get('videoMax', 0):
        raise BridgeError('参考视频数量不符合所选模型/分辨率限制。')
    if audio_count and not image_count and not video_count and limits.get('allowAudioOnly') is not True:
        raise BridgeError('所选模型不支持仅音频生成视频，请同时连接参考图片或选择支持纯音频参考的模型。')
    total = image_count + audio_count + video_count
    if not limits.get('totalMin', 0) <= total <= limits.get('totalMax', 9999):
        raise BridgeError('图片、视频与音频参考的总数量不符合所选模型限制。')


def validate_audio_references(data, model, image_count):
    from audio_flow import decode_audio
    audios = data.get('audios', [])
    if not isinstance(audios, list):
        raise BridgeError('参考音频必须为音频 data URL 数组。')
    videos = data.get('videos', [])
    if not isinstance(videos, list):
        raise BridgeError('参考视频必须为视频 data URL 数组。')
    validate_reference_counts(model, image_count, len(audios) + len(data.get('character_voices', [])), len(videos))
    audio_reference_indexes(data['prompt'], len(audios))
    decoded = [decode_audio(value) for value in audios]
    maximum = min(20 * 1024 * 1024, model.get('limits', {}).get('audioSizeMax', 0) * 1024 * 1024)
    minimum = model.get('limits', {}).get('audioSizeMin', 0) * 1024 * 1024
    if any(not minimum <= len(raw) <= maximum for raw in decoded):
        raise BridgeError('参考音频文件大小不符合所选模型/分辨率限制。')
    return decoded


def validate_audio_limits(model, audio_refs, voices=()):
    limits = model.get('limits', {})
    voices = list(voices.values()) if isinstance(voices, dict) else list(voices)
    refs = list(audio_refs) + voices
    if len(refs) > limits.get('audioMax', 0):
        raise BridgeError('独立音频与人物音色合计数量超过当前视频模型限制。')
    total = 0
    seen = set()
    for ref in refs:
        duration = ref.get('duration', ref.get('voiceDuration'))
        size = ref.get('bytes', ref.get('voiceBytes'))
        if (type(duration) not in (int, float) or not math.isfinite(duration)
                or not limits.get('audioDurationMin', 0) <= duration <= limits.get('audioDurationMax', 0)):
            raise BridgeError('参考音频或人物音色时长不符合所选模型/分辨率限制。')
        if size is not None and (type(size) is not int or not limits.get('audioSizeMin', 0) * 1024 * 1024 <= size
                <= min(20 * 1024 * 1024, limits.get('audioSizeMax', 0) * 1024 * 1024)):
            raise BridgeError('参考音频或人物音色大小不符合所选模型/分辨率限制。')
        # Match native resource deduplication, not the number of prompt mentions.
        identity = (('material', ref.get('imageId') or ref.get('materialId')) if ref.get('imageId') or ref.get('materialId')
                    else ('voice-slot', ref['voiceReferenceIndex']) if ref.get('voiceReferenceIndex')
                    else ('voice', ref['voiceId']) if ref.get('voiceId')
                    else None if ref.get('voiceName')
                    else ref.get('url') or ref.get('sha256'))
        if identity is None or identity not in seen:
            total += duration
            if identity is not None:
                seen.add(identity)
    if total > (limits.get('totalAudioDurationMax') or limits.get('audioDurationMax', 0)):
        raise BridgeError('独立音频与人物音色合计时长超过所选模型上限。')


def prepare_audio_references(decoded, model, root):
    from audio_flow import probe_audio
    from enhancement import MEDIA_TOOLS
    result = []
    inputs = Path(root) / 'video-audio-inputs'
    if decoded:
        inputs.mkdir(exist_ok=True)
    for index, raw in enumerate(decoded, 1):
        digest = hashlib.sha256(raw).hexdigest()
        original = inputs / (digest + '.input')
        original.write_bytes(raw)
        original_meta = probe_audio(original)
        validate_audio_limits(model, [original_meta])
        converted = inputs / (digest + '.mp3')
        if original_meta['codec'] == 'mp3':
            converted.write_bytes(raw)
        else:
            try:
                proc = subprocess.run([str(MEDIA_TOOLS / 'ffmpeg.exe'), '-v', 'error', '-y',
                    '-protocol_whitelist', 'file', '-i', str(original), '-map', '0:a:0', '-vn',
                    '-c:a', 'libmp3lame', '-ar', '48000', str(converted)], capture_output=True,
                    timeout=120, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            except (OSError, subprocess.SubprocessError):
                raise BridgeError('参考音频转换失败，尚未上传。') from None
            if proc.returncode:
                raise BridgeError('参考音频转换失败，尚未上传。')
        meta = probe_audio(converted)
        if abs(meta['duration'] - original_meta['duration']) > 0.25:
            raise BridgeError('音频转换后的时长改变，尚未上传。')
        result.append({**meta, 'path': str(converted), 'sourceSha256': digest, 'name': f'音频{index}.mp3'})
    validate_audio_limits(model, result)
    return result


def audio_extra_props(ref):
    return {'fileType': 3, 'fileSizeBytes': ref['bytes'], 'durationSeconds': ref['duration'],
            'materialType': 0, 'materialId': 0, 'assetSource': 'local-upload',
            'name': ref['name'], 'fileName': ref['name'], 'isOpen': False}


def audio_reference_tag(ref):
    from audio_flow import cdn_url
    if not cdn_url(ref.get('url')):
        raise BridgeError('独立音频上传地址不属于已核验的剧梦存储。')
    props = quote(json.dumps(audio_extra_props(ref), ensure_ascii=False, separators=(',', ':')), safe="~()*!.'-")
    return ('<span data-value="' + html.escape(ref['url'], quote=True)
            + '" data-node-type="inline-mention" data-extra-props="' + props
            + '" data-draggable="true"></span>')


def prompt_reference_order(prompt, refs, audio_refs, video_refs=(), *, allow_video_placeholders=False):
    from bridge import image_reference_indexes
    from video_reference_media import video_reference_indexes
    image_reference_indexes(prompt, len(refs))
    audio_reference_indexes(prompt, len(audio_refs))
    if not allow_video_placeholders:
        video_reference_indexes(prompt,len(video_refs))
    kinds={'图片':'image','音频':'audio','视频':'video'}
    pattern=r'@(图片|音频)([0-9]+)' if allow_video_placeholders else r'@(图片|音频|视频)([0-9]+)'
    return [(kinds[match[1]], int(match[2]) - 1)
            for match in re.finditer(pattern, prompt)]


def ordered_references(prompt, refs, audio_refs, video_refs=(), *, allow_video_placeholders=False):
    sources={'image':refs,'audio':audio_refs,'video':video_refs}
    return [sources[kind][index] for kind, index in prompt_reference_order(prompt, refs, audio_refs, video_refs,
        allow_video_placeholders=allow_video_placeholders)]


def validate_total_reference_size(model, images, audios, videos=(), voices=()):
    limits=model.get('limits',{})
    size=sum(len(raw) for raw in images)+sum(ref['bytes'] for ref in [*audios,*videos])
    size+=sum(ref.get('voiceBytes',0) for ref in voices)
    if not limits.get('totalSizeMin',0)*1024*1024 <= size <= limits.get('totalSizeMax',9999)*1024*1024:
        raise BridgeError('图片、视频与音频参考的总大小超过所选模型限制。')


def verify_audio_files(refs):
    for ref in refs:
        path = Path(ref['path'])
        try:
            raw = path.read_bytes()
        except OSError:
            raise BridgeError('已准备的音频文件丢失，请重新准备任务。') from None
        if len(raw) != ref['bytes'] or hashlib.sha256(raw).hexdigest() != ref['sha256']:
            raise BridgeError('已准备的音频内容改变，停止提交。')
