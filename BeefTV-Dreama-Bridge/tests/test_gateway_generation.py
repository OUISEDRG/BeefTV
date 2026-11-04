import unittest
from unittest.mock import patch
from bridge import DreamaGateway,BridgeError
from model_catalog import models

class GatewayGenerationTests(unittest.TestCase):
    def test_paid_commands_require_explicit_submission_and_route_every_eid(self):
        gateway=DreamaGateway()
        with patch.object(gateway,'_invoke',return_value={'taskId':123}) as execute:
            with self.assertRaises(BridgeError): gateway.run('video','produce','--yes')
            with self.assertRaises(BridgeError): gateway.submit_video({'status':'prepared'})
            self.assertEqual(execute.call_count,0)
            for spec in models().values():
                job={'status':'submitting','approvedAt':1,'model':spec['id'],'duration':spec['durations'][0],'storyboardId':123}
                gateway.submit_video(job)
                args=execute.call_args.args
                self.assertEqual(args[args.index('--llm')+1],spec['id'])
                self.assertEqual(args[args.index('--storyboard-id')+1],123)
                self.assertIn('--yes',args)
                if spec['resolution']: self.assertEqual(args[args.index('--resolution')+1],spec['resolution'])
                else: self.assertNotIn('--resolution',args)
    def test_task_response_must_match_requested_id(self):
        gateway=DreamaGateway()
        with patch.object(gateway,'run',return_value={'items':[{'taskId':999,'status':3,'urls':['wrong']}]}):
            with self.assertRaises(BridgeError): gateway.task_status(123)
