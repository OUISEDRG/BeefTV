"""Standalone video audio references: native prompt contract and shared limits.

Only local fixtures and mocked CLI/media boundaries are used. No Dreama account,
upload endpoint, or generation endpoint is called by this suite.
"""
import base64
import copy
import hashlib
import html
import json
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import quote, unquote

from bridge import Bridge, BridgeError, DreamaGateway
import model_catalog

try:
    import video_audio_references as audio_refs
except ModuleNotFoundError:
    audio_refs = None


URL = 'https://jzinnovation.jxbaobian.cn/resourceBucket/audio/standalone.mp3?x=1&y=2'
SECOND_URL = 'https://jzinnovation.jxbaobian.cn/resourceBucket/audio/second.mp3'
VOICE = {'image_index': 1, 'name': '说话人物', 'voice_id': '575709'}


def model(**limits):
    return {'id': 'catalog-model-eid-without-family-name',
            'minImages': limits.get('imageMin', 0),
            'maxImages': limits.get('imageMax', 9), 'limits': {
                'totalMin': 1, 'totalMax': 12, 'imageMin': 0, 'imageMax': 9,
                'audioMin': 0, 'audioMax': 3, 'audioSizeMin': 0,
                'audioSizeMax': 15, 'audioDurationMin': 1,
                'audioDurationMax': 15, 'totalAudioDurationMax': 15,
                'allowAudioOnly': True, **limits}}


def data_url(raw=b'local-audio-fixture'):
    return 'data:audio/mpeg;base64,' + base64.b64encode(raw).decode()


def request(prompt='@音频1 跟随节拍运动。', count=1, **extra):
    return {'prompt': prompt, 'audios': [data_url(bytes([i + 1])) for i in range(count)], **extra}


def reference(**extra):
    return {'url': URL, 'sha256': hashlib.sha256(b'audio').hexdigest(),
            'sourceSha256': hashlib.sha256(b'source').hexdigest(),
            'name': '对白 & <节奏>.mp3', 'duration': 4.5, 'bytes': 12345, **extra}


def native_props(ref):
    # This independent literal is the observed native gG contract, not a call to
    # the production serializer under test.
    return {'fileType': 3, 'fileSizeBytes': ref['bytes'],
            'durationSeconds': ref['duration'], 'materialType': 0,
            'materialId': 0, 'assetSource': 'local-upload',
            'name': ref['name'], 'fileName': ref['name'], 'isOpen': False}


def native_tag(ref, **changed_props):
    props = {**native_props(ref), **changed_props}
    encoded = quote(json.dumps(props, ensure_ascii=False, separators=(',', ':')), safe='')
    return ('<span data-value="' + html.escape(ref['url'], quote=True)
            + '" data-node-type="inline-mention" data-extra-props="' + encoded
            + '" data-draggable="true"></span>')


def image_tag(image_id=21):
    props = quote(json.dumps({'fileType': 1, 'materialId': image_id, 'materialType': 3}))
    return ('<span data-value="" data-node-type="inline-mention" data-extra-props="'
            + props + '" data-draggable="true" data-type="mention"></span>')


class HelperTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(audio_refs, 'Standalone video audio support is missing')


