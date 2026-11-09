import copy
import queue
import unittest

from completion_bridge import CompletionBridge, BridgeError, build_input


def done():
    return {'method': 'turn/completed', 'params': {'turn': {'id': 't1', 'status': 'completed'}}}


def delta(text):
    return {'method': 'item/agentMessage/delta', 'params': {'delta': text}}


def tool(name='canvas_get', arguments=None, call_id='c1'):
    return {'id': 101, 'method': 'item/tool/call', 'params': {
        'threadId': 'thread1', 'turnId': 't1', 'callId': call_id, 'tool': name,
        'arguments': arguments or {'canvasId': 'abc'}}}


class FakeRPC:
    def __init__(self, events):
        self.events = queue.Queue()
        for event in events: self.events.put(event)
        self.started = []
        self.replies = []
        self.closed = False
    def start_thread(self, **kwargs): self.thread_args = kwargs; return 'thread1'
    def start_turn(self, input_items, effort='high'): self.started.append(input_items); return 't1'
    def next_event(self, timeout=1): return self.events.get(timeout=timeout)
    def reply_tool(self, rpc_id, content_items, success=True):
        self.replies.append((rpc_id, content_items, success))
    def cancel(self): pass
    def close(self): self.closed = True


TOOLS = [{'type':'function','function':{'name':'canvas_get','description':'Read canvas',
         'parameters':{'type':'object','properties':{'canvasId':{'type':'string'}}}}}]


