import copy
import io
import json
import unittest
import zipfile

import install_modalities as install


class ModalitiesInstallTests(unittest.TestCase):
    def specs(self):
        return {'eid-with-unicode‑dash': {
            'id': 'eid-with-unicode‑dash', 'label': '九梦图片', 'resolutions': ['2K', '3K', '4K'],
            'aspectRatios': ['9:16', '1:1', '16:9'], 'defaultResolution': '2K',
            'maxImages': 9, 'maxOutputs': 1, 'promptMaxChars': 5000,
        }}

    def channels(self):
        return install.channels('fake-local-secret', self.specs(),
                                {'t': {'id': 't', 'label': 'Text'}},
                                {'a': {'id': 'a', 'label': 'Audio'}},
                                {'v': {'id': 'v', 'label': 'Voice', 'voiceId': '123'}})

    def document(self):
        return {'schemaVersion': 1, 'revision': 39, 'config': {
            'channels': [{'id': 'keep', 'models': ['prior'], 'apiKey': 'unrelated-secret', 'enabled': False}],
            'models': ['keep::prior'], 'imageModels': ['keep::prior'], 'imageModel': 'keep::prior',
            'textModels': [], 'textModel': '', 'audioModels': [], 'audioModel': '',
            'videoModels': ['video::old'], 'videoModel': 'video::old', 'vquality': '720',
            'assistantModel': 'assistant::selected', 'audioVoice': 'alloy', 'systemPrompt': 'unchanged',
        }}

    def test_preserves_other_channels_defaults_video_and_input(self):
        before = self.document()
        original = copy.deepcopy(before)
        result = install.merge(before, 'fake-local-secret', self.channels())
        self.assertEqual(before, original)
        for key in ('imageModel', 'assistantModel', 'videoModel', 'videoModels', 'vquality', 'systemPrompt'):
            self.assertEqual(result['config'][key], before['config'][key])
        self.assertEqual(result['config']['channels'][0], before['config']['channels'][0])
        self.assertEqual(result['revision'], 39)
        self.assertEqual(result['config']['textModel'], 'dreama-text-generation::t')

    def test_merge_is_idempotent_and_preserves_user_toggles(self):
        doc = install.merge(self.document(), 'fake-local-secret', self.channels())
        doc['config']['channels'][1].update(enabled=False, pinned=True, concurrencyLimit=3)
        result = install.merge(doc, 'fake-local-secret', self.channels())
        self.assertEqual(result, doc)

    def test_collision_refuses_to_replace_other_credentials(self):
        doc = install.merge(self.document(), 'fake-local-secret', self.channels())
        doc['config']['channels'][1]['apiKey'] = 'different'
        with self.assertRaisesRegex(RuntimeError, '不属于'):
            install.merge(doc, 'fake-local-secret', self.channels())

    def test_duplicate_channel_fails_closed(self):
        doc = install.merge(self.document(), 'fake-local-secret', self.channels())
        doc['config']['channels'].append(copy.deepcopy(doc['config']['channels'][1]))
        with self.assertRaisesRegex(RuntimeError, '不唯一'):
            install.merge(doc, 'fake-local-secret', self.channels())

    def test_image_family_keeps_eid_and_native_resolution_choices(self):
        profile = self.channels()[0]['modelProfiles'][0]
        self.assertEqual(profile['model'], 'eid-with-unicode‑dash')
        image = profile['capabilityConfig']['image']
        self.assertEqual(image['quality']['values'], ['2K', '3K', '4K'])
        self.assertEqual(image['size']['parameter'], 'aspect_ratio')
        self.assertEqual(image['size']['values'], ['9:16', '1:1', '16:9'])
        self.assertFalse(image['size']['allowCustom'])
        self.assertFalse(image['references']['maskSupported'])

    def test_text_is_not_advertised_as_streaming_or_agent(self):
        text = self.channels()[1]['modelProfiles'][0]['capabilityConfig']['text']
        self.assertFalse(text['streaming'])
        self.assertFalse(text['thinking'])
        self.assertEqual(text['references']['maxImages'], 0)
        manifest = json.loads((install.ROOT / 'dreama-modalities-manifest.json').read_text('utf-8'))
        provider = next(p for p in manifest['contributes']['providers'] if p['id'] == 'dreama-text-async')
        self.assertNotIn('agent', provider)
        self.assertNotIn('agent', provider['scopes'])
        self.assertEqual(provider['create']['body']['messages'], {'$ref': 'request.messages'})
        self.assertEqual(provider['response']['text'], {'$ref': 'response.text'})

    def test_plugin_package_has_only_manifest_and_durable_contract(self):
        data = install.build_plugin()
        self.assertEqual(data, install.build_plugin())
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.assertEqual(archive.namelist(), ['manifest.json'])
            manifest = json.loads(archive.read('manifest.json'))
        self.assertEqual(manifest['id'], install.PLUGIN_ID)
        self.assertEqual({p['id'] for p in manifest['contributes']['providers']}, {
            'dreama-images-async', 'dreama-text-async', 'dreama-audio-async',
            'dreama-videos-async', 'dreama-extension-async', 'dreama-enhancement-async'})
        self.assertNotIn(b'fake-local-secret', data)
        for provider in manifest['contributes']['providers']:
            self.assertEqual(provider['create']['headers']['Idempotency-Key'], {'$ref': 'request.extra.idempotencyKey'})
            self.assertEqual(provider['poll']['method'], 'GET')
            self.assertIn('{{taskId}}', provider['poll']['path'])
            self.assertEqual(provider['auth'], {'type': 'bearer', 'field': 'apiKey'})

    def test_audio_reference_and_voice_modes_are_distinct(self):
        reference, voices = self.channels()[2:]
        self.assertEqual(reference['modelProfiles'][0]['mediaCapabilities']['audio']['referenceKinds'], ['audio'])
        self.assertEqual(voices['modelProfiles'][0]['mediaCapabilities']['audio']['referenceKinds'], [])
        manifest = json.loads((install.ROOT / 'dreama-modalities-manifest.json').read_text('utf-8'))
        provider = next(p for p in manifest['contributes']['providers'] if p['id'] == 'dreama-audio-async')
        body = provider['create']['body']
        self.assertEqual(body['audios']['$map']['from'], {'$sortByOrder': {'$ref': 'request.audios'}})
        self.assertEqual(body['voice']['$omitEmpty']['$if']['then'], None)
        self.assertEqual(body['voice']['$omitEmpty']['$if']['else'], {'$ref': 'request.extra.audioVoice'})


if __name__ == '__main__':
    unittest.main()
