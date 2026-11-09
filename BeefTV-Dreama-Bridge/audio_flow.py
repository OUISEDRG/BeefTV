"""Audio preparation, polling and delivery for the existing Provider API."""
import hashlib
import json
import os
import re
import secrets
import subprocess
import time
import uuid

import requests

from bridge import BridgeError, decode_image
from dreama_compat.audio import (
    AudioGateway, CLONE, MAX_AUDIO, SEED, audio_models, cdn_url, decode_audio, estimate_points,
    input_payload, probe_audio, resolve_audio_model, validate_audio_request, voices,
)
from dreama_compat.enhancement import MEDIA_TOOLS, file_digest
from generation import Generation, POLLABLE
from job_binding import freeze, job_scope, preparation_scope

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
    billing_source='estimated'
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
        with self.bridge.task_lock(key):
            job = self.bridge.get(key)
            if not job or job.get('status') not in POLLABLE:
                return job
            job['lastPollAt'] = time.time()
            try:
                if job['status'] in ('submitting', 'submission_unknown'):
                    task_id = self.recover_task_id(job)
                    if not self.valid_task_id(task_id):
                        self.bridge.tasks.mark_unknown(key)
                        raise BridgeError('尚未查到对应音频任务；只读对账，不自动重提。')
                    self.bridge.tasks.record_receipt(key,task_id)
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
