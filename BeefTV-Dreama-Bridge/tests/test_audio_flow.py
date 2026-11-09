from tests.fixtures_budget import intent_fields
import copy
import json
import io
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from bridge import Bridge, BridgeError
from tests.fixtures_budget import FundedBridge as Bridge
import audio_flow as a

URL = 'https://jzinnovation.jxbaobian.cn/resourceBucket/audio/result.mp3'
TASK = 'f7c333d0-9178-4c41-89cf-e5715e1b83b9'


def job():
    return {'id': 'test-audio', 'kind': 'audio', 'model': a.SEED, 'taskType': 'voiceover-generate-doubao',
            'prompt': '  原始文案\n不要改写。 ', 'status': 'submitting', 'approvedAt': 1,
            'pointsEstimate': 120, 'approvedPoints': 120,
            'taskName': 'BeefTV_audio_31a5b287b5aa435198ccf632443fe068',
            'identity': {'userId': 17921}, 'references': [], 'emotionVector': [],
            'audioConfig': {'format': 'mp3', 'sample_rate': 48000, 'pitch_rate': 0,
                            'speech_rate': 0, 'loudness_rate': 0}, **intent_fields(120)}


def task_row(j=None):
    j = j or job()
    return {'id': TASK, 'userId': j['identity']['userId'], 'taskName': j['taskName'],
            'taskType': j['taskType'], 'inputPayload': a.input_payload(j), 'status': 'COMPLETED',
            'outputPayload': {'resourceUrl': URL}}


class AudioRequestTests(unittest.TestCase):
    def test_all_catalog_voices_have_explicit_bound_models(self):
        self.assertEqual(len(a.voices()), 79)
        for vid, voice in a.voices().items():
            model = a.resolve_audio_model(a.CLONE + '-' + vid)
            self.assertEqual(model['voiceId'], vid)
            self.assertIn(voice['name'], model['label'])

    def test_seed_keeps_exact_prompt_and_maps_parameters(self):
        prompt = '  中文\n英文 text  '
        values = a.validate_audio_request({'model': a.SEED, 'input': prompt, 'pitch': -2, 'speed': 1.25, 'volume': 0.5})
        self.assertEqual(values[1], prompt)
        self.assertEqual(values[5], {'format': 'mp3', 'sample_rate': 48000, 'pitch_rate': -2,
                                    'speech_rate': 25, 'loudness_rate': -50})

    def test_no_silent_unsupported_voice_or_parameters(self):
        for fields in [{'voice': 'alloy'}, {'instructions': '改成别的'}, {'format': 'wav'}, {'speed': 2.1},
                       {'pitch': True}, {'input': 'different'}, {'unknown': 1}]:
            with self.subTest(fields=fields), self.assertRaises(BridgeError):
                a.validate_audio_request({'model': a.SEED, 'prompt': '原文', **fields})

    def test_clone_requires_one_voice_source(self):
        voice = next(iter(a.voices()))
        a.validate_audio_request({'model': a.CLONE, 'input': '你好', 'voice': voice})
        a.validate_audio_request({'model': a.CLONE + '-' + voice, 'input': '你好'})
        for data in [{'model': a.CLONE}, {'model': a.CLONE, 'voice': voice, 'audios': [URL]},
                     {'model': a.CLONE, 'voice': voice, 'speed': 1.2}]:
            with self.subTest(data=data), self.assertRaises(BridgeError):
                a.validate_audio_request({'input': '你好', **data})

    def test_reference_limits(self):
        a.validate_audio_request({'model': a.SEED, 'input': '你好', 'audios': [URL] * 3})
        for refs in [{'audios': [URL] * 4}, {'audios': [URL], 'images': ['x']}, {'images': ['x', 'y']}]:
            with self.assertRaises(BridgeError):
                a.validate_audio_request({'model': a.SEED, 'input': '你好', **refs})


