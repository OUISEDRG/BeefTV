"""Independent review coverage; fake RPC only, no generation or live account."""
import copy
from pathlib import Path
import tempfile
import unittest

from completion_bridge import BridgeError, CompletionBridge
from model_catalog import ModelCatalog
from reasoning_settings import ReasoningSettings
from test_reasoning_settings import MODELS, RecordingRPC
from test_completion_bridge import TOOLS, tool, delta, done


class ModelSettingsReviewTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.catalog = ModelCatalog(MODELS)
        self.settings = ReasoningSettings(Path(self.directory.name)/'settings.json', self.catalog,
                                          {'gpt-6-astra':'high'})
        self.rpcs = []
        def factory():
            rpc = RecordingRPC([tool()])
            self.rpcs.append(rpc)
            return rpc
        self.bridge = CompletionBridge(factory, self.catalog.model_ids, reasoning=self.settings,
                                       authorize_submission=lambda: None)
        self.addCleanup(self.bridge.close)
        self.request = {'model':'gpt-6-astra','messages':[{'role':'user','content':'read canvas'}],
                        'tools':TOOLS,'reasoning_effort':'medium'}

    def test_model_change_rejected_and_effort_changes_cannot_retarget_pending_tool(self):
        first = self.bridge.complete(self.request)
        call_id = first['choices'][0]['message']['tool_calls'][0]['id']
        self.settings.update('gpt-6-astra','low',0)
        follow = copy.deepcopy(self.request)
        follow['messages'] += [first['choices'][0]['message'],
                               {'role':'tool','tool_call_id':call_id,'content':'canvas read result'}]
        follow['model'] = 'gpt-6-luna'
        follow['reasoning_effort'] = 'ultra'
        with self.assertRaises(BridgeError) as error:
            self.bridge.complete(follow)
        self.assertEqual(error.exception.code, 'tool_context_mismatch')
        self.assertEqual(len(self.rpcs), 1)
        rpc = self.rpcs[0]
        self.assertEqual(rpc.replies, [])
        self.assertFalse(rpc.closed)
        follow['model'] = 'gpt-6-astra'
        rpc.events.put(delta('done')); rpc.events.put(done())
        result = self.bridge.complete(follow)
        self.assertEqual(result['model'], 'gpt-6-astra')
        self.assertEqual(rpc.thread_args['model'], 'gpt-6-astra')
        self.assertEqual(rpc.effort, 'high')
        self.assertEqual(len(rpc.started), 1)

    def test_active_settings_default_overrides_even_unsupported_request_effort(self):
        rpc = RecordingRPC([delta('done'),done()])
        self.bridge.factory = lambda:rpc
        request = copy.deepcopy(self.request)
        request['model'] = 'gpt-6-luna'
        request['reasoning_effort'] = 'ultra'  # Unsupported on Luna; page/default is authoritative.
        self.bridge.complete(request)
        self.assertEqual(rpc.thread_args['model'], 'gpt-6-luna')
        self.assertEqual(rpc.effort, 'medium')
        self.assertEqual(len(rpc.started), 1)


if __name__ == '__main__': unittest.main()
