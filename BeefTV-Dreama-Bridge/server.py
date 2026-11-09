"""Loopback BeefTV adapter with user-authorized background opt-out execution."""
import hashlib
import hmac
import json
import logging
import os
import secrets
import stat
import threading
import uuid
import re
import time
from contextlib import ExitStack
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, unquote, parse_qs
from bridge import Bridge, BridgeError, MODEL, validate_request
from generation import Generation, POLLABLE
from model_catalog import models,selected_models
from enhancement import EnhancementFlow, enhancement_models, validate_enhancement_request
from extension import ExtensionFlow, extension_models, selected_extension_models, validate_extension_request
from auto_execution import AutoExecution
from image_flow import ImageFlow, image_models, validate_image_request
from media_delivery import DeliveryError, identifier
from audio_flow import AudioFlow, audio_models, voices, resolve_audio_model, validate_audio_request
from text_flow import text_models, TEXT_BINDING, TEXT_AUTH_ERROR
from task_repository import own_task_ledger

ROOT = Path(__file__).resolve().parent
PORT = 18765
MAX_BODY = 128 * 1024 * 1024
MAX_VIDEO_REFERENCE_BODY = 512 * 1024 * 1024

def cached_size(path, size):
    try:
        return path.is_file() and path.stat().st_size == size
    except OSError:
        return False

def execution_policy():
    return {'mode':'automatic','budgetRequired':True}

class Conflict(BridgeError):
    pass

