import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import install_images as installer


def fixture():
    return {'schemaVersion': 1, 'revision': 42, 'futureEnvelope': {'keep': True}, 'config': {
        'channels': [
            {'id': 'codex-local', 'apiKey': 'text-channel-secret', 'enabled': True,
             'models': ['gpt-6-astra'], 'modelProfiles': [{'model': 'gpt-6-astra', 'capability': 'text'}]},
            {'id': 'old-image', 'apiKey': 'image-channel-secret', 'models': ['existing'],
             'modelProfiles': [{'custom': True}], 'unknown': [1, 2]},
        ],
        'models': ['codex-local::gpt-6-astra', 'old-image::existing'],
        'textModels': ['codex-local::gpt-6-astra'],
        'imageModels': ['old-image::existing'], 'imageModel': 'old-image::existing',
        'textModel': 'codex-local::gpt-6-astra', 'assistantModel': 'codex-local::gpt-6-astra',
        'model': 'old-image::existing', 'videoModel': 'keep-video::model',
        'assistantDefaultAuthorization': 'full', 'futureSetting': {'nested': [1, 2, 3]},
    }}


def archive_with_manifest(manifest):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        archive.writestr('manifest.json', json.dumps(manifest))
    return output.getvalue()


class MergeTests(unittest.TestCase):
    def test_preserves_text_assistant_other_channels_and_defaults(self):
        before = fixture()
        original = copy.deepcopy(before)
        after = installer.merge(before, 'test-image-token')
        self.assertEqual(before, original)
        expected = copy.deepcopy(after)
        expected['config']['channels'].pop()
        expected['config']['models'].pop()
        expected['config']['imageModels'].pop()
        self.assertEqual(expected, original)
        self.assertEqual(after['config']['channels'][-1]['interfaceType'], 'codex-image')
        profile = after['config']['channels'][-1]['modelProfiles'][0]
        self.assertEqual(profile['referenceMediaTransport'], {'image': 'inline'})
        image = profile['capabilityConfig']['image']
        self.assertEqual(image['references']['maxImages'], 4)
        self.assertEqual(image['references']['maxImageBytes'], 8 * 1024 * 1024)
        self.assertEqual(image['fixedOutputs'], 1)
        self.assertFalse(image['references']['maskSupported'])

    def test_make_default_changes_only_image_selection(self):
        normal = installer.merge(fixture(), 'test-image-token')
        default = installer.merge(fixture(), 'test-image-token', make_default=True)
        self.assertEqual(default['config']['imageModel'], installer.MODEL_KEY)
        default['config']['imageModel'] = normal['config']['imageModel']
        self.assertEqual(default, normal)

    def test_resolution_upgrade_migrates_only_selected_codex_quality(self):
        for selected, old, expected in [('old-image::existing', 'auto', 'auto'),
                                         (installer.MODEL_KEY, 'auto', '1k'),
                                         (installer.MODEL_KEY, '8k', '8k')]:
            with self.subTest(selected=selected, quality=old):
                before = fixture()
                before['config'].update(imageModel=selected, quality=old)
                after = installer.merge(before, 'test-image-token')
                self.assertEqual(after['config']['quality'], expected)
                self.assertEqual(after['config']['imageModel'], selected)

    def test_idempotent_merge_preserves_extra_metadata(self):
        once = installer.merge(fixture(), 'test-image-token')
        once['config']['channels'][-1]['futureChannelField'] = {'kept': True}
        self.assertEqual(installer.merge(once, 'test-image-token'), once)

    def test_rejects_duplicate_and_foreign_channel_ids(self):
        once = installer.merge(fixture(), 'our-token')
        for field, value in [('apiKey', 'foreign-token'), ('baseUrl', 'https://example.invalid'),
                             ('interfaceType', 'openai-image')]:
            with self.subTest(field=field):
                altered = copy.deepcopy(once)
                altered['config']['channels'][-1][field] = value
                with self.assertRaises(ValueError):
                    installer.merge(altered, 'our-token')
        once['config']['channels'].append(copy.deepcopy(once['config']['channels'][-1]))
        with self.assertRaises(ValueError):
            installer.merge(once, 'our-token')

    def test_package_has_no_credentials_and_is_reproducible(self):
        data = installer.pack()
        self.assertEqual(data, installer.pack())
        manifest = installer.package_manifest(data)
        provider = manifest['contributes']['providers'][0]
        self.assertEqual(provider['create']['method'], 'POST')
        self.assertEqual(provider['poll']['method'], 'GET')
        self.assertEqual(provider['cancel']['method'], 'DELETE')
        self.assertEqual(provider['mediaTransport'], {'image': 'inline'})
        self.assertEqual(provider['create']['headers']['Idempotency-Key'], {'$ref': 'request.extra.idempotencyKey'})
        self.assertEqual(provider['response']['images'], {'$ref': 'response.images'})
        self.assertEqual(provider['baseUrl'], 'http://127.0.0.1:18766')
        self.assertNotIn('apiKey', provider['create']['body'])

    def test_preview_needs_no_credentials_or_installed_app(self):
        result = subprocess.run([sys.executable, '-X', 'utf8', installer.__file__,
                                 '--bridge-root', 'Z:/does-not-exist', '--make-default'],
                                capture_output=True, text=True, encoding='utf-8', check=True)
        preview = json.loads(result.stdout)
        self.assertEqual(preview['channel']['apiKey'], '[local-bridge-token]')
        self.assertEqual(preview['paidTestsSubmitted'], 0)
        self.assertTrue(preview['makeDefault'])


