"""Independent regression fixtures. No real Codex or BeefTV calls."""
import copy
import unittest

from completion_bridge import BridgeError, build_input
import test_completion_bridge as fixtures
from test_completion_bridge import TOOLS, delta, done, tool


class ReviewCases(unittest.TestCase):
    setUp = fixtures.BridgeTests.setUp
    tearDown = fixtures.BridgeTests.tearDown
    make = fixtures.BridgeTests.make
    request = fixtures.BridgeTests.request
    consume = fixtures.BridgeTests.consume

    def follow_with_image(self, request, response, image='data:image/png;base64,AA=='):
        follow = copy.deepcopy(request)
        call_id = response['choices'][0]['message']['tool_calls'][0]['id']
        follow['messages'] += [response['choices'][0]['message'],
            {'role':'tool','tool_call_id':call_id,'content':'canvas preview'},
            {'role':'user','content':[
                {'type':'text','text':'Attached image(s) from tool result:'},
                {'type':'image_url','image_url':{'url':image}}
            ]}]
        return follow

    def test_native_tool_image_result_keeps_original_rpc(self):
        self.make([tool()])
        request = self.request(tools=TOOLS)
        first = self.consume(request)
        original = self.instances[0]
        # Exact structure produced by installed pi SDK convertMessages when
        # BeefTV tool returns {type:'image', mimeType, data} beside text.
        follow = self.follow_with_image(request, first)
        original.events.put(delta('已查看画布')); original.events.put(done())
        response = self.consume(follow)
        self.assertEqual(len(self.instances), 1, 'tool images must not start a second inference')
        self.assertEqual(response['choices'][0]['message']['content'], '已查看画布')
        self.assertEqual(original.replies[0][0], 101)
        self.assertIn({'type':'inputImage','imageUrl':'data:image/png;base64,AA=='}, original.replies[0][1])

    def test_second_tool_after_image_keeps_context(self):
        self.make([tool()])
        request = self.request(tools=TOOLS)
        first = self.consume(request)
        rpc = self.instances[0]
        second_call = tool(call_id='c2'); second_call['id']=102
        rpc.events.put(second_call)
        follow = self.follow_with_image(request, first)
        second = self.consume(follow)
        third = copy.deepcopy(follow)
        third['messages'] += [second['choices'][0]['message'], {
            'role':'tool','tool_call_id':second['choices'][0]['message']['tool_calls'][0]['id'],
            'content':'read done'}]
        rpc.events.put(delta('全部完成')); rpc.events.put(done())
        response = self.consume(third)
        self.assertEqual(response['choices'][0]['message']['content'], '全部完成')
        self.assertEqual(len(self.instances), 1)
        self.assertEqual([r[0] for r in rpc.replies], [101,102])

    def test_changed_image_result_rejected(self):
        self.make([tool()])
        request = self.request(tools=TOOLS)
        first = self.consume(request)
        rpc = self.instances[0]
        second_call = tool(call_id='c2'); second_call['id']=102
        rpc.events.put(second_call)
        follow = self.follow_with_image(request, first)
        self.consume(follow)
        altered = self.follow_with_image(request, first, 'data:image/png;base64,BBBB')
        with self.assertRaises(BridgeError) as caught: self.consume(altered)
        self.assertEqual(caught.exception.code, 'tool_result_conflict')
        self.assertEqual(len(rpc.replies), 1)

    def test_unknown_call_image_does_not_create_inference(self):
        self.make([])
        request = self.request(tools=TOOLS)
        fake_response={'choices':[{'message':{'role':'assistant','content':None,'tool_calls':[{
            'id':'call_btv_expired','type':'function','function':{'name':'canvas_get','arguments':'{}'}}]}}]}
        follow = self.follow_with_image(request, fake_response)
        with self.assertRaises(BridgeError) as caught: self.consume(follow)
        self.assertEqual(caught.exception.code, 'tool_session_expired')
        self.assertEqual(self.instances, [])

    def test_native_process_exit_fails_immediately(self):
        self.make([{'method':'bridge/process/exited','params':{}}])
        with self.assertRaises(BridgeError) as caught:
            self.consume(self.request())
        self.assertEqual(caught.exception.code, 'codex_disconnected')

    def test_historical_image_bytes_stay_out_of_text_context(self):
        historical_url='data:image/png;base64,' + ('AbCd'*150000)
        current_url='data:image/png;base64,BB=='
        original=[{'role':'user','content':[
            {'type':'text','text':'原先的图片说明 不修改'},
            {'type':'image_url','image_url':{'url':historical_url}}]},
            {'role':'assistant','content':'之前回复'},
            {'role':'user','content':[
                {'type':'text','text':'\n  现在的用户原文\n'},
                {'type':'image_url','image_url':{'url':current_url}}]}]
        snapshot=copy.deepcopy(original)
        base, developer, inputs=build_input(original)
        self.assertLess(len(developer), 2000)
        self.assertNotIn(historical_url, developer)
        self.assertNotIn('AbCdAbCd', developer)
        self.assertIn('原先的图片说明 不修改', developer)
        self.assertEqual([i['url'] for i in inputs if i['type']=='image'], [historical_url,current_url])
        self.assertEqual(inputs[-2]['text'], '\n  现在的用户原文\n')
        self.assertEqual(original, snapshot, 'building history must not mutate the native request')

    def test_historical_tool_images_reach_next_user_turn_as_images(self):
        image='data:image/png;base64,CC=='
        request=self.request()
        request['messages'] += [
            {'role':'assistant','content':None,'tool_calls':[{'id':'old','type':'function','function':{'name':'canvas_get','arguments':'{}'}}]},
            {'role':'tool','tool_call_id':'old','content':'preview'},
            {'role':'user','content':[{'type':'text','text':'Attached image(s) from tool result:'},
                                    {'type':'image_url','image_url':{'url':image}}]},
            {'role':'assistant','content':'看到了'},
            {'role':'user','content':'继续处理'}]
        _, developer, inputs=build_input(request['messages'])
        self.assertNotIn(image, developer)
        self.assertEqual([i for i in inputs if i['type']=='image'], [{'type':'image','url':image}])
        self.assertEqual(inputs[-1]['text'], '继续处理')


if __name__ == '__main__': unittest.main()
