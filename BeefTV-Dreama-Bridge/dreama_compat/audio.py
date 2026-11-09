"""Official Dreama AI Lab audio tasks, with one-shot submission and exact input.

Contracts observed in production AILab-BH8ZIx7y.js / nestjs-DIQl8Szq.js.
The voice catalog is reference audio; its provider metadata is not a new model.
"""
from job_binding import job_scope, verify_frozen

import base64
import json
import math
import re
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

from dreama_video import BridgeError, DreamaGateway
from .enhancement import MEDIA_TOOLS, file_digest
from . import assets, http

ROOT = Path(__file__).resolve().parents[1]
TASK_PATH = http.AUDIO_TASK
MAX_AUDIO = 20 * 1024 * 1024
SEED = 'dreama-seed-audio-1.0'
CLONE = 'dreama-voice-clone'


def voices():
    body = json.loads((ROOT / 'audio-catalog.json').read_text('utf-8'))
    return {str(row['id']): row for row in body['voices']}


def audio_models():
    return {
        SEED: {'id': SEED, 'label': '剧梦 Seed Audio 1.0',
               'taskType': 'voiceover-generate-doubao', 'maxChars': 2200},
        CLONE: {'id': CLONE, 'label': '剧梦文本配音',
                'taskType': 'voiceover-generate', 'maxChars': 3000},
    }


def resolve_audio_model(model):
    result = audio_models().get(model)
    if result:
        return dict(result)
    if isinstance(model, str) and model.startswith(CLONE + '-'):
        voice = voices().get(model[len(CLONE) + 1:])
        if voice:
            return {**audio_models()[CLONE], 'id': model,
                    'label': '剧梦文本配音 · ' + voice['name'], 'voiceId': str(voice['id'])}
    raise BridgeError('音频模型无效。')


def cdn_url(value):
    if not isinstance(value, str):
        return False
    try:
        p = urlsplit(value)
        return (p.scheme == 'https' and p.hostname == 'jzinnovation.jxbaobian.cn'
                and p.port in (None, 443) and not p.username and not p.password)
    except ValueError:
        return False


