import json
import os
import sqlite3
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from bridge import Bridge, BridgeError, DreamaGateway
from dreama_cli import DreamaCliClient
from dreama_image import ImageGateway
from image_flow import ImageFlow
from audio_flow import AudioFlow, AudioGateway
from enhancement import EnhancementFlow, EnhancementGateway
from extension import ExtensionFlow, ExtensionGateway
from generation import Generation
from tests.fixtures_budget import funded_policy


def cli_response(payload=None, *, exit_code=0, stdout=None):
    return SimpleNamespace(returncode=exit_code, stdout=stdout if stdout is not None else
                           json.dumps({'ok': True, 'result': payload}), stderr='secret-stderr')


def http_response(payload, *, status=200):
    return SimpleNamespace(status_code=status, json=lambda: {'code': '0', 'result': payload})


class TransportBoundaryTests(unittest.TestCase):
    def test_official_quote_transports_preserve_decimal_digits(self):
        from decimal import Decimal
        from requests import Response
        from dreama_compat import http

        exact = '1.000000000000000000000000001'
        client = DreamaCliClient()
        raw = '{"ok":true,"result":{"token":' + exact + '}}'
        with patch.object(client, 'check_version'), patch.object(subprocess, 'run', return_value=cli_response(stdout=raw)):
            quote = client.run('point', 'calc', '--kind', 'video', '--project-id', 7,
                               '--llm', 'fixture', '--duration', 5)['token']
        self.assertEqual(Decimal(str(quote)), Decimal(exact))

        response = Response()
        response.status_code = 200
        response._content = ('{"code":"0","result":{"token":' + exact + '}}').encode()
        job = {'id':'fixture','duration':5,'modelSnapshot':{'eid':'fixture'},'sourceVideo':{'duration':4}}
        with (patch.object(http, 'credential_token', return_value='fixture'),
              patch.object(http.requests, 'post', return_value=response)):
            quote = ExtensionGateway().estimate_extension(job)
        self.assertEqual(Decimal(str(quote)), Decimal(exact))

    def test_allowed_cli_command_cannot_override_runtime_or_add_positionals(self):
        gateway = DreamaGateway()
        bad = [
            ('--id', 7, '--server=https://invalid.example'),
            ('--id', 7, '--raw'),
            ('--id', 7, '--config', 'other.json'),
            ('--id', 7, '--timeout=0s'),
            ('--id', 7, '--yes'),
            ('--id', 7, '--id', 8),
            ('--id', 'zero'),
            ('--id', 7, 'extra'),
            ('--id',),
        ]
        with patch.object(subprocess, 'run') as spawn:
            for args in bad:
                with self.subTest(args=args), self.assertRaises(BridgeError):
                    gateway.run('project', 'get', *args)
            spawn.assert_not_called()

    def test_generic_compat_calls_cannot_submit(self):
        cases = [
            lambda: AudioGateway()._request('POST', payload={}),
            lambda: EnhancementGateway()._request('POST', '/nestjs/tasks', payload={}),
            lambda: ExtensionGateway()._request('/storyboard/api/produceVideo', {}, paid=True),
        ]
        for call in cases:
            with self.subTest(call=call), self.assertRaises(BridgeError):
                call()

    def test_forged_cli_job_fields_do_not_authorize_paid_process(self):
        job = {'id': 'forged', 'kind': 'video', 'status': 'submitting',
               'submissionIntentId': 'a' * 32, 'reservedPoints': '3',
               'pointsEstimate': '3', 'storyboardId': 27, 'duration': 5,
               'model': 'test-model', 'modelSnapshot': {'id': 'test-model', 'resolution': '720P'}}
        with patch.object(subprocess, 'run') as spawn:
            with self.assertRaises(BridgeError):
                DreamaGateway().submit_video(job)
            with self.assertRaises(BridgeError):
                DreamaGateway()._invoke('video', 'produce', '--yes')
            spawn.assert_not_called()

    def test_version_and_environment_and_verbatim_values(self):
        client = DreamaCliClient()
        with patch.dict(os.environ, {'DREAMA_SERVER': 'https://invalid.example'}), \
                patch.object(subprocess, 'run', side_effect=[
                    cli_response(stdout='Dreama cli 1.0.5 env=prod endpoint=https://jzzm.duanju.com/gw'),
                    cli_response({'prompt': 'ok'}), cli_response({'prompt': 'ok'}),
                ]) as run:
            for _ in range(2):
                client.run('prompt', 'assemble', '--project-id', 7, '--kind', 'video',
                           '--prompt', '--server=literal\n镜头 & “动作”', '--ref', 'first', '--ref', 'second')
            self.assertEqual(run.call_count, 3)
            args, opts = run.call_args
            self.assertEqual(args[0][1:], ['prompt', 'assemble', '--project-id=7', '--kind=video',
                             '--prompt=--server=literal\n镜头 & “动作”', '--ref=first', '--ref=second', '--timeout=90s'])
            self.assertFalse(opts['shell'])
            self.assertEqual(opts['timeout'], 100)
            self.assertNotIn('DREAMA_SERVER', opts['env'])
        with patch.object(subprocess, 'run', return_value=cli_response(stdout='Dreama cli 1.0.4 env=prod')) as run:
            with self.assertRaisesRegex(BridgeError, '版本'):
                DreamaCliClient().run('auth', 'whoami')
            self.assertEqual(run.call_count, 1)

    def test_cli_errors_are_mapped_without_logging_provider_payload_or_retrying(self):
        client = DreamaCliClient()
        errors = [
            (cli_response(exit_code=10, stdout='secret-token'), 'action=login'),
            (cli_response(stdout='["secret-token"]'), 'JSON'),
            (subprocess.TimeoutExpired(['secret-token'], 1), '超时'),
        ]
        with patch.object(client, 'check_version'):
            for response, reason in errors:
                with self.subTest(reason=reason), patch.object(subprocess, 'run', side_effect=[response]) as run:
                    with self.assertRaisesRegex(BridgeError, reason) as error:
                        client.run('auth', 'whoami')
                    self.assertNotIn('secret', str(error.exception))
                    self.assertEqual(run.call_count, 1)

    def test_compat_response_parse_and_verb_whitelist(self):
        from dreama_compat import http
        gateway = DreamaGateway()
        with patch.object(http, 'credential_token', return_value='test'), \
                patch.object(http.requests, 'post', return_value=http_response({'item': {'taskId': 7}})) as post:
            result = ExtensionGateway()._request('/storyboard/api/historyList', {})
            self.assertEqual(result, {'taskId': 7})
            self.assertFalse(post.call_args.kwargs['allow_redirects'])
            for path in ('/storyboard/api/produceVideo', '/nestjs/tasks', http.AUDIO_TASK, 'https://other.example'):
                with self.assertRaises(BridgeError):
                    http.request(gateway, 'POST', path)
            self.assertEqual(post.call_count, 1)


