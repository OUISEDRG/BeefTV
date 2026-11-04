import copy
import unittest

from migrate_voice_protocols import merge, summary


class VoiceProtocolMigrationTests(unittest.TestCase):
    def fixture(self):
        def channel(cid, suffix, model):
            return {'id': cid, 'name': '保留名称', 'baseUrl': 'http://127.0.0.1:18765' + suffix,
                    'apiKey': 'fixture-key', 'enabled': False, 'pinned': True,
                    'interfaceType': 'seedance-videos-compatible', 'models': [model],
                    'modelProfiles': [{'model': model, 'protocol': 'seedance-videos-compatible',
                                       'capability': 'video', 'capabilityConfig': {'duration': 13, 'resolutions': ['720p', '480p']},
                                       'defaultOptions': {'custom': 'keep'}}]}
        return {'schemaVersion': 1, 'revision': 27, 'config': {
            'channels': [channel('dreama-local-preflight', '', 'dreama-jiumeng-2.0-fast'),
                         {'id': 'unrelated', 'apiKey': 'another-secret', 'name': '不改'},
                         channel('dreama-video-extension', '/extension', 'dreama-extension-2.5')],
            'videoModel': 'dreama-local-preflight::dreama-jiumeng-2.0-fast', 'vquality': '720',
            'models': ['unrelated::keep'], 'textModel': 'codex::keep', 'assistantModel': 'codex::keep',
            'unknownPreference': {'retain': True}}}

    def test_only_protocols_change_and_input_untouched(self):
        before = self.fixture()
        original = copy.deepcopy(before)
        after = merge(before, 'fixture-key')
        self.assertEqual(before, original)
        for index in (0, 2):
            old_channel = before['config']['channels'][index]
            new_channel = after['config']['channels'][index]
            self.assertNotEqual(old_channel['interfaceType'], new_channel['interfaceType'])
            new_channel['interfaceType'] = old_channel['interfaceType']
            new_channel['modelProfiles'][0]['protocol'] = old_channel['modelProfiles'][0]['protocol']
        self.assertEqual(after, before)

    def test_idempotent_and_safe_summary(self):
        before = self.fixture()
        after = merge(before, 'fixture-key')
        self.assertEqual(merge(after, 'fixture-key'), after)
        receipt = summary(before, after)
        self.assertEqual(len(receipt['changedChannels']), 2)
        self.assertTrue(receipt['videoDefaultPreserved'])
        self.assertTrue(receipt['resolutionPreserved'])
        self.assertNotIn('fixture-key', str(receipt))
        self.assertNotIn('another-secret', str(receipt))

    def test_foreign_or_unknown_channel_fails_closed(self):
        for mutate in (
            lambda doc: doc['config']['channels'][0].update(baseUrl='https://external.invalid'),
            lambda doc: doc['config']['channels'][0].update(apiKey='foreign'),
            lambda doc: doc['config']['channels'][0].update(interfaceType='unknown'),
            lambda doc: doc['config']['channels'].append(copy.deepcopy(doc['config']['channels'][0])),
        ):
            before = self.fixture()
            mutate(before)
            with self.assertRaises(ValueError):
                merge(before, 'fixture-key')


if __name__ == '__main__':
    unittest.main()
