from tests.fixtures_budget import intent_fields
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import quote

import audio_flow as audio
import extension
import voice_bindings as voices
from bridge import Bridge, BridgeError, DreamaGateway, PROJECT, SERIES, validate_request
from tests.fixtures_budget import FundedBridge as Bridge
from tests.test_bridge import FakeGateway, request

URL = 'https://jzinnovation.jxbaobian.cn/resourceBucket/audio/personal.mp3'
ROW = {'id': '575709', 'name': '个人人物', 'source': 'personal', 'previewUrl': URL,
       'voiceUrl': URL, 'videoUrl': URL, 'duration': 4, 'clipDuration': 4}
BINDING = {'image_index': 1, 'name': '人物', 'voice_id': '575709'}


def tag(file_type, image_id=21, material_type=1):
    props = quote(json.dumps({'fileType': file_type, 'materialId': image_id, 'materialType': material_type}))
    return '<span data-value="" data-node-type="inline-mention" data-extra-props="'+props+'" data-draggable="true" data-type="mention"></span>'


class CatalogTests(unittest.TestCase):
    def test_account_scoped_cache_and_public_private_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            credential = Path(directory) / 'fake.json'
            credential.write_text(json.dumps({'accessToken': 'test-account-one'}))
            gateway = Mock(credentials=credential)
            catalog = voices.VoiceCatalog(gateway)
            calls = []
            def read(token, path, body):
                calls.append((token, path, body))
                if path.endswith('listMine'):
                    return {'items': [{'id': 575709 if token.endswith('one') else 575710,
                        'name': 'private', 'voiceUrl': URL, 'duration': 4}], 'totalCount': 1}
                if body.get('provider') == 'douge':
                    return {'items': []}
                return {'items': [{'id': 1, 'name': 'public', 'voiceUrl': URL}], 'totalCount': 1}
            with patch.object(catalog, '_read', side_effect=read):
                first = catalog.get()
                self.assertEqual(first['575709']['source'], 'personal')
                self.assertEqual(first['1']['source'], 'public')
                self.assertEqual(catalog.get(), first)
                self.assertEqual(len(calls), 3)
                credential.write_text(json.dumps({'accessToken': 'test-account-two'}))
                second = catalog.get()
                self.assertNotIn('575709', second)
                self.assertIn('575710', second)
                self.assertEqual(len(calls), 6)
            with patch.object(catalog, '_read', side_effect=BridgeError('offline')):
                with self.assertRaises(BridgeError):
                    catalog.get(force=True)

    def test_foreign_url_or_invalid_id_is_not_a_catalog_voice(self):
        with tempfile.TemporaryDirectory() as directory:
            credential = Path(directory) / 'fake.json'
            credential.write_text(json.dumps({'accessToken': 'fake'}))
            catalog = voices.VoiceCatalog(Mock(credentials=credential))
            with patch.object(catalog, '_read', return_value={'items': [
                {'id': 1, 'name': 'bad', 'voiceUrl': 'http://127.0.0.1/private'},
                {'id': 'abc', 'voiceUrl': URL}]}):
                self.assertEqual(catalog.get(), {})
                with self.assertRaises(BridgeError): catalog.resolve('1')


