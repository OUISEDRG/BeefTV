"""Ordinary multi-video references using Dreama's native uploaded URL mentions.

Preparation is local and preserves MP4 bytes. MOV/WebM files are converted before
any external write; every size and duration is checked against the chosen catalog
variant, separately from the single-source enhancement/extension protocols.
"""
import base64
import hashlib
import html
import json
import math
import re
import subprocess
from pathlib import Path
from urllib.parse import quote

from bridge import BridgeError


def video_reference_indexes(prompt, count):
    indexes = []
    for match in re.finditer(r'@视频([0-9]+)', prompt):
        index = int(match[1]) - 1
        if str(index + 1) != match[1] or not 0 <= index < count:
            raise BridgeError('提示词里的视频编号没有对应参考视频，请检查 @视频N 编号。')
        indexes.append(index)
    if set(indexes) != set(range(count)):
        raise BridgeError('请在原提示词需要的位置引用已连接的视频（如 @视频1）；不会自动改写提示词。')
    return indexes


def validate_video_references(data, model):
    values = data.get('videos', [])
    limits = model.get('limits', {})
    if not isinstance(values, list):
        raise BridgeError('参考视频必须为视频 data URL 数组。')
    maximum = limits.get('videoMax', 0)
    if len(values) > maximum:
        raise BridgeError(f'所选模型/分辨率最多支持 {maximum} 个参考视频。')
    if len(values) < limits.get('videoMin', 0):
        raise BridgeError('所选模型需要连接参考视频。')
    video_reference_indexes(data['prompt'], len(values))
    max_bytes = int(limits.get('videoSizeMax', 0) * 1024 * 1024)
    min_bytes = limits.get('videoSizeMin', 0) * 1024 * 1024
    result = []
    for value in values:
        if not isinstance(value, str):
            raise BridgeError('参考视频数据无效。')
        match = re.fullmatch(r'data:(?:video/(?:mp4|quicktime|webm|x-m4v)|application/octet-stream);base64,(.*)', value, re.S)
        if not match or len(match[1]) > 4 * ((max_bytes + 2) // 3):
            raise BridgeError('参考视频需为内联 MP4、MOV 或 WebM，文件大小须符合所选模型限制。')
        try:
            raw = base64.b64decode(match[1], validate=True)
        except ValueError:
            raise BridgeError('参考视频数据无效。') from None
        if not raw or not min_bytes <= len(raw) <= max_bytes:
            raise BridgeError('参考视频文件大小不符合所选模型/分辨率限制。')
        result.append(raw)
    return result


def validate_video_limits(model, refs):
    limits = model.get('limits', {})
    if len(refs) > limits.get('videoMax', 0):
        raise BridgeError('参考视频数量超过所选模型/分辨率上限。')
    total = 0
    for ref in refs:
        duration, size = ref.get('duration'), ref.get('bytes')
        if (type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0
                or duration < limits.get('videoDurationMin', 0)
                or duration > limits.get('videoDurationMax', 0)):
            raise BridgeError('参考视频时长不符合所选模型/分辨率限制。')
        if (type(size) is not int or size <= 0 or not limits.get('videoSizeMin', 0) * 1024 * 1024
                <= size <= limits.get('videoSizeMax', 0) * 1024 * 1024):
            raise BridgeError('参考视频大小不符合所选模型/分辨率限制。')
        total += duration
    total_max = limits.get('totalVideoDurationMax', 0)
    if total_max > 0 and total > total_max:
        raise BridgeError(f'参考视频总时长超过所选模型上限 {total_max} 秒。')


def _probe_convertible(path):
    from enhancement import MEDIA_TOOLS
    try:
        proc = subprocess.run([str(MEDIA_TOOLS / 'ffprobe.exe'), '-v', 'error',
            '-protocol_whitelist', 'file', '-show_streams', '-show_format', '-of', 'json', str(path)],
            capture_output=True, text=True, encoding='utf-8', timeout=60,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        body = json.loads(proc.stdout)
        streams = [s for s in body.get('streams', []) if s.get('codec_type') == 'video' and not s.get('disposition', {}).get('attached_pic')]
        fmt = body.get('format', {})
        formats = set(fmt.get('format_name', '').split(','))
        duration = float(fmt.get('duration', 0))
        if (proc.returncode or len(streams) != 1 or not formats.intersection({'mov', 'webm'})
                or not math.isfinite(duration) or duration <= 0):
            raise ValueError('invalid video container')
        return {'duration': duration, 'bytes': Path(path).stat().st_size}
    except (ValueError, KeyError, OSError, subprocess.SubprocessError):
        raise BridgeError('无法读取有效的 MP4、MOV 或 WebM 参考视频。') from None


def prepare_video_references(decoded, model, root):
    from enhancement import MEDIA_TOOLS, probe_video
    result = []
    inputs = Path(root) / 'video-reference-inputs'
    if decoded:
        inputs.mkdir(exist_ok=True)
    for index, raw in enumerate(decoded, 1):
        digest = hashlib.sha256(raw).hexdigest()
        original = inputs / (digest + '.input')
        original.write_bytes(raw)
        converted = inputs / (digest + '.mp4')
        try:
            meta = probe_video(original)
        except BridgeError:
            original_meta = _probe_convertible(original)
            validate_video_limits(model, [original_meta])
            try:
                proc = subprocess.run([str(MEDIA_TOOLS / 'ffmpeg.exe'), '-v', 'error', '-y',
                    '-protocol_whitelist', 'file', '-i', str(original), '-map', '0:V:0', '-map', '0:a:0?',
                    '-c:v', 'libx264', '-preset', 'fast', '-crf', '18', '-pix_fmt', 'yuv420p',
                    '-c:a', 'aac', '-movflags', '+faststart', str(converted)],
                    capture_output=True, timeout=180, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            except (OSError, subprocess.SubprocessError):
                raise BridgeError('参考视频转换失败，尚未上传。') from None
            if proc.returncode:
                raise BridgeError('参考视频转换失败，尚未上传。')
            meta = probe_video(converted)
            if abs(meta['duration'] - original_meta['duration']) > 0.25:
                raise BridgeError('参考视频转换后的时长改变，尚未上传。')
        else:
            converted.write_bytes(raw)
        result.append({**meta, 'fileType': 2, 'path': str(converted), 'sourceSha256': digest, 'name': f'视频{index}.mp4'})
    validate_video_limits(model, result)
    return result


def video_extra_props(ref):
    return {'fileType': 2, 'fileSizeBytes': ref['bytes'], 'durationSeconds': ref['duration'],
            'materialType': 0, 'materialId': 0, 'assetSource': 'local-upload',
            'name': ref['name'], 'fileName': ref['name'], 'isOpen': False}


def video_reference_tag(ref):
    from audio_flow import cdn_url
    if not cdn_url(ref.get('url')):
        raise BridgeError('参考视频上传地址不属于已核验的剧梦存储。')
    props = quote(json.dumps(video_extra_props(ref), ensure_ascii=False, separators=(',', ':')), safe="~()*!.'-")
    return ('<span data-value="' + html.escape(ref['url'], quote=True)
            + '" data-node-type="inline-mention" data-extra-props="' + props
            + '" data-draggable="true"></span>')


def verify_video_files(refs):
    from enhancement import file_digest
    for ref in refs:
        path = Path(ref['path'])
        try:
            if path.stat().st_size != ref['bytes'] or file_digest(path) != ref['sha256']:
                raise BridgeError('已准备的参考视频内容改变，停止提交。')
        except OSError:
            raise BridgeError('已准备的参考视频文件丢失，请重新准备任务。') from None
