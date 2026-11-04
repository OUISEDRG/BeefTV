import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler

from completion_bridge import BridgeError, CompletionBridge
from server import create_server


class FakeJobs:
    active_count = 2

    def __init__(self):
        self.calls = []

    def create(self, payload, key):
        self.calls.append(('create', payload, key))
        if not key:
            raise BridgeError('Missing idempotency key', 400, 'idempotency_key_required')
        return {'id': 'image-123', 'status': 'queued', 'images': []}

    def get(self, job_id):
        self.calls.append(('get', job_id))
        return {'id': job_id, 'status': 'succeeded', 'images': [{'dataUrl': 'data:image/png;base64,eA=='}]}

    def cancel(self, job_id):
        self.calls.append(('cancel', job_id))
        return {'id': job_id, 'status': 'cancelled', 'images': []}


class ImageHttpTests(unittest.TestCase):
    def setUp(self):
        def no_inference():
            raise AssertionError('Image HTTP routes must not call the text model')
        self.bridge = CompletionBridge(no_inference, ['test-text-model'])
        self.jobs = FakeJobs()
        self.http = create_server(self.bridge, 'local-test-secret', port=0, image_jobs=self.jobs)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.http.server_port}'
        self.opener = build_opener(ProxyHandler({}))

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.bridge.close()

    def request(self, path, method='GET', data=None, headers=None, auth=True):
        h = {'Content-Type': 'application/json'}
        if auth:
            h['Authorization'] = 'Bearer local-test-secret'
        h.update(headers or {})
        request = Request(self.url + path, data=json.dumps(data).encode() if data is not None else None,
                          method=method, headers=h)
        with self.opener.open(request, timeout=3) as response:
            return response.status, json.load(response)

    def test_submit_poll_cancel_preserve_native_task_and_idempotency_key(self):
        body = {'model': 'codex-image', 'prompt': 'a cup', 'images': [], 'aspectRatio': '1:1'}
        code, submitted = self.request('/v1/images/jobs', 'POST', body, {'Idempotency-Key': 'stable-task-key'})
        self.assertEqual(code, 202)
        self.assertEqual(submitted['status'], 'queued')
        self.assertEqual(self.jobs.calls[0], ('create', body, 'stable-task-key'))
        self.assertEqual(self.request('/v1/images/jobs/image-123')[1]['status'], 'succeeded')
        self.assertEqual(self.request('/v1/images/jobs/image-123', 'DELETE')[1]['status'], 'cancelled')

    def test_all_image_routes_require_auth_and_reject_cross_origin(self):
        for method, path in [('POST', '/v1/images/jobs'), ('GET', '/v1/images/jobs/image-123'),
                             ('DELETE', '/v1/images/jobs/image-123')]:
            with self.assertRaises(HTTPError) as error:
                self.request(path, method, {}, auth=False)
            self.assertEqual(error.exception.code, 401)
            with self.assertRaises(HTTPError) as error:
                self.request(path, method, {}, {'Origin': 'https://outside.example'})
            self.assertEqual(error.exception.code, 403)
        self.assertEqual(self.jobs.calls, [])

    def test_health_accounts_for_image_jobs_without_submitting_work(self):
        _, health = self.request('/health', auth=False)
        self.assertTrue(health['imageGeneration'])
        self.assertEqual(health['activeImageJobs'], 2)
        self.assertEqual(health['activeSessions'], 2)
        self.assertEqual(self.jobs.calls, [])

    def test_invalid_body_and_missing_key_are_explicit_errors(self):
        with self.assertRaises(HTTPError) as error:
            self.request('/v1/images/jobs', 'POST', ['not', 'object'])
        self.assertEqual(error.exception.code, 400)
        with self.assertRaises(HTTPError) as error:
            self.request('/v1/images/jobs', 'POST', {'prompt': 'cup'})
        self.assertEqual(error.exception.code, 400)
        self.assertEqual(json.load(error.exception)['error']['code'], 'idempotency_key_required')


if __name__ == '__main__':
    unittest.main()
