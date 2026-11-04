import base64
import io
import tempfile
import unittest
from pathlib import Path
from PIL import Image

try:
    from bridge import BridgeError, validate_request, decode_image, Bridge, DreamaGateway
except ImportError:
    BridgeError = validate_request = decode_image = Bridge = DreamaGateway = None


def png():
    out = io.BytesIO()
    Image.new('RGB', (512, 512), '#4682b4').save(out, 'PNG')
    return out.getvalue()


def request():
    return {'model': '7573d384-0128-4424-9e90-8a0291b9274b',
            'prompt': '@图片1 桥接参考图片联通验证，不生成视频。', 'duration': 5,
            'aspect_ratio': '9:16', 'resolution': '720p',
            'images': ['data:image/png;base64,' + base64.b64encode(png()).decode()]}


class FakeGateway:
    def __init__(self):
        self.calls = []
    def upload_image(self, data, name):
        self.calls.append('upload')
        return 'https://jzinnovation.jxbaobian.cn/resourceBucket/image/test.png'
    def create_reference(self, name, url):
        self.calls.append('reference')
        return {'assetId': 100, 'imageId': 200, 'name': name, 'url': url}
    def save_and_readback(self, prompt, refs, duration, name):
        self.calls.append('save')
        return {'storyboardId': 300, 'referenceImageIds': [r['imageId'] for r in refs],
                'promptVerified': True, 'referencesVerified': True}
    def estimate(self, duration, model=None):
        self.calls.append('estimate')
        return 100


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(Bridge, 'Bridge implementation is missing')
    def test_reference_image_decodes_without_altering_bytes(self):
        self.assertEqual(decode_image(request()['images'][0]), png())
    def test_rejects_non_image_and_remote_urls(self):
        for value in ['http://127.0.0.1/secret', 'data:image/png;base64,aGVsbG8=']:
            with self.subTest(value=value), self.assertRaises(BridgeError):
                decode_image(value)
    def test_rejects_unsupported_input_before_upstream_writes(self):
        for field, value in [('duration', 2), ('videos', ['video']), ('aspect_ratio', '16:9'),
                             ('model', 'another-model'), ('images', request()['images'] * 10),
                             ('images', None), ('prompt', '')]:
            data = request(); data[field] = value
            gateway = FakeGateway()
            with tempfile.TemporaryDirectory() as d, self.assertRaises(BridgeError):
                Bridge(Path(d), gateway).prepare(data, 'test-key')
            self.assertEqual(gateway.calls, [])
    def test_prepare_returns_verified_references_without_generation(self):
        with tempfile.TemporaryDirectory() as d:
            gateway = FakeGateway()
            result = Bridge(Path(d), gateway).prepare(request(), 'unique-key')
            self.assertEqual(result['status'], 'prepared')
            self.assertEqual(result['storyboardId'], 300)
            self.assertEqual(result['pointsEstimate'], 100)
            self.assertFalse(result['generationSubmitted'])
            self.assertEqual(gateway.calls, ['upload', 'reference', 'save', 'estimate'])
    def test_idempotent_retry_survives_restart(self):
        with tempfile.TemporaryDirectory() as d:
            gateway = FakeGateway()
            first = Bridge(Path(d), gateway).prepare(request(), 'same-key')
            second = Bridge(Path(d), gateway).prepare(request(), 'same-key')
            self.assertEqual(first, second)
            self.assertEqual(len(gateway.calls), 4)
            changed = request(); changed['prompt'] = '@图片1 different'
            with self.assertRaises(BridgeError):
                Bridge(Path(d), gateway).prepare(changed, 'same-key')
    def test_unknown_write_is_not_retried(self):
        class Uncertain(FakeGateway):
            def create_reference(self, name, url):
                self.calls.append('reference')
                raise TimeoutError('unknown server result')
        with tempfile.TemporaryDirectory() as d:
            gateway = Uncertain()
            with self.assertRaises(BridgeError):
                Bridge(Path(d), gateway).prepare(request(), 'uncertain')
            with self.assertRaises(BridgeError):
                Bridge(Path(d), gateway).prepare(request(), 'uncertain')
            self.assertEqual(gateway.calls, ['upload', 'reference'])
    def test_generation_endpoints_are_not_in_http_allowlist(self):
        self.assertNotIn('/storyboard/api/produceVideo', DreamaGateway.ALLOWED_PATHS)
        self.assertNotIn('/storyboard/task/generateImage', DreamaGateway.ALLOWED_PATHS)

    def test_official_success_with_null_result_is_a_valid_write_acknowledgement(self):
        import json
        from unittest.mock import Mock, patch
        gateway = DreamaGateway()
        with tempfile.TemporaryDirectory() as directory:
            gateway.credentials = Path(directory) / 'fake-credentials.json'
            gateway.credentials.write_text(json.dumps({'accessToken': 'test-only'}))
            for path in ('/storyboard/api/updateProjectLibrary', '/storyboard/api/save'):
                response = Mock(status_code=200)
                response.json.return_value = {'code': 0, 'msg': 'success', 'result': None, 'traceId': 'unit-only'}
                with patch('bridge.requests.post', return_value=response):
                    self.assertEqual(gateway.post(path, {'id': 123}), {})

    def test_object_receipts_remain_available_and_malformed_receipts_are_explicit(self):
        import json
        from unittest.mock import Mock, patch
        gateway = DreamaGateway()
        with tempfile.TemporaryDirectory() as directory:
            gateway.credentials = Path(directory) / 'fake-credentials.json'
            gateway.credentials.write_text(json.dumps({'accessToken': 'test-only'}))
            for result, expected in [({'bucketName': 'test'}, {'bucketName': 'test'}),
                                     ({'item': {'id': 12}}, {'id': 12}), ({'item': None}, {})]:
                response = Mock(status_code=200); response.json.return_value = {'code': 0, 'result': result}
                with patch('bridge.requests.post', return_value=response):
                    self.assertEqual(gateway.post('/user/resource/getOssToken', {}), expected)
            response.json.return_value = {'code': 0, 'result': ['unexpected']}
            with patch('bridge.requests.post', return_value=response), self.assertRaises(BridgeError):
                gateway.post('/user/resource/getOssToken', {})
    def test_prompt_never_adds_reference_description_to_original(self):
        self.assertTrue(hasattr(DreamaGateway, 'prompt_with_references'))
        original = '镜头保持不动。\n角色转身。'
        text = DreamaGateway.prompt_with_references(original, [{'name': 'one'}, {'name': 'two'}])
        self.assertEqual(text,original)
    def test_reference_tags_require_exact_typed_ids_and_order(self):
        from urllib.parse import quote
        import json
        def tag(image_id, material_type=3):
            props=quote(json.dumps({'fileType':1,'materialId':image_id,'materialType':material_type}))
            return '<span data-value="" data-node-type-inline-mention data-draggable="true" data-type="mention" data-extra-props="'+props+'"></span>'
        verify=getattr(DreamaGateway,'verify_reference_tags',None)
        self.assertIsNotNone(verify)
        refs=[{'imageId':21},{'imageId':32}]
        verify(tag(21)+tag(32),refs)
        for text in [tag(321)+tag(32),tag(32)+tag(21),tag(21,1)+tag(32),'plain 21 32',tag(21)]:
            with self.assertRaises(BridgeError): verify(text,refs)
        for text in [tag(21.0),tag(True),tag(21).replace('</span>','bad</span>'),
                     tag(21).replace('data-value=""','data-value="bad"'),
                     tag(21).replace('data-node-type-inline-mention',''),
                     tag(21).replace('</span>',''),tag(21).replace('</span>','<b></b></span>')]:
            with self.subTest(text=text), self.assertRaises(BridgeError): verify(text,[{'imageId':21}])
    def test_nonfinite_duration_rejected_as_validation_error(self):
        for duration in [float('nan'),float('inf')]:
            data=request(); data['duration']=duration
            with self.assertRaises(BridgeError): validate_request(data)
    def test_existing_cdn_image_is_verified_and_reused_without_reupload(self):
        from unittest.mock import patch, Mock
        gateway=DreamaGateway()
        with patch.object(gateway,'post',return_value={'bucketName':'resourceBucket','token':'not-real'}), \
             patch('bridge.requests.get',return_value=Mock(status_code=200,content=png())), \
             patch('bridge.requests.post') as upload:
            self.assertTrue(gateway.upload_image(png(),'test').endswith('.png'))
            upload.assert_not_called()


if __name__ == '__main__':
    unittest.main()
