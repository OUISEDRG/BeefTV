"""Durable one-shot submission, reconciliation, polling and verified video caching."""
import hashlib
import json
import math
import os
import secrets
import subprocess
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit
import requests
from bridge import BridgeError
from job_binding import job_scope

POLLABLE={'submitting','submission_unknown','queued','processing','download_failed'}

def download_video(url,path):
    parsed=urlsplit(url)
    if parsed.scheme!='https' or parsed.hostname!='jzinnovation.jxbaobian.cn' or parsed.port not in (None,443) or parsed.username:
        raise BridgeError('视频地址不在已核实的剧梦CDN中。')
    tmp=path.with_suffix('.part')
    try:
        with requests.get(url,stream=True,timeout=(10,60),allow_redirects=False) as response:
            if response.status_code!=200: raise BridgeError('视频下载HTTP失败。')
            total=0; digest=hashlib.sha256()
            with tmp.open('wb') as out:
                for chunk in response.iter_content(1024*1024):
                    total+=len(chunk)
                    if total>2*1024**3: raise BridgeError('视频超过2GB缓存限制。')
                    out.write(chunk); digest.update(chunk)
        probe=Path('D:/BeefTv/agent-host/media-runtime/ffprobe.exe')
        if not probe.is_file(): raise BridgeError('缺少视频校验工具ffprobe。')
        p=subprocess.run([str(probe),'-v','error','-protocol_whitelist','file','-show_streams','-show_format','-of','json',str(tmp)],
                         capture_output=True,text=True,encoding='utf-8',timeout=60,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        info=json.loads(p.stdout)
        videos=[s for s in info.get('streams',[]) if s.get('codec_type')=='video' and s.get('width',0)>0 and s.get('height',0)>0]
        duration=float(info.get('format',{}).get('duration',0))
        if p.returncode or not videos or not math.isfinite(duration) or duration<=0:
            raise BridgeError('结果不是可读取的视频。')
        os.replace(tmp,path)
        return {'bytes':total,'sha256':digest.hexdigest(),'duration':duration,'width':videos[0]['width'],'height':videos[0]['height']}
    finally:
        tmp.unlink(missing_ok=True)


class Generation:
    def __init__(self,bridge,download=download_video,gateway=None):
        self.bridge=bridge; self.gateway=gateway or bridge.gateway; self.download=download
        self.videos=bridge.root/'videos'; self.videos.mkdir(exist_ok=True)

    def valid_task_id(self,value): return type(value) is int and value>0
    def reconcile_task_id(self,job): return self.gateway.inspect_board(job).get('videoTaskId')
    def query_task_status(self,job): return self.gateway.task_status(job['taskId'])
    def check_ready(self,job): self.gateway.check_ready(job)
    def estimate_job(self,job): return self.gateway.estimate(job['duration'],job['model'])
    def finalize_video(self,job,path,meta): return meta

    @job_scope
    def approve(self,key,quote_id,points,*,before_submit=None):
        # The lock spans validation, durable intent and the single CLI invocation.
        # No generic CLI entry accepts `video produce`.
        with self.bridge.lock:
            job=self.bridge.get(key)
            if not job or job.get('status')!='prepared' or not job.get('quoteId') or job['quoteId']!=quote_id:
                raise BridgeError('任务不在待确认状态或报价已失效。')
            if type(points) not in (int,float) or not math.isfinite(points) or points!=job['pointsEstimate']:
                raise BridgeError('确认积分与当前报价不一致。')
            self.bridge.ensure_not_cancelled(key)
            if before_submit: before_submit()
            self.check_ready(job)
            fresh=self.estimate_job(job)
            if type(fresh) not in (int,float) or not math.isfinite(fresh) or fresh<0:
                raise BridgeError('无法取得当前积分。')
            if fresh!=points:
                job.update(pointsEstimate=fresh,quoteId=uuid.uuid4().hex)
                self.bridge.update(key,job)
                raise BridgeError('积分已变化，请重新确认。')
            self.bridge.ensure_not_cancelled(key)
            if before_submit: before_submit()
            job.update(status='submitting',stage='submit',approvedAt=time.time(),approvedPoints=points,
                       generationSubmitted=None)
            self.bridge.update(key,job)
            try:
                receipt=self.gateway.submit_video(job)
                task_id=receipt.get('taskId') if isinstance(receipt,dict) else receipt
                if not self.valid_task_id(task_id):
                    task_id=self.reconcile_task_id(job)
                if not self.valid_task_id(task_id): raise BridgeError('提交回执未得到唯一任务ID。')
                job.update(taskId=task_id,status='queued',stage='poll',generationSubmitted=True)
                self.bridge.update(key,job)
                return job
            except Exception as error:
                detail=str(error) if isinstance(error,BridgeError) else type(error).__name__
                job.update(status='submission_unknown',error=detail,submissionError=detail)
                self.bridge.update(key,job)
                raise BridgeError('提交回执不明，正在只读对账；不会自动再次扣费。') from None

    def cancel(self,key):
        with self.bridge.lock:
            job=self.bridge.get(key)
            if not job or job['status']!='prepared': raise BridgeError('只能取消尚未确认的本地任务。')
            job.update(status='cancelled',stage='cancelled_before_submit')
            self.bridge.update(key,job)

    @job_scope
    def poll(self,key):
        with self.bridge.lock:
            job=self.bridge.get(key)
            if not job or job['status'] not in POLLABLE: return job
            job['lastPollAt']=time.time()
            try:
                if job['status'] in ('submitting','submission_unknown'):
                    # Dedicated board, exact project/series/prompt checked by gateway.
                    task_id=self.reconcile_task_id(job)
                    if not self.valid_task_id(task_id):
                        job.update(status='submission_unknown',error='尚未查到对应任务ID；需对账，不自动重提。')
                        self.bridge.update(key,job); return job
                    job.update(taskId=task_id,status='queued',stage='poll',generationSubmitted=True)
                    job.pop('error',None)
                    self.bridge.update(key,job)
                response=self.query_task_status(job)
                status=response.get('status')
                if type(status) is not int or status not in (1,2,3,4,5): raise BridgeError('上游任务状态未知。')
                if status in (1,2): job['status']='queued' if status==1 else 'processing'
                elif status in (4,5):
                    job.update(status='failed' if status==4 else 'cancelled',stage='terminal',error='剧梦任务失败' if status==4 else '剧梦任务已取消')
                else:
                    urls=response.get('urls',[])
                    job['status']='download_failed'
                    if not isinstance(urls,list) or len(urls)!=1 or not isinstance(urls[0],str):
                        raise BridgeError('任务成功但未返回唯一视频URL。')
                    media_key=job.get('mediaKey') or secrets.token_hex(24)+'.mp4'
                    job['mediaKey']=media_key
                    self.bridge.update(key,job)
                    path=self.videos/media_key
                    try:
                        meta=self.download(urls[0],path)
                        meta=self.finalize_video(job,path,meta)
                    except Exception:
                        path.unlink(missing_ok=True)
                        raise
                    job.update(status='completed',stage='complete',mediaKey=media_key,video=meta,
                               upstreamVideoUrl=urls[0],completedAt=time.time())
                    job.pop('error',None)
                self.bridge.update(key,job)
            except Exception as error:
                if job['status']=='submitting': job['status']='submission_unknown'
                job['error']=str(error) if isinstance(error,BridgeError) else type(error).__name__
                self.bridge.update(key,job)
            return job