class BindingValidationTests(unittest.TestCase):
    def test_canvas_audio_mime_aliases_decode_without_bypassing_media_probe(self):
        for mime in ('audio/webm', 'audio/opus', 'audio/wave', 'audio/vnd.wave', 'audio/x-flac',
                     'audio/x-mpeg', 'audio/x-pn-wav', 'audio/m4a', 'audio/aacp'):
            value = 'data:'+mime+';base64,YQ=='
            self.assertEqual(audio.decode_audio(value), b'a')
            with tempfile.TemporaryDirectory() as directory:
                flow = audio.AudioFlow(Bridge(directory))
                with patch.object(audio, 'probe_audio', side_effect=BridgeError('invalid audio')) as probe, patch.object(flow.gateway, 'upload_audio') as upload:
                    with self.assertRaises(BridgeError): flow._audio_reference(value)
                    probe.assert_called_once()
                    upload.assert_not_called()
        for mime in ('video/webm', 'audio/vndXwave', 'application/octet-stream'):
            with self.assertRaises(BridgeError): audio.decode_audio('data:'+mime+';base64,YQ==')

    def test_catalog_sample_metadata_is_strict_and_bounded(self):
        response = Mock(status_code=200, content=b'{}')
        for duration, size in [('NaN', '2'), (True, '2'), ('4', '1.5'), ('4', -1), ('4', True)]:
            response.json.return_value = {'format': {'duration': duration, 'size': size}}
            with patch('dreama_compat.voice.requests.get', return_value=response), self.assertRaises(BridgeError):
                voices.media_info(URL)
        response.json.return_value = {'format': {'duration': '4.000000', 'size': '352844'}}
        with patch('dreama_compat.voice.requests.get', return_value=response):
            self.assertEqual(voices.media_info(URL), {'duration': 4, 'bytes': 352844})

    def test_slot_is_explicit_unique_and_sources_exclusive(self):
        invalid = [[BINDING, BINDING], [{**BINDING, 'image_index': 0}],
                   [{**BINDING, 'image_index': True}], [{**BINDING, 'voice_id': 575709}],
                   [{**BINDING, 'audio': 'data:audio/mpeg;base64,YQ=='}],
                   [{**BINDING, 'url': URL}], [{**BINDING, 'voice_id': 'https://bad'}]]
        for bindings in invalid:
            with self.subTest(bindings=bindings), self.assertRaises(BridgeError):
                voices.validate_bindings({'character_voices': bindings}, 2)

    def test_request_limits_before_remote_writes(self):
        data = request(); data['character_voices'] = [BINDING]
        validate_request(data)
        data['character_voices'] = [{**BINDING, 'image_index': 2}]
        with self.assertRaises(BridgeError): validate_request(data)
        with patch.object(voices, 'video_limits', return_value={'audioMax': 0}):
            with self.assertRaises(BridgeError):
                voices.validate_video_bindings({'character_voices': [BINDING]}, 1, {})

    def test_duration_constraints_and_unknown_voice_fail_before_upload(self):
        catalog = Mock(); catalog.resolve.return_value = {**ROW, 'duration': 31, 'clipDuration': 31}
        with tempfile.TemporaryDirectory() as directory, patch.object(voices, 'catalog', return_value=catalog), patch.object(voices, 'media_info', return_value={'duration': 31, 'bytes': 100}):
            bridge = Bridge(directory, FakeGateway())
            with self.assertRaises(BridgeError):
                voices.prepare_video_voices([BINDING], {'id': request()['model']}, bridge)
            self.assertEqual(bridge.gateway.calls, [])
        with tempfile.TemporaryDirectory() as directory, patch.object(voices, 'catalog', return_value=catalog), patch.object(voices, 'media_info', return_value={'duration': 4, 'bytes': 16*1024*1024}):
            with self.assertRaises(BridgeError):
                voices.prepare_video_voices([BINDING], {'id': request()['model']}, Bridge(directory, FakeGateway()))


