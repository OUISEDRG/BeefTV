"""Authenticated loopback service. Starting the service never starts inference."""
from __future__ import annotations
import argparse
import hmac
import json
import os
from pathlib import Path
import secrets
import select
import re
import socket
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from completion_bridge import BridgeError, CompletionBridge

VERSION='1.4.0'
MAX_BODY=24*1024*1024


def create_server(bridge, token, port=18766, status=None, settings=None, image_jobs=None):
    sessions={}
    session_lock=threading.RLock()
    class Handler(BaseHTTPRequestHandler):
        protocol_version='HTTP/1.0'
        def log_message(self, *args): pass  # Never log prompts, tool payloads or credentials.
        def setup(self):
            super().setup(); self.connection.settimeout(650)
        def json(self,status_code,data):
            body=json.dumps(data,ensure_ascii=False).encode('utf-8')
            self.send_response(status_code)
            self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Content-Length',str(len(body)))
            self.send_header('Cache-Control','no-store')
            self.end_headers(); self.wfile.write(body)
        def error(self,error):
            self.json(error.status,{'error':{'message':str(error),'type':'local_codex_error','code':error.code,'reason':error.code}})
        def image_request(self, action, job_id=None):
            if image_jobs is None:
                return self.error(BridgeError('Codex 内置生图尚未启用。',404,'image_generation_unavailable'))
            try:
                if action=='create':
                    length=int(self.headers.get('Content-Length','0'))
                    if not 0<length<=MAX_BODY: raise BridgeError('生图请求大小无效。',413,'invalid_image_request')
                    raw=self.rfile.read(length)
                    if len(raw)!=length: raise BridgeError('生图请求内容不完整。')
                    payload=json.loads(raw)
                    if not isinstance(payload,dict): raise BridgeError('生图请求必须为 JSON 对象。')
                    result=image_jobs.create(payload,self.headers.get('Idempotency-Key'))
                    return self.json(202,result)
                if action=='get': return self.json(200,image_jobs.get(job_id))
                if action=='cancel': return self.json(200,image_jobs.cancel(job_id))
            except BridgeError as error: return self.error(error)
            except (ValueError,TypeError): return self.error(BridgeError('无效的生图请求。',400,'invalid_image_request'))
            except Exception: return self.error(BridgeError('本机生图任务处理失败，请检查桥接服务。',500,'image_job_error'))
        def allowed(self,auth=True):
            host=self.headers.get('Host','').split(':')[0].lower()
            if host not in ('127.0.0.1','localhost'):
                self.error(BridgeError('Host is not allowed.',403)); return False
            origin=self.headers.get('Origin')
            # Native BeefTV backend is the only client; no browser cross-origin API.
            if origin and origin not in ('null',f'http://127.0.0.1:{self.server.server_port}',f'http://localhost:{self.server.server_port}'):
                self.error(BridgeError('Cross-origin API access is not allowed.',403)); return False
            if auth and not hmac.compare_digest(self.headers.get('Authorization',''), 'Bearer '+token):
                self.error(BridgeError('本机桥接认证失败。',401,'invalid_api_key')); return False
            return True
        def settings_session(self):
            cookie=SimpleCookie()
            try: cookie.load(self.headers.get('Cookie',''))
            except Exception: return None
            session=cookie.get('btv_settings')
            value=session.value if session else None
            with session_lock:
                expiry=sessions.get(value,0)
                return value if expiry>time.monotonic() else None
        def settings_allowed(self,write=False):
            if not self.allowed(auth=False): return False
            if settings is None:
                self.error(BridgeError('设置页未启用。',404)); return False
            session=self.settings_session()
            if not session:
                self.error(BridgeError('请重新打开本机设置页。',403,'settings_session_required')); return False
            if write:
                origins=(f'http://127.0.0.1:{self.server.server_port}',f'http://localhost:{self.server.server_port}')
                if (self.headers.get('Origin') not in origins or
                    not hmac.compare_digest(self.headers.get('X-CSRF-Token',''),session)):
                    self.error(BridgeError('设置页验证已失效，请刷新后再保存。',403,'csrf_failed')); return False
            return True
        def settings_page(self):
            if not self.allowed(auth=False): return
            if settings is None: return self.error(BridgeError('设置页未启用。',404))
            session=self.settings_session() or secrets.token_urlsafe(32)
            with session_lock:
                for item,expiry in list(sessions.items()):
                    if expiry<=time.monotonic(): del sessions[item]
                if len(sessions)>128: del sessions[next(iter(sessions))]
                sessions[session]=time.monotonic()+7200
            page=(Path(__file__).parent/'settings.html').read_text('utf-8')
            page=page.replace('__CSRF_TOKEN__',session)
            page=re.sub(r'<script(?=[ >])',f'<script nonce="{session}"',page)
            body=page.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type','text/html; charset=utf-8')
            self.send_header('Content-Length',str(len(body)))
            self.send_header('Cache-Control','no-store')
            self.send_header('Set-Cookie',f'btv_settings={session}; Path=/settings; HttpOnly; SameSite=Strict; Max-Age=7200')
            self.send_header('Content-Security-Policy',f"default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-{session}'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.send_header('X-Content-Type-Options','nosniff')
            self.end_headers(); self.wfile.write(body)
        def do_GET(self):
            path=urlsplit(self.path).path
            if path in ('/settings','/settings/'):
                return self.settings_page()
            if path=='/settings/data':
                if self.settings_allowed(): self.json(200,settings.snapshot())
                return
            if not self.allowed(auth=path!='/health'): return
            image_match=re.fullmatch(r'/v1/images/jobs/([A-Za-z0-9_-]{1,80})',path)
            if image_match: return self.image_request('get',image_match.group(1))
            if path in ('/v1/reasoning-settings','/reasoning-settings'):
                if settings is None: return self.error(BridgeError('模型推理设置未启用。',404,'reasoning_unavailable'))
                return self.json(200,settings.snapshot())
            if path=='/health':
                return self.json(200,{'service':'beeftv-codex','version':VERSION,'ready':True,
                                      'models':bridge.models,'activeSessions':sum(not c.closed for c in bridge.chains)+(image_jobs.active_count if image_jobs else 0),
                                      'activeImageJobs':image_jobs.active_count if image_jobs else 0,
                                      'imageGeneration':image_jobs is not None,
                                      'settingsUrl':'http://127.0.0.1:'+str(self.server.server_port)+'/settings',
                                      'nativeReasoningSettings':settings is not None,
                                      'automaticTestGeneration':False,**(status or {})})
            if path in ('/v1/models','/models'):
                return self.json(200,{'object':'list','data':[{'id':m,'object':'model','owned_by':'local-codex'} for m in bridge.models]})
            self.error(BridgeError('Not found.',404,'not_found'))
        def update_reasoning(self):
            if settings is None: return self.error(BridgeError('模型推理设置未启用。',404,'reasoning_unavailable'))
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=4096: raise BridgeError('设置请求大小无效。',413)
                body=self.rfile.read(length)
                if len(body)!=length: raise BridgeError('设置请求内容不完整。')
                payload=json.loads(body)
                if not isinstance(payload,dict): raise BridgeError('设置请求格式无效。')
                if not {'model','effort','expectedRevision'}<=set(payload): raise BridgeError('设置请求缺少字段。')
                return self.json(200,settings.update(payload['model'],payload['effort'],payload['expectedRevision']))
            except BridgeError as error: return self.error(error)
            except (ValueError,TypeError,KeyError): return self.error(BridgeError('设置请求格式无效。'))
        def do_PUT(self):
            if not self.allowed(): return
            if urlsplit(self.path).path not in ('/v1/reasoning-settings','/reasoning-settings'):
                return self.error(BridgeError('Not found.',404,'not_found'))
            return self.update_reasoning()
        def do_DELETE(self):
            if not self.allowed(): return
            image_match=re.fullmatch(r'/v1/images/jobs/([A-Za-z0-9_-]{1,80})',urlsplit(self.path).path)
            if image_match: return self.image_request('cancel',image_match.group(1))
            return self.error(BridgeError('Not found.',404,'not_found'))
        def do_POST(self):
            if urlsplit(self.path).path=='/settings/data':
                if not self.settings_allowed(write=True): return
                return self.update_reasoning()
            if not self.allowed(): return
            if urlsplit(self.path).path=='/v1/images/jobs': return self.image_request('create')
            if urlsplit(self.path).path == '/shutdown':
                self.json(200,{'stopping':True})
                threading.Thread(target=self.server.shutdown,daemon=True).start()
                return
            if urlsplit(self.path).path not in ('/v1/chat/completions','/chat/completions'):
                return self.error(BridgeError('Not found.',404,'not_found'))
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=MAX_BODY: raise BridgeError('请求大小无效。',413)
                data=self.rfile.read(length)
                if len(data)!=length: raise BridgeError('请求体未完整收到。')
                payload=json.loads(data)
                if not isinstance(payload,dict): raise BridgeError('请求体必须为 JSON 对象。')
                key=self.headers.get('Idempotency-Key') or self.headers.get('X-Idempotency-Key')
                if key and len(key)>200: raise BridgeError('Idempotency-Key 过长。')
                if not payload.get('stream'):
                    iterator=bridge.stream(payload,key)
                    chunks=[]
                    try:
                        for chunk in iterator:
                            if select.select([self.connection],[],[],0)[0]:
                                if not self.connection.recv(1,socket.MSG_PEEK):
                                    raise ConnectionAbortedError('Client disconnected')
                            if chunk is not None: chunks.append(chunk)
                        return self.json(200,bridge.assemble(chunks))
                    finally: iterator.close()
                iterator=bridge.stream(payload,key)
                # Validate and initialize before sending HTTP 200 so early errors are native errors.
                first=next(iterator)
            except BridgeError as error: return self.error(error)
            except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError): return
            except (ValueError,TypeError,KeyError): return self.error(BridgeError('无效 JSON 请求。'))
            except Exception:
                return self.error(BridgeError('本机 Codex 启动或连接失败，请检查 Codex 登录和本机桥接服务。',502,'codex_unavailable'))
            self.send_response(200)
            self.send_header('Content-Type','text/event-stream; charset=utf-8')
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Accel-Buffering','no')
            self.end_headers()
            def send(chunk):
                body=': ping\n\n' if chunk is None else 'data: '+json.dumps(chunk,ensure_ascii=False)+'\n\n'
                self.wfile.write(body.encode('utf-8')); self.wfile.flush()
            try:
                send(first)
                for chunk in iterator: send(chunk)
                self.wfile.write(b'data: [DONE]\n\n'); self.wfile.flush()
            except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError,OSError): pass
            except Exception as error:
                message=str(error) if isinstance(error,BridgeError) else '本机 Codex 连接中断，未自动重试。'
                try:
                    send({'error':{'message':message,'type':'local_codex_error','code':getattr(error,'code','codex_error')}})
                    self.wfile.write(b'data: [DONE]\n\n'); self.wfile.flush()
                except OSError: pass
            finally:
                iterator.close()
    return ThreadingHTTPServer(('127.0.0.1',port),Handler)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parent)
    parser.add_argument('--port',type=int,default=18766)
    parser.add_argument('--model',help='可选：仅开放指定原生模型；默认开放完整可见目录')
    parser.add_argument('--probe',action='store_true',help='Read account and model catalog only, never run a turn')
    args=parser.parse_args()
    root=args.root.resolve(); state=root/'state'; state.mkdir(parents=True,exist_ok=True)
    work=root/'runtime'; work.mkdir(parents=True,exist_ok=True)
    config=json.loads((root/'runtime-config.json').read_text('utf-8'))
    if config.get('codexHome'):
        codex_home=Path(config['codexHome']).resolve(strict=True)
        temporary=codex_home/'.tmp'; temporary.mkdir(exist_ok=True)
        os.environ.update(CODEX_HOME=str(codex_home),TEMP=str(temporary),TMP=str(temporary))
    from codex_rpc import RpcSession
    from model_catalog import ModelCatalog, probe_native_catalog
    from reasoning_settings import ReasoningSettings
    from image_jobs import ImageJobs
    from image_rpc import generate_image, ImageGenerationError
    def factory(): return RpcSession(config['codexExecutable'],str(work))
    # Only account/read + model/list at startup; no thread or turn is created.
    proof=probe_native_catalog(config['codexExecutable'],str(work),cache_path=root/'model-catalog.json')
    if args.probe:
        print(json.dumps(proof,ensure_ascii=False,indent=2)); return
    if not proof.get('account',{}).get('signedIn'):
        raise RuntimeError('请先在本机 Codex 登录，再启动桥接。')
    catalog=ModelCatalog(proof)
    if args.model:
        catalog.get_model(args.model)
        catalog=ModelCatalog(proof,allowed_ids=[args.model])
    token_path=state/'local-api-key'
    if not token_path.exists(): token_path.write_text(secrets.token_urlsafe(48),'utf-8')
    token=token_path.read_text('utf-8').strip()
    if len(token)<32: raise RuntimeError('Local API token is missing or too short.')
    initial={'gpt-6-astra':'high'} if 'gpt-6-astra' in catalog.model_ids else {}
    settings=ReasoningSettings(state/'reasoning-settings.json',catalog,initial_overrides=initial)
    bridge=CompletionBridge(factory,catalog.model_ids,reasoning=settings)
    def image_generator(job_dir,prompt,references,aspect_ratio,transparent,cancelled,on_progress,resolution='auto'):
        def progress(event):
            phase=event.get('phase') if isinstance(event,dict) else None
            if phase=='generating': on_progress('rendering')
            if phase=='completed': on_progress('finalizing')
        try:
            return generate_image(config['codexExecutable'],job_dir,prompt,references,
                                  aspect_ratio=aspect_ratio,transparent=transparent,resolution=resolution,
                                  cancelled=cancelled,on_progress=progress)
        except ImageGenerationError as error:
            code='image_result_invalid' if error.code in ('invalid_image','image_too_large','image_output_unavailable') else error.code
            raise BridgeError('Codex 内置生图未完成。',502,code) from error
    images=ImageJobs(root,image_generator)
    http=create_server(bridge,token,args.port,status={'authMode':'local-codex-managed','pid':os.getpid(),
                        'signedIn':True,'accountType':proof['account'].get('type'),'inferenceTested':False},settings=settings,image_jobs=images)
    (state/'server.pid').write_text(str(os.getpid()),'ascii')
    def cleanup():
        while not stopped.wait(30): bridge.prune()
    stopped=threading.Event()
    threading.Thread(target=cleanup,daemon=True).start()
    try: http.serve_forever(poll_interval=0.5)
    finally:
        stopped.set(); http.server_close(); images.close(); bridge.close()
        (state/'server.pid').unlink(missing_ok=True)


if __name__=='__main__': main()
