import http.cookiejar
import json
import re
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


class SettingsHttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.settings=ReasoningSettings(Path(self.tmp.name)/'settings.json',ModelCatalog(MODELS),{'gpt-6-astra':'high'})
        def factory(): raise AssertionError('Settings must never start inference')
        self.bridge=CompletionBridge(factory,['gpt-6-astra','gpt-6-luna'],reasoning=self.settings)
        self.http=create_server(self.bridge,'secret-api-token',port=0,settings=self.settings)
        threading.Thread(target=self.http.serve_forever,daemon=True).start()
        self.base='http://127.0.0.1:'+str(self.http.server_port)
        self.jar=http.cookiejar.CookieJar(); self.client=build_opener(HTTPCookieProcessor(self.jar))
    def tearDown(self): self.http.shutdown(); self.http.server_close(); self.bridge.close()
    def request(self,path,data=None,headers=None):
        return self.client.open(Request(self.base+path,data=json.dumps(data).encode() if data is not None else None,
                                       headers=headers or {}),timeout=3)
    def open_page(self):
        with self.request('/settings') as response:
            html=response.read().decode()
            self.assertNotIn('secret-api-token',html)
            self.assertIn('frame-ancestors',response.headers['Content-Security-Policy'])
            self.assertRegex(html,r'<script nonce="[A-Za-z0-9_-]+" id="settings-script">')
        return next(c.value for c in self.jar if c.name=='btv_settings')
    def test_page_catalog_and_save_without_inference(self):
        csrf=self.open_page()
        with self.request('/settings/data') as response: state=json.load(response)
        self.assertEqual(len(state['models']),2)
        with self.request('/settings/data',{'model':'gpt-6-astra','effort':'ultra','expectedRevision':0},
                          {'Origin':self.base,'X-CSRF-Token':csrf,'Content-Type':'application/json'}) as response:
            self.assertEqual(json.load(response)['overrides']['gpt-6-astra'],'ultra')
        self.assertEqual(self.settings.resolve('gpt-6-astra'),'ultra')
    def test_no_session_csrf_or_wrong_origin_cannot_write(self):
        body={'model':'gpt-6-astra','effort':'low','expectedRevision':0}
        with self.assertRaises(HTTPError) as error: self.request('/settings/data',body)
        self.assertEqual(error.exception.code,403); error.exception.close()
        csrf=self.open_page()
        for headers in ({'Origin':self.base},{'Origin':'https://example.com','X-CSRF-Token':csrf}):
            with self.assertRaises(HTTPError) as error: self.request('/settings/data',body,headers)
            self.assertEqual(error.exception.code,403); error.exception.close()
        self.assertEqual(self.settings.resolve('gpt-6-astra'),'high')
    def test_bad_effort_and_stale_revision_are_errors(self):
        csrf=self.open_page(); headers={'Origin':self.base,'X-CSRF-Token':csrf}
        for body,status in (({'model':'gpt-6-luna','effort':'ultra','expectedRevision':0},400),
                            ({'model':'gpt-6-astra','effort':'low','expectedRevision':99},409)):
            with self.assertRaises(HTTPError) as error: self.request('/settings/data',body,headers)
            self.assertEqual(error.exception.code,status); error.exception.close()
    def test_settings_cookie_does_not_grant_inference_access(self):
        self.open_page()
        with self.assertRaises(HTTPError) as error:
            self.request('/v1/chat/completions',{'model':'gpt-6-astra','messages':[{'role':'user','content':'x'}]})
        self.assertEqual(error.exception.code,401); error.exception.close()

if __name__=='__main__': unittest.main()
