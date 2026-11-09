import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from bridge import Bridge, BridgeError
from generation import Generation
from tests.test_task_repository import policy

class FakePaidGateway:
    def __init__(self, task_id=700): self.calls=[]; self.task_id=task_id; self.receipt=None
    def submit_video(self, job): self.calls.append('paid'); self.receipt=self.task_id; return {'taskId': self.task_id}
    def inspect_board(self, job): return {'videoTaskId':self.receipt, 'imageTaskId':self.receipt}
    def task_status(self, *args): return {'status':3,'urls':['https://jzinnovation.jxbaobian.cn/fixture.mp4']}
    def estimate(self, *args): return 1.25
    def check_ready(self, job): pass

class SubmissionTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(hasattr(Generation, 'submit'), 'P1 automatic budgeted submission is missing')
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def prepared(self, *, configured=False, kind='video', key='j'):
        bridge=Bridge(self.root, budget_policy=policy(daily='10',limit='3') if configured else None)
        job={'id':key,'status':'prepared','kind':kind,'model':'fixture','duration':5,'quoteId':'q',
             'pointsEstimate':1.25,'requestDigest':'digest-'+key,'generationSubmitted':False}
        with bridge.db() as db: db.execute('INSERT INTO jobs VALUES (?,?,?)',(key,job['requestDigest'],json.dumps(job)))
        bridge.update(key,job)
        return bridge, job

    def test_unconfigured_blocks_all_five_real_flow_submit_boundaries(self):
        from image_flow import ImageFlow
        from extension import ExtensionFlow
        from enhancement import EnhancementFlow
        from audio_flow import AudioFlow
        for n,(kind,cls) in enumerate(zip(('video','image','extension','enhancement','audio'),
                                          (Generation,ImageFlow,ExtensionFlow,EnhancementFlow,AudioFlow))):
            bridge,job=self.prepared(kind=kind,key=str(n))
            gateway=FakePaidGateway()
            flow=cls(bridge,gateway=gateway)
            with patch.object(flow,'check_ready'), patch.object(flow,'estimate_job',return_value=1.25):
                with self.assertRaises(BridgeError): flow.submit(job['id'])
            self.assertEqual(gateway.calls,[])
            self.assertIsNone(bridge.tasks.get(job['id'])['billing'])

    def test_intent_is_durable_and_network_does_not_hold_sqlite_writer(self):
        bridge,job=self.prepared(configured=True); gateway=FakePaidGateway()
        original=gateway.submit_video
        def submit(value):
            self.assertEqual(bridge.tasks.get('j')['state'],'submission_intent')
            with bridge.db() as db: db.execute('BEGIN IMMEDIATE')
            return original(value)
        gateway.submit_video=submit
        flow=Generation(bridge,gateway=gateway)
        result=flow.submit('j')
        self.assertEqual(result['taskId'],700)
        self.assertEqual(gateway.calls,['paid'])
        for field in ('approvedAt','approvedPoints','autoExecuteAt'): self.assertNotIn(field,result)
        self.assertIsNone(bridge.tasks.get('j')['billing']['actualPoints'])
        with self.assertRaises(BridgeError): flow.submit('j')

    def test_budget_removed_after_reservation_blocks_paid_transport_and_releases(self):
        from submission import require_paid_intent
        bridge, job = self.prepared(configured=True)
        gateway = FakePaidGateway()

        def submit(saved):
            self.assertEqual(bridge.tasks.get(job['id'])['billing']['settlementStatus'], 'reserved')
            bridge._budget_policy = None
            require_paid_intent('video', saved, consume=True)
            gateway.calls.append('paid')
            return {'taskId':700}

        gateway.submit_video = submit
        with self.assertRaises(BridgeError):
            Generation(bridge, gateway=gateway).submit(job['id'])
        self.assertEqual(gateway.calls, [])
        self.assertEqual(bridge.tasks.get(job['id'])['billing']['settlementStatus'], 'released')
        self.assertIsNone(bridge.tasks.get(job['id'])['identity']['upstreamTaskId'])
        self.assertIs(bridge.get(job['id'])['generationSubmitted'], False)

    def test_budget_removed_while_sqlite_writer_is_held_blocks_paid_transport(self):
        from concurrent.futures import ThreadPoolExecutor
        from contextlib import closing, contextmanager
        import threading
        from submission import require_paid_intent
        bridge, job = self.prepared(configured=True)
        gateway = FakePaidGateway()
        boundary, proceed, recheck = (threading.Event() for _ in range(3))
        connect = bridge.tasks.connect

        @contextmanager
        def watch_recheck():
            with connect() as db:
                if boundary.is_set():
                    recheck.set()
                yield db

        def submit(saved):
            boundary.set()
            self.assertTrue(proceed.wait(3))
            require_paid_intent('video', saved, consume=True)
            gateway.calls.append('paid')
            return {'taskId':700}

        gateway.submit_video = submit
        with patch.object(bridge.tasks,'connect',watch_recheck), ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(Generation(bridge,gateway=gateway).submit, job['id'])
            self.assertTrue(boundary.wait(3))
            with closing(sqlite3.connect(bridge.db_path)) as blocker, blocker:
                blocker.execute('BEGIN IMMEDIATE')
                proceed.set()
                self.assertTrue(recheck.wait(3))
                bridge._budget_policy = None
            with self.assertRaises(BridgeError):
                pending.result(timeout=5)
        self.assertEqual(gateway.calls, [])
        self.assertEqual(bridge.tasks.get(job['id'])['billing']['settlementStatus'], 'released')

    def test_stale_peer_poll_cannot_reverse_a_proven_local_rejection(self):
        from concurrent.futures import ThreadPoolExecutor
        import threading
        from submission import require_paid_intent
        bridge, job = self.prepared(configured=True)
        gateway, peer_gateway = FakePaidGateway(), FakePaidGateway()
        peer = Bridge(self.root, peer_gateway)
        entered, resume = threading.Event(), threading.Event()

        def inspect(saved):
            entered.set()
            self.assertTrue(resume.wait(3))
            return {'videoTaskId':None}

        peer_gateway.inspect_board = inspect
        with ThreadPoolExecutor(max_workers=1) as pool:
            def submit(saved):
                nonlocal stale_poll
                stale_poll = pool.submit(Generation(peer).poll, job['id'])
                self.assertTrue(entered.wait(3))
                bridge._budget_policy = None
                require_paid_intent('video', saved, consume=True)
                gateway.calls.append('paid')

            stale_poll = None
            gateway.submit_video = submit
            try:
                with self.assertRaises(BridgeError):
                    Generation(bridge, gateway=gateway).submit(job['id'])
            finally:
                resume.set()
            self.assertIsNotNone(stale_poll)
            latest = stale_poll.result(timeout=5)
        self.assertEqual((latest['status'], latest['generationSubmitted']), ('failed', False))
        self.assertEqual(peer.tasks.get(job['id'])['billing']['settlementStatus'], 'released')
        self.assertEqual(gateway.calls, [])

    def test_quote_change_automatically_rechecks_budget(self):
        bridge,job=self.prepared(configured=True); gateway=FakePaidGateway()
        gateway.estimate=lambda *args: 2
        Generation(bridge,gateway=gateway).submit('j')
        billing=bridge.tasks.get('j')['billing']
        self.assertEqual(billing['estimatedPoints'],'1.25')
        self.assertEqual(billing['quotedPoints'],'2')
        self.assertEqual(billing['reservedPoints'],'2')

    def test_lost_receipt_reopen_only_reconciles_never_resubmits(self):
        bridge,job=self.prepared(configured=True); gateway=FakePaidGateway()
        def lost(value): gateway.calls.append('paid'); gateway.receipt=700; raise TimeoutError()
        gateway.submit_video=lost
        with self.assertRaises(BridgeError): Generation(bridge,gateway=gateway).submit('j')
        self.assertEqual(bridge.get('j')['status'],'submission_unknown')
        reopened=Bridge(self.root, budget_policy=None)
        def download(url,path): path.write_bytes(b'fixture'); return {'bytes':7}
        result=Generation(reopened,gateway=gateway,download=download).poll('j')
        self.assertEqual(result['status'],'completed')
        self.assertTrue((self.root/'videos'/result['mediaKey']).exists())
        self.assertEqual(gateway.calls,['paid'])
        self.assertEqual(reopened.tasks.get('j')['state'],'artifact_ready')

    def test_crash_after_intent_before_return_is_not_new_submission(self):
        bridge,job=self.prepared(configured=True); gateway=FakePaidGateway()
        def crash(value): raise SystemExit('simulated abrupt exit')
        gateway.submit_video=crash
        with self.assertRaises(SystemExit): Generation(bridge,gateway=gateway).submit('j')
        reopened=Bridge(self.root, budget_policy=None)
        flow=Generation(reopened,gateway=gateway)
        with self.assertRaises(BridgeError): flow.submit('j')
        flow.poll('j')
        self.assertEqual(reopened.get('j')['status'],'submission_unknown')
        self.assertEqual(reopened.tasks.get('j')['billing']['settlementStatus'],'unknown')

    def test_receipt_database_failure_is_unknown_and_not_retried(self):
        bridge,job=self.prepared(configured=True); gateway=FakePaidGateway()
        with patch.object(bridge.tasks,'record_receipt',side_effect=sqlite3.OperationalError('injected write failure')):
            with self.assertRaises(BridgeError): Generation(bridge,gateway=gateway).submit('j')
        self.assertEqual(bridge.get('j')['status'],'submission_unknown')
        with self.assertRaises(BridgeError): Generation(bridge,gateway=gateway).submit('j')
        self.assertEqual(gateway.calls,['paid'])

    def test_known_cli_preflight_failure_releases_without_running_paid_process(self):
        import subprocess
        from dreama_cli import DreamaCliClient
        bridge, job = self.prepared(configured=True)
        gateway = FakePaidGateway()
        client = DreamaCliClient()
        gateway.submit_video = lambda saved: client.submit(saved, 'video', 'produce', '--project-id', 1,
            '--series-id', 2, '--storyboard-id', 3, '--llm', 'fixture', '--duration', 5,
            '--produce-video-format', 'mp4', '--yes')
        with (patch.object(client, '_check_paid_parameters'),
              patch.object(client, 'check_version', side_effect=BridgeError('fixture incompatible CLI')),
              patch.object(subprocess, 'run') as execute):
            with self.assertRaises(BridgeError):
                Generation(bridge, gateway=gateway).submit(job['id'])
        execute.assert_not_called()
        self.assertEqual(bridge.tasks.get(job['id'])['billing']['settlementStatus'], 'released')
        self.assertIs(bridge.get(job['id'])['generationSubmitted'], False)

    def test_all_five_flows_query_and_deliver_after_budget_is_removed(self):
        import hashlib
        from image_flow import ImageFlow
        from extension import ExtensionFlow
        from enhancement import EnhancementFlow
        from audio_flow import AudioFlow
        for n,(kind,cls) in enumerate(zip(('video','image','extension','enhancement','audio'),
                                          (Generation,ImageFlow,ExtensionFlow,EnhancementFlow,AudioFlow))):
            bridge,job=self.prepared(configured=True,kind=kind,key='deliver-'+str(n))
            task='f7c333d0-9178-4c41-89cf-e5715e1b83b9' if kind in ('audio','enhancement') else 700
            gateway=FakePaidGateway(task)
            def download(url,path):
                path.write_bytes(b'fixture')
                return {'bytes':7,'width':1,'height':1,'mimeType':'image/png','sha256':hashlib.sha256(b'fixture').hexdigest()}
            flow=cls(bridge,gateway=gateway,download=download)
            with patch.object(flow,'check_ready'),patch.object(flow,'estimate_job',return_value=1.25): flow.submit(job['id'])
            bridge._budget_policy=None
            with patch.object(flow,'finalize_video',side_effect=lambda j,p,m:m): result=flow.poll(job['id'])
            self.assertEqual(result['status'],'completed',kind)
            self.assertEqual(gateway.calls,['paid'])
            bill=bridge.tasks.get(job['id'])['billing']
            if kind in ('audio','enhancement'):
                self.assertEqual(bill['billingSource'],'estimated')
                self.assertIsNone(bill['quotedPoints'])

    def test_operator_statement_updates_original_charge_while_budget_is_unconfigured(self):
        from contextlib import redirect_stderr, redirect_stdout
        from io import StringIO
        from reconcile_billing import main
        from server import make_server
        bridge, job = self.prepared(configured=True)
        gateway = FakePaidGateway()
        self.assertEqual(Generation(bridge, gateway=gateway).submit(job['id'])['taskId'], 700)
        original = bridge.tasks.get(job['id'])['billing']
        bridge._budget_policy = None
        proof = self.root / 'statement.json'
        proof.write_text(json.dumps({'schemaVersion':1, 'bridgeJobId':job['id'], 'intentId':original['intentId'],
            'upstreamTaskId':700, 'source':'official_statement', 'reference':'fixture-line-1',
            'outcome':'charge_confirmed', 'actualPoints':'1.50'}), encoding='utf-8')
        server = make_server(bridge, 'fixture-key', 0, policy={'mode':'manual'})
        try:
            with redirect_stderr(StringIO()), self.assertRaises(SystemExit) as error:
                main(['--state', str(self.root), '--evidence', str(proof)])
            self.assertEqual(error.exception.code, 1)
            self.assertIsNone(bridge.tasks.get(job['id'])['billing']['actualPoints'])
        finally:
            server.server_close()
        with redirect_stdout(StringIO()) as output:
            self.assertEqual(main(['--state', str(self.root), '--evidence', str(proof)]), 0)
        audit = json.loads(output.getvalue())
        self.assertEqual(audit['anomalies'], ['charge_variance'])
        self.assertEqual(audit['billing']['actualPoints'], '1.50')
        self.assertFalse(bridge.budget_is_configured())
        self.assertEqual(bridge.get(job['id'])['taskId'], 700)
        self.assertEqual(gateway.calls, ['paid'])

    def test_two_processes_can_only_execute_one_paid_boundary(self):
        import os,subprocess,sys,time
        bridge,job=self.prepared(configured=True)
        code='''
import sys,time
from pathlib import Path
from bridge import Bridge,BridgeError
from generation import Generation
from tests.test_budgeted_submission import FakePaidGateway
from tests.fixtures_budget import funded_policy
root=Path(sys.argv[1]); worker=sys.argv[2]
bridge=Bridge(root,budget_policy=funded_policy()); gateway=FakePaidGateway()
def paid(job):
    with (root/'paid-boundaries.txt').open('a') as out: out.write(worker+'\\n')
    return {'taskId':700}
gateway.submit_video=paid
(root/('ready-'+worker)).touch()
deadline=time.monotonic()+10
while not (root/'go').exists():
    if time.monotonic()>deadline: raise SystemExit(2)
    time.sleep(.01)
try: Generation(bridge,gateway=gateway).submit('j')
except BridgeError: pass
'''
        workers=[subprocess.Popen([sys.executable,'-B','-c',code,str(self.root),str(n)],stdout=subprocess.PIPE,stderr=subprocess.PIPE) for n in range(2)]
        try:
            deadline=time.monotonic()+10
            while len(list(self.root.glob('ready-*')))<2 and time.monotonic()<deadline: time.sleep(.01)
            self.assertEqual(len(list(self.root.glob('ready-*'))),2)
            (self.root/'go').touch()
            for worker in workers:
                stdout,stderr=worker.communicate(timeout=15)
                self.assertEqual(worker.returncode,0,stderr.decode('utf-8',errors='replace'))
            self.assertEqual(len((self.root/'paid-boundaries.txt').read_text().splitlines()),1)
        finally:
            for worker in workers:
                if worker.poll() is None: worker.kill(); worker.communicate()

if __name__=='__main__': unittest.main()
