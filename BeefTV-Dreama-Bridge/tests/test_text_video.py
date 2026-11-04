"""Regression: canvas operation inference must not disable supported Dreama inputs."""
import tempfile
import unittest
from unittest.mock import patch
from bridge import Bridge, BridgeError, DreamaGateway, validate_request
from install_channel import channel
from tests.test_bridge import FakeGateway, request

IMAGE_REQUIRED = '305d1705-aa58-4ab2-aa56-5aa033a84a60'

class TextVideoTests(unittest.TestCase):
    def test_canvas_profiles_allow_text_and_single_image_where_supported(self):
        profiles=channel('test-secret')['modelProfiles']
        self.assertEqual(len(profiles),4)
        for profile in profiles:
            with self.subTest(model=profile['model']):
                v=profile['capabilityConfig']['video']
                self.assertIn('image_to_video',v['operations'])
                self.assertIn('reference_to_video',v['operations'])
                self.assertEqual(v['operationReferences']['image_to_video']['minImages'],1)
                if profile['model']==IMAGE_REQUIRED:
                    self.assertNotIn('text_to_video',v['operations'])
                    self.assertEqual(v['references']['minImages'],1)
                else:
                    self.assertIn('text_to_video',v['operations'])
                    self.assertEqual(v['references']['minImages'],0)
                    self.assertEqual(v['operationReferences']['text_to_video']['maxImages'],0)

    def test_text_requests_omit_images_or_use_empty_list_for_all_21_models(self):
        profiles=channel('test-secret')['modelProfiles']
        for profile in profiles:
            data=request(); data['model']=profile['model']; data['prompt']='海面泛起微波。'
            data['resolution']=profile['capabilityConfig']['video']['defaultResolution']
            for omitted in (False,True):
                with self.subTest(model=profile['model'],omitted=omitted):
                    if omitted: data.pop('images',None)
                    else: data['images']=[]
                    if profile['model']==IMAGE_REQUIRED:
                        with self.assertRaises(BridgeError): validate_request(data)
                    else:
                        try: actual=validate_request(data)
                        except BridgeError as error: self.fail(str(error))
                        self.assertEqual(actual,(data['prompt'],5,[]))

    def test_text_prepare_persists_quote_without_upload_or_submit(self):
        data=request(); data.pop('images'); data['prompt']='海面泛起微波。'
        gateway=FakeGateway()
        with tempfile.TemporaryDirectory() as root:
            try: job=Bridge(root,gateway).prepare(data,'text-only')
            except BridgeError as error: self.fail(str(error))
            self.assertEqual(job['status'],'prepared')
            self.assertEqual(job['references'],[])
            self.assertEqual(job['pointsEstimate'],100)
            self.assertFalse(job['generationSubmitted'])
            self.assertEqual(gateway.calls,['save','estimate'])

    def test_empty_references_do_not_add_fictitious_reference_section(self):
        prompt='海面泛起微波，镜头缓慢推进。'
        self.assertEqual(DreamaGateway.prompt_with_references(prompt,[]),prompt)

    def test_gateway_saves_plain_prompt_without_reference_tags(self):
        prompt='海面 <微波> & 光线\n镜头缓慢推进。'
        saved_html='<p>海面 &lt;微波&gt; &amp; 光线<br>镜头缓慢推进。</p>'
        gateway=DreamaGateway()
        responses=[{}, {'items':[{'id':300}]}, {'items':[{'id':300,'videoPrompt':saved_html}]}]
        def response(*args):
            if args[:2]==('prompt','assemble'):
                raise BridgeError('Dreama 1.0.5 requires --ref for prompt assemble')
            return responses.pop(0)
        with patch.object(gateway,'run',side_effect=response) as cli, patch.object(gateway,'post',return_value={}) as save:
            try: saved=gateway.save_and_readback(prompt,[],5,'text-test')
            except BridgeError as error: self.fail(str(error))
            args=cli.call_args_list[0].args
            self.assertEqual(args[:2],('storyboard','create'))
            self.assertNotIn('--prompt',args)
            self.assertEqual(save.call_args.args[1]['videoPrompt'],saved_html)
            self.assertNotIn('--ref',args)
            self.assertEqual(saved['referenceImageIds'],[])
            self.assertTrue(saved['promptVerified'])
