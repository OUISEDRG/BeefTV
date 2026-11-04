import hashlib
import json
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import quote
from bridge import Bridge, BridgeError, DreamaGateway, PROJECT, SERIES
from tests.test_bridge import FakeGateway, request

def official_tag(image_id):
    props=quote(json.dumps({'fileType':1,'materialId':image_id,'materialType':3}))
    return '<span data-value="" data-node-type-inline-mention data-draggable="true" data-type="mention" data-extra-props="'+props+'"></span>'

class VerbatimPromptTests(unittest.TestCase):
    def test_long_repeated_references_are_saved_without_windows_command_line_overflow(self):
        # A 5K user prompt expands beyond 32K after official image/voice tags.
        # The full text must be saved via HTTP, while CLI arguments stay small.
        original = ('@图片1 原文和换行都保留。\n' * 180) + '结束 & <原文>'
        tag = official_tag(21)
        expected = '<p>' + (tag+' 原文和换行都保留。<br>') * 180 + '结束 &amp; &lt;原文&gt;</p>'
        self.assertGreater(len(expected), 32767)
        saved = ''
        gateway = DreamaGateway()
        def cli(*args):
            if sum(len(str(a)) for a in args) > 32767:
                raise OSError('Windows command line too long')
            if args[:2] == ('prompt', 'assemble'):
                return {'prompt': '<p>'+tag+'</p>', 'mode': 'drama'}
            if args[:2] == ('storyboard', 'create'):
                self.assertNotIn('--prompt', args)
                return {}
            if args[:2] == ('storyboard', 'list'):
                return {'items': [{'id': 300, 'videoPrompt': saved}]}
            self.fail('unexpected CLI write: '+str(args[:2]))
        def post(path, body):
            nonlocal saved
            self.assertEqual(path, '/storyboard/api/save')
            self.assertEqual(body, {'id': 300, 'seriesId': SERIES, 'videoPrompt': expected,
                                    'duration': 30, 'produceType': 5})
            saved = body['videoPrompt']
            return {}
        with patch.object(gateway, 'run', side_effect=cli), patch.object(gateway, 'post', side_effect=post):
            result = gateway.save_and_readback(original, [{'imageId': 21, 'name': 'unique'}], 30, 'long')
        self.assertEqual(saved, expected)
        self.assertEqual(result['savedPromptHash'], hashlib.sha256(expected.encode()).hexdigest())
        self.assertEqual(result['promptReferenceIndexes'], [0] * 180)

    def test_saved_prompt_preserves_text_and_inline_reference_position(self):
        original='  @图片1 做一个4s的自我介绍,对着屏幕说你好\n保留 <符号> & 标点！  '
        tag=official_tag(21)
        expected='<p>  '+tag+' 做一个4s的自我介绍,对着屏幕说你好<br>保留 &lt;符号&gt; &amp; 标点！  </p>'
        refs=[{'imageId':21,'name':'unique_reference'}]
        # The official assembler may add instructions. Only its reference tag
        # may enter the saved user prompt; its prose must never be forwarded.
        responses=[{'prompt':'<p>禁止字幕。参考图说明：'+tag+'</p>','mode':'drama'},
                   {},{'items':[{'id':300}]},{'items':[{'id':300,'videoPrompt':expected}]}]
        gateway=DreamaGateway()
        with patch.object(gateway,'run',side_effect=responses) as cli, patch.object(gateway,'post',return_value={}) as post:
            try: result=gateway.save_and_readback(original,refs,4,'verbatim')
            except BridgeError as error: self.fail(str(error))
            create=cli.call_args_list[1].args
            self.assertNotIn('--prompt',create)
            self.assertEqual(post.call_args.args[1]['videoPrompt'],expected)
            self.assertEqual(result['promptReferenceIndexes'],[0])
            self.assertEqual(result['promptPolicy'],'verbatim-v1')

    def test_reference_order_and_repetitions_follow_user_text(self):
        original='@图片2 看向 @图片1，然后 @图片2 转身。'
        refs=[{'imageId':21,'name':'first'},{'imageId':32,'name':'second'}]
        a,b=official_tag(21),official_tag(32)
        expected='<p>'+b+' 看向 '+a+'，然后 '+b+' 转身。</p>'
        responses=[{'prompt':'<p>'+a+b+'</p>','mode':'drama'}, {},{'items':[{'id':300}]},
                   {'items':[{'id':300,'videoPrompt':expected}]}]
        gateway=DreamaGateway()
        with patch.object(gateway,'run',side_effect=responses), patch.object(gateway,'post',return_value={}):
            try: result=gateway.save_and_readback(original,refs,4,'ordered')
            except BridgeError as error: self.fail(str(error))
            self.assertEqual(result['promptReferenceIndexes'],[1,0,1])

    def test_unmentioned_or_unknown_images_are_not_silently_added(self):
        for prompt in ('没有图片标记的原文','@图片2 不存在','@图片0 错误序号'):
            data=request(); data['prompt']=prompt
            gateway=FakeGateway()
            with tempfile.TemporaryDirectory() as root:
                with self.assertRaises(BridgeError): Bridge(root,gateway).prepare(data,'invalid-ref')
            self.assertEqual(gateway.calls,[])

    def test_readback_uses_original_mention_order_for_new_jobs(self):
        saved='<p>'+official_tag(32)+' '+official_tag(21)+' '+official_tag(32)+'</p>'
        job={'storyboardId':300,'references':[{'imageId':21},{'imageId':32}],
             'promptReferenceIndexes':[1,0,1],'promptPolicy':'verbatim-v1',
             'savedPromptHash':hashlib.sha256(saved.encode()).hexdigest()}
        board={'id':300,'seriesId':SERIES,'projectId':PROJECT,'videoPrompt':saved,'videoTaskId':0}
        gateway=DreamaGateway()
        with patch.object(gateway,'run',return_value={'items':[board]}):
            try: self.assertEqual(gateway.inspect_board(job),board)
            except BridgeError as error: self.fail(str(error))
