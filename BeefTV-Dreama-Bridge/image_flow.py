"""Image preparation, polling and delivery for the existing Provider API."""
import hashlib
import json
import re
import secrets
import time
import uuid

from bridge_errors import BridgeError
from dreama_image import MEDIA_KEY_PATTERN, ImageGateway, _positive_points, build_image_models, download_image, image_models, validate_image_request
from generation import Generation, POLLABLE
from job_binding import freeze, preparation_scope, job_scope, project_id, series_id

class ImageFlow(Generation):
    def __init__(self,bridge,gateway=None,download=download_image):
        super().__init__(bridge,download,gateway=gateway or ImageGateway())
        self.images=bridge.root/'images'; self.images.mkdir(exist_ok=True)

    @job_scope
    def estimate_job(self,job): return _positive_points(self.gateway.estimate_image(job))
    @job_scope
    def reconcile_task_id(self,job): return self.gateway.inspect_board(job).get('imageTaskId')

    @preparation_scope
    def prepare(self,data,key):
        checked=validate_image_request(data); spec=checked['model']
        if not isinstance(key,str) or not 1<=len(key)<=150: raise BridgeError('请求ID无效。')
        digest=hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        frozen=freeze(data,self.gateway,spec)
        with self.bridge.lock:
            with self.bridge.db() as db:
                db.execute('BEGIN IMMEDIATE'); row=db.execute('SELECT digest,result FROM jobs WHERE key=?',(key,)).fetchone()
                if row and json.loads(row[1]).get('status')=='preparation_queued':
                    if row[0]!=digest: raise BridgeError('同一请求ID对应不同内容。')
                    self.bridge.ensure_not_cancelled(key)
                    row=None
                if row:
                    if row[0]!=digest: raise BridgeError('同一请求ID对应不同内容。')
                    previous=json.loads(row[1])
                    if previous.get('kind')!='image' or previous['status']!='prepared': raise BridgeError('请求已有记录，请先对账；不重复创建。')
                    return previous
                job={'id':key,'kind':'image','model':spec['id'],'selectedModel':data['model'],'modelLabel':spec['label'],
                     'prompt':checked['prompt'],'resolution':checked['resolution'],'aspect_ratio':checked['aspect_ratio'],'n':1,
                     'status':'preparing','stage':'references','createdAt':time.time(),'requestDigest':digest,
                     'name':'BeefTV_Image_'+uuid.uuid4().hex[:12],'generationSubmitted':False,
                     'projectId':project_id(),'seriesId':series_id(),'references':[]}
                job.update(frozen)
                db.execute('INSERT INTO jobs VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET result=excluded.result',(key,digest,json.dumps(job)))
            try:
                job['identity']=frozen['bindingSnapshot']['identity'] or self.gateway.identity(); self.bridge.update(key,job)
                for index,raw in enumerate(checked['images'],1):
                    name=job['name']+'_参考图'+str(index); job['stage']='reference:'+str(index); self.bridge.update(key,job)
                    url=self.gateway.upload_image(raw,name)
                    job['references'].append(self.gateway.create_reference(name,url)); self.bridge.update(key,job)
                job['stage']='save'; self.bridge.update(key,job)
                self.gateway.prepare_board(job); self.bridge.update(key,job)
                job['pointsEstimate']=self.estimate_job(job)
                job.update(status='prepared',stage='complete',quoteId=uuid.uuid4().hex)
                self.bridge.update(key,job); return job
            except Exception as error:
                job.update(status='unknown',error=str(error) if isinstance(error,BridgeError) else type(error).__name__)
                self.bridge.update(key,job)
                raise BridgeError('图像预检中止，未提交收费任务。'+job['error']) from None

    @job_scope
    def poll(self,key):
        with self.bridge.task_lock(key):
            job=self.bridge.get(key)
            if not job or job.get('kind')!='image' or job.get('status') not in POLLABLE: return job
            job['lastPollAt']=time.time()
            try:
                if job['status'] in ('submitting','submission_unknown'):
                    task_id=self.recover_task_id(job)
                    if not self.valid_task_id(task_id):
                        self.bridge.tasks.mark_unknown(key)
                        job.update(status='submission_unknown',error='尚未查到图像任务ID；正在对账，不自动重提。')
                        self.bridge.update(key,job); return job
                    self.bridge.tasks.record_receipt(key,task_id)
                    job.update(taskId=task_id,status='queued',stage='poll',generationSubmitted=True); job.pop('error',None)
                    self.bridge.update(key,job)
                response=self.query_task_status(job); status=response.get('status')
                if type(status) is not int or status not in (1,2,3,4,5): raise BridgeError('图像任务状态未知。')
                if status in (1,2): job['status']='queued' if status==1 else 'processing'
                elif status in (4,5):
                    job.update(status='failed' if status==4 else 'cancelled',stage='terminal',error='图像任务失败' if status==4 else '图像任务已取消')
                else:
                    job['status']='download_failed'; urls=response.get('urls')
                    if not isinstance(urls,list) or not 1<=len(urls)<=32 or any(not isinstance(url,str) or not url for url in urls):
                        raise BridgeError('图像任务成功但未返回有效图像列表。')
                    if job.get('upstreamImageUrls') and job['upstreamImageUrls']!=urls:
                        raise BridgeError('图像任务结果列表已变化，停止回传并等待对账。')
                    keys=job.get('mediaKeys') or [secrets.token_hex(24)+'.img' for _ in urls]
                    if len(keys)!=len(urls) or any(not isinstance(k,str) or not re.fullmatch(MEDIA_KEY_PATTERN,k) for k in keys):
                        raise BridgeError('图像缓存标识无效。')
                    job.update(upstreamImageUrls=urls,mediaKeys=keys); self.bridge.update(key,job)
                    images=[]
                    try:
                        for url,media_key in zip(urls,keys):
                            meta=self.download(url,self.images/media_key); images.append({**meta,'mediaKey':media_key})
                    except Exception:
                        for media_key in keys: (self.images/media_key).unlink(missing_ok=True)
                        raise
                    job.update(status='completed',stage='complete',images=images,result={'images':images},completedAt=time.time())
                    job.pop('error',None)
                self.bridge.update(key,job)
            except Exception as error:
                job['error']=str(error) if isinstance(error,BridgeError) else type(error).__name__
                self.bridge.update(key,job)
            return job