class AudioRequestValidationTests(HelperTests):
    def test_decodes_audio_without_changing_bytes_or_requiring_a_model_name(self):
        self.assertEqual(audio_refs.validate_audio_references(request(), model(), 0), [b'\x01'])

    def test_every_connected_audio_must_be_explicitly_referenced(self):
        for prompt in ('没有音频标记。', '@音频1 只有第一条。', '@音频0 无效。',
                       '@音频3 越界。', '@音频1 @音频2 @音频3 越界。'):
            with self.subTest(prompt=prompt), self.assertRaises(BridgeError):
                audio_refs.validate_audio_references(request(prompt, count=2), model(), 0)
        self.assertEqual(audio_refs.validate_audio_references(
            request('@音频2 先响，@音频1 再响，@音频2 重复。', count=2), model(), 0),
            [b'\x01', b'\x02'])

    def test_audio_mentions_without_connected_slots_are_rejected(self):
        with self.assertRaises(BridgeError):
            audio_refs.validate_audio_references(request('@音频1 不存在。', count=0), model(), 1)

    def test_rejects_remote_urls_invalid_mime_and_malformed_payloads(self):
        invalid = [None, {}, 'data:audio/mpeg;base64,YQ==', [URL],
                   ['data:video/mp4;base64,YQ=='], ['data:audio/mpeg;base64,!!!!'],
                   ['data:audio/mpeg;base64,'], [True]]
        for audios in invalid:
            with self.subTest(audios=audios), self.assertRaises(BridgeError):
                audio_refs.validate_audio_references({'prompt': '@音频1', 'audios': audios}, model(), 0)

    def test_disabled_or_unknown_audio_capability_is_not_assumed_supported(self):
        for spec in (model(audioMax=0), {**model(), 'id': 'unknown-eid', 'limits': {}}):
            with self.subTest(spec=spec), self.assertRaises(BridgeError):
                audio_refs.validate_audio_references(request(), spec, 1)

    def test_audio_and_character_voices_share_the_same_count_limit(self):
        mixed = request(character_voices=[VOICE])
        with self.assertRaises(BridgeError):
            audio_refs.validate_audio_references(mixed, model(audioMax=1), 1)
        self.assertEqual(audio_refs.validate_audio_references(mixed, model(audioMax=2), 1), [b'\x01'])

    def test_total_minimum_is_satisfied_by_images_and_audio_together(self):
        self.assertEqual(audio_refs.validate_audio_references(request(), model(totalMin=2), 1), [b'\x01'])
        with self.assertRaises(BridgeError):
            audio_refs.validate_audio_references(request(), model(totalMin=3), 1)

    def test_total_maximum_includes_audio_and_voice_references(self):
        mixed = request(character_voices=[VOICE])
        with self.assertRaises(BridgeError):
            audio_refs.validate_audio_references(mixed, model(totalMax=2), 1)
        self.assertEqual(audio_refs.validate_audio_references(mixed, model(totalMax=3), 1), [b'\x01'])

    def test_audio_only_requirement_is_independent_of_audio_capacity(self):
        with self.assertRaises(BridgeError):
            audio_refs.validate_audio_references(request(), model(allowAudioOnly=False), 0)
        self.assertEqual(audio_refs.validate_audio_references(request(), model(allowAudioOnly=False), 1), [b'\x01'])
        with self.assertRaises(BridgeError):
            audio_refs.validate_audio_references(request(), model(imageMin=1), 0)
        with self.assertRaises(BridgeError):
            audio_refs.validate_audio_references(request(), model(audioMin=2), 1)


class AudioMetadataLimitTests(HelperTests):
    def test_duration_and_size_limits_include_the_exact_boundary(self):
        spec = model(audioDurationMin=2, audioDurationMax=5, audioSizeMax=1,
                     totalAudioDurationMax=10)
        audio_refs.validate_audio_limits(spec, [reference(duration=2, bytes=1048576)])
        audio_refs.validate_audio_limits(spec, [reference(duration=5, bytes=1048576)])
        for changes in ({'duration': 1.99}, {'duration': 5.01}, {'bytes': 1048577}):
            with self.subTest(changes=changes), self.assertRaises(BridgeError):
                audio_refs.validate_audio_limits(spec, [reference(**changes)])

    def test_unreadable_metadata_is_not_treated_as_zero_duration(self):
        for changes in ({'duration': float('nan')}, {'duration': float('inf')},
                        {'duration': True}, {'duration': 0}, {'bytes': True},
                        {'bytes': -1}):
            with self.subTest(changes=changes), self.assertRaises(BridgeError):
                audio_refs.validate_audio_limits(model(), [reference(**changes)])

    def test_character_voices_and_uploaded_audio_share_total_duration(self):
        voice = {'voiceId': 575709, 'voiceDuration': 7, 'voiceUrl': SECOND_URL,
                 'voiceBytes': 100, 'voiceName': '人物', 'voiceSource': 'personal'}
        audio_refs.validate_audio_limits(model(totalAudioDurationMax=12), [reference(duration=5)], voices=[voice])
        with self.assertRaises(BridgeError):
            audio_refs.validate_audio_limits(model(totalAudioDurationMax=11), [reference(duration=5)], voices=[voice])

    def test_total_duration_falls_back_to_per_audio_maximum(self):
        refs = [reference(duration=4), reference(url=SECOND_URL, duration=3)]
        with self.assertRaises(BridgeError):
            audio_refs.validate_audio_limits(model(totalAudioDurationMax=0, audioDurationMax=6), refs)

    def test_repeated_resource_does_not_repeat_its_duration_budget(self):
        same_resource = reference(duration=5)
        audio_refs.validate_audio_limits(model(totalAudioDurationMax=5), [same_resource, same_resource])


