"""Dreama preparation and provider job persistence."""
from job_binding import project_id, series_id, preparation_scope, freeze, validate_metadata
from model_catalog import resolve_model

import hashlib
import json
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


from bridge_errors import BridgeError
from budget_policy import BudgetError, validated_quote
from dreama_video import DreamaGateway, MODEL, PROJECT, SERIES, MAX_IMAGE, decode_image, image_reference_indexes


def validate_request(data):
    if not isinstance(data, dict):
        raise BridgeError('请求必须是JSON对象。')
    allowed = {'model', 'prompt', 'images', 'videos', 'audios', 'duration', 'seconds', 'aspect_ratio', 'resolution', 'character_voices', 'dreama'}
    if set(data) - allowed:
        raise BridgeError('请求含尚未支持的扩展参数，未提交。')
    binding=validate_metadata(data)
    try:model=resolve_model(data.get('model'),data.get('resolution'))
    except ValueError as error:raise BridgeError(str(error)) from None
    prompt = data.get('prompt', '')
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > model['promptMaxChars']:
        raise BridgeError('提示词为空或超出该模型字数限制。')
    duration = data.get('duration', data.get('seconds', 5))
    if type(duration) not in (int,float) or duration not in model['durations']:
        raise BridgeError('时长不在该模型支持的固定秒数范围。')
    if data.get('aspect_ratio', binding['aspectRatio']) != binding['aspectRatio']:
        raise BridgeError('请求画幅与冻结项目绑定不一致。')
    images = data.get('images', [])
    if not isinstance(images, list) or not model['minImages'] <= len(images) <= model['maxImages']:
        raise BridgeError(f"该模型需要 {model['minImages']}–{model['maxImages']} 张参考图。")
    image_reference_indexes(prompt,len(images))
    from voice_bindings import validate_video_bindings
    validate_video_bindings(data, len(images), model)
    from video_audio_references import validate_audio_references
    from video_reference_media import validate_video_references
    validate_audio_references(data, model, len(images))
    validate_video_references(data,model)
    decoded = [decode_image(image) for image in images]
    return prompt, int(duration), decoded


