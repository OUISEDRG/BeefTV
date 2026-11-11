"""HTTP-to-ledger integration with fake upstreams; no real generation calls."""
import hashlib
import errno
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest

import requests
from audio_flow import AudioFlow, SEED, input_payload
from auto_execution import AutoExecution
from bridge import Bridge, BridgeError
from tests.fixtures_budget import FundedBridge as Bridge
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
        self.policy = {'mode': 'automatic'}
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

    def create(self, kind, key, *, beef_task_id=None):
        path = '/image/v1/images' if kind == 'image' else '/audio/v1/audio/tasks'
        data = image_request() if kind == 'image' else {'model': SEED, 'input': '  原文\n保留空白。 '}
        headers = {**self.headers, 'Idempotency-Key': key}
        if beef_task_id is not None:
            headers['X-BeefTV-Task-ID'] = beef_task_id
        response = requests.post(self.url + path, json=data, headers=headers, timeout=5)
        self.assertEqual(response.status_code, 202, response.text)
        self.server.wait_for_jobs()
        job = self.bridge.get(key)
        self.assertEqual(job['status'], 'prepared', job)
        self.assertEqual(job['kind'], kind)
        return path, data, job

    def paid_count(self, kind):
        if kind == 'image': return self.image_gateway.calls.count('submit')
        return sum(isinstance(x, tuple) and x[0] == 'submit' for x in self.audio_gateway.calls)

    def execute_automatic(self):
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

    def test_image_automatic_budgeted_returns_all_results_and_recovers_cache_without_recharge(self):
        path, data, j = self.create('image', 'http-image')
        self.execute_automatic()
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

    def test_audio_automatic_budgeted_returns_mp3_and_byte_range(self):
        path, data, j = self.create('audio', 'http-audio')
        self.execute_automatic()
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

    def test_image_artifacts_ack_are_bound_to_each_verified_output(self):
        path, data, job = self.create('image', 'http-delivery', beef_task_id='beef-owned')
        self.execute_automatic()
        self.image_gateway.status = 3
        self.server.images.poll(job['id'])
        poll = self.url + path + '/' + job['id']
        out = requests.get(poll, headers=self.headers, timeout=5).json()
        self.assertEqual(out['status'], 'succeeded')
        self.assertEqual(len(out['images']), 2)
        artifacts = out['artifacts']
        self.assertEqual([a['outputIndex'] for a in artifacts], [0, 1])
        self.assertEqual(len({a['artifactId'] for a in artifacts}), 2)
        self.assertEqual({a['taskId'] for a in artifacts}, {'beef-owned'})
        self.assertEqual({a['sha256'] for a in artifacts}, {hashlib.sha256(self.image_bytes).hexdigest()})
        replay = requests.post(self.url + path, json=data, timeout=5,
                               headers={**self.headers, 'Idempotency-Key': job['id'], 'X-BeefTV-Task-ID': 'foreign'})
        self.assertEqual(replay.status_code, 409)

        ack_path = self.url + '/v1/jobs/' + job['id'] + '/ack'
        headers = {**self.headers, 'Idempotency-Key': 'ack-first'}
        ack = {key: value for key, value in artifacts[0].items() if key != 'mimeType'}
        ack.update(resourceId='beef-resource-0', acknowledgedAt='2026-11-07T04:15:00Z')
        for field, value in (('taskId', 'foreign'), ('outputIndex', 1), ('sha256', '0'*64),
                             ('sizeBytes', len(self.image_bytes) + 1)):
            rejected = requests.post(ack_path, json={**ack, field: value}, headers=headers, timeout=5)
            self.assertEqual(rejected.status_code, 409, rejected.text)
            self.assertEqual(rejected.json()['error']['code'], 'media_ack_mismatch')
        self.assertEqual(requests.post(ack_path, json=ack, timeout=5).status_code, 401)
        absent = requests.post(self.url + '/v1/jobs/unknown-media-job/ack', json=ack, headers=headers, timeout=5)
        self.assertEqual((absent.status_code, absent.json()['error']['code']), (404, 'media_job_not_found'))
        original = self.server.images.images / out['images'][0]['url'].rsplit('/', 1)[-1]
        original.write_bytes(b'x' * len(self.image_bytes))
        changed = requests.post(ack_path, json=ack, headers=headers, timeout=5)
        self.assertEqual((changed.status_code, changed.json()['error']['code']), (409, 'media_result_unavailable'))
        original.write_bytes(self.image_bytes)
        first = requests.post(ack_path, json=ack, headers=headers, timeout=5)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json(), {'acknowledged': True, 'artifactId': ack['artifactId'], 'cleanupState': 'held'})
        self.assertEqual(requests.post(ack_path, json=ack, headers=headers, timeout=5).json(), first.json())
        self.assertEqual(requests.post(ack_path, json={**ack, 'resourceId': 'other'}, headers=headers, timeout=5).status_code, 409)
        self.assertEqual(requests.get(out['images'][0]['url'], timeout=5).content, self.image_bytes)
        self.assertEqual(requests.get(out['images'][1]['url'], timeout=5).content, self.image_bytes)
        if os.name != 'nt':
            return
        retention = self.bridge.root / 'media-retention.json'
        retention.write_text('{"schemaVersion":1,"retentionSeconds":60}', encoding='utf-8')
        due = time.time() + 120
        self.assertEqual(self.bridge.delivery.cleanup.sweep(now=due), 0)
        second = {key: value for key, value in artifacts[1].items() if key != 'mimeType'}
        second.update(resourceId='beef-resource-1', acknowledgedAt='2026-11-07T04:15:00Z')
        self.assertEqual(requests.post(ack_path, json=second, headers={**headers, 'Idempotency-Key':'ack-second'}, timeout=5).status_code, 200)
        self.assertEqual(self.bridge.delivery.cleanup.sweep(now=due), 2)
        for image in out['images']:
            self.assertEqual(requests.get(image['url'], timeout=5).status_code, 404)
        history = requests.get(poll, headers=self.headers, timeout=5).json()
        self.assertEqual(history['status'], 'succeeded')
        self.assertEqual(history['artifacts'], artifacts)
        self.assertFalse(history.get('images'))
        self.assertEqual([item['cleanupState'] for item in history['deliveryReceipts']], ['deleted', 'deleted'])
        self.assertEqual(requests.get(ack_path, timeout=5).status_code, 401)
        self.assertEqual(requests.get(ack_path, headers=self.headers, timeout=5).json()['deliveryReceipts'],
                         history['deliveryReceipts'])
        self.assertEqual(requests.post(ack_path, json=ack, headers=headers, timeout=5).json(), first.json())
        self.assertEqual(self.bridge.get(job['id'])['status'], 'completed')
        self.assertEqual(self.paid_count('image'), 1)

    def test_pre_submit_cancel_is_rejected_for_both_modalities(self):
        for kind in ('image','audio'):
            path,data,j=self.create(kind,'cancel-'+kind)
            response=requests.post(self.url+'/v1/jobs/'+j['id']+'/cancel',json={},headers=self.headers,timeout=5)
            self.assertEqual(response.status_code,404,response.text)
            self.assertEqual(self.bridge.get(j['id'])['status'],'prepared')
            self.server.auto_execution.step()
            self.assertEqual(self.paid_count(kind),1)
            self.assertEqual(self.bridge.get(j['id'])['status'],'queued')

    def test_original_key_replay_can_attach_legacy_media_without_enabling_cleanup(self):
        path, data, job = self.create('image', 'legacy-unbound-image')
        self.execute_automatic()
        self.image_gateway.status = 3
        self.server.images.poll(job['id'])
        poll = self.url + path + '/' + job['id']
        previous = requests.get(poll, headers=self.headers, timeout=5).json()
        self.assertNotIn('artifacts', previous)
        replay = requests.post(self.url + path, json=data, timeout=5,
                               headers={**self.headers, 'Idempotency-Key':job['id'], 'X-BeefTV-Task-ID':'recovery-task'})
        self.assertEqual(replay.status_code, 202, replay.text)
        attached = requests.get(poll, headers=self.headers, timeout=5).json()
        self.assertEqual(attached['images'], previous['images'])
        for artifact in attached['artifacts']:
            ack = {key: value for key, value in artifact.items() if key != 'mimeType'}
            index = artifact['outputIndex']
            ack.update(resourceId=f'recovered-{index}', acknowledgedAt='2026-11-07T04:15:00Z')
            accepted = requests.post(self.url + '/v1/jobs/' + job['id'] + '/ack', json=ack, timeout=5,
                                     headers={**self.headers, 'Idempotency-Key':f'legacy-ack-{index}'})
            self.assertEqual(accepted.status_code, 200, accepted.text)
        self.bridge.delivery.cleanup.policy.write_text('{"schemaVersion":1,"retentionSeconds":60}', encoding='utf-8')
        self.assertEqual(self.bridge.delivery.cleanup.sweep(now=time.time() + 120), 0)
        self.assertEqual(requests.get(poll, headers=self.headers, timeout=5).json()['images'], previous['images'])
        self.assertEqual(self.paid_count('image'), 1)

    def test_http_ack_and_cleanup_for_video_extension_enhancement_and_audio(self):
        from enhancement import EnhancementFlow, probe_video
        from extension import ExtensionFlow
        from generation import Generation
        from tests.test_bridge import request as video_request
        from tests.test_enhancement import source_request
        from tests.test_enhancement_flow import FakeEnhancer
        from tests.test_extension import FakeGateway as FakeExtension, request as extension_request
        from tests.test_generation import GenerationGateway

        raw_video = (Path(__file__).parent / 'fixtures/sample.mp4').read_bytes()

        def download_video(url, target):
            target.write_bytes(raw_video)
            return probe_video(target)

        video = self.bridge.gateway = GenerationGateway()
        video.response = {'status':3, 'urls':['https://jzinnovation.jxbaobian.cn/result.mp4']}
        extension = FakeExtension()
        enhancement = FakeEnhancer()
        extension.state = enhancement.state = self.audio_gateway.status = 3
        self.server.flow = Generation(self.bridge, download=download_video, gateway=video)
        self.server.extensions = ExtensionFlow(self.bridge, gateway=extension, download=download_video)
        self.server.enhancements = EnhancementFlow(self.bridge, gateway=enhancement, download=download_video)
        routes = (
            ('video', '/v1/videos', video_request(), self.server.flow, raw_video, 'video/mp4', video.calls, 'paid-submit'),
            ('extension', '/extension/v1/videos', extension_request(), self.server.extensions, raw_video, 'video/mp4', extension.calls, None),
            ('enhancement', '/enhancement/v1/videos', source_request(), self.server.enhancements, raw_video, 'video/mp4', enhancement.calls, 'submit'),
            ('audio', '/audio/v1/audio/tasks', {'model':SEED,'input':'fixture audio'}, self.server.audios, self.audio_bytes, 'audio/mpeg', self.audio_gateway.calls, None),
        )
        self.bridge.delivery.cleanup.policy.write_text('{"schemaVersion":1,"retentionSeconds":60}', encoding='utf-8')
        for kind, route, payload, flow, raw, mime, calls, submitted in routes:
            with self.subTest(kind=kind):
                job_id = 'ack-route-' + kind
                created = requests.post(self.url + route, json=payload, timeout=5,
                    headers={**self.headers,'Idempotency-Key':job_id,'X-BeefTV-Task-ID':'beef-' + kind})
                self.assertEqual(created.status_code, 202, created.text)
                self.server.wait_for_jobs()
                self.execute_automatic()
                job = flow.poll(job_id)
                self.assertEqual(job['status'], 'completed', job.get('error'))
                poll = self.url + route + '/' + job_id
                result = requests.get(poll, headers=self.headers, timeout=5).json()
                field = 'audio_url' if kind == 'audio' else 'video_url'
                self.assertEqual(requests.get(result[field], timeout=5).content, raw)
                artifact, = result['artifacts']
                self.assertEqual((artifact['taskId'], artifact['outputIndex'], artifact['mimeType'],
                                  artifact['sha256'], artifact['sizeBytes']),
                                 ('beef-' + kind, 0, mime, hashlib.sha256(raw).hexdigest(), len(raw)))
                ack = {key: value for key, value in artifact.items() if key != 'mimeType'}
                ack.update(resourceId='resource-' + kind, acknowledgedAt='2026-11-07T04:15:00Z')
                response = requests.post(self.url + '/v1/jobs/' + job_id + '/ack', json=ack, timeout=5,
                    headers={**self.headers,'Idempotency-Key':'ack-' + kind})
                self.assertEqual(response.status_code, 200, response.text)
                if os.name == 'nt':
                    self.assertEqual(self.bridge.delivery.cleanup.sweep(now=time.time() + 120), 1)
                    history = requests.get(poll, headers=self.headers, timeout=5).json()
                    self.assertEqual(history['status'], 'succeeded' if kind == 'audio' else 'completed')
                    self.assertEqual(history['artifacts'], [artifact])
                    self.assertNotIn(field, history)
                    self.assertEqual(history['deliveryReceipts'][0]['cleanupState'], 'deleted')
                if kind == 'audio':
                    self.assertEqual(self.paid_count('audio'), 1)
                elif kind == 'extension':
                    self.assertEqual(sum(paid for _, _, paid in calls), 1)
                else:
                    self.assertEqual(calls.count(submitted), 1)

    def test_download_write_and_ack_transaction_failure_preserve_original_job(self):
        path, data, job = self.create('image', 'delivery-faults', beef_task_id='beef-faults')
        self.execute_automatic()
        self.image_gateway.status = 3
        flow = self.server.images
        download = flow.download
        failures = iter((ConnectionError('fixture download interrupted'), OSError(errno.ENOSPC, 'fixture disk full')))

        def fail_download(url, target):
            target.write_bytes(b'partial transfer')
            raise next(failures)

        flow.download = fail_download
        for expected in ('ConnectionError', 'OSError'):
            latest = flow.poll(job['id'])
            self.assertEqual((latest['status'], latest['error']), ('download_failed', expected))
            self.assertEqual(list(flow.images.glob('*.img')), [])
            self.assertEqual(self.paid_count('image'), 1)
        flow.download = download
        self.assertEqual(flow.poll(job['id'])['status'], 'completed')
        out = requests.get(self.url + path + '/' + job['id'], headers=self.headers, timeout=5).json()
        artifact = out['artifacts'][0]
        ack = {key: value for key, value in artifact.items() if key != 'mimeType'}
        ack.update(resourceId='resource-faults', acknowledgedAt='2026-11-07T04:15:00Z')
        target = self.url + '/v1/jobs/' + job['id'] + '/ack'
        headers = {**self.headers,'Idempotency-Key':'fault-ack'}
        with self.bridge.db() as db:
            db.execute("""CREATE TRIGGER fixture_fail_cleanup_insert BEFORE INSERT ON delivery_cleanup
                BEGIN SELECT RAISE(ABORT, 'fixture atomic ACK write failed'); END""")
        failed = requests.post(target, json=ack, headers=headers, timeout=5)
        self.assertEqual((failed.status_code, failed.json()['error']['code']), (500, 'media_ack_unavailable'))
        with self.bridge.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM delivery_acks').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM delivery_cleanup').fetchone()[0], 0)
            db.execute('DROP TRIGGER fixture_fail_cleanup_insert')
        accepted = requests.post(target, json=ack, headers=headers, timeout=5)
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertEqual(requests.get(out['images'][0]['url'], timeout=5).content, self.image_bytes)
        self.assertEqual(self.paid_count('image'), 1)

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
            self.assertFalse(result['requires_confirmation'], result)
            self.assertIn('自动执行已暂停', result['message'])
            self.assertIn('账号核对失败', result['message'])
            self.server.auto_execution.step()
            self.assertEqual(self.paid_count(kind), 0)

    def test_persisted_submission_hold_is_terminal_for_polling_without_submission(self):
        for kind in ('image', 'audio'):
            path, data, job = self.create(kind, 'held-' + kind)
            job['submissionHold'] = 'user_requested'
            self.bridge.update(job['id'], job)
            self.server.auto_execution.step()
            result = requests.get(self.url + path + '/' + job['id'], headers=self.headers, timeout=5).json()
            self.assertEqual(result['status'], 'failed', result)
            self.assertEqual(result['error']['code'], 'submission_held')
            self.assertIs(result['generationSubmitted'], False)
            self.assertIn('暂停', result['message'])
            self.assertEqual(self.paid_count(kind), 0)


if __name__ == '__main__':
    unittest.main()
