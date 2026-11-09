import copy,json,tempfile,unittest
from unittest.mock import patch
from bridge import Bridge,BridgeError,DreamaGateway
from tests.fixtures_budget import FundedBridge as Bridge
from tests.test_bridge import request,FakeGateway

class TruncatedNames(unittest.TestCase):
    def test_existing_child_name_can_be_truncated_but_ids_url_voice_must_match(self):
        g=DreamaGateway();name='BeefTV_5c659e07ed5c_参考图1'
        row={'assetId':212535,'name':name,'voiceId':0,'uploadVoiceUrl':'https://voice',
             'images':[{'imageId':233930,'name':'BeefTV_5c659e07ed5c_','resourceUrl':['https://image']}]}
        ref={'name':name,'url':'https://image','voiceId':0,'uploadVoiceUrl':'https://voice'}
        with patch.object(g,'run',return_value={'items':[row]}):
            a,i=g.read_character_reference(ref)
            self.assertEqual((a['assetId'],i['imageId']),(212535,233930))
            with self.assertRaises(BridgeError):g.read_character_reference({**ref,'imageId':999})
        with patch.object(g,'run',return_value={'items':[row,copy.deepcopy(row)]}):
            with self.assertRaises(BridgeError):g.read_character_reference(ref)

    def test_created_reference_uses_actual_child_name_for_assembler(self):
        g=DreamaGateway();row={'assetId':1,'name':'long-parent','voiceId':0,'uploadVoiceUrl':'https://voice',
            'images':[{'imageId':2,'name':'short-child','resourceUrl':['https://image']}]}
        with patch.object(g,'run',return_value={'items':[row]}),patch.object(g,'post',return_value={}):
            ref=g.create_character_reference('long-parent','https://image',{'voiceId':0,'uploadVoiceUrl':'https://voice'})
        self.assertEqual(ref['name'],'short-child')
        self.assertEqual(ref['assetName'],'long-parent')

class InterruptedGateway(FakeGateway):
    def create_reference(self,name,url):
        self.calls.append('reference');self.saved={'assetId':100,'imageId':200,'name':name,'url':url}
        raise BridgeError('真实参考图绑定回读不唯一或URL不符。')
    def recover_reference(self,name,url,voice=None):
        if name!=self.saved['name'] or url!=self.saved['url']:raise BridgeError('wrong recovery')
        self.calls.append('reconcile');return self.saved.copy()

class ResumePreparation(unittest.TestCase):
    def test_recovered_task_can_only_submit_once_even_after_restart(self):
        from tests.test_generation import GenerationGateway
        from generation import Generation
        class Gateway(InterruptedGateway,GenerationGateway):pass
        with tempfile.TemporaryDirectory() as d:
            g=Gateway();b=Bridge(d,g);data=request()
            with self.assertRaises(BridgeError):b.prepare(data,'original')
            job=b.prepare(data,'original',recover=True)
            Generation(b).approve('original',job['quoteId'],100)
            b=Bridge(d,g)
            with self.assertRaises(BridgeError):b.prepare(data,'original',recover=True)
            with self.assertRaises(BridgeError):Generation(b).approve('original',job['quoteId'],100)
            self.assertEqual(g.calls.count('paid-submit'),1)

    def test_http_resume_is_authenticated_idempotent_and_never_recreates(self):
        import threading,requests
        from server import make_server
        with tempfile.TemporaryDirectory() as d:
            g=InterruptedGateway();b=Bridge(d,g);data=request()
            with self.assertRaises(BridgeError):b.prepare(data,'original')
            server=make_server(b,'secret',0,policy={'mode':'manual'})
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            url='http://127.0.0.1:'+str(server.server_port)+'/v1/jobs/original/resume'
            body={'confirm':True,'request':data};headers={'Authorization':'Bearer secret'}
            try:
                self.assertEqual(requests.post(url,json=body).status_code,401)
                altered=copy.deepcopy(body);altered['request']['duration']=6
                self.assertEqual(requests.post(url,json=altered,headers=headers).status_code,400)
                self.assertEqual(requests.post(url,json=body,headers=headers).status_code,202)
                server.wait_for_jobs()
                self.assertEqual(b.get('original')['status'],'prepared')
                self.assertEqual(requests.post(url,json=body,headers=headers).status_code,202)
                server.wait_for_jobs()
                self.assertEqual(g.calls,['upload','reference','reconcile','save','estimate'])
            finally:
                server.shutdown();server.server_close();thread.join()

    def test_same_task_resumes_binding_without_recreating_asset_or_submitting(self):
        with tempfile.TemporaryDirectory() as d:
            g=InterruptedGateway();b=Bridge(d,g);data=request()
            with self.assertRaises(BridgeError):b.prepare(data,'original')
            job=b.prepare(data,'original',recover=True)
            self.assertEqual(job['id'],'original');self.assertEqual(job['status'],'prepared')
            self.assertEqual(g.calls,['upload','reference','reconcile','save','estimate'])
            self.assertFalse(job['generationSubmitted'])
            self.assertEqual(b.prepare(data,'original',recover=True),job)
            self.assertEqual(len(g.calls),5)
    def test_changed_payload_and_any_submission_evidence_are_rejected(self):
        for field,value in [('generationSubmitted',None),('taskId',123),('storyboardId',123),('approvedAt',1),('autoAttemptedQuoteId','q')]:
            with self.subTest(field=field),tempfile.TemporaryDirectory() as d:
                g=InterruptedGateway();b=Bridge(d,g);data=request()
                with self.assertRaises(BridgeError):b.prepare(data,'original')
                job=b.get('original');job[field]=value;b.update('original',job)
                with self.assertRaises(BridgeError):b.prepare(data,'original',recover=True)
                self.assertEqual(g.calls,['upload','reference'])
        with tempfile.TemporaryDirectory() as d:
            g=InterruptedGateway();b=Bridge(d,g);data=request()
            with self.assertRaises(BridgeError):b.prepare(data,'original')
            data['duration']=6
            with self.assertRaises(BridgeError):b.prepare(data,'original',recover=True)
            self.assertEqual(g.calls,['upload','reference'])

if __name__=='__main__':unittest.main()