class AudioLocalPreparationTests(HelperTests):
    @staticmethod
    def metadata(path, *, duration=4, codec='mp3'):
        raw = Path(path).read_bytes()
        return {'duration': duration, 'bytes': len(raw),
                'sha256': hashlib.sha256(raw).hexdigest(), 'codec': codec,
                'sampleRate': 48000, 'channels': 2}

    def test_existing_mp3_keeps_original_bytes_and_frozen_source_digest(self):
        raw = b'fixture-mp3-audio'
        with tempfile.TemporaryDirectory() as root, \
                patch('audio_flow.probe_audio', side_effect=self.metadata), \
                patch.object(audio_refs.subprocess, 'run', side_effect=AssertionError('MP3 should not be reencoded')):
            refs = audio_refs.prepare_audio_references([raw], model(), root)
            self.assertEqual(len(refs), 1)
            ref = refs[0]
            self.assertEqual(Path(ref['path']).read_bytes(), raw)
            self.assertEqual(ref['sourceSha256'], hashlib.sha256(raw).hexdigest())
            self.assertEqual(ref['sha256'], hashlib.sha256(raw).hexdigest())
            self.assertEqual(ref['bytes'], len(raw))
            self.assertEqual(ref['duration'], 4)
            self.assertEqual(Path(ref['path']).suffix, '.mp3')

    def test_non_mp3_is_locally_converted_and_output_metadata_is_reprobed(self):
        source, converted = b'fixture-opus-audio', b'fixture-normalized-mp3-audio'
        def probe(path):
            return self.metadata(path, codec='opus' if Path(path).suffix == '.input' else 'mp3')
        def convert(args, **kwargs):
            self.assertEqual(args[args.index('-protocol_whitelist') + 1], 'file')
            self.assertEqual(Path(args[args.index('-i') + 1]).read_bytes(), source)
            Path(args[-1]).write_bytes(converted)
            return Mock(returncode=0)
        with tempfile.TemporaryDirectory() as root, \
                patch('audio_flow.probe_audio', side_effect=probe), \
                patch.object(audio_refs.subprocess, 'run', side_effect=convert), \
                patch('requests.sessions.Session.request', side_effect=AssertionError('Preparation must stay local')):
            refs = audio_refs.prepare_audio_references([source], model(), root)
            ref = refs[0]
            self.assertEqual(Path(ref['path']).read_bytes(), converted)
            self.assertEqual(ref['sourceSha256'], hashlib.sha256(source).hexdigest())
            self.assertEqual(ref['sha256'], hashlib.sha256(converted).hexdigest())
            self.assertEqual(ref['bytes'], len(converted))

    def test_probe_failure_does_not_produce_an_uploadable_reference(self):
        with tempfile.TemporaryDirectory() as root, \
                patch('audio_flow.probe_audio', side_effect=BridgeError('Invalid media')), \
                patch.object(audio_refs.subprocess, 'run', side_effect=AssertionError('Invalid input must not be converted')):
            with self.assertRaises(BridgeError):
                audio_refs.prepare_audio_references([b'invalid-media'], model(), root)
            self.assertEqual(list(Path(root).rglob('*.mp3')), [])

    def test_conversion_that_changes_duration_is_rejected(self):
        def probe(path):
            original = Path(path).suffix == '.input'
            return self.metadata(path, duration=4 if original else 5,
                                 codec='opus' if original else 'mp3')
        def convert(args, **kwargs):
            Path(args[-1]).write_bytes(b'changed-audio')
            return Mock(returncode=0)
        with tempfile.TemporaryDirectory() as root, \
                patch('audio_flow.probe_audio', side_effect=probe), \
                patch.object(audio_refs.subprocess, 'run', side_effect=convert):
            with self.assertRaises(BridgeError):
                audio_refs.prepare_audio_references([b'opus-audio'], model(), root)


