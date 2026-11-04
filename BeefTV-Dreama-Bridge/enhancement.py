"""Dreama's existing video-enhancement API, separate from prompt generation."""
from job_binding import project_id, series_id, job_scope, preparation_scope, freeze, verify_frozen, validate_metadata, verify_write_identity

import base64
import hashlib
import json
import math
import os
import re
import subprocess
import time
import uuid
from fractions import Fraction
from pathlib import Path
import requests
from bridge import BridgeError, DreamaGateway
from generation import Generation, download_video

MAX_VIDEO=40*1024*1024
MAX_SECONDS=120
MEDIA_TOOLS=Path('D:/BeefTv/agent-host/media-runtime')
BASE='https://jzzm.duanju.com/gw'

def enhancement_models():
    return {
        'dreama-enhance-a':{'id':'dreama-enhance-a','label':'剧梦视频增强 A','type':'video-enhancement-aliyun','priority':None,'rate':1},
        'dreama-enhance-b':{'id':'dreama-enhance-b','label':'剧梦视频增强 B','type':'video-enhancement','priority':None,'rate':1},
        'dreama-enhance-c-low':{'id':'dreama-enhance-c-low','label':'剧梦视频增强 C · 闲时','type':'video-enhancement-c','priority':'low','rate':1},
        'dreama-enhance-c-high':{'id':'dreama-enhance-c-high','label':'剧梦视频增强 C · 优先','type':'video-enhancement-c','priority':'high','rate':2},
    }

def validate_enhancement_request(data):
    if not isinstance(data,dict) or data.get('model') not in enhancement_models():
        raise BridgeError('增强模型无效。')
    allowed={'model','prompt','videos','images','audios','duration','seconds','aspect_ratio','resolution','dreama'}
    if set(data)-allowed or data.get('images') or data.get('audios'):
        raise BridgeError('视频增强只接收一个原视频，不接收图片或独立音频。')
    videos=data.get('videos')
    if not isinstance(videos,list) or len(videos)!=1 or not isinstance(videos[0],str):
        raise BridgeError('请连接且仅连接一个原视频。')
    return decode_video(videos[0])