class BridgeTests(unittest.TestCase):
    def setUp(self): self.instances = []
    def make(self, events):
        def factory():
            rpc = FakeRPC(events); self.instances.append(rpc); return rpc
        self.bridge = CompletionBridge(factory, models=['gpt-6-astra'], timeout=0.1,
                                       authorize_submission=lambda: None)
        return self.bridge
    def tearDown(self):
        if hasattr(self, 'bridge'): self.bridge.close()
    def request(self, **extra):
        return {'model':'gpt-6-astra','messages':[{'role':'user','content':'原样  不修改\n你好'}], **extra}
    def consume(self, payload):
        return self.bridge.complete(payload)
    def test_new_completion_is_closed_by_default(self):
        self.bridge = CompletionBridge(lambda: self.fail('Default bridge must not start RPC'), ['gpt-6-astra'])
        with self.assertRaises(BridgeError) as denied:
            self.bridge.complete(self.request())
        self.assertEqual((denied.exception.status, denied.exception.code), (409, 'budget_not_configured'))
    def test_exact_text_and_json_response(self):
        self.make([delta('你好'), delta('世界'), done()])
        response = self.consume(self.request())
        self.assertEqual(response['choices'][0]['message']['content'], '你好世界')
        self.assertEqual(self.instances[0].started[0], [{'type':'text','text':'原样  不修改\n你好','text_elements':[]}])
        self.assertTrue(self.instances[0].closed)
    def test_tool_result_resumes_same_rpc_and_replay_is_cached(self):
        self.make([tool()])
        request = self.request(tools=TOOLS)
        first = self.consume(request)
        self.assertEqual(first['choices'][0]['finish_reason'], 'tool_calls')
        call = first['choices'][0]['message']['tool_calls'][0]
        rpc = self.instances[0]
        self.assertFalse(rpc.closed)
        continuation = copy.deepcopy(request)
        continuation['messages'] += [first['choices'][0]['message'], {
            'role':'tool','tool_call_id':call['id'],'content':'{"nodeId":"n1"}'}]
        rpc.events.put(delta('完成')); rpc.events.put(done())
        result = self.consume(continuation)
        self.assertEqual(result['choices'][0]['message']['content'], '完成')
        self.assertEqual(rpc.replies, [(101,[{'type':'inputText','text':'{"nodeId":"n1"}'}],True)])
        self.assertEqual(len(self.instances), 1)
        self.assertEqual(self.consume(continuation), result)
        self.assertEqual(len(rpc.replies), 1)
    def test_revoked_budget_allows_existing_tool_result_and_original_replay(self):
        self.make([tool()])
        request = self.request(tools=TOOLS)
        first = self.bridge.complete(request, idempotency_key='original')
        rpc = self.instances[0]
        self.bridge.authorize_submission = None
        self.assertEqual(self.bridge.complete(request, idempotency_key='original'), first)
        message = first['choices'][0]['message']
        follow = copy.deepcopy(request)
        follow['messages'] += [message, {'role':'tool','tool_call_id':message['tool_calls'][0]['id'],
                                         'content':'existing tool completed'}]
        rpc.events.put(delta('已完成')); rpc.events.put(done())
        self.assertEqual(self.bridge.complete(follow)['choices'][0]['message']['content'], '已完成')
        with self.assertRaises(BridgeError) as denied:
            self.bridge.complete(self.request(), idempotency_key='new')
        self.assertEqual(denied.exception.code, 'budget_not_configured')
        self.assertEqual(len(self.instances), 1)
        self.assertEqual(len(rpc.started), 1)
        self.assertEqual(len(rpc.replies), 1)
    def test_wrong_context_and_tools_rejected_before_reply(self):
        self.make([tool()])
        request = self.request(tools=TOOLS)
        first = self.consume(request)
        call = first['choices'][0]['message']['tool_calls'][0]
        follow = copy.deepcopy(request)
        follow['messages'][0]['content']='changed'
        follow['messages'] += [first['choices'][0]['message'], {'role':'tool','tool_call_id':call['id'],'content':'x'}]
        with self.assertRaises(BridgeError): self.consume(follow)
        self.assertEqual(self.instances[0].replies, [])
    def test_expired_tool_does_not_start_new_inference(self):
        self.make([])
        request = self.request(tools=TOOLS)
        request['messages'] += [{'role':'assistant','content':None,'tool_calls':[{'id':'call_btv_missing','type':'function','function':{'name':'canvas_get','arguments':'{}'}}]},
                                {'role':'tool','tool_call_id':'call_btv_missing','content':'x'}]
        with self.assertRaises(BridgeError): self.consume(request)
        self.assertEqual(self.instances, [])
    def test_unknown_model_rejected(self):
        self.make([])
        with self.assertRaises(BridgeError): self.consume(self.request(model='unknown'))
        self.assertEqual(self.instances, [])
    def test_undeclared_tool_fails_and_closes(self):
        self.make([tool('shell')])
        with self.assertRaises(BridgeError): self.consume(self.request(tools=TOOLS))
        self.assertTrue(self.instances[0].closed)
    def test_failed_turn_is_not_success(self):
        self.make([{'method':'turn/completed','params':{'turn':{'status':'failed','error':{'message':'quota exhausted'}}}}])
        with self.assertRaisesRegex(BridgeError, 'quota exhausted'): self.consume(self.request())
        self.assertTrue(self.instances[0].closed)
    def test_timeout_closes_without_retry(self):
        self.make([])
        with self.assertRaises(BridgeError): self.consume(self.request())
        self.assertEqual(len(self.instances), 1)
        self.assertTrue(self.instances[0].closed)
    def test_public_reasoning_summary_only(self):
        self.make([{'method':'item/reasoning/textDelta','params':{'delta':'private'}},
                   {'method':'item/reasoning/summaryTextDelta','params':{'delta':'公开进度'}},delta('答案'),done()])
        chunks=list(self.bridge.stream(self.request()))
        import json
        wire=json.dumps(chunks,ensure_ascii=False)
        self.assertIn('公开进度',wire)
        self.assertNotIn('private',wire)
        self.assertEqual(self.bridge.assemble(chunks)['choices'][0]['message']['content'],'答案')
    def test_image_and_history_roles_are_kept(self):
        messages=[{'role':'system','content':'system exact'}, {'role':'user','content':'old'},
                  {'role':'assistant','content':'reply'}, {'role':'user','content':[
                      {'type':'text','text':'new'}, {'type':'image_url','image_url':{'url':'data:image/png;base64,AA=='}}]}]
        base, developer, inputs = build_input(messages)
        self.assertIn('system exact', base)
        self.assertIn('"role": "assistant"', developer)
        self.assertEqual(inputs[0]['text'], 'new')
        self.assertEqual(inputs[1], {'type':'image','url':'data:image/png;base64,AA=='})


if __name__ == '__main__': unittest.main()
