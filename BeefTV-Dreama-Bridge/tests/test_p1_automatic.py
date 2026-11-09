import tempfile
import unittest
from bridge import Bridge, BridgeError
from generation import Generation
from auto_execution import AutoExecution
from tests.test_bridge import request
from tests.test_generation import GenerationGateway
from tests.fixtures_budget import funded_policy

class AutomaticContractTests(unittest.TestCase):
    def test_health_reports_new_generation_disabled_until_budget_is_configured(self):
        import threading,requests
        from server import make_server
        with tempfile.TemporaryDirectory() as root:
            bridge=Bridge(root,GenerationGateway())
            server=make_server(bridge,'synthetic-health-token',0)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                response=requests.get('http://127.0.0.1:'+str(server.server_port)+'/health',timeout=5)
                self.assertFalse(response.json()['generationEnabled'])
                self.assertTrue(response.json()['autoGeneration'])
                self.assertFalse(response.json()['requiresPerJobConfirmation'])
            finally:
                server.shutdown();server.server_close();thread.join()

    def test_no_budget_stays_unsubmitted_and_policy_change_can_unblock(self):
        with tempfile.TemporaryDirectory() as root:
            gateway=GenerationGateway(); bridge=Bridge(root,gateway)
            flow=Generation(bridge); job=bridge.prepare(request(),'auto-p1')
            engine=AutoExecution(bridge,lambda _:flow,{'mode':'automatic'})
            engine.step()
            self.assertEqual(bridge.get(job['id'])['stage'],'budget_blocked')
            self.assertNotIn('paid-submit',gateway.calls)
            bridge._budget_policy=funded_policy()
            engine.step()
            self.assertEqual(gateway.calls.count('paid-submit'),1)
            self.assertNotIn('autoExecuteAt',bridge.get(job['id']))

    def test_pre_submit_cancel_is_not_a_new_execution_state(self):
        with tempfile.TemporaryDirectory() as root:
            gateway=GenerationGateway(); bridge=Bridge(root,gateway,budget_policy=funded_policy())
            flow=Generation(bridge); bridge.prepare(request(),'new')
            engine=AutoExecution(bridge,lambda _:flow,{'mode':'automatic'})
            with self.assertRaises(BridgeError): engine.cancel('new')
            self.assertEqual(bridge.get('new')['status'],'prepared')

if __name__=='__main__': unittest.main()