class NativePaidBoundaryTests(unittest.TestCase):
    ROUTES = {
        'video': (Generation, DreamaGateway, 'video produce', 701),
        'image': (ImageFlow, ImageGateway, 'image produce', 702),
        'extension': (ExtensionFlow, ExtensionGateway, '/storyboard/api/produceVideo', 703),
        'enhancement': (EnhancementFlow, EnhancementGateway, '/nestjs/tasks', 'f7c333d0-9178-4c41-89cf-e5715e1b83b9'),
        'audio': (AudioFlow, AudioGateway, '/nestjs/ai-lab/voiceovers/task', 'f7c333d0-9178-4c41-89cf-e5715e1b83ba'),
    }

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name
        for target, value in [
            ('dreama_cli.DreamaCliClient.check_version', None),
            ('dreama_compat.http.credential_token', 'test-only'),
        ]:
            patcher = patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def prepared(self, kind, *, funded=True, key=None, **extra):
        flow_class, gateway_class, _, _ = self.ROUTES[kind]
        bridge = Bridge(self.root, budget_policy=funded_policy() if funded else None)
        gateway = gateway_class()
        gateway.identity = lambda: {'userId': 17}
        key = key or kind
        model = 'dreama-enhance-c-high' if kind == 'enhancement' else 'test-model'
        job = {'id': key, 'kind': kind, 'status': 'prepared', 'requestDigest': 'digest-' + key,
               'quoteId': 'quote', 'pointsEstimate': 2, 'duration': 5, 'model': model,
               'modelSnapshot': {'id': model, 'eid': model, 'resolution': '720P', 'enabled': True},
               'storyboardId': 97, 'aspect_ratio': '16:9', 'resolution': '3K',
               'projectId': 91, 'seriesId': 92,
               'sourceVideoUrl': 'https://jzinnovation.jxbaobian.cn/test.mp4',
               'sourceVideo': {'duration': 4, 'width': 640, 'height': 360},
               'clientRequestId': 'fixture-request', 'identity': {'userId': 17},
               'taskType': 'voiceover-generate-doubao', 'taskName': 'fixture-audio',
               'prompt': '镜头与声音', 'audioConfig': {}, 'references': [], 'promptReferenceIndexes': [], **extra}
        with bridge.db() as db:
            db.execute('INSERT INTO jobs VALUES (?,?,?)', (key, job['requestDigest'], json.dumps(job)))
        bridge.update(key, job)
        flow = flow_class(bridge, gateway=gateway)
        flow.check_ready = lambda job: None
        flow.estimate_job = lambda job: 2
        return flow, job

    def test_all_five_native_routes_block_unconfigured_budget_even_via_approve(self):
        with patch.object(subprocess, 'run') as cli, patch('dreama_compat.http.requests.post') as http:
            for kind in self.ROUTES:
                with self.subTest(kind=kind):
                    flow, job = self.prepared(kind, funded=False)
                    with self.assertRaises(BridgeError):
                        flow.approve(job['id'], job['quoteId'], job['pointsEstimate'])
                    self.assertIsNone(flow.bridge.tasks.get(job['id'])['billing'])
            cli.assert_not_called()
            http.assert_not_called()

    def test_all_five_routes_send_once_only_with_the_committed_intent(self):
        for kind, (_, _, route, task_id) in self.ROUTES.items():
            with self.subTest(kind=kind):
                flow, job = self.prepared(kind)
                native = flow.gateway.submit_video
                paid_calls = []

                def intercept(value):
                    self.assertEqual(flow.bridge.tasks.get(kind)['state'], 'submission_intent')
                    with self.assertRaises(BridgeError):
                        native({**value, 'storyboardId': 98})
                    receipt = native(value)
                    with self.assertRaises(BridgeError):
                        native(value)
                    return receipt

                def cli(args, **opts):
                    paid_calls.append(' '.join(args[1:3]))
                    self.assertIn('--project-id=91', args)
                    self.assertIn('--series-id=92', args)
                    if kind == 'image':
                        self.assertIn('--image-resolution=3K', args)
                        self.assertIn('--proportion=16:9', args)
                    return cli_response({'taskId': task_id})

                def post(url, **opts):
                    paid_calls.append(url.removeprefix('https://jzzm.duanju.com/gw'))
                    with flow.bridge.db() as db:
                        db.execute('BEGIN IMMEDIATE')
                    return http_response({'taskId': task_id})

                with patch.object(flow.gateway, 'submit_video', side_effect=intercept), \
                        patch.object(subprocess, 'run', side_effect=cli), \
                        patch('dreama_compat.http.requests.post', side_effect=post):
                    self.assertEqual(flow.submit(job['id'])['taskId'], task_id)
                self.assertEqual(paid_calls, [route])
                with self.assertRaises(BridgeError):
                    flow.submit(job['id'])

    def test_cli_or_receipt_failure_restarts_read_only_with_null_budget(self):
        for outcome in ('timeout', 'empty', 'cli_crash', 'process_exit', 'receipt_lock', 'receipt_full'):
            with self.subTest(outcome=outcome):
                flow, job = self.prepared('video', key=outcome)
                flow.reconcile_task_id = lambda job: None
                failure = {'timeout': subprocess.TimeoutExpired(['fixture'], 1),
                           'empty': cli_response(None),
                           'cli_crash': cli_response(exit_code=0xC0000005, stdout=''),
                           'process_exit': SystemExit(9),
                           'receipt_lock': cli_response({'taskId': 701}),
                           'receipt_full': cli_response({'taskId': 701})}[outcome]
                with patch.object(subprocess, 'run', side_effect=[failure]) as cli:
                    if outcome in ('receipt_lock', 'receipt_full'):
                        error = 'locked' if outcome == 'receipt_lock' else 'database or disk is full'
                        with patch.object(flow.bridge.tasks, 'record_receipt', side_effect=sqlite3.OperationalError(error)):
                            with self.assertRaises(BridgeError):
                                flow.submit(job['id'])
                    else:
                        with self.assertRaises(SystemExit if outcome == 'process_exit' else BridgeError):
                            flow.submit(job['id'])
                    cli.assert_called_once()
                bridge = Bridge(self.root)
                durable = bridge.get(job['id'])
                self.assertIn(durable['status'], ('submitting', 'submission_unknown'))
                with patch.object(subprocess, 'run') as cli:
                    with self.assertRaises(BridgeError):
                        flow.gateway.submit_video({**durable, 'status': 'submitting'})
                    cli.assert_not_called()
                flow = Generation(bridge, gateway=flow.gateway, download=self.download)
                flow.reconcile_task_id = lambda job: 701
                with patch.object(subprocess, 'run', return_value=cli_response(
                        {'items': [{'taskId': 701, 'status': 3, 'urls': ['https://example.invalid/test.mp4']}]})) as cli:
                    self.assertEqual(flow.poll(job['id'])['status'], 'completed')
                    self.assertEqual(cli.call_args.args[0][1:3], ['task', 'status'])

    def test_existing_tasks_use_native_read_routes_and_deliver_without_budget(self):
        from dreama_compat.audio import input_payload
        from dreama_compat.enhancement import enhancement_models
        cdn = 'https://jzinnovation.jxbaobian.cn/fixture/'
        for kind, (flow_class, _, _, task_id) in self.ROUTES.items():
            with self.subTest(kind=kind):
                old, job = self.prepared(kind)
                durable = old.bridge.tasks.reserve_intent(job, 2, funded_policy())
                old.bridge.tasks.record_receipt(job['id'], task_id)
                job.update(taskId=task_id, status='queued', generationSubmitted=True)
                old.bridge.update(job['id'], job)
                bridge = Bridge(self.root)
                flow = flow_class(bridge, gateway=old.gateway, download=self.download)
                flow.finalize_video = lambda job, path, meta: meta
                urls = [cdn + 'a.png', cdn + 'b.png'] if kind == 'image' else [cdn + 'video.mp4']
                rows = {
                    'enhancement': {'taskId': task_id, 'userId': '17', 'status': 'SUCCESS', 'type':
                                    enhancement_models()['dreama-enhance-c-high']['type'],
                                    'payload': {'clientRequestId': job['clientRequestId'],
                                                'videoUrl': job['sourceVideoUrl']}, 'outputUrl': urls[0]},
                    'audio': {'total': 1, 'tasks': [{'taskId': task_id, 'userId': '17', 'status': 'SUCCESS',
                                'taskType': job['taskType'], 'taskName': job['taskName'],
                                'inputPayload': input_payload(job), 'outputPayload': {'resourceUrl': cdn + 'voice.mp3'}}]},
                }
                with patch.object(subprocess, 'run', return_value=cli_response(
                        {'items': [{'taskId': task_id, 'status': 3, 'urls': urls}]})) as cli, \
                        patch('dreama_compat.http.requests.request', return_value=http_response(rows.get(kind))) as read, \
                        patch('dreama_compat.http.requests.post') as post:
                    delivered = flow.poll(job['id'])
                    self.assertEqual(delivered['status'], 'completed', delivered.get('error'))
                    self.assertEqual(bridge.tasks.get(job['id'])['billing']['intentId'], durable['intentId'])
                    self.assertFalse(bridge.budget_is_configured())
                    post.assert_not_called()
                    if kind in rows:
                        cli.assert_not_called()
                        self.assertEqual(read.call_args.args[0], 'GET')
                    else:
                        read.assert_not_called()
                        self.assertEqual(cli.call_args.args[0][1:3], ['task', 'status'])
                    if kind == 'image':
                        self.assertEqual(delivered['upstreamImageUrls'], urls)
                        self.assertEqual(len(delivered['mediaKeys']), 2)

    @staticmethod
    def download(url, path):
        path.write_bytes(b'fixture')
        return {'bytes': 7}