class Server(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, bridge, token, port, policy=None):
        self.bridge, self.token = bridge, token
        self.ledger_owner = None
        self.execution_policy=execution_policy() if policy is None else policy
        if self.execution_policy.get('mode')=='background-countdown': self.execution_policy=execution_policy()
        self.flow=Generation(bridge)
        self.enhancements=EnhancementFlow(bridge)
        self.extensions=ExtensionFlow(bridge)
        self.images=ImageFlow(bridge)
        self.audios=AudioFlow(bridge)
        self.jobs = {}; self.jobs_lock = threading.Lock()
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.stop_event=threading.Event()
        self.auto_execution=AutoExecution(bridge,self.flow_for,self.execution_policy)
        super().__init__(('127.0.0.1', port), Handler)
        self.bridge.recover_preparations()
        self.auto_execution.recover_interrupted()
        self.resume_preparations()
        self.poller=threading.Thread(target=self.poll_loop,daemon=True); self.poller.start()
        self.auto_worker=threading.Thread(target=self.auto_loop,daemon=True); self.auto_worker.start()
        self.cleanup_worker = threading.Thread(target=bridge.delivery.cleanup.loop, args=(self.stop_event,),
                                               name='dreama-media-cleanup', daemon=True)
        self.cleanup_worker.start()
    def list_jobs(self):
        with self.bridge.db() as db:
            jobs=[json.loads(row[0]) for row in db.execute('SELECT result FROM jobs ORDER BY rowid DESC')]
        catalog={**models(),**extension_models(),**enhancement_models(),**image_models(),**audio_models()}
        for job in jobs:job.setdefault('modelLabel',catalog.get(job.get('model'),{}).get('label'))
        return jobs
    def flow_for(self,job):
        if job and job.get('kind')=='image': return self.images
        if job and job.get('kind')=='audio': return self.audios
        if job and job.get('kind')=='extension': return self.extensions
        return self.enhancements if job and job.get('kind')=='enhancement' else self.flow
    def poll_loop(self):
        while not self.stop_event.wait(5):
            try:
                jobs = self.list_jobs()
            except Exception as error:
                logging.getLogger(__name__).error('Polling task list failed (%s); retrying next cycle', type(error).__name__)
                continue
            for job in jobs:
                if self.stop_event.is_set(): return
                try:
                    if job['status'] in POLLABLE and time.time()-job.get('lastPollAt',0)>=10:
                        self.flow_for(job).poll(job['id'])
                except Exception as error:
                    logging.getLogger(__name__).error('Polling job failed (%s); continuing other tasks', type(error).__name__)
    def auto_loop(self):
        while not self.stop_event.wait(.25):
            try:self.auto_execution.step()
            except Exception:pass  # no retry of an already persisted submission attempt
    def wait_for_jobs(self):
        self.executor.submit(lambda: None).result(timeout=120)
    def resume_preparations(self):
        with self.bridge.db() as db:
            rows=db.execute('SELECT p.key,p.payload,p.kind,j.digest,j.result FROM preparation_requests p JOIN jobs j ON j.key=p.key ORDER BY j.rowid').fetchall()
        for key,payload,kind,digest,raw in rows:
            if json.loads(raw).get('status')=='preparation_queued' and payload:
                self.jobs[key]=digest
                self.executor.submit(self.work,json.loads(payload),key,kind)
    def server_close(self):
        try:
            self.stop_event.set()
            for name in ('poller', 'auto_worker', 'cleanup_worker'):
                worker = getattr(self, name, None)
                if worker is not None:
                    worker.join()
            self.executor.shutdown(wait=True)
            super().server_close()
        finally:
            if self.ledger_owner is not None:
                self.ledger_owner.close()
    def submit(self, data, key, kind=None, *, beef_task_id=None):
        from job_binding import validate_metadata
        if not isinstance(data,dict): raise BridgeError('请求必须为JSON对象。')
        binding=validate_metadata(data)
        if kind=='image': validate_image_request(data)
        elif kind=='audio': validate_audio_request(data)
        elif isinstance(data,dict) and data.get('model') in extension_models(): validate_extension_request(data)
        elif isinstance(data,dict) and data.get('model') in enhancement_models(): validate_enhancement_request(data)
        else: validate_request(data)
        if key is not None and (not isinstance(key, str) or not 1 <= len(key) <= 150 or any(ord(c) < 33 or ord(c) > 126 for c in key)):
            raise BridgeError('请求ID格式无效。')
        if beef_task_id is not None and (key is None or not identifier(beef_task_id)):
            raise DeliveryError('invalid_task_identity', 'BeefTV 任务关联需要有效的任务 ID 与原幂等键。', 400)
        digest = hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        with self.jobs_lock:
            with self.bridge.db() as db:
                db.execute('BEGIN IMMEDIATE')
                if key is None:
                    prior=db.execute('SELECT key FROM jobs WHERE digest=? LIMIT 1',(digest,)).fetchone()
                    if prior or digest in self.jobs.values():
                        raise Conflict('相同内容已有预检记录。客户端未提供稳定请求ID，无法区分重试与重新预检；已拒绝重复写入。重做需提供新的Idempotency-Key。')
                    key='beeftv-'+uuid.uuid4().hex
                row = db.execute('SELECT digest,result FROM jobs WHERE key=?',(key,)).fetchone()
                if row:
                    if row[0] != digest: raise BridgeError('相同请求ID内容不一致。')
                    if key not in self.jobs and json.loads(row[1])['status'] in ('unknown','preparing'):
                        raise BridgeError('已有未确认记录，请先对账；不会重复写入。')
                    self.bridge.delivery.bind(db,key,beef_task_id)
                    return key
                actual_kind=kind or ('extension' if data['model'] in extension_models() else 'enhancement' if data['model'] in enhancement_models() else 'video')
                job={'id':key,'kind':actual_kind,'status':'preparation_queued','stage':'queue','createdAt':time.time(),
                     'generationSubmitted':False,'model':data['model'],
                     **{field:binding[field] for field in ('projectId','seriesId','canvasId','nodeId')}}
                db.execute('INSERT INTO jobs VALUES (?,?,?)',(key,digest,json.dumps(job,ensure_ascii=False)))
                db.execute('INSERT INTO preparation_requests(key,payload,kind) VALUES (?,?,?)',
                           (key,json.dumps(data,ensure_ascii=False),kind))
                self.bridge.delivery.bind(db,key,beef_task_id,on_admission=True)
            self.jobs[key] = digest
            self.executor.submit(self.work, data, key, kind)
            return key
    def resume(self, data, key):
        if not isinstance(data, dict): raise BridgeError('恢复请求必须为JSON对象。')
        digest = hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        with self.jobs_lock:
            with self.bridge.db() as db:
                row=db.execute('SELECT digest,result FROM jobs WHERE key=?',(key,)).fetchone()
            if not row or row[0] != digest: raise BridgeError('恢复请求必须与原任务内容完全相同。')
            if key in self.jobs: return key
            job=json.loads(row[1])
            if job['status'] != 'unknown': return key
            if (job.get('kind','video') != 'video' or not re.fullmatch(r'bind:[1-9][0-9]*',job.get('stage',''))
                    or job.get('generationSubmitted') is not False
                    or any(job.get(k) is not None for k in ('taskId','storyboardId','approvedAt','autoAttemptedQuoteId'))):
                raise BridgeError('不在可恢复的图片绑定阶段，或已有提交痕迹；只能对账，不能恢复准备。')
            self.jobs[key]=digest
            self.executor.submit(self.work,data,key,recover=True)
            return key

    def work(self, data, key, kind=None, *, recover=False):
        try:
            self.bridge.ensure_not_cancelled(key)
            if kind=='image': self.images.prepare(data,key)
            elif kind=='audio': self.audios.prepare(data,key)
            elif data['model'] in extension_models(): self.extensions.prepare(data,key)
            elif data['model'] in enhancement_models(): self.enhancements.prepare(data,key)
            else: self.bridge.prepare(data,key,**({"recover":True} if recover else {}))
        except Exception as error:
            current=self.bridge.get(key)
            if current and current['status']=='preparation_queued':
                current.update(status='failed',stage='validation',error=str(error) if isinstance(error,BridgeError) else type(error).__name__)
                self.bridge.update(key,current)
        finally:
            with self.bridge.db() as db: db.execute("UPDATE preparation_requests SET payload='' WHERE key=?",(key,))
            with self.jobs_lock: self.jobs.pop(key,None)


