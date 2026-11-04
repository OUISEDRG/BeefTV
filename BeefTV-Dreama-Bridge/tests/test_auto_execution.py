import tempfile
import unittest
from bridge import Bridge,BridgeError
from generation import Generation
from tests.test_bridge import request
from tests.test_generation import GenerationGateway
try:
    from auto_execution import AutoExecution
except ImportError:
    AutoExecution=None

class AutoExecutionTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(AutoExecution,'Backend countdown implementation is missing')
        self.tmp=tempfile.TemporaryDirectory();self.g=GenerationGateway();self.b=Bridge(self.tmp.name,self.g)
        self.flow=Generation(self.b);self.now=200
        self.policy={'mode':'background-countdown','delaySeconds':5,'effectiveAt':100}
        self.job=self.b.prepare(request(),'auto-1');self.job['createdAt']=101;self.b.update('auto-1',self.job)
        self.engine=self.restart()
    def restart(self):return AutoExecution(self.b,lambda job:self.flow,self.policy,now=lambda:self.now)
    def tearDown(self):
        if hasattr(self,'tmp'):self.tmp.cleanup()
    def test_five_seconds_then_exactly_one_submission_without_any_page(self):
        self.engine.step();self.assertEqual(self.b.get('auto-1')['autoExecuteAt'],205)
        self.now=204.999;self.engine.step();self.assertNotIn('paid-submit',self.g.calls)
        self.now=205;self.engine.step();self.assertEqual(self.b.get('auto-1')['status'],'queued')
        self.now=300;self.engine.step();self.restart().step();self.assertEqual(self.g.calls.count('paid-submit'),1)
    def test_cancellation_before_deadline_survives_restart(self):
        self.engine.step();self.now=204.9;self.engine.cancel('auto-1');self.now=300;self.restart().step()
        self.assertEqual(self.b.get('auto-1')['status'],'cancelled');self.assertNotIn('paid-submit',self.g.calls)
    def test_restart_preserves_deadline_and_does_not_need_browser(self):
        self.engine.step();self.now=204;engine=self.restart();engine.step();self.assertEqual(self.b.get('auto-1')['autoExecuteAt'],205)
        self.now=205;engine.step();self.assertEqual(self.g.calls.count('paid-submit'),1)
    def test_old_unknown_failed_and_already_submitted_jobs_are_excluded(self):
        for changes in [{'createdAt':99},{'status':'submission_unknown'},{'status':'unknown'},{'status':'failed'},{'status':'cancelled'},{'status':'queued'},{'generationSubmitted':True},{'quoteId':None}]:
            job={**self.job,**changes};self.b.update('auto-1',job);self.engine.step();self.now+=10;self.engine.step()
        self.assertNotIn('paid-submit',self.g.calls)
    def test_unknown_paid_receipt_is_never_retried_automatically(self):
        def lost(job):self.g.calls.append('paid-submit');raise TimeoutError()
        self.g.submit_video=lost;self.engine.step();self.now=205;self.engine.step()
        self.assertEqual(self.b.get('auto-1')['status'],'submission_unknown')
        self.now=300;self.restart().step();self.assertEqual(self.g.calls.count('paid-submit'),1)
    def test_new_quote_gets_five_fresh_seconds_and_uses_new_price(self):
        self.engine.step();self.g.price=120;self.now=205;self.engine.step()
        self.assertNotIn('paid-submit',self.g.calls);self.engine.step()
        self.assertEqual(self.b.get('auto-1')['autoExecuteAt'],210)
        self.now=209.9;self.engine.step();self.assertNotIn('paid-submit',self.g.calls)
        self.now=210;self.engine.step();self.assertEqual(self.b.get('auto-1')['approvedPoints'],120)
    def test_validation_failure_is_held_instead_of_repeated_every_tick(self):
        def invalid(job):self.g.calls.append('check-ready');raise BridgeError('source changed')
        self.g.check_ready=invalid;self.engine.step();self.now=205;self.engine.step();self.now=400;self.restart().step()
        self.assertEqual(self.g.calls.count('check-ready'),1);self.assertNotIn('paid-submit',self.g.calls)
        self.assertIn('source changed',self.b.get('auto-1')['autoExecuteError'])
    def test_cancel_request_during_pre_submit_checks_prevents_charge(self):
        self.g.check_ready=lambda job:self.engine.request_cancel(job['id'])
        self.engine.step();self.now=205;self.engine.step();self.assertNotIn('paid-submit',self.g.calls)
    def test_crash_during_readiness_pauses_with_manual_recovery_available(self):
        self.engine.step();job=self.b.get('auto-1')
        job.update(autoAttemptedQuoteId=job['quoteId'],autoAttemptedAt=205);self.b.update('auto-1',job)
        self.now=210;engine=self.restart()
        self.assertTrue(hasattr(engine,'recover_interrupted'),'Interrupted pre-submit recovery is missing')
        engine.recover_interrupted();engine.step()
        recovered=self.b.get('auto-1')
        self.assertEqual(recovered['status'],'prepared');self.assertIn('中断',recovered['autoExecuteError'])
        self.assertNotIn('paid-submit',self.g.calls)
        self.flow.approve('auto-1',recovered['quoteId'],recovered['pointsEstimate'])
        self.assertEqual(self.g.calls.count('paid-submit'),1)
    def test_recovery_does_not_clear_validation_error_or_unknown_paid_receipt(self):
        self.engine.step();job=self.b.get('auto-1')
        job.update(autoAttemptedQuoteId=job['quoteId'],autoAttemptedAt=205,autoExecuteError='source changed');self.b.update('auto-1',job)
        self.now=210;engine=self.restart()
        self.assertTrue(hasattr(engine,'recover_interrupted'),'Interrupted pre-submit recovery is missing')
        engine.recover_interrupted();engine.step();self.assertNotIn('paid-submit',self.g.calls)
        job.pop('autoExecuteError');job.update(status='submission_unknown');self.b.update('auto-1',job)
        engine.recover_interrupted();engine.step();self.assertNotIn('paid-submit',self.g.calls)

if __name__=='__main__':unittest.main()
