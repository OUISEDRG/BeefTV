"""Enhancement preparation and video delivery for the existing Provider API."""
import hashlib
import json
import math
import os
import re
import subprocess
import time
import uuid

from bridge_errors import BridgeError
from dreama_compat.enhancement import (
    EnhancementGateway, MAX_SECONDS, MAX_VIDEO, MEDIA_TOOLS, decode_video, enhancement_models, estimate_points,
    file_digest, probe_video, validate_enhancement_request,
)
from generation import Generation, download_video
from job_binding import freeze, job_scope, preparation_scope, project_id, series_id

class EnhancementFlow(Generation):
    billing_source='estimated'
    def __init__(self,bridge,gateway=None,download=download_video):
        super().__init__(bridge,download,gateway=gateway or EnhancementGateway())
        self.inputs=bridge.root/'enhancement-inputs'; self.inputs.mkdir(exist_ok=True)

    @preparation_scope
    def prepare(self,data,key):
        raw=validate_enhancement_request(data)
        if not isinstance(key,str) or not 1<=len(key)<=150: raise BridgeError('请求ID无效。')
        digest=hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        frozen=freeze(data,self.gateway,enhancement_models()[data['model']])
        with self.bridge.lock:
            with self.bridge.db() as db:
                db.execute('BEGIN IMMEDIATE')
                row=db.execute('SELECT digest,result FROM jobs WHERE key=?',(key,)).fetchone()
                if row and json.loads(row[1]).get('status')=='preparation_queued':
                    if row[0]!=digest: raise BridgeError('同一请求ID对应不同内容。')
                    self.bridge.ensure_not_cancelled(key)
                    row=None
                if row:
                    if row[0]!=digest: raise BridgeError('同一请求ID对应不同内容。')
                    prior=json.loads(row[1])
                    if prior['status']!='prepared': raise BridgeError('请求已有记录，请对账；不重复创建。')
                    return prior
                job={'id':key,'kind':'enhancement','model':data['model'],'status':'preparing','stage':'probe',
                     'createdAt':time.time(),'requestDigest':digest,'generationSubmitted':False,
                     'clientRequestId':str(uuid.uuid4()),'projectId':project_id(),'seriesId':series_id()}
                job.update(frozen)
                db.execute('INSERT INTO jobs VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET result=excluded.result',(key,digest,json.dumps(job)))
            try:
                source_key=hashlib.sha256(raw).hexdigest()+'.mp4'; source=self.inputs/source_key
                if not source.exists():
                    temporary=source.with_suffix('.tmp'); temporary.write_bytes(raw); os.replace(temporary,source)
                if file_digest(source)!=source_key[:-4]: raise BridgeError('本地原视频字节校验失败。')
                meta=probe_video(source); points=estimate_points(meta,data['model'])
                job.update(sourceKey=source_key,sourceVideo=meta,duration=math.ceil(meta['duration']),pointsEstimate=points,
                           identity=frozen['bindingSnapshot']['identity'] or self.gateway.identity(),stage='upload')
                self.bridge.update(key,job)
                job['sourceVideoUrl']=self.gateway.upload_video(source,meta)
                job.update(status='prepared',stage='complete',quoteId=uuid.uuid4().hex)
                self.bridge.update(key,job); return job
            except Exception as error:
                job.update(status='unknown',error=str(error) if isinstance(error,BridgeError) else type(error).__name__)
                self.bridge.update(key,job)
                raise BridgeError('增强预检中止，未提交扣费任务。'+job['error']) from None

    def valid_task_id(self,value):
        try: return isinstance(value,str) and str(uuid.UUID(value))==value
        except ValueError: return False

    @job_scope
    def reconcile_task_id(self,job): return self.gateway.find_task(job)
    @job_scope
    def query_task_status(self,job): return self.gateway.task_status(job['taskId'],job)

    @job_scope
    def verified_source(self,job):
        key=job.get('sourceKey','')
        if not re.fullmatch(r'[0-9a-f]{64}\.mp4',key): raise BridgeError('原视频缓存标识无效。')
        source=self.inputs/key
        if not source.is_file() or file_digest(source)!=job['sourceVideo']['sha256']:
            raise BridgeError('原视频缓存已改变或缺失，请重新创建任务。')
        return source

    @job_scope
    def check_ready(self,job):
        self.verified_source(job)
        self.gateway.check_ready(job)

    @job_scope
    def finalize_video(self,job,path,meta):
        actual=probe_video(path); source=self.verified_source(job)
        if abs(actual['duration']-job['sourceVideo']['duration'])>0.25:
            raise BridgeError('增强视频时长与原视频不一致，暂未回传。')
        if job['sourceVideo']['hasAudio']:
            temporary=path.with_suffix('.audio.mp4')
            try:
                result=subprocess.run([str(MEDIA_TOOLS/'ffmpeg.exe'),'-v','error','-y','-protocol_whitelist','file','-i',str(path),
                    '-protocol_whitelist','file','-i',str(source),'-map','0:v:0','-map','1:a',
                    '-c','copy','-movflags','+faststart',str(temporary)],capture_output=True,timeout=120,
                    creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                if result.returncode: raise BridgeError('原视频音轨保留失败，暂未回传。')
                actual=probe_video(temporary)
                if not actual['hasAudio'] or abs(actual['duration']-job['sourceVideo']['duration'])>0.25:
                    raise BridgeError('保留音轨后的成片校验失败。')
                os.replace(temporary,path); actual['audioRestored']=True
            finally: temporary.unlink(missing_ok=True)
        return actual
