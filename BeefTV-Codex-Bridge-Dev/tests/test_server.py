import json
import threading
import socket
import time
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from completion_bridge import CompletionBridge
from test_completion_bridge import FakeRPC, delta, done
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
        with self.request('/health',auth=False) as r: self.assertEqual(json.load(r)['service'],'beeftv-codex')
        with self.request('/v1/models') as r: self.assertEqual(json.load(r)['data'][0]['id'],'gpt-6-astra')
        self.assertEqual(self.calls,[])
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


if __name__=='__main__': unittest.main()