class VoicePromptTests(unittest.TestCase):
    def test_created_character_voice_is_saved_through_official_binding_action(self):
        # The live create endpoint writes the parent voice only. The official
        # voice-selector save propagates it to the child consumed by assemble.
        gateway = DreamaGateway()
        voice = {'voiceId': 575709, 'uploadVoiceUrl': '', 'voiceUrl': URL}
        row = {'assetId': 10, 'name': 'role', 'voiceId': 575709,
               'images': [{'imageId': 21, 'name': 'role', 'resourceUrl': ['https://image']}]}
        child_voice = 0
        def post(path, body):
            nonlocal child_voice
            if path == '/storyboard/api/updateProjectLibrary':
                self.assertEqual(body, {'id': 10, 'name': 'role', 'projectId': 412186,
                                       'voiceId': 575709, 'uploadVoiceUrl': ''})
                child_voice = body['voiceId']
            return {}
        def run(*args):
            if args[:2] == ('library', 'list'):
                return {'items': [row]}
            prompt = args[args.index('--prompt') + 1]
            return {'mode': 'drama', 'failed': [],
                    'prompt': '<p>'+tag(3 if prompt == '参考role音色' and child_voice else 1)+'</p>'}
        with patch.object(gateway, 'post', side_effect=post), patch.object(gateway, 'run', side_effect=run):
            ref = gateway.create_character_reference('role', 'https://image', voice)
            saved, indexes = gateway.render_verbatim_prompt('@图片1 说你好', [ref])
        self.assertEqual(saved, '<p>'+tag(1)+tag(3)+' 说你好</p>')
        self.assertEqual(indexes, [0])

    def test_original_text_and_reference_order_with_audio_metadata(self):
        refs = [{'imageId': 21, 'name': 'role', 'materialType': 1, 'voiceId': 575709},
                {'imageId': 32, 'name': 'scene'}]
        original = '  @图片2 看着 @图片1 说：你好。\n再看 @图片1。 '
        image, sound, plain = tag(1), tag(3), tag(1, 32, 3)
        gateway = DreamaGateway()
        responses = [{'mode': 'drama', 'prompt': '<p>不要复制的说明'+image+plain+'</p>'},
                     {'mode': 'drama', 'prompt': '<p>参考'+sound+'音色</p>'}]
        with patch.object(gateway, 'run', side_effect=responses):
            saved, order = gateway.render_verbatim_prompt(original, refs)
        self.assertEqual(saved, '<p>  '+plain+' 看着 '+image+sound+' 说：你好。<br>再看 '+image+sound+'。 </p>')
        self.assertEqual(order, [1, 0, 0])
        self.assertNotIn('参考', saved)
        self.assertNotIn('不要复制', saved)
        gateway.verify_reference_tags(saved, [refs[i] for i in order])
        with self.assertRaises(BridgeError):
            gateway.verify_reference_tags(saved.replace(sound, ''), [refs[i] for i in order])

    def test_character_create_readback_and_changed_voice_detection(self):
        gateway = DreamaGateway()
        voice = {'voiceId': 575709, 'uploadVoiceUrl': '', 'voiceUrl': URL}
        row = {'assetId': 10, 'name': 'unique', 'voiceId': 575709,
               'images': [{'imageId': 21, 'name': 'unique', 'resourceUrl': ['https://image']} ]}
        with patch.object(gateway, 'post', return_value={}) as post, patch.object(gateway, 'run', return_value={'items': [row]}):
            ref = gateway.create_character_reference('unique', 'https://image', voice)
            self.assertEqual(post.call_args_list[0].args[1]['type'], 1)
            self.assertEqual(post.call_args_list[0].args[1]['voiceId'], 575709)
            self.assertEqual(ref['materialType'], 1)
            self.assertEqual(ref['imageId'], 21)
        row['voiceId'] = 575710
        with patch.object(gateway, 'run', return_value={'items': [row]}), self.assertRaises(BridgeError):
            gateway.read_character_reference(ref)

    def test_prepared_request_is_idempotent_and_unknown_role_write_is_not_retried(self):
        class Gateway(FakeGateway):
            def create_character_reference(self, name, url, voice):
                self.calls.append('character')
                return {'assetId': 10, 'imageId': 21, 'name': name, 'url': url, 'materialType': 1, **voice}
        catalog = Mock(); catalog.resolve.return_value = ROW
        data = request(); data['character_voices'] = [BINDING]
        with tempfile.TemporaryDirectory() as directory, patch.object(voices, 'catalog', return_value=catalog), patch.object(voices, 'media_info', return_value={'duration': 4, 'bytes': 100}):
            gateway = Gateway(); bridge = Bridge(directory, gateway)
            first = bridge.prepare(data, 'voice-once')
            self.assertEqual(first, bridge.prepare(data, 'voice-once'))
            self.assertEqual(gateway.calls.count('character'), 1)
            with patch.object(gateway, 'create_character_reference', side_effect=TimeoutError) as create:
                for _ in range(2):
                    with self.assertRaises(BridgeError): bridge.prepare(data, 'voice-unknown')
                create.assert_called_once()