class AudioNativePromptTests(HelperTests):
    def test_serializer_uses_a_url_atom_without_a_character_material(self):
        ref = reference()
        self.assertEqual(audio_refs.audio_extra_props(ref), native_props(ref))
        class Tags(HTMLParser):
            def __init__(self):
                super().__init__(); self.attrs = []; self.body = []
            def handle_starttag(self, tag, attrs):
                self.attrs.append((tag, dict(attrs)))
            def handle_data(self, data):
                self.body.append(data)
        parser = Tags(); parser.feed(audio_refs.audio_reference_tag(ref))
        self.assertEqual(len(parser.attrs), 1)
        tag, attrs = parser.attrs[0]
        self.assertEqual(tag, 'span')
        self.assertEqual(attrs['data-value'], URL)
        self.assertEqual(attrs['data-node-type'], 'inline-mention')
        self.assertEqual(attrs['data-draggable'], 'true')
        self.assertEqual(json.loads(unquote(attrs['data-extra-props'])), native_props(ref))
        self.assertEqual(parser.body, [])

    def test_readback_accepts_native_audio_without_legacy_data_type_attribute(self):
        ref = reference()
        DreamaGateway.verify_reference_tags('<p>音乐 ' + native_tag(ref) + '</p>', [ref])

    def test_readback_rejects_changed_url_or_audio_metadata(self):
        ref = reference()
        bad_tags = [native_tag({**ref, 'url': SECOND_URL}),
                    native_tag(ref, fileType=1), native_tag(ref, materialId=21),
                    native_tag(ref, materialType=1), native_tag(ref, fileSizeBytes=1),
                    native_tag(ref, durationSeconds=1), native_tag(ref, name='替换.mp3'),
                    native_tag(ref, fileName='替换.mp3'), native_tag(ref, isOpen=True)]
        for tag in bad_tags:
            with self.subTest(tag=tag), self.assertRaises(BridgeError):
                DreamaGateway.verify_reference_tags('<p>' + tag + '</p>', [ref])

    def test_readback_rejects_missing_extra_reordered_and_nonempty_audio_tags(self):
        first, second = reference(), reference(url=SECOND_URL, name='second.mp3')
        for text in ('', native_tag(first) + native_tag(second),
                     native_tag(first).replace('</span>', '不应出现正文</span>'),
                     native_tag(first).replace('</span>', '<!--hidden--></span>')):
            with self.subTest(text=text), self.assertRaises(BridgeError):
                DreamaGateway.verify_reference_tags('<p>' + text + '</p>', [first])
        with self.assertRaises(BridgeError):
            DreamaGateway.verify_reference_tags('<p>' + native_tag(second) + native_tag(first) + '</p>', [first, second])

    def test_mixed_prompt_keeps_user_text_and_interleaved_audio_order(self):
        first, second = reference(), reference(url=SECOND_URL, name='second.mp3')
        image = {'imageId': 21, 'name': 'picture'}
        gateway = DreamaGateway()
        official = {'mode': 'drama', 'prompt': '<p>禁止复制组装器正文' + image_tag() + '</p>'}
        prompt = '  @音频2 跟随 @图片1\n@音频1 & <对白>，再次 @音频2。 '
        with patch.object(gateway, 'run', return_value=official):
            saved, indexes = gateway.render_verbatim_prompt(prompt, [image], audio_refs=[first, second])
        self.assertEqual(indexes, [0])
        self.assertNotIn('禁止复制组装器正文', saved)
        self.assertIn(' &amp; &lt;对白&gt;，再次 ', saved)
        self.assertTrue(saved.startswith('<p>  '))
        self.assertTrue(saved.endswith('。 </p>'))
        self.assertIn('<br>', saved)
        DreamaGateway.verify_reference_tags(saved, [second, image, first, second])

    def test_audio_only_prompt_does_not_use_image_cli_assembly(self):
        gateway = DreamaGateway(); ref = reference()
        with patch.object(gateway, 'run', side_effect=AssertionError('Audio URL references need no image library lookup')):
            saved, indexes = gateway.render_verbatim_prompt('@音频1 同步动作。', [], audio_refs=[ref])
        self.assertEqual(indexes, [])
        self.assertTrue(saved.endswith(' 同步动作。</p>'))
        DreamaGateway.verify_reference_tags(saved, [ref])


