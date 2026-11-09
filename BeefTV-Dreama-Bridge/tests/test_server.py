import sqlite3
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
import requests
from tests.test_bridge import FakeGateway, request
from bridge import Bridge
from tests.fixtures_budget import FundedBridge as Bridge


class PollLoopResilienceTests(unittest.TestCase):
    def test_transient_errors_keep_later_jobs_and_cycles_running(self):
        from server import Server
        jobs = [{'id': 'broken', 'status': 'queued'}, {'id': 'healthy', 'status': 'queued'},
                {'id': 'unapproved', 'status': 'prepared'}]
        stop = Mock()
        stop.wait.side_effect = [False, False, True]
        stop.is_set.return_value = False
        poll = Mock(side_effect=[sqlite3.OperationalError('readonly'), None])
        worker = SimpleNamespace(stop_event=stop,
                                 list_jobs=Mock(side_effect=[sqlite3.OperationalError('locked'), jobs]),
                                 flow_for=Mock(return_value=SimpleNamespace(poll=poll)))
        Server.poll_loop(worker)
        self.assertEqual([call.args[0] for call in poll.call_args_list], ['broken', 'healthy'])


class HTTPTests(unittest.TestCase):
    def setUp(self):
        from server import make_server
        self.tmp = tempfile.TemporaryDirectory()
        self.gateway = FakeGateway()
        self.server = make_server(Bridge(self.tmp.name, self.gateway), 'test-secret', 0, policy={'mode':'manual'})
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = 'http://127.0.0.1:' + str(self.server.server_port)
        self.headers = {'Authorization': 'Bearer test-secret', 'Idempotency-Key': 'http-test'}
    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        self.tmp.cleanup()
    def test_auth_required_and_browser_origin_rejected(self):
        self.assertEqual(requests.post(self.url+'/v1/videos', json=request()).status_code, 401)
        self.assertEqual(requests.post(self.url+'/v1/videos', json=request(), headers={**self.headers,'Origin':'https://example.com'}).status_code, 403)
        self.assertEqual(self.gateway.calls, [])
    def test_execution_policy_is_authenticated_and_does_not_itself_submit(self):
        self.server.execution_policy={'mode':'background-countdown','delaySeconds':5,'effectiveAt':100}
        self.assertEqual(requests.get(self.url+'/v1/execution-policy').status_code,401)
        result=requests.get(self.url+'/v1/execution-policy',headers=self.headers)
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json(),{'mode':'background-countdown','delaySeconds':5,'effectiveAt':100})
        self.assertEqual(self.gateway.calls,[])
    def test_background_worker_submits_after_budget_without_any_console_request(self):
        import time
        from generation import Generation
        from tests.test_generation import GenerationGateway
        gateway=GenerationGateway();self.server.bridge.gateway=gateway
        self.server.flow=Generation(self.server.bridge)
        self.server.execution_policy={'mode':'automatic'}
        self.server.auto_execution.policy=self.server.execution_policy
        response=requests.post(self.url+'/v1/videos',json=request(),headers=self.headers)
        self.assertEqual(response.status_code,202);key=response.json()['id'];self.server.wait_for_jobs()
        deadline=time.monotonic()+7
        while time.monotonic()<deadline:
            job=self.server.bridge.get(key)
            if job['status']=='queued':break
            time.sleep(.1)
        self.assertEqual(job['status'],'queued',job)
        self.assertNotIn('approvedAt',job)
        self.assertNotIn('autoExecuteAt',job)
        self.assertTrue(job['submissionIntentId'])
        self.assertEqual(gateway.calls.count('paid-submit'),1)
    def test_paused_auto_validation_is_reported_to_beeftv_as_needing_attention(self):
        from auto_execution import AutoExecution
        response=requests.post(self.url+'/v1/videos',json=request(),headers=self.headers)
        key=response.json()['id'];self.server.wait_for_jobs();job=self.server.bridge.get(key)
        job.update(autoExecuteError='source changed',autoAttemptedQuoteId=job['quoteId'],autoExecuteQuoteId=job['quoteId'])
        self.server.bridge.update(key,job)
        self.server.auto_execution=AutoExecution(self.server.bridge,self.server.flow_for,{'mode':'background-countdown','delaySeconds':5,'effectiveAt':1})
        result=requests.get(self.url+'/v1/videos/'+key,headers=self.headers).json()
        self.assertFalse(result['requires_confirmation']);self.assertIn('source changed',result['message'])
    def test_retired_manual_execution_and_cancel_routes_cannot_change_a_task(self):
        from generation import Generation
        from tests.test_generation import GenerationGateway
        gateway=GenerationGateway();self.server.bridge.gateway=gateway;self.server.flow=Generation(self.server.bridge)
        response=requests.post(self.url+'/v1/videos',json=request(),headers=self.headers)
        key=response.json()['id'];self.server.wait_for_jobs();job=self.server.bridge.get(key)
        for path, payload in (
            ('/v1/jobs/'+key+'/approve', {'confirm':True,'quoteId':job['quoteId'],'points':100}),
            ('/v1/jobs/'+key+'/cancel', {}),
            ('/integration/v1/tasks/'+key+'/cancel', {}),
        ):
            response=requests.post(self.url+path,json=payload,headers=self.headers)
            self.assertEqual(response.status_code,404,response.text)
        self.assertEqual(self.server.bridge.get(key),job)
        self.assertNotIn('paid-submit',gateway.calls)
    def test_preflight_preserves_existing_request_while_automatic_worker_is_disabled(self):
        r = requests.post(self.url+'/v1/videos', json=request(), headers=self.headers)
        self.assertEqual(r.status_code, 202, r.text)
        job = r.json()['id']
        self.server.wait_for_jobs()
        result = requests.get(self.url+'/v1/videos/'+job, headers=self.headers).json()
        self.assertEqual(result['status'], 'pending')
        self.assertFalse(result['requires_confirmation'])
        self.assertNotIn('video_url', result)
        self.assertFalse(result['generationSubmitted'])
        requests.post(self.url+'/v1/videos', json=request(), headers=self.headers)
        self.server.wait_for_jobs()
        self.assertEqual(len(self.gateway.calls), 4)
    def test_validation_before_writes_and_unknown_endpoint(self):
        data=request(); data['images']=None
        self.assertEqual(requests.post(self.url+'/v1/videos',json=data,headers=self.headers).status_code,400)
        self.assertEqual(requests.post(self.url+'/v1/generate',json=request(),headers=self.headers).status_code,404)
        self.assertEqual(self.gateway.calls, [])
    def test_anonymous_duplicate_is_rejected_across_restart_not_silently_merged(self):
        headers={'Authorization':'Bearer test-secret'}
        r=requests.post(self.url+'/v1/videos',json=request(),headers=headers)
        self.assertEqual(r.status_code,202)
        self.server.wait_for_jobs()
        r=requests.post(self.url+'/v1/videos',json=request(),headers=headers)
        self.assertEqual(r.status_code,409)
        self.assertEqual(len(self.gateway.calls),4)
        from server import make_server
        other=make_server(Bridge(self.tmp.name,self.gateway),'test-secret',0,policy={'mode':'manual'})
        try:
            from bridge import BridgeError
            with self.assertRaises(BridgeError): other.submit(request(),None)
        finally: other.server_close()
        r=requests.post(self.url+'/v1/videos',json=request(),headers=self.headers)
        self.assertEqual(r.status_code,202)
        self.server.wait_for_jobs()
        self.assertEqual(len(self.gateway.calls),8)
    def test_http_polling_and_range_video_return_after_budgeted_submit(self):
        from tests.test_generation import GenerationGateway
        from generation import Generation
        gateway=GenerationGateway(); self.server.bridge.gateway=gateway
        def download(url,path): path.write_bytes(b'0123456789'); return {'bytes':10,'duration':5}
        self.server.flow=Generation(self.server.bridge,download=download)
        r=requests.post(self.url+'/v1/videos',json=request(),headers=self.headers)
        key=r.json()['id']; self.server.wait_for_jobs()
        jobs=requests.get(self.url+'/v1/jobs',headers=self.headers).json()['data']
        job=next(x for x in jobs if x['id']==key)
        self.assertNotIn('paid-submit',gateway.calls)
        self.assertEqual(self.server.flow.submit(key)['status'],'queued')
        gateway.response={'status':3,'urls':['https://jzinnovation.jxbaobian.cn/result.mp4']}
        self.server.flow.poll(key)
        result=requests.get(self.url+'/v1/videos/'+key,headers=self.headers).json()
        self.assertEqual(result['status'],'completed')
        video=requests.get(result['video_url'],headers={'Range':'bytes=2-5'})
        self.assertEqual(video.status_code,206); self.assertEqual(video.content,b'2345')
        self.assertEqual(requests.get(self.url+'/media/unknown.mp4').status_code,404)
        cached=self.server.flow.videos/self.server.bridge.get(key)['mediaKey']
        cached.unlink()
        result=requests.get(self.url+'/v1/videos/'+key,headers=self.headers).json()
        self.assertEqual(result['status'],'processing')
        self.server.flow.poll(key)
        self.assertTrue(cached.exists())
        self.assertEqual(gateway.calls.count('paid-submit'),1)

    def test_extension_http_returns_new_clip_after_budgeted_submit(self):
        import shutil
        from pathlib import Path
        from extension import ExtensionFlow
        from enhancement import probe_video
        from tests.test_extension import FakeGateway as ExtGateway, request as ext_request
        gateway=ExtGateway()
        sample=Path(__file__).parent/'fixtures/sample.mp4'
        def download(url,path):
            shutil.copyfile(sample,path)
            return probe_video(path)
        self.server.extensions=ExtensionFlow(self.server.bridge,gateway=gateway,download=download)
        for prefix,count in [('',4),('/enhancement',4),('/extension',1)]:
            response=requests.get(self.url+prefix+'/v1/models',headers=self.headers)
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(len(response.json()['data']),count)
        invalid=ext_request();invalid['videos']*=2
        self.assertEqual(requests.post(self.url+'/extension/v1/videos',json=invalid,headers=self.headers).status_code,400)
        self.assertEqual(gateway.calls,[])
        response=requests.post(self.url+'/extension/v1/videos',json=ext_request(),headers=self.headers)
        self.assertEqual(response.status_code,202,response.text)
        key=response.json()['id'];self.server.wait_for_jobs();job=self.server.bridge.get(key)
        self.assertEqual(job['kind'],'extension');self.assertEqual(job['duration'],5)
        self.assertEqual(job['sourceVideo']['duration'],4);self.assertEqual(job['pointsEstimate'],76)
        self.assertFalse(any(paid for _,_,paid in gateway.calls))
        pending=requests.get(self.url+'/extension/v1/videos/'+key,headers=self.headers).json()
        self.assertFalse(pending['requires_confirmation'])
        self.assertEqual(self.server.extensions.submit(key)['status'],'queued')
        gateway.state=3;self.server.extensions.poll(key)
        result=requests.get(self.url+'/extension/v1/videos/'+key,headers=self.headers).json()
        self.assertEqual(result['status'],'completed')
        self.assertEqual(requests.get(result['video_url']).content,sample.read_bytes())
        self.assertEqual(sum(paid for _,_,paid in gateway.calls),1)
        self.assertEqual(self.gateway.calls,[])

    def test_enhancement_http_returns_original_duration_after_budgeted_submit(self):
        import shutil
        import uuid
        from pathlib import Path
        from enhancement import EnhancementFlow,probe_video
        from tests.test_enhancement_flow import FakeEnhancer
        from tests.test_enhancement import source_request
        class Gateway(FakeEnhancer):
            def submit_video(self, job):
                self.calls.append('submit')
                self.task = str(uuid.uuid5(uuid.NAMESPACE_OID, job['model']))
                return {'taskId': self.task}

        sample = Path(__file__).parent / 'fixtures/sample.mp4'
        def download(url,path):
            shutil.copyfile(sample,path)
            return probe_video(path)
        normal=requests.get(self.url+'/v1/models',headers=self.headers).json()['data']
        self.assertFalse(any(m['id'].startswith('dreama-enhance-') for m in normal))
        self.server.enhancements=EnhancementFlow(self.server.bridge,gateway=Gateway(),download=download)
        catalog=requests.get(self.url+'/enhancement/v1/models',headers=self.headers).json()['data']
        models = {'dreama-enhance-a': 1, 'dreama-enhance-b': 1,
                  'dreama-enhance-c-low': 1, 'dreama-enhance-c-high': 2}
        self.assertEqual({m['id'] for m in catalog if m['id'].startswith('dreama-enhance-')}, set(models))
        for model, points in models.items():
            with self.subTest(model=model):
                gateway = Gateway()
                self.server.enhancements=EnhancementFlow(self.server.bridge,gateway=gateway,download=download)
                headers = {**self.headers, 'Idempotency-Key': model}
                response = requests.post(self.url+'/enhancement/v1/videos', json=source_request(model), headers=headers)
                self.assertEqual(response.status_code, 202, response.text)
                key = response.json()['id']
                self.server.wait_for_jobs()
                job = self.server.bridge.get(key)
                self.assertEqual(job['model'], model)
                self.assertEqual(job['duration'], 1)
                self.assertEqual(job['pointsEstimate'], points)
                pending = requests.get(self.url+'/enhancement/v1/videos/'+key, headers=headers).json()
                self.assertFalse(pending['requires_confirmation'])
                self.assertNotIn('submit', gateway.calls)
                self.assertEqual(self.server.enhancements.submit(key)['status'], 'queued')
                gateway.state = 3
                delivered = self.server.enhancements.poll(key)
                self.assertEqual(delivered['taskId'], gateway.task)
                result = requests.get(self.url+'/enhancement/v1/videos/'+key, headers=headers).json()
                self.assertEqual(result['status'], 'completed')
                self.assertEqual(requests.get(result['video_url']).content, sample.read_bytes())
                self.assertEqual(gateway.calls.count('submit'), 1)
        self.assertEqual(self.gateway.calls, [])

if __name__ == '__main__': unittest.main()
