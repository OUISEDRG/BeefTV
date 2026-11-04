"""Official Dreama AI Lab audio tasks, with one-shot submission and exact input.

Contracts observed in production AILab-BH8ZIx7y.js / nestjs-DIQl8Szq.js.
The voice catalog is reference audio; its provider metadata is not a new model.
"""
from job_binding import project_id, series_id, job_scope, preparation_scope, freeze, verify_frozen, validate_metadata, verify_write_identity

import base64
import hashlib
import json
import math
import os
import re
import secrets
import subprocess
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import requests
from bridge import BridgeError, DreamaGateway, decode_image
from enhancement import EnhancementGateway, MEDIA_TOOLS, file_digest
from generation import Generation, POLLABLE

ROOT = Path(__file__).resolve().parent
BASE = 'https://jzzm.duanju.com/gw'
TASK_PATH = '/nestjs/ai-lab/voiceovers/task'
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
        self.identity_gateway = EnhancementGateway()

    def validate_binding(self,binding,spec):
        self.verify_project(binding)
        return self.identity()

    def verify_project(self,binding): return self.base.verify_project(binding)

    def identity(self):
        return self.identity_gateway.identity()

    def _request(self, method, path=TASK_PATH, *, payload=None, params=None):
        if method=='POST': verify_write_identity(self)
        if path != TASK_PATH or method not in ('GET', 'POST'):
            raise BridgeError('音频接口不在已核实范围。')
        token = json.loads(self.base.credentials.read_text('utf-8-sig'))['accessToken']
        response = requests.request(method, BASE + path, headers={'Authorization': token},
            json=payload, params=params, timeout=(10, 90), allow_redirects=False)
        if response.status_code not in (200, 201):
            raise BridgeError('音频接口HTTP失败：' + str(response.status_code))
        body = response.json()
        if str(body.get('code')) != '0':
            raise BridgeError('音频接口业务失败，code=' + str(body.get('code')))
        return body.get('result', {})

    def upload_audio(self, path, meta):
        auth = self.base.post('/user/resource/getOssToken', {'category': 'resource'})
        bucket = auth.get('bucketName', '')
        if not re.fullmatch(r'[A-Za-z0-9_-]+', bucket):
            raise BridgeError('音频上传bucket无效。')
        key = bucket + '/audio/' + meta['sha256'] + '.mp3'
        url = 'https://jzinnovation.jxbaobian.cn/' + key
        def verify(response):
            if response.status_code != 200:
                return False
            digest, size = hashlib.sha256(), 0
            for chunk in response.iter_content(1024 * 1024):
                size += len(chunk)
                if size > meta['bytes']:
                    raise BridgeError('云端音频大小不一致。')
                digest.update(chunk)
            if size != meta['bytes'] or digest.hexdigest() != meta['sha256']:
                raise BridgeError('云端音频校验失败。')
            return True
        with requests.get(url, stream=True, timeout=(10, 60), allow_redirects=False) as response:
            if verify(response):
                return url
            if response.status_code != 404:
                raise BridgeError('无法核实云端参考音频。')
        with Path(path).open('rb') as source:
            response = requests.post('https://up-z2.qiniup.com', data={'token': auth['token'], 'key': key},
                files={'file': ('reference.mp3', source, 'audio/mpeg')}, timeout=(10, 120), allow_redirects=False)
        if response.status_code != 200 or response.json().get('key') != key:
            raise BridgeError('音频上传回执不匹配。')
        with requests.get(url, stream=True, timeout=(10, 60), allow_redirects=False) as response:
            if not verify(response):
                raise BridgeError('音频上传后无法回读。')
        return url

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
        if job.get('kind') != 'audio' or job.get('status') != 'submitting' or not job.get('approvedAt') or job.get('approvedPoints') != job.get('pointsEstimate'):
            raise BridgeError('音频任务缺少本次费用确认。')
        result = self._request('POST', payload={**job['identity'], 'taskType': job['taskType'],
            'taskName': job['taskName'], 'inputPayload': input_payload(job)})
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


