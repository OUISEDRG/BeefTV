"""HTTP-to-ledger integration with fake upstreams; no real generation calls."""
import hashlib
import tempfile
import threading
import unittest

import requests
from audio_flow import AudioFlow, SEED, input_payload
from auto_execution import AutoExecution
from bridge import Bridge, BridgeError
from image_flow import ImageFlow
from server import make_server
from tests.test_image_flow import FakeImageGateway, request as image_request, png


class FakeAudioGateway:
    def __init__(self):
        self.calls = []
        self.status = 1
        self.task = None
    def identity(self): return {'userId': 17921}
    def check_ready(self, job): self.calls.append('check')
    def submit_video(self, job):
        self.calls.append(('submit', input_payload(job)))
        self.task = '9cb33bbf-a335-498f-b0b2-409e1e70b28b'
        return {'taskId': self.task}
    def find_task(self, job): return self.task
    def task_status(self, task, job):
        return {'taskId': task, 'status': self.status,
                'urls': ['https://jzinnovation.jxbaobian.cn/audio.mp3'] if self.status == 3 else []}


class ModalitiesHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.bridge = Bridge(self.tmp.name)
        self.server = make_server(self.bridge, 'test-only-secret', 0, policy={'mode': 'manual'})
        # Drive the persisted worker clock explicitly, without waiting five real
        # seconds or letting the separate background loop race test assertions.
        self.server.stop_event.set()
        self.server.poller.join(timeout=2)
        self.server.auto_worker.join(timeout=2)
        self.image_gateway = FakeImageGateway()
        self.audio_gateway = FakeAudioGateway()
        self.image_bytes, self.audio_bytes = png(), b'mocked-mp3-body'
        def image_download(url, path):
            path.write_bytes(self.image_bytes)
            return {'bytes': len(self.image_bytes), 'mimeType': 'image/png',
                    'sha256': hashlib.sha256(self.image_bytes).hexdigest(), 'width': 320, 'height': 320}
        def audio_download(url, path):
            path.write_bytes(self.audio_bytes)
            return {'bytes': len(self.audio_bytes), 'mimeType': 'audio/mpeg', 'duration': 2}
        self.server.images = ImageFlow(self.bridge, gateway=self.image_gateway, download=image_download)
        self.server.audios = AudioFlow(self.bridge, gateway=self.audio_gateway, download=audio_download)
        self.clock = 1000.0
        self.policy = {'mode': 'background-countdown', 'delaySeconds': 5, 'effectiveAt': 1}
        self.server.execution_policy = self.policy
        self.server.auto_execution = AutoExecution(self.bridge, self.server.flow_for, self.policy, now=lambda: self.clock)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = 'http://127.0.0.1:' + str(self.server.server_port)
        self.headers = {'Authorization': 'Bearer test-only-secret'}

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def create(self, kind, key):
        path = '/image/v1/images' if kind == 'image' else '/audio/v1/audio/tasks'
        data = image_request() if kind == 'image' else {'model': SEED, 'input': '  原文\n保留空白。 '}
        response = requests.post(self.url + path, json=data, headers={**self.headers, 'Idempotency-Key': key}, timeout=5)
        self.assertEqual(response.status_code, 202, response.text)
        self.server.wait_for_jobs()
        job = self.bridge.get(key)
        self.assertEqual(job['status'], 'prepared', job)
        self.assertEqual(job['kind'], kind)
        return path, data, job

    def paid_count(self, kind):
        if kind == 'image': return self.image_gateway.calls.count('submit')
        return sum(isinstance(x, tuple) and x[0] == 'submit' for x in self.audio_gateway.calls)

    def tick_five_seconds(self):
        self.server.auto_execution.step()
        self.clock += 4.999
        self.server.auto_execution.step()
        self.assertEqual(self.paid_count('image') + self.paid_count('audio'), 0)
        self.clock += .001
        self.server.auto_execution.step()

    def test_catalogs_authentication_and_validation_before_side_effects(self):
        for path, count in [('/image/v1/models', 21), ('/audio/v1/models', 81)]:
            self.assertEqual(requests.get(self.url + path, timeout=5).status_code, 401)
            response = requests.get(self.url + path, headers=self.headers, timeout=5)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(response.json()['data']), count)
        response = requests.post(self.url + '/audio/v1/audio/tasks', json={'model': SEED, 'input': 'hello', 'voice': 'alloy'}, headers=self.headers, timeout=5)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.server.list_jobs(), [])
        self.assertEqual(self.audio_gateway.calls, [])

    def test_image_automatic_deadline_returns_all_results_and_recovers_cache_without_recharge(self):
        path, data, j = self.create('image', 'http-image')
        self.tick_five_seconds()
        self.assertEqual(self.bridge.get(j['id'])['status'], 'queued')
        self.assertEqual(self.paid_count('image'), 1)
        self.assertEqual(requests.get(self.url + '/v1/videos/' + j['id'], headers=self.headers, timeout=5).status_code, 404)
        self.image_gateway.status = 3
        self.server.images.poll(j['id'])
        response = requests.get(self.url + path + '/' + j['id'], headers=self.headers, timeout=5)
        result = response.json()
        self.assertEqual(result['status'], 'succeeded', result)
        self.assertEqual(len(result['images']), 2)
        self.assertEqual(result['data'], result['images'])
        for item in result['images']:
            media = requests.get(item['url'], timeout=5)
            self.assertEqual(media.status_code, 200)
            self.assertEqual(media.headers['Content-Type'], 'image/png')
            self.assertEqual(media.content, self.image_bytes)
        latest = self.bridge.get(j['id'])
        cached = self.server.images.images / latest['images'][0]['mediaKey']
        cached.unlink()
        self.assertEqual(requests.get(self.url + path + '/' + j['id'], headers=self.headers, timeout=5).json()['status'], 'processing')
        self.server.images.poll(j['id'])
        self.assertTrue(cached.exists())
        self.assertEqual(self.paid_count('image'), 1)

    def test_audio_automatic_deadline_returns_mp3_and_byte_range(self):
        path, data, j = self.create('audio', 'http-audio')
        self.tick_five_seconds()
        self.assertEqual(self.paid_count('audio'), 1)
        paid = next(x[1] for x in self.audio_gateway.calls if isinstance(x, tuple))
        self.assertEqual(paid['text_prompt'], data['input'])
        self.audio_gateway.status = 3
        self.server.audios.poll(j['id'])
        result = requests.get(self.url + path + '/' + j['id'], headers=self.headers, timeout=5).json()
        self.assertEqual(result['status'], 'succeeded', result)
        response = requests.get(result['audio_url'], headers={'Range': 'bytes=2-5'}, timeout=5)
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.headers['Content-Type'], 'audio/mpeg')
        self.assertEqual(response.content, self.audio_bytes[2:6])
        latest = self.bridge.get(j['id'])
        (self.server.audios.audios / latest['mediaKey']).unlink()
        self.assertEqual(requests.get(self.url + path + '/' + j['id'], headers=self.headers, timeout=5).json()['status'], 'processing')
        self.server.audios.poll(j['id'])
        self.assertEqual(self.paid_count('audio'), 1)

    def test_cancel_before_deadline_prevents_both_modalities(self):
        for kind in ('image', 'audio'):
            path, data, j = self.create(kind, 'cancel-' + kind)
            self.server.auto_execution.step()
            response = requests.post(self.url + '/v1/jobs/' + j['id'] + '/cancel', json={}, headers=self.headers, timeout=5)
            self.assertEqual(response.status_code, 200, response.text)
            self.clock += 10
            self.server.auto_execution.step()
            self.assertEqual(self.paid_count(kind), 0)
            self.assertEqual(self.bridge.get(j['id'])['status'], 'cancelled')
            self.assertEqual(requests.get(self.url + path + '/' + j['id'], headers=self.headers, timeout=5).json()['status'], 'failed')

    def test_duplicate_http_id_does_not_prepare_or_submit_twice(self):
        for kind in ('image', 'audio'):
            path, data, j = self.create(kind, 'duplicate-' + kind)
            response = requests.post(self.url + path, json=data, headers={**self.headers, 'Idempotency-Key': j['id']}, timeout=5)
            self.assertEqual(response.status_code, 202, response.text)
            self.server.wait_for_jobs()
            changed = dict(data)
            changed['prompt' if kind == 'image' else 'input'] = 'changed'
            response = requests.post(self.url + path, json=changed, headers={**self.headers, 'Idempotency-Key': j['id']}, timeout=5)
            self.assertEqual(response.status_code, 400)
            response = requests.post(self.url + path, json=data, headers=self.headers, timeout=5)
            self.assertEqual(response.status_code, 409)
        self.assertEqual(self.image_gateway.calls.count('save'), 1)
        self.server.auto_execution.step()
        self.clock += 5
        self.server.auto_execution.step()
        self.server.auto_execution.step()
        self.assertEqual(self.paid_count('image'), 1)
        self.assertEqual(self.paid_count('audio'), 1)

    def test_paused_readiness_is_visible_for_image_and_audio(self):
        def fail(job):
            raise BridgeError('账号核对失败')
        self.image_gateway.check_ready = fail
        self.audio_gateway.check_ready = fail
        for kind in ('image', 'audio'):
            path, data, j = self.create(kind, 'paused-' + kind)
            self.server.auto_execution.step()
            self.clock += 5
            self.server.auto_execution.step()
            result = requests.get(self.url + path + '/' + j['id'], headers=self.headers, timeout=5).json()
            self.assertTrue(result['requires_confirmation'], result)
            self.assertIn('自动执行已暂停', result['message'])
            self.assertIn('账号核对失败', result['message'])
            self.server.auto_execution.step()
            self.assertEqual(self.paid_count(kind), 0)


if __name__ == '__main__':
    unittest.main()
