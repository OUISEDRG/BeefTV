"""BeefTV Durable context tails must not turn tool results into new requests.

Fake native transport only; no Codex inference or real canvas writes.
"""
import copy
import unittest

from completion_bridge import BridgeError, CompletionBridge
from test_completion_bridge import FakeRPC, TOOLS, delta, done, tool


def studio(revision=1, plan='', restored=''):
    blocks = [part for part in (restored, f'<beeftv_plan>\n{plan}\n</beeftv_plan>' if plan else '',
                               f'<beeftv_studio>\nrevision {revision}\n</beeftv_studio>') if part]
    return {'role': 'user', 'content': [{'type': 'text', 'text': '\n\n'.join(blocks)}]}


def request(user='Read reference', tail=None):
    return {'model': 'gpt-6-astra', 'tools': copy.deepcopy(TOOLS), 'messages': [
        {'role': 'system', 'content': 'Only the provided canvas tools.'},
        {'role': 'user', 'content': user}, tail or studio()]}


def follow(payload, answer, tail=None, image=False, result='read completed'):
    value = copy.deepcopy(payload)
    value['messages'].pop()  # Durable replaces its transient tail for each request.
    call = answer['choices'][0]['message']['tool_calls'][0]
    value['messages'] += [answer['choices'][0]['message'], {
        'role': 'tool', 'tool_call_id': call['id'], 'content': result}]
    if image:
        value['messages'].append({'role': 'user', 'content': [
            {'type': 'text', 'text': 'Attached image(s) from tool result:'},
            {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,AA=='}}]})
    value['messages'].append(tail or studio())
    return value


class StudioContinuationTests(unittest.TestCase):
    def setUp(self):
        self.instances = []
        def factory():
            rpc = FakeRPC([tool()])
            self.instances.append(rpc)
            return rpc
        self.bridge = CompletionBridge(factory, ['gpt-6-astra'], timeout=.1)

    def tearDown(self):
        self.bridge.close()

    def test_twelve_tool_calls_keep_one_native_turn_and_current_plan(self):
        payload = request()
        answer = self.bridge.complete(payload)
        rpc = self.instances[0]
        for index in range(1, 13):
            next_call = tool(call_id=f'native-{index + 1}')
            next_call['id'] = 101 + index
            rpc.events.put(next_call if index < 12 else delta('finished'))
            if index == 12:
                rpc.events.put(done())
            payload = follow(payload, answer, studio(index + 1, f'step {index} done'))
            answer = self.bridge.complete(payload)
            self.assertEqual(len(self.instances), 1, 'A tool result plus studio tail is not a new user turn')
            self.assertEqual(len(rpc.replies), index)
            self.assertIn(f'step {index} done', '\n'.join(p.get('text', '') for p in rpc.replies[-1][1]))
        self.assertEqual(len(rpc.started), 1)
        self.assertEqual(answer['choices'][0]['message']['content'], 'finished')
        self.assertTrue(rpc.closed)

    def test_images_and_restored_skills_follow_existing_native_call(self):
        payload = request()
        answer = self.bridge.complete(payload)
        rpc = self.instances[0]
        restored = ('[系统恢复：以下技能之前已加载，因对话被压缩，原文补回。继续按它的步骤、数量和画幅限制执行；用户换了话题就不再适用。]\n\n'
                    '<skill versionId="v1" contentHash="abc">Use the reference.</skill>')
        rpc.events.put(delta('viewed')); rpc.events.put(done())
        value = follow(payload, answer, studio(2, 'viewing', restored), image=True)
        self.assertEqual(self.bridge.complete(value)['choices'][0]['message']['content'], 'viewed')
        self.assertEqual(len(self.instances), 1)
        self.assertIn({'type': 'inputImage', 'imageUrl': 'data:image/png;base64,AA=='}, rpc.replies[0][1])
        self.assertIn(restored, '\n'.join(p.get('text', '') for p in rpc.replies[0][1]))

    def test_initial_user_image_remains_real_image_input(self):
        image = 'data:image/png;base64,BB=='
        self.bridge.complete(request([{'type': 'text', 'text': '  Original\nuser'},
                                      {'type': 'image_url', 'image_url': {'url': image}}]))
        inputs = self.instances[0].started[0]
        self.assertIn({'type': 'image', 'url': image}, inputs)
        self.assertIn({'type': 'text', 'text': '  Original\nuser', 'text_elements': []}, inputs)

    def test_changed_user_or_system_cannot_resume_tool(self):
        payload = request()
        answer = self.bridge.complete(payload)
        for index in (0, 1):
            with self.subTest(index=index):
                value = follow(payload, answer)
                value['messages'][index]['content'] = 'changed identity'
                with self.assertRaises(BridgeError) as caught:
                    self.bridge.complete(value)
                self.assertEqual(caught.exception.code, 'tool_context_mismatch')
        self.assertEqual(len(self.instances), 1)
        self.assertEqual(self.instances[0].replies, [])

    def test_expired_call_with_tail_never_starts_new_inference(self):
        payload = request()
        answer = {'choices': [{'message': {'role': 'assistant', 'content': None, 'tool_calls': [
            {'id': 'call_btv_expired', 'type': 'function', 'function': {'name': 'canvas_get', 'arguments': '{}'}}]}}]}
        with self.assertRaises(BridgeError) as caught:
            self.bridge.complete(follow(payload, answer))
        self.assertEqual(caught.exception.code, 'tool_session_expired')
        self.assertEqual(self.instances, [])

    def test_identical_retry_is_cached_and_changed_result_rejected(self):
        payload = request()
        answer = self.bridge.complete(payload)
        rpc = self.instances[0]
        second = tool(call_id='second'); second['id'] = 102; rpc.events.put(second)
        value = follow(payload, answer, studio(2))
        second_answer = self.bridge.complete(value)
        self.assertEqual(self.bridge.complete(value), second_answer)
        changed = follow(payload, answer, studio(2), result='changed result')
        with self.assertRaises(BridgeError) as caught:
            self.bridge.complete(changed)
        self.assertEqual(caught.exception.code, 'tool_result_conflict')
        self.assertEqual(len(rpc.replies), 1)
        self.assertEqual(len(self.instances), 1)

    def test_two_interleaved_conversations_keep_separate_native_results(self):
        first, second = request('first canvas'), request('second canvas')
        a, b = self.bridge.complete(first), self.bridge.complete(second)
        first_rpc, second_rpc = self.instances
        for rpc, text in ((first_rpc, 'first done'), (second_rpc, 'second done')):
            rpc.events.put(delta(text)); rpc.events.put(done())
        self.assertEqual(self.bridge.complete(follow(second, b, studio(2)))['choices'][0]['message']['content'], 'second done')
        self.assertEqual(first_rpc.replies, [])
        self.assertEqual(self.bridge.complete(follow(first, a, studio(3)))['choices'][0]['message']['content'], 'first done')
        self.assertEqual(len(self.instances), 2)
        self.assertEqual([len(r.replies) for r in self.instances], [1, 1])

    def test_admission_failure_does_not_poison_idempotency_key(self):
        payload = request()
        self.bridge.max_sessions = 0
        with self.assertRaises(BridgeError) as caught:
            self.bridge.complete(payload, 'same-request')
        self.assertEqual(caught.exception.code, 'busy')
        self.assertEqual(self.instances, [])
        self.bridge.max_sessions = 8
        answer = self.bridge.complete(payload, 'same-request')
        self.assertEqual(answer['choices'][0]['finish_reason'], 'tool_calls')
        self.assertEqual(len(self.instances), 1)

    def test_busy_does_not_evict_a_pending_tool(self):
        self.bridge.max_sessions = 1
        payload = request()
        answer = self.bridge.complete(payload)
        rpc = self.instances[0]
        with self.assertRaises(BridgeError) as caught:
            self.bridge.complete(request('different user'))
        self.assertEqual(caught.exception.code, 'busy')
        self.assertFalse(rpc.closed)
        rpc.events.put(delta('continued')); rpc.events.put(done())
        result = self.bridge.complete(follow(payload, answer))
        self.assertEqual(result['choices'][0]['message']['content'], 'continued')
        self.assertEqual(len(self.instances), 1)

    def test_ordinary_user_text_resembling_context_stays_user_input(self):
        text = '<beeftv_studio>\nSome user text\n</beeftv_studio>\nActually change the plan'
        payload = request()
        payload['messages'][-1] = {'role': 'user', 'content': text}
        self.bridge.complete(payload)
        self.assertEqual(self.instances[0].started[0][-1]['text'], text)


if __name__ == '__main__':
    unittest.main()
