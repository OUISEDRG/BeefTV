import json
import sys
import tempfile
import threading
import socket
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from completion_bridge import BridgeError, CompletionBridge
from test_completion_bridge import FakeRPC, delta, done
from test_image_jobs import DATA_URL, output
from test_reasoning_settings import MODELS
import server
from server import create_server


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.calls=[]
        def factory(): self.calls.append(1); return FakeRPC([delta('测试文字'),done()])
        self.bridge=CompletionBridge(factory,['gpt-6-astra'])
        self.http=create_server(self.bridge,'secret-for-test',port=0)
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True); self.thread.start()
        self.url='http://127.0.0.1:'+str(self.http.server_port)
    def tearDown(self):
        self.http.shutdown(); self.http.server_close(); self.bridge.close()
    def request(self,path,data=None,auth=True,headers=None):
        h={'Content-Type':'application/json',**(headers or {})}
        if auth: h['Authorization']='Bearer secret-for-test'
        return urlopen(Request(self.url+path,data=json.dumps(data).encode() if data else None,headers=h),timeout=3)
    def test_health_and_models_never_start_inference(self):
        with self.request('/health',auth=False) as r: health = json.load(r)
        self.assertEqual(health['service'],'beeftv-codex')
        self.assertEqual(health['submissionAuthorization'],
                         {'text':'authenticated-local','image':'disabled'})
        with self.request('/v1/models') as r: self.assertEqual(json.load(r)['data'][0]['id'],'gpt-6-astra')
        self.assertEqual(self.calls,[])
    def test_explicit_authorization_blocks_both_text_http_modes_before_rpc(self):
        def deny():
            raise BridgeError('Fixture authorization denied', 409, 'submission_denied')
        self.bridge.authorize_submission = deny
        with self.request('/health',auth=False) as response:
            self.assertEqual(json.load(response)['submissionAuthorization']['text'], 'custom-authorization')
        for stream in (False, True):
            with self.subTest(stream=stream):
                data={'model':'gpt-6-astra','stream':stream,'messages':[{'role':'user','content':'fixture'}]}
                with self.assertRaises(HTTPError) as denied:
                    self.request('/v1/chat/completions',data,headers={'Idempotency-Key':f'p6-budget-off-{stream}'})
                with denied.exception as response:
                    self.assertEqual((response.code,json.load(response)['error']['code']),
                                     (409,'submission_denied'))
                self.assertEqual(self.calls,[])
                self.assertEqual(self.bridge.chains,[])
                self.assertEqual(self.bridge.idempotency,{})
    def test_unauthorized_and_cross_origin_denied(self):
        with self.assertRaises(HTTPError) as e: self.request('/v1/models',auth=False)
        self.assertEqual(e.exception.code,401)
        with self.assertRaises(HTTPError) as e: self.request('/v1/models',headers={'Origin':'https://evil.example'})
        self.assertEqual(e.exception.code,403)
        self.assertEqual(self.calls,[])
    def test_nonstream_text(self):
        with self.request('/v1/chat/completions',{'model':'gpt-6-astra','messages':[{'role':'user','content':'x'}]}) as r:
            self.assertEqual(json.load(r)['choices'][0]['message']['content'],'测试文字')
    def test_sse_text_terminal_and_done(self):
        with self.request('/chat/completions',{'model':'gpt-6-astra','stream':True,'messages':[{'role':'user','content':'x'}]}) as r:
            body=r.read().decode()
            self.assertIn('测试文字',body); self.assertIn('"finish_reason": "stop"',body)
            self.assertTrue(body.endswith('data: [DONE]\n\n'))
    def test_bad_model_is_explicit_error(self):
        with self.assertRaises(HTTPError) as e: self.request('/v1/chat/completions',{'model':'bad','messages':[{'role':'user','content':'x'}]})
        self.assertEqual(e.exception.code,404); self.assertEqual(self.calls,[])
    def test_idempotent_nonstream_does_not_regenerate(self):
        data={'model':'gpt-6-astra','messages':[{'role':'user','content':'x'}]}
        with self.request('/v1/chat/completions',data,headers={'Idempotency-Key':'abc'}) as r: first=json.load(r)
        with self.request('/v1/chat/completions',data,headers={'Idempotency-Key':'abc'}) as r: second=json.load(r)
        self.assertEqual(first,second); self.assertEqual(len(self.calls),1)
    def test_nonstream_disconnect_cancels_inference(self):
        rpc=FakeRPC([])
        self.bridge.factory=lambda:rpc
        data=json.dumps({'model':'gpt-6-astra','messages':[{'role':'user','content':'x'}]}).encode()
        client=socket.create_connection(('127.0.0.1',self.http.server_port))
        client.sendall((f'POST /v1/chat/completions HTTP/1.0\r\nHost: 127.0.0.1\r\nAuthorization: Bearer secret-for-test\r\nContent-Length: {len(data)}\r\n\r\n').encode()+data)
        limit=time.monotonic()+2
        while not rpc.started and time.monotonic()<limit: time.sleep(0.01)
        client.close()
        while not rpc.closed and time.monotonic()<limit: time.sleep(0.02)
        self.assertTrue(rpc.closed)


