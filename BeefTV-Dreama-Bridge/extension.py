"""Extension preparation and delivery for the existing Provider API."""
import hashlib
import json
import os
import re
import time
import uuid

from bridge_errors import BridgeError
from dreama_compat.extension import (
    ExtensionGateway, build_models, extension_draft, extension_models, extension_target_props,
    extension_target_tag, resolve_extension_model, selected_extension_models, validate_extension_request,
    validate_source, without_verified_target_mentions,
)
from dreama_compat.enhancement import decode_video, file_digest, probe_video
from dreama_video import decode_image, image_reference_indexes
from generation import Generation, download_video
from job_binding import freeze, job_scope, preparation_scope, project_id, series_id

class ExtensionFlow(Generation):
    billing_source='compat_quote'
    def __init__(self,bridge,gateway=None,download=download_video):
        super().__init__(bridge,download,gateway=gateway or ExtensionGateway())
        self.inputs=bridge.root/'extension-inputs';self.inputs.mkdir(exist_ok=True)

    @preparation_scope
    def prepare(self,data,key):
        prompt,duration,raw,images=validate_extension_request(data)
        spec=resolve_extension_model(data)
        if not isinstance(key,str) or not 1<=len(key)<=150:raise BridgeError('请求ID无效。')
        digest=hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        frozen=freeze(data,self.gateway,spec)
        with self.bridge.lock:
            with self.bridge.db() as db:
                db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT digest,result FROM jobs WHERE key=?',(key,)).fetchone()
                if row and json.loads(row[1]).get('status')=='preparation_queued':
                    if row[0]!=digest: raise BridgeError('同一请求ID对应不同内容。')
                    self.bridge.ensure_not_cancelled(key)
                    row=None
                if row:
                    if row[0]!=digest:raise BridgeError('同一请求ID对应不同内容。')
                    prior=json.loads(row[1])
                    if prior['status']!='prepared':raise BridgeError('已有记录需先对账，不重复准备。')
                    return prior
                job={'id':key,'kind':'extension','model':spec['id'],'selectedModel':data['model'],
                     'resolution':spec['resolution'],'modelLabel':spec['label'],'duration':duration,'prompt':prompt,
                     'status':'preparing','stage':'probe','createdAt':time.time(),'requestDigest':digest,
                     'name':'BExt_'+uuid.uuid4().hex[:12],'generationSubmitted':False,
                     'projectId':project_id(),'seriesId':series_id(),'references':[]}
                job.update(frozen)
                db.execute('INSERT INTO jobs VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET result=excluded.result',(key,digest,json.dumps(job)))
            try:
                from voice_bindings import validate_video_bindings, prepare_video_voices
                bindings=validate_video_bindings(data,len(images),spec)
                character_voices=prepare_video_voices(bindings,spec,self.bridge) if bindings else {}
                source_key=hashlib.sha256(raw).hexdigest()+'.mp4';source=self.inputs/source_key
                if not source.exists():
                    tmp=source.with_suffix('.tmp');tmp.write_bytes(raw);os.replace(tmp,source)
                if file_digest(source)!=source_key[:-4]:raise BridgeError('原视频缓存校验失败。')
                meta=probe_video(source);validate_source(meta,spec)
                job.update(sourceKey=source_key,sourceVideo=meta,identity=frozen['bindingSnapshot']['identity'] or self.gateway.media.identity(),stage='upload')
                self.bridge.update(key,job)
                job['sourceVideoUrl']=self.gateway.media.upload_video(source,meta);self.bridge.update(key,job)
                for index,image in enumerate(images,1):
                    name='B'+hashlib.sha256(job['name'].encode()).hexdigest()[:12]+'_'+str(index);job['stage']='reference:'+str(index);self.bridge.update(key,job)
                    url=self.gateway.upload_image(image,name)
                    ref=(self.gateway.create_character_reference(name,url,character_voices[index])
                         if index in character_voices else self.gateway.create_reference(name,url))
                    job['references'].append(ref);self.bridge.update(key,job)
                job['stage']='save';self.bridge.update(key,job)
                self.gateway.prepare_board(job);self.bridge.update(key,job)
                job['pointsEstimate']=self.estimate_job(job)
                job.update(status='prepared',stage='complete',quoteId=uuid.uuid4().hex)
                self.bridge.update(key,job);return job
            except Exception as error:
                job.update(status='unknown',error=str(error) if isinstance(error,BridgeError) else type(error).__name__)
                self.bridge.update(key,job)
                raise BridgeError('延长预检中止，未提交收费任务。'+job['error']) from None

    @job_scope
    def estimate_job(self,job):return self.gateway.estimate_extension(job)

    @job_scope
    def reconcile_task_id(self,job):return self.gateway.submitted_task_id(job)

    @job_scope
    def check_ready(self,job):
        key=job.get('sourceKey','')
        if not re.fullmatch(r'[0-9a-f]{64}\.mp4',key):raise BridgeError('原视频缓存标识无效。')
        source=self.inputs/key
        if not source.is_file() or file_digest(source)!=job['sourceVideo']['sha256']:raise BridgeError('原视频已改变或缺失，请重新准备。')
        self.gateway.check_ready(job)