def decode_audio(value):
    if not isinstance(value, str):
        raise BridgeError('参考音频格式无效。')
    match = re.fullmatch(r'data:(audio/(?:mpeg|x-mpeg|mp3|wav|x-wav|x-pn-wav|wave|vnd\.wave|ogg|webm|opus|aac|aacp|flac|x-flac|mp4|m4a|x-m4a));base64,(.*)', value, re.S)
    if not match or len(match[2]) > 4 * ((MAX_AUDIO + 2) // 3):
        raise BridgeError('参考音频需为不超过20MiB的音频data URL。')
    try:
        raw = base64.b64decode(match[2], validate=True)
    except ValueError:
        raise BridgeError('参考音频数据无效。') from None
    if not raw or len(raw) > MAX_AUDIO:
        raise BridgeError('参考音频为空或超过20MiB。')
    return raw


def probe_audio(path):
    try:
        proc = subprocess.run([str(MEDIA_TOOLS / 'ffprobe.exe'), '-v', 'error',
            '-protocol_whitelist', 'file', '-show_streams', '-show_format', '-of', 'json', str(path)],
            capture_output=True, text=True, encoding='utf-8', timeout=60,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        body = json.loads(proc.stdout)
        streams = body.get('streams', [])
        audio = [s for s in streams if s.get('codec_type') == 'audio']
        duration = float(body.get('format', {}).get('duration', 0))
        if proc.returncode or not audio or any(s.get('codec_type') == 'video' for s in streams) or not math.isfinite(duration) or duration <= 0:
            raise ValueError('invalid audio')
        return {'duration': duration, 'bytes': Path(path).stat().st_size,
                'sha256': file_digest(path), 'sampleRate': int(audio[0].get('sample_rate', 0)),
                'channels': audio[0].get('channels'), 'codec': audio[0].get('codec_name')}
    except (ValueError, KeyError, OSError, subprocess.SubprocessError):
        raise BridgeError('文件不是有效的独立音频。') from None


def number(data, key, default, lower, upper):
    value = data.get(key, default)
    if type(value) not in (int, float) or not math.isfinite(value) or not lower <= value <= upper:
        raise BridgeError(f'音频参数 {key} 超出支持范围。')
    return value


def validate_audio_request(data):
    if not isinstance(data, dict):
        raise BridgeError('音频请求必须是JSON对象。')
    allowed = {'model', 'prompt', 'input', 'voice', 'audios', 'images', 'pitch', 'speed',
               'volume', 'format', 'response_format', 'instructions', 'emotion_vector', 'character_voices', 'dreama'}
    if set(data) - allowed:
        raise BridgeError('音频请求含未支持的参数。')
    spec = resolve_audio_model(data.get('model'))
    prompt = data.get('prompt', data.get('input'))
    if 'input' in data and 'prompt' in data and data['input'] != data['prompt']:
        raise BridgeError('音频正文与提示词不一致，未改写或提交。')
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > spec['maxChars']:
        raise BridgeError('音频正文为空或超出模型字数限制。')
    if data.get('instructions'):
        raise BridgeError('该接口不支持独立instructions；请将需要的描述直接写在正文中。')
    if data.get('format', data.get('response_format', 'mp3')) != 'mp3':
        raise BridgeError('剧梦音频目前返回MP3，请选择MP3格式。')
    audios, images = data.get('audios', []), data.get('images', [])
    if not isinstance(audios, list) or not isinstance(images, list):
        raise BridgeError('参考素材必须使用列表。')
    if len(audios) > 3 or len(images) > 1 or audios and images:
        raise BridgeError('参考生音频支持最多3条音频或1张图片，不能混合。')
    voice = str(data.get('voice') or '')
    from voice_bindings import validate_bindings, catalog
    bindings = validate_bindings(data, len(images), audio=True)
    bound_voice = None
    if bindings:
        if audios or images:
            raise BridgeError('人物音色绑定不能与其他音频或图片参考混用，请只保留一个说话人物。')
        binding = bindings[0]
        if 'voice_id' in binding:
            bound_voice = catalog().resolve(binding['voice_id'])
            if voice not in ('', 'dreama-auto', binding['voice_id']):
                raise BridgeError('所选音色与人物绑定音色冲突，请使用自动音色或匹配的音色。')
            voice = binding['voice_id']
        else:
            if voice not in ('', 'dreama-auto') or spec.get('voiceId'):
                raise BridgeError('模型固定音色与人物声音样本冲突，请选择普通文本配音模型。')
            audios = [binding['audio']]
    selected_voice = spec.get('voiceId')
    if selected_voice:
        if voice and voice not in ('dreama-auto', selected_voice):
            raise BridgeError('所选音色与模型绑定音色不一致。')
        voice = selected_voice
    if voice and voice != 'dreama-auto' and not bound_voice and voice not in voices():
        raise BridgeError('所选音色不是剧梦音色，请在任务页选择剧梦音色。')
    pitch = number(data, 'pitch', 0, -12, 12)
    speed = number(data, 'speed', 1, 0.5, 2)
    volume = number(data, 'volume', 1, 0.5, 2)
    emotion = data.get('emotion_vector', [])
    if emotion and (not isinstance(emotion, list) or len(emotion) != 8 or
                    any(type(n) not in (int, float) or not math.isfinite(n) or not 0 <= n <= 1 for n in emotion)):
        raise BridgeError('情绪向量需为8个0–1数值。')
    if spec['taskType'] == 'voiceover-generate':
        if images or len(audios) > 1 or (audios and voice not in ('', 'dreama-auto')):
            raise BridgeError('文本配音需选择一个剧梦音色，或连接一条参考音频。')
        if pitch != 0 or speed != 1 or volume != 1:
            raise BridgeError('剧梦文本配音接口不支持独立音调、语速或音量参数。')
        if not audios and voice in ('', 'dreama-auto'):
            raise BridgeError('请选择剧梦音色或连接一条参考音频后再生成。')
    elif voice not in ('', 'dreama-auto'):
        if audios or images:
            raise BridgeError('请使用指定音色或参考素材中的一种，避免覆盖所选音色。')
        audios = [(bound_voice or voices()[voice])['voiceUrl']]
    return spec, prompt, audios, images, voice, {
        'format': 'mp3', 'sample_rate': 48000, 'pitch_rate': pitch,
        'speech_rate': round((speed - 1) * 100, 6), 'loudness_rate': round((volume - 1) * 100, 6)}, emotion


def estimate_points(job):
    return 120 if job['taskType'] == 'voiceover-generate-doubao' else math.ceil(len(job['prompt']) / 100)


def input_payload(job):
    if job['taskType'] == 'voiceover-generate-doubao':
        return {'text_prompt': job['prompt'], 'audio_config': job['audioConfig'],
                **({'references': job['references']} if job['references'] else {})}
    return {'audio': job['voiceUrl'], 'value': job['prompt'], 'emo_vector': job['emotionVector']}


class AudioGateway:
    def __init__(self):
        self.base = DreamaGateway()

    def validate_binding(self,binding,spec):
        self.verify_project(binding)
        return self.identity()

    def verify_project(self,binding): return self.base.verify_project(binding)

    def identity(self):
        return self.base.identity()

    def _request(self, method, path=TASK_PATH, *, payload=None, params=None, job=None):
        if path != TASK_PATH or method not in ('GET', 'POST'):
            raise BridgeError('音频接口不在已核实范围。')
        if method == 'POST':
            return http.submit(self.base, 'audio', job, payload, identity_gateway=self)
        return http.request(self.base, 'GET', path, params=params) or {}

    def upload_audio(self, path, meta):
        return assets.upload_audio(self.base, path, meta)

    @job_scope
    def check_ready(self, job):
        verify_frozen(self,job)
        if self.identity() != job['identity']:
            raise BridgeError('Dreama账号已改变，请重新创建音频任务。')
        if job.get('characterVoiceId'):
            from voice_bindings import catalog
            current = catalog().resolve(job['characterVoiceId'], force=True)
            if current['voiceUrl'] != job.get('characterVoiceUrl'):
                raise BridgeError('人物音色样本已改变，请重新创建音频任务。')

    @job_scope
    def submit_video(self, job):
        from submission import verify_submission_intent
        verify_submission_intent(job)
        if job.get('kind') != 'audio':
            raise BridgeError('音频任务缺少本次费用确认。')
        result = self._request('POST', payload={**job['identity'], 'taskType': job['taskType'],
            'taskName': job['taskName'], 'inputPayload': input_payload(job)}, job=job)
        if isinstance(result, dict):
            result = result.get('item', result)
            return {'taskId': result.get('id') or result.get('taskId')}
        return {'taskId': None}

    @staticmethod
    def matches(row, job):
        if not isinstance(row, dict):
            return False
        actual = row.get('inputPayload')
        expected = input_payload(job)
        return (str(row.get('userId')) == str(job['identity']['userId'])
                and row.get('taskName') == job['taskName'] and row.get('taskType') == job['taskType']
                and isinstance(actual, dict) and all(actual.get(k) == v for k, v in expected.items())
                and ('references' in expected or not actual.get('references')))

    @job_scope
    def find_rows(self, job):
        matched = []
        for page in range(1, 11):
            result = self._request('GET', params={**job['identity'], 'taskType': job['taskType'],
                'taskName': job['taskName'], 'page': page, 'pageSize': 100})
            rows, total = result.get('tasks'), result.get('total')
            if not isinstance(rows, list) or type(total) is not int or total < 0:
                raise BridgeError('音频任务列表无法完整核对。')
            matched += [row for row in rows if self.matches(row, job)]
            if len(matched) > 1:
                raise BridgeError('同一音频请求有多个任务，需要人工对账。')
            if page * 100 >= total:
                return matched
            if not rows:
                break
        raise BridgeError('音频任务列表不完整，未重提。')

    @job_scope
    def find_task(self, job):
        rows = self.find_rows(job)
        return str(rows[0].get('id') or rows[0].get('taskId') or '') if rows else None

    @job_scope
    def task_status(self, task_id, job):
        rows = self.find_rows(job)
        if len(rows) != 1 or str(rows[0].get('id') or rows[0].get('taskId')) != str(task_id):
            raise BridgeError('音频任务ID或原始输入绑定不一致。')
        row = rows[0]
        statuses = {'PENDING': 1, 'QUEUED': 1, 'QUEUING': 1, 'WAITING': 1, 'CREATED': 1,
                    'PROCESSING': 2, 'RUNNING': 2, 'COMPLETED': 3, 'SUCCEEDED': 3, 'SUCCESS': 3,
                    'FAILED': 4, 'ERROR': 4, 'CANCELLED': 5, 'CANCELED': 5}
        status = statuses.get(str(row.get('status', '')).upper())
        if status is None:
            raise BridgeError('音频任务状态暂无法识别。')
        url = (row.get('outputPayload') or {}).get('resourceUrl')
        return {'taskId': task_id, 'status': status, 'urls': [url] if cdn_url(url) else []}