class Bridge:
    def __init__(self, root, gateway=None, *, budget_policy=None):
        from media_delivery import LEDGER_SCHEMA_VERSION, MediaDelivery
        self.root = Path(root); self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / 'jobs.sqlite3'
        self.gateway = gateway or DreamaGateway()
        self.lock = threading.Lock()
        self._task_locks = {}
        self._task_locks_guard = threading.Lock()
        self._budget_policy = budget_policy
        with self.db() as db:
            version=db.execute('PRAGMA user_version').fetchone()[0]
            if version > LEDGER_SCHEMA_VERSION:
                raise BridgeError('任务账本版本高于本执行器支持范围，禁止降级写入。')
            db.execute('CREATE TABLE IF NOT EXISTS jobs (key TEXT PRIMARY KEY, digest TEXT NOT NULL, result TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS preparation_requests (key TEXT PRIMARY KEY, payload TEXT NOT NULL, kind TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0)')
            if version<2: db.execute('PRAGMA user_version=2')
        from catalog_cache import restore
        restore(self.root)
        from task_repository import TaskRepository
        self.tasks = TaskRepository(self.db_path)
        self.delivery = MediaDelivery(self)

    def task_lock(self, key):
        with self._task_locks_guard:
            return self._task_locks.setdefault(key, threading.RLock())

    def budget_config(self):
        from budget_policy import read_policy
        import copy
        return copy.deepcopy(self._budget_policy) if self._budget_policy is not None else read_policy(self.root/'budget-policy.json')

    def budget_is_configured(self):
        from budget_policy import budget_window,BudgetError,KINDS
        config=self.budget_config()
        for kind in KINDS:
            try: budget_window(config,kind,time.time())
            except BudgetError: continue
            return True
        return False

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.db_path, timeout=10)
        try:
            connection.execute('PRAGMA foreign_keys=ON')
            with connection:
                yield connection
        finally:
            connection.close()

    def get(self, key):
        with self.db() as db:
            row = db.execute('SELECT result FROM jobs WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def update(self, key, result):
        requested_status=result.get('status')
        result['schemaVersion']=2
        with self.db() as db:
            # Serialize cancellation and the paid-submit intent before either
            # reads ledger state. No external operation is inside this txn.
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT result FROM jobs WHERE key=?',(key,)).fetchone()
            previous=json.loads(row[0]) if row else {}
            if previous.get('status') == 'failed' and previous.get('generationSubmitted') is False:
                released = db.execute("SELECT 1 FROM p1_submissions WHERE job_id=? AND settlement_status='released'",
                                      (key,)).fetchone()
                if released:
                    # A peer may finish a read-only poll after the proven no-charge result.
                    result.clear()
                    result.update(previous)
                    return
            for field in ('bindingSnapshot','modelSnapshot','audioReferenceSnapshot','videoReferenceSnapshot'):
                if field in previous and previous[field]!=result.get(field):
                    raise BridgeError('任务冻结快照不可修改。')
            binding=result.get('bindingSnapshot',{})
            if result.get('modelSnapshot') and result.get('model')!=result['modelSnapshot'].get('id'):
                raise BridgeError('执行模型与冻结模型快照不一致。')
            for field in ('projectId','seriesId','identity','canvasId','nodeId'):
                if field in result and field in binding and result[field]!=binding[field]:
                    raise BridgeError('任务运行字段与冻结绑定不一致。')
            cancellation=db.execute('SELECT cancel_requested FROM preparation_requests WHERE key=?',(key,)).fetchone()
            if requested_status=='submitting' and cancellation and cancellation[0]:
                raise BridgeError('已请求取消，未提交收费任务。')
            if cancellation and cancellation[0] and result.get('status') in ('preparing','prepared','unknown'):
                result.update(status='cancelled',stage='cancelled_before_submit',cancelRequested=True)
            db.execute('UPDATE jobs SET result=? WHERE key=?', (json.dumps(result, ensure_ascii=False), key))
            self.tasks.sync_job(db,key,result)
        if requested_status!='cancelled' and result.get('status')=='cancelled' and previous.get('status') in ('preparation_queued','preparing'):
            raise BridgeError('准备已取消，未提交收费任务。')

    def recover_preparations(self):
        from job_binding import historical
        with self.db() as db:
            rows=db.execute('SELECT key,result FROM jobs').fetchall()
            for key,raw in rows:
                job=json.loads(raw)
                job['schemaVersion']=2
                if 'bindingSnapshot' not in job and job.get('status')!='preparation_queued':
                    job['bindingSnapshot']=historical(job)
                    job['migrationVersion']=1
                    if not job['bindingSnapshot'].get('identity'):
                        job['bindingVerificationRequired']=True
                if job.get('status')=='preparing':
                    job.update(status='unknown',error='准备过程被中断，可能已有远端写入；只对账，不自动重做。')
                db.execute('UPDATE jobs SET result=? WHERE key=?',(json.dumps(job,ensure_ascii=False),key))
                # Keep original preparation payloads for reconciliation and migration.

    def ensure_not_cancelled(self,key):
        with self.db() as db:
            row=db.execute('SELECT cancel_requested FROM preparation_requests WHERE key=?',(key,)).fetchone()
        if row and row[0]: raise BridgeError('已请求取消，未提交收费任务。')

    @preparation_scope
    def prepare(self, data, key, *, recover=False):
        prompt, duration, images = validate_request(data)
        spec=resolve_model(data['model'],data.get('resolution'))
        if not isinstance(key, str) or not 1 <= len(key) <= 150:
            raise BridgeError('请求ID无效。')
        digest = hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        frozen=freeze(data,self.gateway,spec,requires_standalone_audio=bool(data.get('audios')))
        resume_index = None
        with self.lock:
            with self.db() as db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute('SELECT digest,result FROM jobs WHERE key=?', (key,)).fetchone()
                if row and json.loads(row[1]).get('status')=='preparation_queued':
                    if row[0]!=digest: raise BridgeError('同一请求ID对应不同内容。')
                    self.ensure_not_cancelled(key)
                    row=None
                if row:
                    result = json.loads(row[1])
                    if row[0] != digest:
                        raise BridgeError('同一请求ID对应不同内容。')
                    if result['status'] == 'prepared':
                        return result
                    stage = re.fullmatch(r'bind:([1-9][0-9]*)', result.get('stage', ''))
                    if (not recover or result['status'] != 'unknown' or not stage
                            or result.get('generationSubmitted') is not False
                            or any(result.get(k) is not None for k in ('taskId','storyboardId','approvedAt','autoAttemptedQuoteId'))
                            or data.get('audios') or data.get('videos')
                            or result.get('bindingSnapshot') != frozen.get('bindingSnapshot')
                            or result.get('modelSnapshot') != frozen.get('modelSnapshot')):
                        raise BridgeError('该请求已有未确认操作或提交痕迹，不能恢复准备；不会重复提交。')
                    self.ensure_not_cancelled(key)
                    resume_index = int(stage[1])
                    if len(result['references']) != resume_index-1 or not result.get('lastUploadedUrl'):
                        raise BridgeError('恢复检查点不完整。')
                    name = result['name']
                    result['recoveryHistory'] = result.get('recoveryHistory', []) + [{'stage':result['stage'],'at':time.time()}]
                    result.update(status='preparing')
                    result.pop('error', None)
                else:
                    if recover:
                        raise BridgeError('恢复必须使用已有任务ID。')
                    name = 'BeefTV_' + uuid.uuid4().hex[:12]
                    result = {'id': key, 'name': name, 'status': 'preparing', 'createdAt': time.time(),
                              'model':spec['id'],'selectedModel':data['model'],'resolution':spec['resolution'],
                              'modelLabel':spec['name']+' · '+(spec['resolution'] or '默认'),
                              'duration':duration,'prompt':prompt,'requestDigest':digest,
                              'projectId': project_id(), 'seriesId': series_id(), 'generationSubmitted': False, 'references': []}
                    result.update(frozen)
                db.execute('INSERT INTO jobs VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET result=excluded.result', (key, digest, json.dumps(result)))
            try:
                from voice_bindings import validate_video_bindings, prepare_video_voices
                from video_audio_references import validate_audio_references, prepare_audio_references, validate_total_reference_size
                from video_reference_media import validate_video_references, prepare_video_references
                bindings=validate_video_bindings(data,len(images),spec)
                audio_refs=prepare_audio_references(validate_audio_references(data,spec,len(images)),spec,self.root)
                video_refs=prepare_video_references(validate_video_references(data,spec),spec,self.root)
                validate_total_reference_size(spec,images,audio_refs,video_refs)
                character_voices=prepare_video_voices(bindings,spec,self,standalone_audio=audio_refs,
                    reference_images=images,reference_videos=video_refs) if bindings else {}
                if audio_refs:
                    result['audioReferences']=[]
                    for index, ref in enumerate(audio_refs,1):
                        result['stage']='upload-audio:'+str(index); self.update(key,result)
                        url=self.gateway.upload_audio_reference(ref['path'],ref)
                        result['audioReferences'].append({**ref,'url':url}); self.update(key,result)
                    result['audioReferenceSnapshot']=json.loads(json.dumps(result['audioReferences']))
                    self.update(key,result)
                if video_refs:
                    result['videoReferences']=[]
                    for index,ref in enumerate(video_refs,1):
                        result['stage']='upload-video:'+str(index); self.update(key,result)
                        url=self.gateway.upload_video_reference(ref['path'],ref)
                        result['videoReferences'].append({**ref,'url':url}); self.update(key,result)
                    result['videoReferenceSnapshot']=json.loads(json.dumps(result['videoReferences']))
                    self.update(key,result)
                if resume_index:
                    self.gateway.check_character_references(result['references']) if hasattr(self.gateway,'check_character_references') else None
                for index, image in enumerate(images, 1):
                    self.ensure_not_cancelled(key)
                    if resume_index and index < resume_index:
                        continue
                    if index == resume_index:
                        pending = result.get('pendingReference') or {'name':name+'_参考图'+str(index),'url':result['lastUploadedUrl']}
                        ref = self.gateway.recover_reference(pending['name'],pending['url'],character_voices.get(index))
                    else:
                        # ASCII and <=16 characters: below the upstream 18-character child-name limit.
                        ref_name = 'B' + hashlib.sha256(name.encode()).hexdigest()[:12] + '_' + str(index)
                        result['stage'] = 'upload:' + str(index); self.update(key, result)
                        url = self.gateway.upload_image(image, ref_name)
                        result['lastUploadedUrl'] = url
                        result['pendingReference'] = {'name':ref_name,'url':url}
                        result['stage'] = 'bind:' + str(index); self.update(key, result)
                        ref = (self.gateway.create_character_reference(ref_name,url,character_voices[index])
                               if index in character_voices else self.gateway.create_reference(ref_name, url))
                    result['references'].append(ref)
                    result.pop('pendingReference', None)
                    self.update(key, result)
                result['stage'] = 'save'; self.update(key, result)
                result.update(self.gateway.save_and_readback(prompt, result['references'], duration, name,
                    **({'audio_refs':result['audioReferences']} if audio_refs else {}),
                    **({'video_refs':result['videoReferences']} if video_refs else {})))
                result['stage'] = 'estimate'; self.update(key, result)
                try:
                    result['pointsEstimate'] = validated_quote(self.gateway.estimate(duration,result['model']), maximum=1000000)
                except BudgetError:
                    raise BridgeError('无法确认积分预估。') from None
                result['quoteId']=uuid.uuid4().hex
                result['status'] = 'prepared'; result['stage'] = 'complete'
                self.update(key, result)
                return result
            except Exception as error:
                result['status'] = 'unknown'
                result['error'] = str(error) if isinstance(error, BridgeError) else type(error).__name__
                self.update(key, result)
                raise BridgeError('预检中止；已保留任务记录，不自动重试。' + result['error']) from None
