import tempfile
import unittest
from pathlib import Path
from tests.test_bridge import FakeGateway,request
from bridge import Bridge,BridgeError

class GenerationGateway(FakeGateway):
    def __init__(self): super().__init__(); self.task_id=0; self.price=100; self.response={'status':1,'urls':[]}
    def estimate(self,duration,model=None): return self.price
    def check_ready(self,job):
        if self.task_id: raise BridgeError('existing')
    def submit_video(self,job):
        self.calls.append('paid-submit'); self.task_id=987
        return {'taskId':987}
    def inspect_board(self,job): return {'videoTaskId':self.task_id}
    def task_status(self,task_id): return dict(self.response,taskId=task_id)

class GenerationTests(unittest.TestCase):
    def setUp(self):
        from generation import Generation
        self.tmp=tempfile.TemporaryDirectory(); self.g=GenerationGateway(); self.b=Bridge(self.tmp.name,self.g)
        self.job=self.b.prepare(request(),'job-1')
        def download(url,path): path.write_bytes(b'test-video'); return {'bytes':10,'duration':5}
        self.flow=Generation(self.b,download=download)
    def tearDown(self): self.tmp.cleanup()
    def test_preparation_never_submits_and_approval_is_bound_to_quote(self):
        self.assertNotIn('paid-submit',self.g.calls)
        with self.assertRaises(BridgeError): self.flow.approve('job-1','wrong',100)
        self.assertNotIn('paid-submit',self.g.calls)
        self.flow.approve('job-1',self.job['quoteId'],100)
        self.assertEqual(self.b.get('job-1')['taskId'],987)
        with self.assertRaises(BridgeError): self.flow.approve('job-1',self.job['quoteId'],100)
        self.assertEqual(self.g.calls.count('paid-submit'),1)
    def test_price_change_requires_new_confirmation(self):
        self.g.price=120
        with self.assertRaises(BridgeError): self.flow.approve('job-1',self.job['quoteId'],100)
        self.assertNotIn('paid-submit',self.g.calls)
        job=self.b.get('job-1'); self.assertEqual(job['pointsEstimate'],120)
        self.assertNotEqual(job['quoteId'],self.job['quoteId'])
    def test_pending_failure_and_success_with_verified_file(self):
        self.flow.approve('job-1',self.job['quoteId'],100)
        self.flow.poll('job-1'); self.assertEqual(self.b.get('job-1')['status'],'queued')
        self.g.response={'status':2,'urls':[]}; self.flow.poll('job-1')
        self.assertEqual(self.b.get('job-1')['status'],'processing')
        self.g.response={'status':3,'urls':['https://jzinnovation.jxbaobian.cn/result.mp4']}; self.flow.poll('job-1')
        job=self.b.get('job-1'); self.assertEqual(job['status'],'completed')
        self.assertTrue((Path(self.tmp.name)/'videos'/job['mediaKey']).is_file())
    def test_unknown_submit_reconciles_only_without_resubmitting_after_restart(self):
        def lost(job): self.g.task_id=987; self.g.calls.append('paid-submit'); raise TimeoutError()
        self.g.submit_video=lost
        with self.assertRaises(BridgeError): self.flow.approve('job-1',self.job['quoteId'],100)
        self.assertEqual(self.b.get('job-1')['status'],'submission_unknown')
        from generation import Generation
        flow=Generation(Bridge(self.tmp.name,self.g)); flow.poll('job-1')
        self.assertEqual(self.b.get('job-1')['taskId'],987)
        self.assertEqual(self.g.calls.count('paid-submit'),1)
    def test_failed_cancelled_and_missing_video_never_report_completed(self):
        self.flow.approve('job-1',self.job['quoteId'],100)
        for upstream,expected in [(3,'download_failed'),(4,'failed'),(5,'cancelled')]:
            job=self.b.get('job-1'); job['status']='queued'; self.b.update('job-1',job)
            self.g.response={'status':upstream,'urls':[]}; self.flow.poll('job-1')
            self.assertEqual(self.b.get('job-1')['status'],expected)