class AudioGatewayTests(unittest.TestCase):
    def test_submit_is_gated_and_exact(self):
        gw, j = a.AudioGateway(), job()
        with patch.object(gw, '_request', return_value={'id': TASK}) as request:
            with self.assertRaises(BridgeError):
                gw.submit_video({**j, 'status': 'prepared'})
            request.assert_not_called()
            self.assertEqual(gw.submit_video(j), {'taskId': TASK})
            self.assertEqual(request.call_args.kwargs['payload']['inputPayload']['text_prompt'], j['prompt'])

    def test_reconcile_checks_name_type_identity_and_input(self):
        gw, j = a.AudioGateway(), job()
        row = task_row(j)
        with patch.object(gw, '_request', return_value={'tasks': [row], 'total': 1}) as request:
            self.assertEqual(gw.find_task(j), TASK)
            self.assertEqual(gw.task_status(TASK, j)['urls'], [URL])
            self.assertTrue(all(call.args[0] == 'GET' for call in request.call_args_list))
        for key, value in [('userId', 99), ('taskName', 'unrelated'), ('taskType', 'different'),
                           ('inputPayload', {**row['inputPayload'], 'text_prompt': 'changed'})]:
            wrong = copy.deepcopy(row)
            wrong[key] = value
            with self.subTest(key=key), patch.object(gw, '_request', return_value={'tasks': [wrong], 'total': 1}):
                self.assertIsNone(gw.find_task(j))

    def test_duplicate_or_incomplete_reconcile_fails_closed(self):
        gw, j = a.AudioGateway(), job()
        for response in [{'tasks': [task_row(j), task_row(j)], 'total': 2},
                         {'tasks': [], 'total': 5000}]:
            with patch.object(gw, '_request', return_value=response), self.assertRaises(BridgeError):
                gw.find_task(j)

    def test_price_uses_native_hold_and_clone_character_count(self):
        j = job()
        self.assertEqual(a.estimate_points(j), 120)
        j.update(taskType='voiceover-generate', prompt='你' * 101)
        self.assertEqual(a.estimate_points(j), 2)


class AudioFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.bridge = Bridge(Path(self.temp.name))
        self.gateway = a.AudioGateway()
        self.gateway.identity = lambda: {'userId': 17921}
        self.gateway.verify_project = lambda binding: None
        self.flow = a.AudioFlow(self.bridge, gateway=self.gateway, download=self.download)

    @staticmethod
    def download(url, path):
        path.write_bytes(b'mocked-mp3')
        return {'bytes': 10, 'duration': 2, 'mimeType': 'audio/mpeg'}

    def test_prepare_never_submits_and_preserves_prompt(self):
        with patch.object(self.gateway, '_request') as request:
            j = self.flow.prepare({'model': a.SEED, 'input': '  原文\n '}, 'audio-exact')
            request.assert_not_called()
        self.assertEqual(j['status'], 'prepared')
        self.assertEqual(j['prompt'], '  原文\n ')
        self.assertEqual(j['pointsEstimate'], 120)

    def test_one_shot_submit_and_completed_audio_file(self):
        j = self.flow.prepare({'model': a.SEED, 'input': '你好'}, 'audio-completed')
        with patch.object(self.gateway, 'submit_video', return_value={'taskId': TASK}) as submit:
            self.flow.approve(j['id'], j['quoteId'], 120)
            with self.assertRaises(BridgeError):
                self.flow.approve(j['id'], j['quoteId'], 120)
            submit.assert_called_once()
        with patch.object(self.gateway, 'task_status', return_value={'status': 3, 'urls': [URL]}):
            final = self.flow.poll(j['id'])
        self.assertEqual(final['status'], 'completed')
        self.assertTrue(final['mediaKey'].endswith('.mp3'))
        self.assertTrue((self.flow.audios / final['mediaKey']).is_file())
        self.assertEqual(final['audio']['mimeType'], 'audio/mpeg')

    def test_unknown_submit_reconciles_without_paid_retry(self):
        j = self.flow.prepare({'model': a.SEED, 'input': '你好'}, 'audio-unknown')
        with patch.object(self.gateway, 'submit_video', side_effect=TimeoutError) as submit:
            with self.assertRaises(BridgeError):
                self.flow.approve(j['id'], j['quoteId'], 120)
            self.assertEqual(self.bridge.get(j['id'])['status'], 'submission_unknown')
            with patch.object(self.gateway, 'find_task', return_value=TASK), patch.object(self.gateway, 'task_status', return_value={'status': 2}):
                final = self.flow.poll(j['id'])
            submit.assert_called_once()
        self.assertEqual(final['status'], 'processing')
        self.assertNotIn('error', final)


class AudioMediaTests(unittest.TestCase):
    def test_native_wave_result_is_verified_and_returned_as_mp3(self):
        buffer = io.BytesIO()
        with wave.open(buffer, 'wb') as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(16000)
            out.writeframes(b'\0\0' * 16000)
        class Response:
            status_code = 200
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def iter_content(self, size): yield buffer.getvalue()
        with tempfile.TemporaryDirectory() as directory, patch.object(a.requests, 'get', return_value=Response()):
            path = Path(directory) / 'result.mp3'
            result = a.download_audio(URL, path)
            self.assertEqual(result['codec'], 'mp3')
            self.assertEqual(result['convertedFrom'], 'pcm_s16le')
            self.assertAlmostEqual(result['duration'], 1, delta=0.15)
            self.assertEqual(a.probe_audio(path)['codec'], 'mp3')


if __name__ == '__main__':
    unittest.main()