def decode_video(value):
    if not isinstance(value,str): raise BridgeError('原视频数据无效。')
    prefixes=('data:video/mp4;base64,','data:application/octet-stream;base64,')
    if not value.startswith(prefixes): raise BridgeError('增强输入需为内联MP4，不读取任意网址或本地路径。')
    encoded=value.split(',',1)[1]
    if len(encoded)>4*((MAX_VIDEO+2)//3): raise BridgeError('原视频超过40MiB，请先压缩或裁剪。')
    try: raw=base64.b64decode(encoded,validate=True)
    except ValueError: raise BridgeError('视频数据无效。') from None
    if not raw or len(raw)>MAX_VIDEO: raise BridgeError('原视频为空或超过40MiB。')
    return raw

def file_digest(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''): digest.update(chunk)
    return digest.hexdigest()

def probe_video(path):
    try:
        result=subprocess.run([str(MEDIA_TOOLS/'ffprobe.exe'),'-v','error','-protocol_whitelist','file',
            '-show_streams','-show_format','-of','json',str(path)],capture_output=True,text=True,
            encoding='utf-8',timeout=60,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        body=json.loads(result.stdout)
        streams=body.get('streams',[]); videos=[s for s in streams if s.get('codec_type')=='video' and s.get('width',0)>0 and s.get('height',0)>0]
        duration=float(body.get('format',{}).get('duration',0))
        if result.returncode or len(videos)!=1 or not math.isfinite(duration) or duration<=0:
            raise ValueError('invalid video')
        fmt=body.get('format',{})
        if ('mp4' not in fmt.get('format_name','').split(',') or
            fmt.get('tags',{}).get('major_brand','').strip() not in
            {'isom','iso2','iso3','iso4','iso5','iso6','iso7','iso8','iso9','mp41','mp42','avc1','hvc1','dash','M4V','MSNV'}):
            raise ValueError('not MP4 container')
        v=videos[0]; fps=float(Fraction(v.get('avg_frame_rate') or v.get('r_frame_rate') or '0'))
        if not math.isfinite(fps) or fps<=0: raise ValueError('invalid frame rate')
        return {'width':v['width'],'height':v['height'],'duration':duration,'fps':fps,
                'hasAudio':any(s.get('codec_type')=='audio' for s in streams),
                'bytes':Path(path).stat().st_size,'sha256':file_digest(path)}
    except (ValueError,KeyError,ZeroDivisionError,subprocess.SubprocessError,OSError):
        raise BridgeError('无法读取有效MP4视频或视频帧率。') from None

def estimate_points(meta,model):
    spec=enhancement_models().get(model)
    duration=meta.get('duration'); fps=meta.get('fps')
    if not spec or type(duration) not in (int,float) or not math.isfinite(duration) or not 0<duration<=MAX_SECONDS:
        raise BridgeError('视频增强当前支持最长120秒的原视频。')
    if type(fps) not in (int,float) or not math.isfinite(fps) or fps<=0:
        raise BridgeError('无法确认原视频帧率。')
    if spec['type']=='video-enhancement-c' and fps>30:
        raise BridgeError('剧梦视频增强C不支持超过30fps的视频，请选A/B或先处理帧率。')
    return math.ceil(duration)*spec['rate']

class EnhancementGateway:
    def __init__(self): self.base=DreamaGateway()

    def validate_binding(self,binding,spec):
        self.verify_project(binding)
        return self.identity()

    def verify_project(self,binding): return self.base.verify_project(binding)

    def _request(self,method,path,*,payload=None,params=None):
        if method=='POST': verify_write_identity(self)
        if not ((method=='GET' and (path in ('/nestjs/tasks','/nestjs/tasks/running-count') or re.fullmatch(r'/nestjs/tasks/[0-9a-f-]{36}',path))) or (method=='POST' and path=='/nestjs/tasks')):
            raise BridgeError('增强接口不在已核实范围。')
        token=json.loads(self.base.credentials.read_text('utf-8-sig'))['accessToken']
        response=requests.request(method,BASE+path,headers={'Authorization':token},json=payload,params=params,
                                  timeout=(10,90),allow_redirects=False)
        if response.status_code!=200: raise BridgeError('增强接口HTTP失败：'+str(response.status_code))
        data=response.json()
        if str(data.get('code'))!='0': raise BridgeError('增强接口业务失败，code='+str(data.get('code')))
        return data.get('result',{})

    def identity(self):
        account=self.base._invoke('auth','whoami')
        if type(account.get('userId')) is not int or account['userId']<=0 or account.get('expired'):
            raise BridgeError('无法确认当前Dreama账号。')
        return {key:account[key] for key in ('userId','teamId','orgId') if type(account.get(key)) is int and account[key]>0}

    def counts(self):
        result=self._request('GET','/nestjs/tasks/running-count',params={'type':'video-enhancement,video-enhancement-aliyun,video-enhancement-c'})
        if not isinstance(result,dict): raise BridgeError('无法核对增强队列。')
        return result

    def upload_video(self,path,meta):
        auth=self.base.post('/user/resource/getOssToken',{'category':'resource'})
        bucket=auth.get('bucketName','')
        if not re.fullmatch(r'[A-Za-z0-9_-]+',bucket): raise BridgeError('视频上传授权bucket无效。')
        key=bucket+'/video/'+meta['sha256']+'.mp4'; url='https://jzinnovation.jxbaobian.cn/'+key
        def verify(response):
            if response.status_code!=200: return False
            digest=hashlib.sha256(); size=0
            for chunk in response.iter_content(1024*1024):
                size+=len(chunk)
                if size>meta['bytes']: raise BridgeError('云端视频大小与原文件不一致。')
                digest.update(chunk)
            if size!=meta['bytes'] or digest.hexdigest()!=meta['sha256']: raise BridgeError('云端视频字节校验失败。')
            return True
        with requests.get(url,stream=True,timeout=(10,60),allow_redirects=False) as existing:
            if verify(existing): return url
            if existing.status_code!=404: raise BridgeError('无法核实云端已有视频。')
        with Path(path).open('rb') as source:
            uploaded=requests.post('https://up-z2.qiniup.com',data={'token':auth['token'],'key':key},
                files={'file':('source.mp4',source,'video/mp4')},timeout=(10,120),allow_redirects=False)
        if uploaded.status_code!=200 or uploaded.json().get('key')!=key: raise BridgeError('视频上传回执不匹配。')
        with requests.get(url,stream=True,timeout=(10,60),allow_redirects=False) as saved:
            if not verify(saved): raise BridgeError('上传后视频无法回读。')
        return url

    @job_scope
    def check_ready(self,job):
        verify_frozen(self,job)
        if self.identity()!=job['identity']: raise BridgeError('Dreama账号已改变，请重新创建增强任务。')
        spec=enhancement_models()[job['model']]
        estimate_points(job['sourceVideo'],job['model'])
        if spec['priority']:
            counts=self.counts(); key='video-enhancement-c-'+('priority' if spec['priority']=='high' else 'leisure')
            count=counts.get(key)
            if type(count) is not int or count<0: raise BridgeError('无法读取增强C的实时队列。')
            if count>=(1500 if spec['priority']=='high' else 4500): raise BridgeError('所选增强C队列已满，请稍后确认。')

    def estimate(self,duration,model):
        return int(duration)*enhancement_models()[model]['rate']

    @job_scope
    def submit_video(self,job):
        if job.get('kind')!='enhancement' or job.get('status')!='submitting' or not job.get('approvedAt') or job.get('approvedPoints')!=job.get('pointsEstimate'):
            raise BridgeError('视频增强缺少本次费用确认。')
        spec=enhancement_models()[job['model']]; identity=job['identity']; meta=job['sourceVideo']
        payload={'clientRequestId':job['clientRequestId'],'userId':str(identity['userId']),
                 'projectId':project_id(),'seriesId':series_id(),'type':spec['type'],'videoUrl':job['sourceVideoUrl'],
                 'name':'BeefTV · '+spec['label'],'duration':math.ceil(meta['duration']),
                 'videoWidth':meta['width'],'videoHeight':meta['height'],'enablePointDeduction':True}
        if identity.get('teamId'): payload['teamId']=identity['teamId']
        if identity.get('orgId'): payload.update(orgId=identity['orgId'],groupId=identity['orgId'])
        if spec['priority']: payload['priority']=spec['priority']
        result=self._request('POST','/nestjs/tasks',payload=payload)
        return {'taskId':result.get('taskId') or result.get('id')}

    @staticmethod
    def matches(row,job,task_id=None):
        if not isinstance(row,dict): return False
        payload=row.get('payload') or {}
        binding=job.get('bindingSnapshot',{})
        if binding.get('source')=='canvas' or binding.get('canvasId'):
            if any(payload.get(k)!=binding[k] for k in ('projectId','seriesId')): return False
        return ((task_id is None or (row.get('taskId')==task_id and row.get('id',task_id)==task_id))
                and str(row.get('userId'))==str(job['identity']['userId'])
                and row.get('type')==enhancement_models()[job['model']]['type']
                and payload.get('clientRequestId')==job['clientRequestId']
                and payload.get('videoUrl')==job['sourceVideoUrl'])

    @job_scope
    def task_status(self,task_id,job):
        if not re.fullmatch(r'[0-9a-f-]{36}',str(task_id)): raise BridgeError('增强任务ID无效。')
        row=self._request('GET','/nestjs/tasks/'+task_id)
        if not self.matches(row,job,task_id): raise BridgeError('增强任务ID、账号或原视频绑定不一致。')
        statuses={'PENDING':1,'QUEUED':1,'WAITING':1,'CREATED':1,'PROCESSING':2,'RUNNING':2,
                  'COMPLETED':3,'SUCCEEDED':3,'SUCCESS':3,'FAILED':4,'ERROR':4,'CANCELLED':5,'CANCELED':5}
        status=statuses.get(str(row.get('status','')).upper())
        if status is None: raise BridgeError('增强任务状态暂无法识别。')
        result=row.get('result') or {}
        if isinstance(result,str):
            try: result=json.loads(result)
            except ValueError: result={}
        if not isinstance(result,dict): result={}
        candidates=[row.get('resultUrl'),row.get('outputUrl')]+[result.get(k) for k in ('MediaUrl','mediaUrl','outputUrl','resourceUrl','resultUrl')]
        url=next((v for v in candidates if isinstance(v,str) and v.startswith('https://')),None)
        return {'taskId':task_id,'status':status,'urls':[url] if url else []}

    @job_scope
    def find_task(self,job):
        matches=[]
        for page in range(1,11):
            result=self._request('GET','/nestjs/tasks',params={'userId':job['identity']['userId'],'page':page,'size':100,'type':enhancement_models()[job['model']]['type']})
            rows=result.get('items',[])
            matches.extend(row['taskId'] for row in rows if self.matches(row,job) and row.get('taskId'))
            if len(set(matches))>1: raise BridgeError('同一增强请求对应多个任务，需要人工对账。')
            if not rows or page*100>=result.get('totalCount',0): break
        return matches[0] if matches else None


class EnhancementFlow(Generation):
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
