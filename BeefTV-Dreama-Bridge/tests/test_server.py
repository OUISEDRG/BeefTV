import tempfile
import threading
import unittest
import requests
from tests.test_bridge import FakeGateway, request
from bridge import Bridge


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
    def test_background_worker_submits_after_deadline_without_any_console_request(self):
        import time
        from generation import Generation
        from tests.test_generation import GenerationGateway
        gateway=GenerationGateway();self.server.bridge.gateway=gateway
        self.server.flow=Generation(self.server.bridge)
        self.server.execution_policy={'mode':'background-countdown','delaySeconds':5,'effectiveAt':time.time()-1}
        self.server.auto_execution.policy=self.server.execution_policy
        response=requests.post(self.url+'/v1/videos',json=request(),headers=self.headers)
        self.assertEqual(response.status_code,202);key=response.json()['id'];self.server.wait_for_jobs()
        deadline=time.monotonic()+7
        while time.monotonic()<deadline:
            job=self.server.bridge.get(key)
            if job['status']=='queued':break
            time.sleep(.1)
        self.assertEqual(job['status'],'queued',job)
        self.assertGreaterEqual(job['approvedAt']-job['autoExecuteAt'],0)
        self.assertEqual(gateway.calls.count('paid-submit'),1)
    def test_paused_auto_validation_is_reported_to_beeftv_as_needing_attention(self):
        from auto_execution import AutoExecution
        response=requests.post(self.url+'/v1/videos',json=request(),headers=self.headers)
        key=response.json()['id'];self.server.wait_for_jobs();job=self.server.bridge.get(key)
        job.update(autoExecuteError='source changed',autoAttemptedQuoteId=job['quoteId'],autoExecuteQuoteId=job['quoteId'])
        self.server.bridge.update(key,job)
        self.server.auto_execution=AutoExecution(self.server.bridge,self.server.flow_for,{'mode':'background-countdown','delaySeconds':5,'effectiveAt':1})
        result=requests.get(self.url+'/v1/videos/'+key,headers=self.headers).json()
        self.assertTrue(result['requires_confirmation']);self.assertIn('source changed',result['message'])
    def test_manual_execution_honors_cancel_arriving_during_readiness_checks(self):
        from generation import Generation
        from tests.test_generation import GenerationGateway
        gateway=GenerationGateway();self.server.bridge.gateway=gateway;self.server.flow=Generation(self.server.bridge)
        response=requests.post(self.url+'/v1/videos',json=request(),headers=self.headers)
        key=response.json()['id'];self.server.wait_for_jobs();job=self.server.bridge.get(key)
        gateway.check_ready=lambda job:self.server.auto_execution.request_cancel(job['id'])
        response=requests.post(self.url+'/v1/jobs/'+key+'/approve',json={'confirm':True,'quoteId':job['quoteId'],'points':100},headers=self.headers)
        self.assertEqual(response.status_code,400,response.text);self.assertNotIn('paid-submit',gateway.calls)
    def test_preflight_waits_for_explicit_confirmation_without_generation(self):
        r = requests.post(self.url+'/v1/videos', json=request(), headers=self.headers)
        self.assertEqual(r.status_code, 202, r.text)
        job = r.json()['id']
        self.server.wait_for_jobs()
        result = requests.get(self.url+'/v1/videos/'+job, headers=self.headers).json()
        self.assertEqual(result['status'], 'pending')
        self.assertTrue(result['requires_confirmation'])
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
    def test_http_confirmation_polling_and_range_video_return(self):
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
        payload={'quoteId':job['quoteId'],'points':100,'confirm':True}
        r=requests.post(self.url+'/v1/jobs/'+key+'/approve',json=payload,headers=self.headers)
        self.assertEqual(r.status_code,200,r.text)
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

    def test_extension_http_routes_confirmation_and_returns_new_clip(self):
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
        self.assertTrue(pending['requires_confirmation'])
        response=requests.post(self.url+'/v1/jobs/'+key+'/approve',json={'confirm':True,'quoteId':job['quoteId'],'points':76},headers=self.headers)
        self.assertEqual(response.status_code,200,response.text)
        gateway.state=3;self.server.extensions.poll(key)
        result=requests.get(self.url+'/extension/v1/videos/'+key,headers=self.headers).json()
        self.assertEqual(result['status'],'completed')
        self.assertEqual(requests.get(result['video_url']).content,sample.read_bytes())
        self.assertEqual(sum(paid for _,_,paid in gateway.calls),1)
        self.assertEqual(self.gateway.calls,[])

    def test_enhancement_http_routes_confirmation_and_returns_original_duration(self):
        import shutil
        from pathlib import Path
        from enhancement import EnhancementFlow,probe_video
        from tests.test_enhancement_flow import FakeEnhancer
        from tests.test_enhancement import source_request
        gateway=FakeEnhancer()
        def download(url,path):
            shutil.copyfile(Path(__file__).parent/'fixtures/sample.mp4',path)
            return probe_video(path)
        self.server.enhancements=EnhancementFlow(self.server.bridge,gateway=gateway,download=download)
        normal=requests.get(self.url+'/v1/models',headers=self.headers).json()['data']
        self.assertFalse(any(m['id'].startswith('dreama-enhance-') for m in normal))
        catalog=requests.get(self.url+'/enhancement/v1/models',headers=self.headers).json()['data']
        self.assertEqual(len([m for m in catalog if m['id'].startswith('dreama-enhance-')]),4)
        response=requests.post(self.url+'/enhancement/v1/videos',json=source_request(),headers=self.headers)
        self.assertEqual(response.status_code,202,response.text)
        key=response.json()['id']; self.server.wait_for_jobs(); job=self.server.bridge.get(key)
        self.assertEqual(job['duration'],1); self.assertEqual(job['pointsEstimate'],1)
        pending=requests.get(self.url+'/enhancement/v1/videos/'+key,headers=self.headers).json()
        self.assertTrue(pending['requires_confirmation']); self.assertNotIn('submit',gateway.calls)
        response=requests.post(self.url+'/v1/jobs/'+key+'/approve',json={'confirm':True,'quoteId':job['quoteId'],'points':1},headers=self.headers)
        self.assertEqual(response.status_code,200,response.text)
        gateway.state=3; self.server.enhancements.poll(key)
        result=requests.get(self.url+'/enhancement/v1/videos/'+key,headers=self.headers).json()
        self.assertEqual(result['status'],'completed')
        self.assertEqual(requests.get(result['video_url']).content,(Path(__file__).parent/'fixtures/sample.mp4').read_bytes())
        self.assertEqual(gateway.calls.count('submit'),1); self.assertEqual(self.gateway.calls,[])

if __name__ == '__main__': unittest.main()
