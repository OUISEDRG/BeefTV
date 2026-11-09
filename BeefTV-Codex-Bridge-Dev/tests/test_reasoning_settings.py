import copy
import tempfile
import unittest
from pathlib import Path
from completion_bridge import CompletionBridge, BridgeError
from model_catalog import ModelCatalog
from reasoning_settings import ReasoningSettings
from test_completion_bridge import FakeRPC, delta, done, tool, TOOLS

MODELS=[{'id':'gpt-6-astra','model':'gpt-6-astra','displayName':'GPT-6 Astra',
         'defaultReasoningEffort':'medium','inputModalities':['text','image'],
         'supportedReasoningEfforts':[{'reasoningEffort':x,'description':x} for x in ('low','medium','high','ultra')]},
        {'id':'gpt-6-luna','model':'gpt-6-luna','displayName':'GPT-6 Luna','defaultReasoningEffort':'medium',
         'inputModalities':['text','image'],
         'supportedReasoningEfforts':[{'reasoningEffort':x,'description':x} for x in ('low','medium','high')]}]

class RecordingRPC(FakeRPC):
    def start_turn(self,input_items,effort='high'):
        self.effort=effort
        return super().start_turn(input_items,effort)


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.catalog=ModelCatalog(MODELS)
        self.path=Path(self.tmp.name)/'settings.json'
        self.settings=ReasoningSettings(self.path,self.catalog,initial_overrides={'gpt-6-astra':'high'})
    def test_saved_setting_overrides_legacy_thinking_medium(self):
        self.assertEqual(self.settings.resolve('gpt-6-astra','medium'),'high')
        self.settings.update('gpt-6-astra','ultra',0)
        self.assertEqual(self.settings.resolve('gpt-6-astra','medium'),'ultra')
    def test_default_and_reset_keep_model_specific_defaults(self):
        self.assertEqual(self.settings.resolve('gpt-6-luna'),'medium')
        self.settings.update('gpt-6-astra',None,0)
        self.assertEqual(self.settings.resolve('gpt-6-astra'),'medium')
        self.assertEqual(self.settings.resolve('gpt-6-astra','low'),'medium')
    def test_restart_persists_and_revision_conflict(self):
        self.settings.update('gpt-6-astra','low',0)
        reloaded=ReasoningSettings(self.path,self.catalog,initial_overrides={'gpt-6-astra':'high'})
        self.assertEqual(reloaded.resolve('gpt-6-astra'),'low')
        with self.assertRaises(BridgeError) as error: reloaded.update('gpt-6-astra','high',0)
        self.assertEqual(error.exception.status,409)
    def test_unsupported_effort_is_not_silently_downgraded(self):
        with self.assertRaises((BridgeError,ValueError)): self.settings.update('gpt-6-luna','ultra',0)
        with self.assertRaises((BridgeError,ValueError)): self.settings.resolve('missing','high')
        self.assertEqual(self.settings.snapshot()['revision'],0)
    def test_model_and_effort_reach_rpc_and_chain_pins_effort(self):
        rpc=RecordingRPC([tool()])
        bridge=CompletionBridge(lambda:rpc,self.catalog.model_ids,reasoning=self.settings,
                                authorize_submission=lambda: None)
        self.addCleanup(bridge.close)
        request={'model':'gpt-6-astra','messages':[{'role':'user','content':'原文'}],'tools':TOOLS,'reasoning_effort':'medium'}
        first=bridge.complete(request)
        self.assertEqual(rpc.thread_args['model'],'gpt-6-astra')
        self.assertEqual(rpc.effort,'high')
        self.settings.update('gpt-6-astra','low',0)
        follow=copy.deepcopy(request); follow['messages'] += [first['choices'][0]['message'],{
            'role':'tool','tool_call_id':first['choices'][0]['message']['tool_calls'][0]['id'],'content':'ok'}]
        rpc.events.put(delta('完成')); rpc.events.put(done())
        bridge.complete(follow)
        self.assertEqual(rpc.effort,'high'); self.assertEqual(len(rpc.started),1)
        next_rpc=RecordingRPC([delta('下一次'),done()]); bridge.factory=lambda:next_rpc
        bridge.complete(request)
        self.assertEqual(next_rpc.effort,'low')
    def test_snapshot_cannot_mutate_saved_data(self):
        snap=self.settings.snapshot(); snap['overrides']['gpt-6-astra']='ultra'
        self.assertEqual(self.settings.resolve('gpt-6-astra'),'high')

if __name__=='__main__': unittest.main()
