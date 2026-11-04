import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import subprocess
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import install_codex as installer
from verify_codex_readonly import verify_snapshot


def native_catalog():
    ids = ['gpt-6.1-sol', 'gpt-6-astra', 'gpt-6.1-terra', 'gpt-6-sol',
           'gpt-6-luna', 'gpt-5.3-codex', 'gpt-5.2-codex', 'gpt-5.1-codex-mini']
    return {'models': [{'id': model, 'model': model, 'displayName': 'Native ' + model,
                        'isDefault': index == 0, 'supportedReasoningEfforts': [{'reasoningEffort': 'high'}]}
                       for index, model in enumerate(ids)]}


def fixture():
    return {'schemaVersion': 1, 'revision': 42, 'assistantDefaultAuthorization': 'preserved', 'config': {
        'channels': [{'id': 'dreama-video', 'apiKey': 'keep-secret', 'models': ['video-1'], 'enabled': True},
                     {'id': 'dreama-image', 'apiKey': 'keep-image-secret', 'modelProfiles': [{'custom': True}]}],
        'models': ['dreama-video::video-1'], 'textModels': ['old::text'],
        'textModel': '', 'assistantModel': 'beefapi::gpt-6-astra',
        'videoModel': 'dreama-video::video-1', 'audioModels': ['dreama-audio::audio-1'],
        'imageModel': 'dreama-image::image-1', 'unknownFutureSetting': {'nested': [1, 2, 3]},
    }}