class AudioBindingTests(unittest.TestCase):
    def setUp(self):
        self.catalog = Mock(); self.catalog.resolve.return_value = ROW
        self.patch = patch.object(voices, 'catalog', return_value=self.catalog)
        self.patch.start(); self.addCleanup(self.patch.stop)
        metadata = patch.object(voices, 'media_info', return_value={'duration': 4, 'bytes': 100})
        metadata.start(); self.addCleanup(metadata.stop)

    def test_single_personal_voice_consumed_by_both_audio_engines(self):
        with tempfile.TemporaryDirectory() as directory:
            bridge = Bridge(directory)
            gateway = audio.AudioGateway(); gateway.identity = lambda: {'userId': 1}
            gateway.verify_project = lambda binding: None
            flow = audio.AudioFlow(bridge, gateway=gateway)
            prompt = '  原文\n不改。 '
            for model in (audio.SEED, audio.CLONE):
                job = flow.prepare({'model': model, 'input': prompt, 'images': [],
                    'character_voices': [BINDING]}, model)
                self.assertEqual(job['prompt'], prompt)
                self.assertEqual(job['characterVoiceId'], '575709')
                payload = audio.input_payload(job)
                if model == audio.SEED:
                    self.assertEqual(payload['references'], [{'audio_url': URL}])
                    self.assertNotIn('image_url', str(payload))
                else:
                    self.assertEqual(payload['audio'], URL)
            self.catalog.resolve.return_value = {**ROW, 'voiceUrl': URL+'?changed'}
            with self.assertRaises(BridgeError): gateway.check_ready(job)

    def test_multiple_speakers_and_competing_sources_are_explicit_errors(self):
        for extra in ({'character_voices': [BINDING, BINDING]}, {'audios': [URL]},
                      {'images': ['not-used-as-image']}, {'voice': '1'},
                      {'model': audio.CLONE+'-'+next(iter(audio.voices()))}):
            with self.subTest(extra=extra), self.assertRaises(BridgeError):
                audio.validate_audio_request({'model': audio.SEED, 'input': '原文',
                    'character_voices': [BINDING], **extra})

    def test_audio_sample_is_not_uploaded_by_validation(self):
        binding = {'image_index': 1, 'name': '人物', 'audio': 'data:audio/mpeg;base64,YQ=='}
        with patch.object(audio.AudioGateway, 'upload_audio') as upload:
            values = audio.validate_audio_request({'model': audio.CLONE, 'input': '原文',
                'character_voices': [binding]})
            self.assertEqual(values[2], [binding['audio']])
            upload.assert_not_called()


class ExtensionVoiceTests(unittest.TestCase):
    def test_draft_and_submit_include_voice_at_corresponding_image_slot(self):
        from tests.test_extension import MODEL, SOURCE
        ref = {'imageId': 21, 'name': 'role', 'url': 'https://image', 'materialType': 1,
            'voiceId': 575709, 'uploadVoiceUrl': '', 'voiceUrl': URL, 'voiceName': '人物', 'voiceDuration': 4}
        job = {'model': MODEL, 'kind': 'extension', 'status': 'submitting', 'approvedAt': 1,
            'approvedPoints': 10, 'pointsEstimate': 10, 'sourceVideoUrl': SOURCE,
            'sourceVideo': {'duration': 4, 'bytes': 100}, 'references': [ref],
            'promptReferenceIndexes': [0, 0], 'duration': 4, 'storyboardId': 100, **intent_fields(10)}
        saved = '<p>'+tag(1)+tag(3)+'</p>'
        text, draft = extension.extension_draft(job, {'videoPrompt': saved})
        assets = json.loads(draft)['workspaces']['extend']['referenceAssets']
        self.assertEqual([asset['fileType'] for asset in assets], [2, 1, 3])
        self.assertEqual(assets[2]['voiceId'], 575709)
        self.assertEqual(assets[2]['materialId'], 21)
        gateway = extension.ExtensionGateway()
        with patch.object(gateway, '_request', return_value={'taskId': 10}) as send:
            gateway.submit_video(job)
            self.assertEqual(send.call_args.args[1]['images'], [SOURCE, 'https://image', URL])
        self.assertEqual(text, saved)


class VoiceHTTPTests(unittest.TestCase):
    def test_catalog_requires_local_bearer_and_does_not_publish_internal_fields(self):
        import threading
        import requests
        from server import make_server
        with tempfile.TemporaryDirectory() as directory:
            server = make_server(Bridge(directory), 'fake-test-only-token', 0, policy={'mode':'manual'})
            server.stop_event.set(); server.poller.join(2); server.auto_worker.join(2)
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            try:
                url = 'http://127.0.0.1:'+str(server.server_port)+'/audio/v1/voices'
                catalog = Mock(); catalog.get.return_value = {'575709': ROW}
                with patch.object(voices, 'catalog', return_value=catalog):
                    self.assertEqual(requests.get(url, timeout=3).status_code, 401)
                    catalog.get.assert_not_called()
                    response = requests.get(url, headers={'Authorization':'Bearer fake-test-only-token'}, timeout=3)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json(), {'voices':[{k:v for k,v in ROW.items() if k in ('id','name','source','previewUrl','duration')}]})
                    self.assertEqual(response.headers.get('Cache-Control'), 'no-store')
            finally:
                server.shutdown(); server.server_close(); thread.join()


if __name__ == '__main__':
    unittest.main()