class Handler(BaseHTTPRequestHandler):
    server_version = 'DreamaPreflight/1.0'
    def setup(self):
        super().setup(); self.connection.settimeout(30)
    def log_message(self, *args):
        pass  # never print credentials, prompts, reference bytes or URLs
    def send_json(self, status, body):
        data=json.dumps(body,ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Cache-Control','no-store')
        self.send_header('Content-Length',str(len(data)))
        self.end_headers()
        try: self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError): pass
    def error(self, status, message, code='preflight_error'):
        self.send_json(status,{'error':{'code':code,'message':message}})
    def authorized(self):
        origin=self.headers.get('Origin')
        if (origin and origin!=f'http://127.0.0.1:{self.server.server_port}') or self.headers.get('Sec-Fetch-Site')=='cross-site':
            self.error(403,'仅允许本机应用调用。'); return False
        if not hmac.compare_digest(self.headers.get('Authorization','').encode(), ('Bearer '+self.server.token).encode()):
            self.error(401,'本地转接密钥无效。'); return False
        return True
    def send_completed(self, result, response):
        delivery = self.server.bridge.delivery
        try:
            artifacts=delivery.publish(result)
            if artifacts: response['artifacts']=artifacts
            receipts = delivery.receipts(result['id'])
            if receipts: response['deliveryReceipts'] = receipts
            if delivery.history_only(receipts):
                for field in ('images', 'data', 'audio_url', 'video_url'):
                    response.pop(field, None)
        except DeliveryError as error:
            self.error(error.status,str(error),error.code); return
        self.send_json(200,response)

    def recover_missing_media(self, key, message):
        bridge = self.server.bridge
        with bridge.task_lock(key), bridge.lock:
            latest = bridge.get(key)
            if latest['status'] == 'completed' and not bridge.delivery.history_only(
                    bridge.delivery.receipts(key), bytes_missing=True):
                latest.update(status='download_failed', lastPollAt=0, error=message)
                bridge.update(key, latest)
            return latest
    def do_GET(self):
        path=urlsplit(self.path).path
        enhanced_path=path.startswith('/enhancement/v1/')
        extended_path=path.startswith('/extension/v1/')
        image_path=path.startswith('/image/v1/')
        audio_path=path.startswith('/audio/v1/')
        if enhanced_path: path=path[len('/enhancement'):]
        if extended_path: path=path[len('/extension'):]
        if image_path: path=path[len('/image'):]
        if audio_path: path=path[len('/audio'):]
        if path == '/health':
            automatic=self.server.execution_policy.get('mode')=='automatic'
            self.send_json(200,{'service':'dreama-preflight','status':'ok','generationEnabled':self.server.bridge.budget_is_configured(),'autoGeneration':automatic,'requiresPerJobConfirmation':not automatic,'executionMode':self.server.execution_policy.get('mode'),'autoExecuteDelaySeconds':None,'models':len(selected_models())+len(enhancement_models())+len(selected_extension_models())+len(image_models())+len(audio_models())+len(voices()),'imageModels':len(image_models()),'audioModels':len(audio_models()),'voicePresets':len(voices()),'textModels':0,'textAuthenticationReady':False,'generationModels':len(selected_models()),'enhancementModels':len(enhancement_models()),'extensionModels':len(selected_extension_models()),'promptPolicy':'verbatim-v1'}); return
        if path in ('/console','/console.js'):
            file=ROOT/('console.html' if path=='/console' else path[1:])
            data=file.read_bytes(); self.send_response(200)
            self.send_header('Content-Type','text/html; charset=utf-8' if path=='/console' else 'application/javascript; charset=utf-8')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'unsafe-inline'; img-src 'self' https://jzinnovation.jxbaobian.cn; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            self.send_header('Cache-Control','no-store'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data); return
        if path.startswith('/media/'):
            self.media(path); return
        if not self.authorized(): return
        if path.startswith('/integration/v1/'):
            from integration import get_integration
            try:
                result=get_integration(self.server,path[len('/integration/v1'):],parse_qs(urlsplit(self.path).query))
                self.send_json(200,result)
            except BridgeError as error: self.error(400,str(error))
            except Exception: self.error(503,'集成服务暂不可用；请稍后刷新。')
            return
        if audio_path and path=='/v1/voices':
            try:
                from voice_bindings import catalog
                rows=catalog().get()
                self.send_json(200,{'voices':[{k:v for k,v in row.items() if k in ('id','name','source','previewUrl','duration')} for row in rows.values()]})
            except Exception as error:
                self.error(503,str(error) if isinstance(error,BridgeError) else '剧梦音色目录暂不可用，请稍后刷新。','voice_catalog_unavailable')
            return
        if path=='/v1/execution-policy':
            self.send_json(200,self.server.execution_policy); return
        if path=='/v1/jobs':
            self.send_json(200,{'data':self.server.list_jobs()[:100],'serverTime':time.time()}); return
        if path.startswith('/v1/jobs/') and path.endswith('/ack'):
            key = unquote(path[len('/v1/jobs/'):-len('/ack')])
            if not self.server.bridge.get(key): self.error(404,'未找到对应媒体任务。','media_job_not_found'); return
            self.send_json(200, {'id':key, 'deliveryReceipts':self.server.bridge.delivery.receipts(key)}); return
        if path=='/v1/integration-status':
            self.send_json(200,{'images':{'enabled':len(image_models()),'disabled':[m for m in image_models(True).values() if not m['enabled']]},
                'audio':{'engines':len(audio_models()),'voices':len(voices())},
                'text':{'enabled':False,'binding':TEXT_BINDING,'reason':TEXT_AUTH_ERROR,'models':list(text_models(True).values())}});return
        if path=='/text/v1/models':
            self.send_json(200,{'object':'list','data':[],'message':TEXT_AUTH_ERROR});return
        if path == '/v1/models':
            if image_path or audio_path:
                specs=image_models() if image_path else {**audio_models(),**{('dreama-voice-clone-'+key):resolve_audio_model('dreama-voice-clone-'+key) for key in voices()}}
                self.send_json(200,{'object':'list','data':[{'id':m['id'],'object':'model','owned_by':'dreama','name':m['label'],'capability':'image' if image_path else 'audio'} for m in specs.values()]}); return
            self.send_json(200,{'object':'list','data':[{'id':m['id'],'object':'model','owned_by':'dreama',
                'name':m['label'],'capability':'video'} for m in (selected_extension_models() if extended_path else enhancement_models() if enhanced_path else selected_models()).values()]}); return
        if (image_path and path.startswith('/v1/images/')) or (audio_path and path.startswith('/v1/audio/tasks/')):
            key=unquote(path.rsplit('/',1)[-1]); result=self.server.bridge.get(key)
            with self.server.jobs_lock: active=key in self.server.jobs
            if active:
                self.send_json(200,{'id':key,'status':'processing'}); return
            kind='image' if image_path else 'audio'
            if not result or result.get('kind')!=kind: self.error(404,'未找到对应任务。'); return
            out={'id':key,'status':'pending','message':result.get('error','预检与预算通过后自动执行，完成后自动回传。')}
            if result['status']=='prepared':
                paused=result.get('executionError') or (result.get('autoExecuteError') if result.get('autoAttemptedQuoteId')==result.get('quoteId') else None)
                out.update(requires_confirmation=False,message=('自动执行已暂停：'+paused) if paused else f"预计{result['pointsEstimate']}积分，预算通过后自动执行。")
            if result['status']=='completed':
                if kind=='image':
                    items=result.get('images',[])
                    valid=bool(items) and all(cached_size(self.server.images.images/x['mediaKey'], x.get('bytes')) for x in items)
                    if valid:
                        urls=[{'url':f'http://127.0.0.1:{self.server.server_port}/media/'+x['mediaKey']} for x in items]
                        out.update(status='succeeded',images=urls,data=urls)
                else:
                    file=self.server.audios.audios/result['mediaKey']
                    valid=cached_size(file, result.get('audio',{}).get('bytes'))
                    if valid:out.update(status='succeeded',audio_url=f'http://127.0.0.1:{self.server.server_port}/media/'+result['mediaKey'])
                if not valid:
                    latest = self.recover_missing_media(key, '缓存缺失，正在取回已有结果。')
                    if latest['status'] == 'completed':
                        out.update(status='succeeded')
                        self.send_completed(latest, out); return
                    out.update(status='processing',message='正在取回已有结果，不会重新生成。')
                else:
                    self.send_completed(result,out); return
            elif result['status'] in ('queued','processing','download_failed'):out['status']='processing'
            elif result['status'] in ('failed','cancelled','unknown'):
                detail=result.get('error','任务已取消或准备失败。')
                out.update(status='failed',message=detail,error={'code':result['status'],'message':detail})
            self.send_json(200,out); return
        if path.startswith('/v1/videos/'):
            key=unquote(path[len('/v1/videos/'):])
            result=self.server.bridge.get(key)
            with self.server.jobs_lock: active=key in self.server.jobs
            if active:
                self.send_json(200,{'id':key,'status':'processing','message':'正在准备输入素材与费用预估；尚未提交扣费任务。'}); return
            if not result: self.error(404,'未找到预检记录。'); return
            if result.get('kind') in ('image','audio','text'):self.error(404,'此任务不是视频任务。');return
            if result['status']=='completed':
                cached=self.server.flow.videos/result['mediaKey']
                if not cached_size(cached, result.get('video',{}).get('bytes')):
                    result = self.recover_missing_media(key, '缓存缺失或不完整，正在重新取回已有视频。')
                    if result['status'] == 'completed':
                        self.send_completed(result, {'id':key,'status':'completed',
                                                     'generationSubmitted':result.get('generationSubmitted')})
                        return
            status=result['status']
            out={'id':key,'status':'pending','generationSubmitted':result.get('generationSubmitted')}
            if status=='prepared':
                paused=result.get('executionError') or (result.get('autoExecuteError') if result.get('autoAttemptedQuoteId')==result.get('quoteId') else None)
                message=('自动执行已暂停：'+paused) if paused else f"预计{result['pointsEstimate']}积分，预算通过后自动执行。"
                out.update(requires_confirmation=False,message=message)
            elif status=='completed':
                out.update(status='completed',video_url=f'http://127.0.0.1:{self.server.server_port}/media/'+result['mediaKey'])
                self.send_completed(result,out); return
            elif status in ('queued','submitting','submission_unknown'): out['message']='等待剧梦任务；不明回执只对账不重提。'
            elif status in ('processing','download_failed'): out.update(status='processing',message=result.get('error','剧梦正在生成，完成后自动回传。'))
            else: out.update(status='failed',error={'code':status,'message':result.get('error','任务已取消或预检未完成。')})
            self.send_json(200,out); return
        self.error(404,'接口不存在。')
    def do_HEAD(self):
        path=urlsplit(self.path).path
        if path.startswith('/media/'): self.media(path,head=True)
        else: self.error(404,'接口不存在。')
    def media(self,path,head=False):
        name=path[len('/media/'):]
        if not re.fullmatch(r'[0-9a-f]{48}\.(mp4|mp3|img)',name): self.error(404,'素材不存在。'); return
        if name.endswith('.img'):
            file=self.server.images.images/name
            mime=None
            for job in self.server.list_jobs():
                for item in job.get('images',[]):
                    if item.get('mediaKey')==name:mime=item.get('mimeType')
            if mime not in ('image/png','image/jpeg','image/webp'):self.error(404,'图片不存在。');return
        elif name.endswith('.mp3'):file=self.server.audios.audios/name;mime='audio/mpeg'
        else:file=self.server.flow.videos/name;mime='video/mp4'
        try:
            stream = file.open('rb')
        except OSError:
            self.error(404,'素材不存在。'); return
        with stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode): self.error(404,'素材不存在。'); return
            self.send_media_stream(stream, info.st_size, mime, head)

    def send_media_stream(self, stream, size, mime, head):
        start=0; end=size-1; status=200
        value=self.headers.get('Range')
        if value:
            match=re.fullmatch(r'bytes=(\d*)-(\d*)',value)
            if not match or not any(match.groups()): self.error(416,'无效Range。'); return
            a,b=match.groups()
            if a: start=int(a); end=min(int(b),size-1) if b else size-1
            else: start=max(0,size-int(b))
            if start>=size or start>end: self.error(416,'无效Range。'); return
            status=206
        self.send_response(status); self.send_header('Content-Type',mime); self.send_header('Accept-Ranges','bytes')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Length',str(end-start+1)); self.send_header('Cache-Control','private, max-age=3600')
        if status==206: self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
        self.end_headers()
        if head: return
        try:
            stream.seek(start); remaining=end-start+1
            while remaining:
                block=stream.read(min(65536,remaining))
                if not block: break
                self.wfile.write(block); remaining-=len(block)
        except (BrokenPipeError,ConnectionResetError): pass
    def do_POST(self):
        if not self.authorized(): return
        path=urlsplit(self.path).path
        ordinary_video_request=path=='/v1/videos'
        ack=re.fullmatch(r'/v1/jobs/([^/]+)/ack',path)
        if path.startswith('/text/v1/'):
            self.error(503,TEXT_AUTH_ERROR,'text_auth_unavailable');return
        if path.startswith('/enhancement/v1/'): path=path[len('/enhancement'):]
        if path.startswith('/extension/v1/'): path=path[len('/extension'):]
        kind='image' if path=='/image/v1/images' else 'audio' if path=='/audio/v1/audio/tasks' else None
        integration_create=path=='/integration/v1/series'
        resume=re.fullmatch(r'/v1/jobs/([^/]+)/resume',path)
        if path != '/v1/videos' and not resume and kind is None and not integration_create and not ack:
            self.error(404,'接口不存在。'); return
        try:
            if self.headers.get('Transfer-Encoding'): raise BridgeError('不支持分块请求。')
            if self.headers.get('Content-Type','').split(';')[0].strip() != 'application/json':
                raise BridgeError('请求需为application/json。')
            length=int(self.headers.get('Content-Length','0'))
            maximum=4096 if ack else MAX_VIDEO_REFERENCE_BODY if ordinary_video_request or resume else MAX_BODY
            if not 0 < length <= maximum:
                raise BridgeError('本地视频参考传输总限额为512MiB（含base64编码）；请压缩或减少引用。这不是模型的单文件大小限制。'
                                  if ordinary_video_request else '请求大小无效。')
            raw=self.rfile.read(length)
            if len(raw)!=length: raise BridgeError('请求体不完整。')
            data=json.loads(raw,parse_constant=lambda value: (_ for _ in ()).throw(ValueError('nonfinite')))
            if ack:
                receipt=self.server.bridge.delivery.acknowledge(unquote(ack[1]),data,self.headers.get('Idempotency-Key'))
                self.send_json(200,receipt); return
            if integration_create:
                from integration import create_series
                self.send_json(200,create_series(self.server.bridge.gateway,data)); return
            if resume:
                key=unquote(resume[1])
                if not isinstance(data,dict) or data.get('confirm') is not True: raise BridgeError('恢复需要明确确认。')
                self.server.resume(data.get('request'),key)
                self.send_json(202,{'id':key,'status':'pending'}); return
            key=self.server.submit(data,self.headers.get('Idempotency-Key'),kind=kind,beef_task_id=self.headers.get('X-BeefTV-Task-ID'))
            self.send_json(202,{'id':key,'status':'pending','message':'准备输入素材与费用预估；预算通过后自动执行。'})
        except Conflict as error: self.error(409,str(error),'ambiguous_repeat')
        except DeliveryError as error: self.error(error.status,str(error),error.code)
        except BridgeError as error:
            if ack: self.error(400,str(error),'invalid_media_ack')
            else: self.error(400,str(error))
        except (ValueError, UnicodeError):
            if ack: self.error(400,'媒体交付 ACK 或幂等键无效。','invalid_media_ack')
            else: self.error(400,'请求不是有效JSON。')
        except Exception:
            if ack: self.error(500,'暂时无法保存媒体交付 ACK。','media_ack_unavailable')
            else: self.error(500,'本地预检异常，请查看任务账本；不要重复提交。')


def make_server(bridge, token, port=PORT, policy=None):
    with ExitStack() as ownership:
        ownership.enter_context(own_task_ledger(bridge.root, exclusive=False))
        server = Server(bridge,token,port,policy)
        server.ledger_owner = ownership.pop_all()
        return server


def local_token():
    path=ROOT/'state'/'local-api-key'
    path.parent.mkdir(exist_ok=True)
    if not path.exists():
        with path.open('x',encoding='ascii') as file: file.write(secrets.token_urlsafe(32))
    return path.read_text('ascii').strip()


if __name__ == '__main__':
    server=make_server(Bridge(ROOT/'state'),local_token())
    (ROOT/'state'/'server.pid').write_text(str(os.getpid()),encoding='ascii')
    try: server.serve_forever()
    finally: server.server_close()
