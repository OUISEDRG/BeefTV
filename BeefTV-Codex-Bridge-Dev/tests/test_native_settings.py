import http.cookiejar
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request,build_opener,HTTPCookieProcessor
from completion_bridge import CompletionBridge
from model_catalog import ModelCatalog
from reasoning_settings import ReasoningSettings
from server import create_server
from test_reasoning_settings import MODELS


class NativeSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.settings=ReasoningSettings(Path(self.tmp.name)/'settings.json',ModelCatalog(MODELS),{'gpt-6-astra':'high'})
        def factory(): raise AssertionError('Settings must not invoke a model')
        self.bridge=CompletionBridge(factory,['gpt-6-astra','gpt-6-luna'],reasoning=self.settings)
        self.http=create_server(self.bridge,'native-test-key',port=0,settings=self.settings)
        threading.Thread(target=self.http.serve_forever,daemon=True).start()
        self.base='http://127.0.0.1:'+str(self.http.server_port)
        self.client=build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
    def tearDown(self): self.http.shutdown(); self.http.server_close(); self.bridge.close()
    def request(self,method='GET',body=None,key='native-test-key',path='/v1/reasoning-settings',headers=None):
        extra={'Content-Type':'application/json',**(headers or {})}
        if key: extra['Authorization']='Bearer '+key
        return self.client.open(Request(self.base+path,data=json.dumps(body).encode() if body is not None else None,
                                       method=method,headers=extra),timeout=3)
    def test_native_bearer_read_write_and_persistence(self):
        with self.request() as response: snap=json.load(response)
        self.assertEqual(snap['overrides']['gpt-6-astra'],'high')
        self.assertEqual(len(snap['models']),2)
        with self.request('PUT',{'model':'gpt-6-astra','effort':'ultra','expectedRevision':snap['revision']}) as response:
            updated=json.load(response)
        self.assertEqual(updated['revision'],1)
        self.assertEqual(self.settings.resolve('gpt-6-astra'),'ultra')
        with self.request('GET',path='/reasoning-settings') as response:
            self.assertEqual(json.load(response),updated)
    def test_unauthorized_and_cookie_only_cannot_read_or_write(self):
        with self.request(key=None,path='/settings') as response: response.read()
        for method,body in [('GET',None),('PUT',{'model':'gpt-6-astra','effort':'low','expectedRevision':0})]:
            with self.assertRaises(HTTPError) as error: self.request(method,body,key=None)
            self.assertEqual(error.exception.code,401); error.exception.close()
        self.assertEqual(self.settings.resolve('gpt-6-astra'),'high')
    def test_cas_conflict_not_retried(self):
        with self.assertRaises(HTTPError) as error:
            self.request('PUT',{'model':'gpt-6-astra','effort':'low','expectedRevision':99})
        self.assertEqual(error.exception.code,409)
        self.assertEqual(json.load(error.exception)['error']['reason'],'settings_conflict')
        error.exception.close()
        self.assertEqual(self.settings.resolve('gpt-6-astra'),'high')
    def test_missing_and_unsupported_fields_rejected(self):
        for body in ({'model':'gpt-6-astra','effort':'high'},
                     {'model':'gpt-6-luna','effort':'ultra','expectedRevision':0}):
            with self.assertRaises(HTTPError) as error: self.request('PUT',body)
            self.assertEqual(error.exception.code,400); error.exception.close()
    def test_browser_cross_origin_with_token_still_denied(self):
        with self.assertRaises(HTTPError) as error:
            self.request('PUT',{'model':'gpt-6-astra','effort':'low','expectedRevision':0},headers={'Origin':'https://example.com'})
        self.assertEqual(error.exception.code,403); error.exception.close()

if __name__=='__main__': unittest.main()