class ConfigTests(unittest.TestCase):
    def test_preserves_every_unrelated_setting_and_input(self):
        before = fixture()
        original = copy.deepcopy(before)
        after = installer.merge(before, 'test-local-token')
        self.assertEqual(before, original)
        self.assertEqual(after['revision'], 42)
        self.assertEqual(after['assistantDefaultAuthorization'], before['assistantDefaultAuthorization'])
        self.assertEqual(after['config']['channels'][:-1], before['config']['channels'])
        self.assertEqual(after['config']['unknownFutureSetting'], before['config']['unknownFutureSetting'])
        self.assertEqual(after['config']['textModel'], 'codex-local::gpt-6-astra')
        self.assertEqual(after['config']['assistantModel'], 'codex-local::gpt-6-astra')
        self.assertTrue(verify_snapshot({'config': after['config'], 'revision': 42})['configured'])

    def test_idempotence_and_same_bridge_model_update(self):
        once = installer.merge(fixture(), 'test-local-token')
        self.assertEqual(installer.merge(once, 'test-local-token'), once)
        new = installer.merge(once, 'test-local-token', model='gpt-6.1-sol')
        self.assertEqual(new['config']['textModels'], ['old::text', 'codex-local::gpt-6.1-sol'])
        self.assertEqual(len(new['config']['channels']), 3)

    def test_rejects_channel_collision(self):
        once = installer.merge(fixture(), 'foreign-token')
        with self.assertRaises(ValueError):
            installer.merge(once, 'our-token')
        once['config']['channels'][-1]['apiKey'] = 'our-token'
        once['config']['channels'][-1]['baseUrl'] = 'http://remote.invalid'
        with self.assertRaises(ValueError):
            installer.merge(once, 'our-token')

    def test_rejects_duplicate_channel(self):
        once = installer.merge(fixture(), 'test-local-token')
        once['config']['channels'].append(copy.deepcopy(once['config']['channels'][-1]))
        with self.assertRaises(ValueError):
            installer.merge(once, 'test-local-token')

    def test_encryption_and_wrong_key(self):
        key = os.urandom(32)
        data = installer.encode(fixture(), key)
        self.assertNotIn(b'keep-secret', data)
        self.assertEqual(installer.decode(data, key), fixture())
        with self.assertRaises(Exception):
            installer.decode(data, os.urandom(32))

    def test_readonly_verifier_accepts_redacted_secret(self):
        data = installer.merge(fixture(), 'local-test-token')['config']
        del data['channels'][-1]['apiKey']
        data['channels'][-1]['hasApiKey'] = True
        result = verify_snapshot({'config': data, 'revision': 43})
        self.assertEqual(result['generationRequests'], 0)
        self.assertNotIn('keep-secret', json.dumps(result))
        data['assistantModel'] = 'beefapi::gpt-6-astra'
        with self.assertRaises(RuntimeError):
            verify_snapshot({'config': data})

    def test_full_catalog_names_and_existing_astra_selection_preserved(self):
        once = installer.merge(fixture(), 'test-local-token')
        catalog = native_catalog()
        after = installer.merge(once, 'test-local-token', catalog=catalog)
        channel = after['config']['channels'][-1]
        self.assertEqual(channel['models'], [row['id'] for row in catalog['models']])
        self.assertEqual(channel['modelProfiles'][0]['displayName'], '本机 Codex · Native gpt-6.1-sol')
        self.assertEqual(after['config']['textModel'], 'codex-local::gpt-6-astra')
        self.assertEqual(after['config']['assistantModel'], 'codex-local::gpt-6-astra')
        self.assertEqual(after['config']['channels'][:2], fixture()['config']['channels'])
        result = verify_snapshot({'config': after['config'], 'revision': 43}, catalog=catalog)
        self.assertEqual(result['modelCount'], 8)
        self.assertEqual(installer.merge(after, 'test-local-token', catalog=catalog), after)

    def test_catalog_preserves_distinct_valid_text_and_assistant_choices(self):
        catalog = native_catalog()
        once = installer.merge(fixture(), 'test-local-token', catalog=catalog)
        once['config']['textModel'] = 'codex-local::gpt-6-luna'
        once['config']['assistantModel'] = 'codex-local::gpt-6.1-terra'
        after = installer.merge(once, 'test-local-token', catalog=catalog)
        self.assertEqual(after, once)
        verify_snapshot({'config': after['config']}, catalog=catalog)

    def test_preserves_other_valid_channel_defaults(self):
        before = fixture()
        before['config']['channels'].append({'id': 'other', 'enabled': True,
            'models': ['text-model'], 'modelProfiles': [{'model': 'text-model',
             'capability': 'text', 'protocol': 'chat-completion'}]})
        before['config']['textModel'] = before['config']['assistantModel'] = 'other::text-model'
        after = installer.merge(before, 'test-local-token', catalog=native_catalog())
        self.assertEqual(after['config']['textModel'], 'other::text-model')
        self.assertEqual(after['config']['assistantModel'], 'other::text-model')
        self.assertEqual(after['config']['channels'][:-1], before['config']['channels'])
        verify_snapshot({'config': after['config']}, catalog=native_catalog())

    def test_new_or_invalid_selection_uses_native_catalog_default(self):
        catalog = native_catalog()
        after = installer.merge(fixture(), 'test-local-token', catalog=catalog)
        self.assertEqual(after['config']['textModel'], 'codex-local::gpt-6.1-sol')
        self.assertEqual(after['config']['assistantModel'], 'codex-local::gpt-6.1-sol')
        catalog['defaultModel'] = 'gpt-6-luna'
        after = installer.merge(fixture(), 'test-local-token', catalog=catalog)
        self.assertEqual(after['config']['assistantModel'], 'codex-local::gpt-6-luna')

    def test_removed_local_model_does_not_leave_stale_menu_or_selection(self):
        once = installer.merge(fixture(), 'test-local-token', model='retired-model')
        once['config']['models'].append('other::keep')
        after = installer.merge(once, 'test-local-token', catalog=native_catalog())
        self.assertNotIn('codex-local::retired-model', after['config']['models'])
        self.assertIn('other::keep', after['config']['models'])
        self.assertEqual(after['config']['assistantModel'], 'codex-local::gpt-6.1-sol')

    def test_invalid_catalogs_rejected(self):
        for bad in ({'models': []}, {'models': ['not-object']}, {'models': [{'id': 'x'}, {'id': 'x'}]},
                    {'models': [{'id': 'bad::model'}]}, {'models': [{'id': 'x'}], 'defaultModel': 'missing'},
                    {'models': [{'id': 'x', 'isDefault': True}, {'id': 'y', 'isDefault': True}]}):
            with self.subTest(catalog=bad), self.assertRaises(ValueError):
                installer.build_channel('local-token', catalog=bad)
        self.assertEqual(installer.catalog_models(native_catalog()['models']), installer.catalog_models(native_catalog()))

    def test_verifier_rejects_partial_catalog_and_fake_extra_model(self):
        catalog = native_catalog()
        before = installer.merge(fixture(), 'test-local-token', catalog=catalog)['config']
        partial = copy.deepcopy(before)
        partial['channels'][-1]['models'].pop()
        with self.assertRaises(RuntimeError):
            verify_snapshot({'config': partial}, catalog=catalog)
        extra = copy.deepcopy(before)
        extra['textModels'].append('codex-local::gpt-6-astra-high')
        with self.assertRaises(RuntimeError):
            verify_snapshot({'config': extra}, catalog=catalog)
        renamed = copy.deepcopy(before)
        renamed['channels'][-1]['modelProfiles'][0]['displayName'] = 'Wrong display name'
        with self.assertRaises(RuntimeError):
            verify_snapshot({'config': renamed}, catalog=catalog)

    def test_verifier_accepts_normalized_profile_order_without_mutation(self):
        catalog = native_catalog()
        config = installer.merge(fixture(), 'test-local-token', catalog=catalog)['config']
        config['channels'][-1]['modelProfiles'].sort(key=lambda profile: profile['model'])
        before = copy.deepcopy(config)
        result = verify_snapshot({'config': config}, catalog=catalog)
        self.assertEqual(result['modelCount'], 8)
        self.assertEqual(config, before)

    def test_verifier_rejects_duplicate_missing_or_extra_profiles(self):
        catalog = native_catalog()
        original = installer.merge(fixture(), 'test-local-token', catalog=catalog)['config']
        for change in ('duplicate', 'missing', 'extra'):
            with self.subTest(change=change):
                config = copy.deepcopy(original)
                profiles = config['channels'][-1]['modelProfiles']
                if change == 'duplicate':
                    profiles.append(copy.deepcopy(profiles[0]))
                elif change == 'missing':
                    profiles.pop()
                else:
                    extra = copy.deepcopy(profiles[0])
                    extra['model'] = 'not-in-catalog'
                    profiles.append(extra)
                with self.assertRaises(RuntimeError):
                    verify_snapshot({'config': config}, catalog=catalog)

    def test_verifier_checks_each_reordered_profile_field_strictly(self):
        catalog = native_catalog()
        original = installer.merge(fixture(), 'test-local-token', catalog=catalog)['config']
        original['channels'][-1]['modelProfiles'].sort(key=lambda profile: profile['model'])
        for field, invalid in (('capability', 'image'), ('protocol', 'responses'), ('displayName', 'Wrong display name')):
            with self.subTest(field=field):
                config = copy.deepcopy(original)
                config['channels'][-1]['modelProfiles'][0][field] = invalid
                with self.assertRaises(RuntimeError):
                    verify_snapshot({'config': config}, catalog=catalog)

    def test_catalog_cli_preview_and_legacy_model_entry(self):
        with tempfile.TemporaryDirectory() as path:
            catalog_file = Path(path) / 'model-catalog.json'
            catalog_file.write_text(json.dumps(native_catalog()), 'utf-8')
            script = Path(installer.__file__)
            first = subprocess.run([sys.executable, '-X', 'utf8', str(script), '--catalog', str(catalog_file)],
                                   capture_output=True, text=True, encoding='utf-8', check=True)
            self.assertEqual(len(json.loads(first.stdout)['channel']['models']), 8)
            automatic = subprocess.run([sys.executable, '-X', 'utf8', str(script), '--bridge-root', path],
                                       capture_output=True, text=True, encoding='utf-8', check=True)
            self.assertEqual(json.loads(automatic.stdout)['channel']['models'], json.loads(first.stdout)['channel']['models'])
            legacy = subprocess.run([sys.executable, '-X', 'utf8', str(script), '--bridge-root', path, '--model', 'legacy-model'],
                                    capture_output=True, text=True, encoding='utf-8', check=True)
            self.assertEqual(json.loads(legacy.stdout)['channel']['models'], ['legacy-model'])

    def setup_disk(self, root):
        data, bridge = root / 'data', root / 'bridge'
        data.mkdir(); (bridge / 'state').mkdir(parents=True)
        (bridge / 'state/local-api-key').write_text('local-test-token', 'utf-8')
        key = os.urandom(32)
        original = installer.encode(fixture(), key)
        (data / '.settings-key').write_bytes(key)
        (data / 'local-model-config.json').write_bytes(original)
        return data, bridge, key, original

    def test_atomic_install_backup_and_idempotence(self):
        with tempfile.TemporaryDirectory() as path, patch.object(installer, 'assert_beeftv_closed'):
            data, bridge, key, original = self.setup_disk(Path(path))
            result = installer.install(data, bridge)
            self.assertEqual(result['revision'], 43)
            self.assertEqual(Path(result['backup']).read_bytes(), original)
            actual = installer.decode((data / 'local-model-config.json').read_bytes(), key)
            self.assertEqual(actual['config']['channels'][:2], fixture()['config']['channels'])
            self.assertEqual(actual['assistantDefaultAuthorization'], 'preserved')
            again = installer.install(data, bridge)
            self.assertFalse(again['changed'])
            self.assertEqual(len(list((bridge / 'backups').glob('*'))), 1)

    def test_running_app_does_not_touch_disk(self):
        with tempfile.TemporaryDirectory() as path:
            data, bridge, key, original = self.setup_disk(Path(path))
            with patch.object(installer, 'assert_beeftv_closed', side_effect=RuntimeError('running')):
                with self.assertRaises(RuntimeError):
                    installer.install(data, bridge)
            self.assertEqual((data / 'local-model-config.json').read_bytes(), original)
            self.assertFalse((bridge / 'backups').exists())

    def test_detects_concurrent_writer_without_overwrite(self):
        with tempfile.TemporaryDirectory() as path:
            data, bridge, key, original = self.setup_disk(Path(path))
            calls = []
            altered = installer.encode({**fixture(), 'revision': 99}, key)
            def check():
                calls.append(1)
                if len(calls) == 2:
                    (data / 'local-model-config.json').write_bytes(altered)
            with patch.object(installer, 'assert_beeftv_closed', side_effect=check):
                with self.assertRaisesRegex(RuntimeError, '其他进程'):
                    installer.install(data, bridge)
            self.assertEqual((data / 'local-model-config.json').read_bytes(), altered)
            self.assertFalse(list(data.glob('.codex-model-config-*')))


if __name__ == '__main__':
    unittest.main()