def download_audio(url, path):
    if not cdn_url(url):
        raise BridgeError('音频结果不在剧梦CDN中。')
    temporary = path.with_suffix('.part')
    converted = path.with_suffix('.converted.mp3')
    try:
        with requests.get(url, stream=True, timeout=(10, 60), allow_redirects=False) as response:
            if response.status_code != 200:
                raise BridgeError('音频下载失败。')
            total = 0
            with temporary.open('wb') as output:
                for chunk in response.iter_content(1024 * 1024):
                    total += len(chunk)
                    if total > 100 * 1024 * 1024:
                        raise BridgeError('音频结果超过100MiB。')
                    output.write(chunk)
        meta = probe_audio(temporary)
        if meta['codec'] != 'mp3':
            # Native reference voiceover has no format parameter and may return WAV.
            # BeefTV requested MP3, so convert only the transport format after success.
            proc = subprocess.run([str(MEDIA_TOOLS / 'ffmpeg.exe'), '-v', 'error', '-y',
                '-protocol_whitelist', 'file', '-i', str(temporary), '-map', '0:a:0', '-vn',
                '-c:a', 'libmp3lame', '-q:a', '2', str(converted)], capture_output=True,
                timeout=120, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if proc.returncode:
                raise BridgeError('音频结果转换为MP3失败。')
            converted_meta = probe_audio(converted)
            if converted_meta['codec'] != 'mp3' or abs(converted_meta['duration'] - meta['duration']) > 0.25:
                raise BridgeError('MP3结果时长或编码校验失败。')
            os.replace(converted, temporary)
            meta = {**converted_meta, 'convertedFrom': meta['codec']}
        os.replace(temporary, path)
        return {**meta, 'mimeType': 'audio/mpeg'}
    finally:
        temporary.unlink(missing_ok=True)
        converted.unlink(missing_ok=True)


class AudioFlow(Generation):
    def __init__(self, bridge, gateway=None, download=download_audio):
        super().__init__(bridge, download, gateway=gateway or AudioGateway())
        self.audios = bridge.root / 'audios'
        self.inputs = bridge.root / 'audio-inputs'
        self.audios.mkdir(exist_ok=True)
        self.inputs.mkdir(exist_ok=True)

    def valid_task_id(self, value):
        return (type(value) is int and value > 0) or (isinstance(value, str) and bool(re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value)))

    @job_scope
    def estimate_job(self, job):
        return estimate_points(job)

    @job_scope
    def reconcile_task_id(self, job):
        return self.gateway.find_task(job)

    @job_scope
    def query_task_status(self, job):
        return self.gateway.task_status(job['taskId'], job)

    def _audio_reference(self, value):
        if cdn_url(value):
            return value
        raw = decode_audio(value)
        digest = hashlib.sha256(raw).hexdigest()
        original = self.inputs / (digest + '.input')
        original.write_bytes(raw)
        meta = probe_audio(original)
        if not 0 < meta['duration'] <= 120:
            raise BridgeError('参考音频需为120秒以内。')
        converted = self.inputs / (digest + '.mp3')
        if meta['codec'] == 'mp3':
            converted.write_bytes(raw)
        else:
            proc = subprocess.run([str(MEDIA_TOOLS / 'ffmpeg.exe'), '-v', 'error', '-y',
                '-protocol_whitelist', 'file', '-i', str(original), '-map', '0:a:0', '-vn',
                '-c:a', 'libmp3lame', '-ar', '48000', str(converted)], capture_output=True,
                timeout=120, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if proc.returncode:
                raise BridgeError('参考音频转换失败。')
        return self.gateway.upload_audio(converted, probe_audio(converted))

    @preparation_scope
    def prepare(self, data, key):
        spec, prompt, audios, images, voice, config, emotion = validate_audio_request(data)
        if not isinstance(key, str) or not 1 <= len(key) <= 150:
            raise BridgeError('请求ID无效。')
        digest = hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        frozen=freeze(data,self.gateway,spec)
        with self.bridge.lock:
            with self.bridge.db() as db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute('SELECT digest,result FROM jobs WHERE key=?', (key,)).fetchone()
                if row and json.loads(row[1]).get('status')=='preparation_queued':
                    if row[0]!=digest: raise BridgeError('同一请求ID对应不同内容。')
                    self.bridge.ensure_not_cancelled(key)
                    row=None
                if row:
                    if row[0] != digest:
                        raise BridgeError('同一请求ID对应不同音频内容。')
                    return json.loads(row[1])
                job = {'id': key, 'kind': 'audio', 'model': spec['id'], 'modelLabel': spec['label'],
                       'prompt': prompt, 'taskType': spec['taskType'], 'status': 'preparing', 'stage': 'validate',
                       'createdAt': time.time(), 'requestDigest': digest, 'generationSubmitted': False,
                       'taskName': 'BeefTV_audio_' + uuid.uuid4().hex, 'audioConfig': config,
                       'emotionVector': emotion, 'references': [], 'mimeType': 'audio/mpeg', 'duration': 0}
                job.update(frozen)
                db.execute('INSERT INTO jobs VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET result=excluded.result', (key, digest, json.dumps(job)))
            try:
                job['identity'] = frozen['bindingSnapshot']['identity'] or self.gateway.identity()
                if data.get('character_voices'):
                    from voice_bindings import catalog
                    binding = data['character_voices'][0]
                    job['characterName'] = binding['name']
                    if 'voice_id' in binding:
                        from voice_bindings import media_info
                        selected = catalog().resolve(binding['voice_id'])
                        info = media_info(selected['voiceUrl'])
                        if info['duration'] > 120 or info['bytes'] > MAX_AUDIO:
                            raise BridgeError('所选人物音色样本需为120秒以内且不超过20MiB。')
                        job.update(characterVoiceId=selected['id'], characterVoiceUrl=selected['voiceUrl'])
                        if job['taskType'] == 'voiceover-generate-doubao':
                            # Freeze the URL verified above; a concurrent catalog
                            # refresh must not pair a new binding with an old URL.
                            audios = [selected['voiceUrl']]
                refs = [self._audio_reference(value) for value in audios]
                if job['taskType'] == 'voiceover-generate':
                    job['voiceUrl'] = refs[0] if refs else job.get('characterVoiceUrl') or voices()[voice]['voiceUrl']
                    job['voiceId'] = voice if not refs else None
                else:
                    job['references'] = [{'audio_url': value} for value in refs]
                    if images:
                        job['references'] = [{'image_url': self.gateway.base.upload_image(decode_image(images[0]), 'BeefTV音频参考图')}]
                job.update(pointsEstimate=estimate_points(job), status='prepared', stage='complete', quoteId=uuid.uuid4().hex,
                    pointsDescription='预扣120积分，按剧梦实际结算退还多扣部分' if job['taskType'] == 'voiceover-generate-doubao' else '预计1积分/100字，最终以剧梦结算为准')
                self.bridge.update(key, job)
                return job
            except Exception as error:
                job.update(status='unknown', error=str(error) if isinstance(error, BridgeError) else type(error).__name__)
                self.bridge.update(key, job)
                raise BridgeError('音频预检中止，未提交扣费任务。' + job['error']) from None

    @job_scope
    def poll(self, key):
        with self.bridge.lock:
            job = self.bridge.get(key)
            if not job or job.get('status') not in POLLABLE:
                return job
            job['lastPollAt'] = time.time()
            try:
                if job['status'] in ('submitting', 'submission_unknown'):
                    task_id = self.reconcile_task_id(job)
                    if not self.valid_task_id(task_id):
                        raise BridgeError('尚未查到对应音频任务；只读对账，不自动重提。')
                    job.update(taskId=task_id, status='queued', stage='poll', generationSubmitted=True)
                    job.pop('error', None)
                    self.bridge.update(key, job)
                response = self.query_task_status(job)
                status = response.get('status')
                if type(status) is not int or status not in (1, 2, 3, 4, 5):
                    raise BridgeError('上游音频任务状态未知。')
                if status in (1, 2):
                    job['status'] = 'queued' if status == 1 else 'processing'
                elif status in (4, 5):
                    job.update(status='failed' if status == 4 else 'cancelled', stage='terminal', error='剧梦音频任务失败' if status == 4 else '剧梦音频任务已取消')
                else:
                    job['status'] = 'download_failed'
                    urls = response.get('urls', [])
                    if not isinstance(urls, list) or len(urls) != 1 or not cdn_url(urls[0]):
                        raise BridgeError('音频任务成功但未返回唯一有效音频URL。')
                    media_key = job.get('mediaKey') or secrets.token_hex(24) + '.mp3'
                    job['mediaKey'] = media_key
                    self.bridge.update(key, job)
                    path = self.audios / media_key
                    try:
                        meta = self.download(urls[0], path)
                    except Exception:
                        path.unlink(missing_ok=True)
                        raise
                    job.update(status='completed', stage='complete', audio=meta, upstreamAudioUrl=urls[0], completedAt=time.time())
                    job.pop('error', None)
            except Exception as error:
                if job['status'] == 'submitting':
                    job['status'] = 'submission_unknown'
                job['error'] = str(error) if isinstance(error, BridgeError) else type(error).__name__
            self.bridge.update(key, job)
            return job