class OfflineAudioGateway(DreamaGateway):
    """Emulate only upstream boundaries; keep binding/save/readiness code real."""
    def __init__(self, row, mode_type=3):
        super().__init__()
        self.row = copy.deepcopy(row)
        self.mode_type = mode_type
        self.writes = []
        self.board = None
        self.fail_save = False

    def _invoke(self, *args):
        action = args[:2]
        if action == ('auth', 'whoami'):
            return {'userId': 17, 'teamId': 18}
        if action == ('project', 'get'):
            return {'id': 91, 'promptMode': 'drama', 'proportion': '9:16', 'modeType': self.mode_type}
        if action == ('series', 'get'):
            return {'id': 92, 'projectId': 91}
        if action == ('capability', 'list'):
            return {'items': [copy.deepcopy(self.row)]}
        if action == ('storyboard', 'create'):
            self.writes.append(('create-board', args))
            self.board = {'id': 93, 'projectId': 91, 'seriesId': 92, 'videoTaskId': 0,
                          'videoPrompt': '', 'name': args[args.index('--name') + 1]}
            return {}
        if action == ('storyboard', 'list'):
            return {'items': [copy.deepcopy(self.board)] if self.board else []}
        if action == ('point', 'calc'):
            return {'token': 7}
        raise AssertionError('Unexpected or paid CLI action: ' + repr(action))

    def upload_audio_reference(self, path, meta):
        raw = Path(path).read_bytes()
        if len(raw) != meta['bytes'] or hashlib.sha256(raw).hexdigest() != meta['sha256']:
            raise AssertionError('Upload received metadata from different audio bytes')
        self.writes.append(('upload-audio', copy.deepcopy(meta)))
        return URL

    def post(self, path, payload):
        if path != '/storyboard/api/save' or payload['id'] != 93 or payload['seriesId'] != 92:
            raise AssertionError('Unexpected native write: ' + repr((path, payload)))
        self.writes.append(('save-board', copy.deepcopy(payload)))
        self.board.update(copy.deepcopy(payload))
        if self.fail_save:
            raise TimeoutError('Saved remotely, response outcome unknown')
        return {}


