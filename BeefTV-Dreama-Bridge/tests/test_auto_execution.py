import tempfile
import unittest
from bridge import BridgeError
from tests.fixtures_budget import FundedBridge as Bridge, funded_policy
from generation import Generation
from auto_execution import AutoExecution
from tests.test_bridge import request
from tests.test_generation import GenerationGateway

class AutoExecutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.g=GenerationGateway(); self.b=Bridge(self.tmp.name,self.g)
        self.flow=Generation(self.b); self.now=1794010000
        self.job=self.b.prepare(request(),'auto-1')
        self.policy={'mode':'automatic'}
        self.engine=self.restart()
    def restart(self):
        return AutoExecution(self.b,lambda _:self.flow,self.policy,now=lambda:self.now)

    def test_preflight_then_exactly_one_submission_without_countdown(self):
        self.engine.step()
        self.assertEqual(self.b.get('auto-1')['status'],'queued')
        self.engine.step(); self.restart().step()
        self.assertEqual(self.g.calls.count('paid-submit'),1)
        self.assertNotIn('autoExecuteAt',self.b.get('auto-1'))

    def test_pre_submit_cancel_is_rejected_without_changing_task(self):
        with self.assertRaises(BridgeError): self.engine.cancel('auto-1')
        self.assertEqual(self.b.get('auto-1')['status'],'prepared')
        self.assertNotIn('paid-submit',self.g.calls)

    def test_restart_never_replays_an_existing_intent(self):
        self.engine.step()
        self.b=Bridge(self.tmp.name,self.g); self.flow=Generation(self.b)
        self.restart().recover_interrupted(); self.restart().step()
        self.assertEqual(self.g.calls.count('paid-submit'),1)

    def test_unknown_failed_and_already_submitted_jobs_are_excluded(self):
        for n,changes in enumerate([{'status':'submission_unknown'},{'status':'unknown'},
                                   {'status':'failed'},{'status':'cancelled'},{'status':'queued'},
                                   {'generationSubmitted':True},{'quoteId':None}]):
            key='excluded-'+str(n); row=self.b.prepare(request(),key)
            with self.b.db() as db:
                row.update(changes)
                import json
                db.execute('UPDATE jobs SET result=? WHERE key=?',(json.dumps(row),key))
            self.assertFalse(self.engine.eligible(row))

    def test_unknown_paid_receipt_is_never_retried_automatically(self):
        def lost(job): self.g.calls.append('paid-submit'); raise TimeoutError()
        self.g.submit_video=lost; self.engine.step()
        self.assertEqual(self.b.get('auto-1')['status'],'submission_unknown')
        self.now+=10; self.restart().step()
        self.assertEqual(self.g.calls.count('paid-submit'),1)

    def test_fresh_quote_is_budgeted_automatically(self):
        self.g.price=120
        self.engine.step()
        self.assertEqual(self.g.calls.count('paid-submit'),1)
        self.assertEqual(self.b.tasks.get('auto-1')['billing']['quotedPoints'],'120')
        self.assertNotIn('approvedPoints',self.b.get('auto-1'))

    def test_validation_failure_is_held_instead_of_repeated_every_tick(self):
        def invalid(job): self.g.calls.append('check-ready'); raise BridgeError('source changed')
        self.g.check_ready=invalid
        self.engine.step(); self.now+=10; self.restart().step()
        self.assertEqual(self.g.calls.count('check-ready'),1)
        self.assertNotIn('paid-submit',self.g.calls)
        self.assertIn('source changed',self.b.get('auto-1')['executionError'])

    def test_configured_zero_budget_blocks_paid_work(self):
        value=funded_policy(); value['dailyLimitPoints']='0'; self.b._budget_policy=value
        self.engine.step()
        self.assertNotIn('paid-submit',self.g.calls)
        self.assertEqual(self.b.tasks.get('auto-1')['state'],'blocked')

    def test_crash_during_readonly_preflight_can_resume_before_intent(self):
        def interrupted(job): raise SystemExit('crash-before-intent')
        self.g.check_ready=interrupted
        with self.assertRaises(SystemExit): self.engine.step()
        self.assertIsNone(self.b.tasks.get('auto-1')['billing'])
        self.g.check_ready=lambda _:None
        engine=self.restart(); engine.recover_interrupted(); engine.step()
        self.assertEqual(self.g.calls.count('paid-submit'),1)

    def test_recovery_keeps_validation_error_and_unknown_paid_receipt(self):
        def invalid(job): raise BridgeError('source changed')
        self.g.check_ready=invalid; self.engine.step()
        self.restart().recover_interrupted()
        self.assertIn('source changed',self.b.get('auto-1')['executionError'])
        self.assertNotIn('paid-submit',self.g.calls)

if __name__=='__main__': unittest.main()