class InstallTests(unittest.TestCase):
    def setup_disk(self, root):
        data, bridge, app = root / 'data', root / 'bridge', root / 'app'
        data.mkdir()
        (bridge / 'state').mkdir(parents=True)
        (app / 'plugin-packages').mkdir(parents=True)
        (app / 'BeefTV.exe').write_bytes(b'not-executed')
        (bridge / 'state/local-api-key').write_text('test-install-token', 'utf-8')
        key = os.urandom(32)
        original = installer.encode(fixture(), key)
        (data / '.settings-key').write_bytes(key)
        (data / 'local-model-config.json').write_bytes(original)
        return data, bridge, app, key, original

    def test_install_backs_up_and_is_idempotent_without_secret_output(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(installer, 'assert_beeftv_closed'):
            data, bridge, app, key, original = self.setup_disk(Path(directory))
            result = installer.install(data, bridge, app, make_default=True)
            self.assertEqual(Path(result['backup']).read_bytes(), original)
            self.assertEqual(result['revision'], 43)
            self.assertTrue(result['configChanged'])
            self.assertTrue(result['packageChanged'])
            self.assertEqual(Path(result['package']).read_bytes(), installer.pack())
            saved = installer.decode((data / 'local-model-config.json').read_bytes(), key)
            expected = installer.merge(fixture(), 'test-install-token', make_default=True)
            expected['revision'] += 1
            self.assertEqual(saved, expected)
            twice = installer.install(data, bridge, app, make_default=True)
            self.assertFalse(twice['changed'])
            self.assertEqual(twice['revision'], 43)
            self.assertNotIn('test-install-token', json.dumps(result))
            self.assertNotIn('text-channel-secret', json.dumps(result))
            self.assertFalse(list(data.glob('*.tmp')))
            self.assertFalse(list((app / 'plugin-packages').glob('*.tmp')))

    def test_running_app_refuses_before_reading_any_files(self):
        with patch.object(installer, 'assert_beeftv_closed', side_effect=RuntimeError('app running')):
            with self.assertRaisesRegex(RuntimeError, 'app running'):
                installer.install('Z:/missing', 'Z:/missing', 'Z:/missing')

    def test_app_opening_mid_install_leaves_originals(self):
        with tempfile.TemporaryDirectory() as directory:
            data, bridge, app, key, original = self.setup_disk(Path(directory))
            with patch.object(installer, 'assert_beeftv_closed', side_effect=[None, RuntimeError('app started')]):
                with self.assertRaisesRegex(RuntimeError, 'app started'):
                    installer.install(data, bridge, app)
            self.assertEqual((data / 'local-model-config.json').read_bytes(), original)
            self.assertFalse((app / 'plugin-packages' / installer.PACKAGE_NAME).exists())

    def test_concurrent_config_key_token_or_package_edit_is_preserved(self):
        for field in ('config', 'key', 'token', 'package'):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                data, bridge, app, key, original = self.setup_disk(Path(directory))
                paths = {'config': data / 'local-model-config.json', 'key': data / '.settings-key',
                         'token': bridge / 'state/local-api-key',
                         'package': app / 'plugin-packages' / installer.PACKAGE_NAME}
                calls = []

                def changed():
                    calls.append(True)
                    if len(calls) == 2:
                        paths[field].write_bytes(b'concurrent-change')

                with patch.object(installer, 'assert_beeftv_closed', side_effect=changed):
                    with self.assertRaisesRegex(RuntimeError, '其他进程修改'):
                        installer.install(data, bridge, app)
                self.assertEqual(paths[field].read_bytes(), b'concurrent-change')
                if field != 'config':
                    self.assertEqual((data / 'local-model-config.json').read_bytes(), original)

    def test_config_replace_failure_rolls_back_new_plugin(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(installer, 'assert_beeftv_closed'):
            data, bridge, app, key, original = self.setup_disk(Path(directory))
            real_replace = os.replace

            def fail_config(source, target):
                if Path(target) == data / 'local-model-config.json':
                    raise OSError('config write failed')
                real_replace(source, target)

            with patch.object(installer.os, 'replace', side_effect=fail_config):
                with self.assertRaisesRegex(OSError, 'config write failed'):
                    installer.install(data, bridge, app)
            self.assertEqual((data / 'local-model-config.json').read_bytes(), original)
            self.assertFalse((app / 'plugin-packages' / installer.PACKAGE_NAME).exists())

    def test_owned_package_update_is_backed_up_and_restored_on_failure(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(installer, 'assert_beeftv_closed'):
            data, bridge, app, key, original = self.setup_disk(Path(directory))
            target = app / 'plugin-packages' / installer.PACKAGE_NAME
            previous = archive_with_manifest(installer.package_manifest(installer.pack()))
            target.write_bytes(previous)
            real_replace = os.replace

            def fail_config(source, destination):
                if Path(destination) == data / 'local-model-config.json':
                    raise OSError('config write failed')
                real_replace(source, destination)

            with patch.object(installer.os, 'replace', side_effect=fail_config):
                with self.assertRaises(OSError):
                    installer.install(data, bridge, app)
            self.assertEqual(target.read_bytes(), previous)
            backups = list((bridge / 'backups').glob('codex-images-*.beeftv-plugin'))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), previous)

    def test_restoring_missing_package_does_not_change_config_revision(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(installer, 'assert_beeftv_closed'):
            data, bridge, app, key, original = self.setup_disk(Path(directory))
            installer.install(data, bridge, app)
            prior = (data / 'local-model-config.json').read_bytes()
            (app / 'plugin-packages' / installer.PACKAGE_NAME).unlink()
            restored = installer.install(data, bridge, app)
            self.assertFalse(restored['configChanged'])
            self.assertTrue(restored['packageChanged'])
            self.assertEqual(restored['revision'], 43)
            self.assertEqual((data / 'local-model-config.json').read_bytes(), prior)

    def test_plugin_identifier_collision_refuses_without_config_write(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(installer, 'assert_beeftv_closed'):
            data, bridge, app, key, original = self.setup_disk(Path(directory))
            manifest = installer.package_manifest(installer.pack())
            manifest['id'] = 'foreign-plugin'
            (app / 'plugin-packages/other.beeftv-plugin').write_bytes(archive_with_manifest(manifest))
            with self.assertRaisesRegex(ValueError, '占用'):
                installer.install(data, bridge, app)
            self.assertEqual((data / 'local-model-config.json').read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