class StartupSubmissionTests(unittest.TestCase):
    def test_main_accepts_authenticated_image_and_text_without_budget_callback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image_calls = []
            text_calls = []
            def generate(*args, **kwargs):
                image_calls.append((args, kwargs))
                return output()
            def rpc(*args, **kwargs):
                text_calls.append((args, kwargs))
                return FakeRPC([delta('native fixture'), done()])
            def create(*args, **kwargs):
                http = create_server(*args, **kwargs)
                serve = http.serve_forever
                def exercise(**unused):
                    worker = threading.Thread(target=serve, daemon=True)
                    worker.start()
                    base = f'http://127.0.0.1:{http.server_port}'
                    token = (root / 'state' / 'local-api-key').read_text('utf-8')
                    def request(path, data=None, authenticated=True, key=None):
                        headers = {'Content-Type': 'application/json'}
                        if authenticated:
                            headers['Authorization'] = 'Bearer ' + token
                        if key:
                            headers['Idempotency-Key'] = key
                        req = Request(base + path, headers=headers,
                                      data=json.dumps(data).encode() if data is not None else None)
                        with urlopen(req, timeout=3) as response:
                            return response.status, json.load(response)
                    try:
                        _, health = request('/health', authenticated=False)
                        self.assertEqual(health['submissionAuthorization'],
                                         {'text': 'authenticated-local', 'image': 'authenticated-local'})
                        body = {'model': 'codex-image', 'prompt': 'offline fixture'}
                        with self.assertRaises(HTTPError) as denied:
                            request('/v1/images/jobs', body, authenticated=False, key='startup-image')
                        denied.exception.close()
                        self.assertEqual(denied.exception.code, 401)
                        self.assertEqual(image_calls, [])
                        code, first = request('/v1/images/jobs', body, key='startup-image')
                        self.assertEqual(code, 202)
                        deadline = time.monotonic() + 3
                        while True:
                            _, result = request('/v1/images/jobs/' + first['id'])
                            if result['status'] not in ('queued', 'running'):
                                break
                            if time.monotonic() >= deadline:
                                self.fail('Offline image did not finish')
                            time.sleep(.01)
                        self.assertEqual(result['status'], 'succeeded')
                        self.assertEqual(result['images'][0]['dataUrl'], DATA_URL)
                        self.assertEqual(request('/v1/images/jobs', body, key='startup-image')[1]['id'], first['id'])
                        self.assertEqual(len(image_calls), 1)
                        _, text = request('/v1/chat/completions', {
                            'model': 'gpt-6-astra', 'messages': [{'role': 'user', 'content': 'fixture'}]})
                        self.assertEqual(text['choices'][0]['message']['content'], 'native fixture')
                        self.assertEqual(len(text_calls), 1)
                    finally:
                        http.shutdown()
                        worker.join(timeout=3)
                http.serve_forever = exercise
                return http
            with (patch.object(sys, 'argv', ['server.py', '--root', str(root), '--port', '0']),
                  patch('runtime_config.configure_runtime', return_value=SimpleNamespace(executable='fake-codex')),
                  patch('model_catalog.probe_native_catalog', return_value={
                      'account': {'signedIn': True, 'type': 'chatgpt'}, 'models': MODELS}),
                  patch('codex_rpc.RpcSession', side_effect=rpc),
                  patch('image_rpc.generate_image', side_effect=generate),
                  patch.object(server, 'create_server', side_effect=create)):
                server.main()


if __name__=='__main__': unittest.main()