class AudioBridgePreparationTests(HelperTests):
    def setUp(self):
        super().setUp()
        spec = model()
        self.row = {'eid': spec['id'], 'name': 'Offline catalog model', 'alias': '720p',
                    'isExternal': 0, 'videoTokens': json.dumps([{'duration': 5, 'token': 7}]),
                    'modelLimit': json.dumps(spec['limits'])}
        self.catalog_patch = patch.object(model_catalog, 'LIVE_ITEMS', [self.row])
        self.catalog_patch.start(); self.addCleanup(self.catalog_patch.stop)
        self.probe_patch = patch('audio_flow.probe_audio', side_effect=AudioLocalPreparationTests.metadata)
        self.probe_patch.start(); self.addCleanup(self.probe_patch.stop)
        self.data = {**request(), 'model': spec['id'], 'duration': 5, 'resolution': '720p',
                     'aspect_ratio': '9:16', 'images': [], 'dreama': {
                         'schemaVersion': 1, 'canvasId': 'offline-canvas', 'nodeId': 'offline-video',
                         'projectId': 91, 'seriesId': 92, 'promptMode': 'drama',
                         'aspectRatio': '9:16', 'bindingRevision': 2}}

    def test_real_prepare_free_project_uploads_saves_and_freezes_audio_binding(self):
        gateway = OfflineAudioGateway(self.row)
        with tempfile.TemporaryDirectory() as root:
            bridge = Bridge(root, gateway)
            job = bridge.prepare(self.data, 'audio-job')
            self.assertEqual(job['status'], 'prepared')
            self.assertFalse(job['generationSubmitted'])
            self.assertEqual(job['pointsEstimate'], 7)
            self.assertEqual(job['bindingSnapshot']['projectModeType'], 3)
            self.assertIs(job['bindingSnapshot']['requiresStandaloneAudio'], True)
            self.assertEqual(job['audioReferences'], job['audioReferenceSnapshot'])
            self.assertEqual(job['audioReferences'][0]['url'], URL)
            self.assertEqual(job['references'], [])
            self.assertEqual(job['referenceImageIds'], [])
            self.assertEqual(job['savedPromptHash'], hashlib.sha256(gateway.board['videoPrompt'].encode()).hexdigest())
            self.assertEqual([kind for kind, _ in gateway.writes], ['upload-audio', 'create-board', 'save-board'])
            persisted = bridge.get('audio-job')
            self.assertEqual(persisted['promptReferenceOrder'], [['audio', 0]])
            gateway.check_ready(persisted)

    def test_non_free_or_unknown_project_mode_is_rejected_before_upload(self):
        for mode_type in (1, 2, 4, None, '3'):
            gateway = OfflineAudioGateway(self.row, mode_type=mode_type)
            with self.subTest(mode_type=mode_type), tempfile.TemporaryDirectory() as root:
                bridge = Bridge(root, gateway)
                with self.assertRaises(BridgeError):
                    bridge.prepare(self.data, 'blocked')
                self.assertEqual(gateway.writes, [])
                self.assertIsNone(bridge.get('blocked'))
                self.assertFalse((Path(root) / 'video-audio-inputs').exists())

    def test_audio_snapshot_cannot_be_changed_or_removed_from_ledger(self):
        with tempfile.TemporaryDirectory() as root:
            bridge = Bridge(root, OfflineAudioGateway(self.row))
            bridge.prepare(self.data, 'frozen')
            for remove in (False, True):
                changed = bridge.get('frozen')
                if remove:
                    changed.pop('audioReferenceSnapshot')
                else:
                    changed['audioReferenceSnapshot'][0]['url'] = SECOND_URL
                with self.subTest(remove=remove), self.assertRaises(BridgeError):
                    bridge.update('frozen', changed)
            self.assertEqual(bridge.get('frozen')['audioReferenceSnapshot'][0]['url'], URL)

    def test_unknown_save_outcome_never_reuploads_or_recreates_on_retry(self):
        gateway = OfflineAudioGateway(self.row); gateway.fail_save = True
        with tempfile.TemporaryDirectory() as root:
            bridge = Bridge(root, gateway)
            with self.assertRaises(BridgeError):
                bridge.prepare(self.data, 'uncertain')
            self.assertEqual(bridge.get('uncertain')['status'], 'unknown')
            written = copy.deepcopy(gateway.writes)
            with self.assertRaises(BridgeError):
                Bridge(root, gateway).prepare(self.data, 'uncertain')
            self.assertEqual(gateway.writes, written)
            self.assertEqual([kind for kind, _ in written], ['upload-audio', 'create-board', 'save-board'])

    def test_readiness_rechecks_project_mode_before_any_new_write(self):
        gateway = OfflineAudioGateway(self.row)
        with tempfile.TemporaryDirectory() as root:
            bridge = Bridge(root, gateway); bridge.prepare(self.data, 'prepared')
            writes = copy.deepcopy(gateway.writes)
            gateway.mode_type = 1
            with self.assertRaises(BridgeError):
                gateway.check_ready(bridge.get('prepared'))
            self.assertEqual(gateway.writes, writes)

    def test_readiness_rejects_changed_runtime_audio_and_local_bytes(self):
        gateway = OfflineAudioGateway(self.row)
        with tempfile.TemporaryDirectory() as root:
            bridge = Bridge(root, gateway); bridge.prepare(self.data, 'prepared')
            changed = bridge.get('prepared'); changed['audioReferences'][0]['url'] = SECOND_URL
            with self.assertRaises(BridgeError):
                gateway.check_ready(changed)
            original = bridge.get('prepared')
            Path(original['audioReferences'][0]['path']).write_bytes(b'replaced-local-audio')
            with self.assertRaises(BridgeError):
                gateway.check_ready(original)

    def test_readiness_rejects_remote_prompt_audio_url_tampering(self):
        gateway = OfflineAudioGateway(self.row)
        with tempfile.TemporaryDirectory() as root:
            bridge = Bridge(root, gateway); bridge.prepare(self.data, 'prepared')
            gateway.board['videoPrompt'] = gateway.board['videoPrompt'].replace(html.escape(URL, quote=True), SECOND_URL)
            with self.assertRaises(BridgeError):
                gateway.check_ready(bridge.get('prepared'))


if __name__ == '__main__':
    unittest.main()
