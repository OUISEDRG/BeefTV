import base64
from io import BytesIO
import json
from pathlib import Path
import queue
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import image_rpc
from codex_rpc import RpcError, isolated_overrides


def png():
    stream = BytesIO()
    Image.new('RGB', (8, 8), '#2468ac').save(stream, format='PNG')
    return stream.getvalue()


def image_event(data=None, **extra):
    return {'method': 'item/completed', 'params': {
        'threadId': 'thread-image-test', 'turnId': 'turn-image-test',
        'item': {'type': 'imageGeneration', 'id': 'image-test', 'status': 'completed',
                 'result': base64.b64encode(png() if data is None else data).decode(),
                 **extra}}}


def turn_event(status='completed', turn_id='turn-image-test', **extra):
    return {'method': 'turn/completed', 'params': {'threadId': 'thread-image-test',
            'turn': {'id': turn_id, 'status': status, **extra}}}


class FakeRpc:
    def __init__(self, events=(), *, image_generation=True, capabilities=True, signed_in=True,
                 account_type='chatgpt', cancelled=None):
        self.events = list(events)
        self.capabilities = capabilities
        self.signed_in = signed_in
        self.account_type = account_type
        self.cancelled = cancelled
        self.thread_id = None
        self.turn_id = None
        self.calls = []
        self.closed = False
        self.cancel_count = 0
        self.factory_flags = None
    def factory(self, executable, cwd, **kwargs):
        self.factory_flags = kwargs
        return self
    def request(self, method, params, **kwargs):
        self.calls.append((method, params))
        if method == 'modelProvider/capabilities/read':
            return {'imageGeneration': self.capabilities}
        if method == 'account/read':
            return {'account': {'type': self.account_type} if self.signed_in else None}
        if method == 'model/list':
            return {'data': self.probe()['models'], 'nextCursor': None}
        raise AssertionError(method)
    def probe(self):
        return {'account': {'signedIn': self.signed_in, 'type': self.account_type}, 'models': [
            {'id': 'hidden', 'hidden': True, 'isDefault': True},
            {'id': 'available-default', 'isDefault': True,
             'inputModalities': ['text', 'image'], 'defaultReasoningEffort': 'high',
             'supportedReasoningEfforts': [{'reasoningEffort': 'low'}, {'reasoningEffort': 'high'}]}]}
    def start_thread(self, model, tools, base, developer):
        self.calls.append(('thread/start', {'model': model, 'tools': tools, 'base': base, 'developer': developer}))
        self.thread_id = 'thread-image-test'
        return self.thread_id
    def start_turn(self, inputs, effort):
        self.calls.append(('turn/start', {'input': inputs, 'effort': effort}))
        self.turn_id = 'turn-image-test'
        return self.turn_id
    def next_event(self, timeout):
        if self.cancelled:
            self.cancelled.set()
        if not self.events:
            raise queue.Empty()
        return self.events.pop(0)
    def cancel(self):
        self.cancel_count += 1
    def close(self):
        self.closed = True


class ImageRpcTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.generated = self.root/'generated_images'
        self.generated.mkdir()
    def tearDown(self):
        self.temp.cleanup()
    def generate(self, fake, **kwargs):
        return image_rpc.generate_image('fake-codex', self.root, 'A blue cube', [],
                                        _rpc_factory=fake.factory, _generated_root=self.generated, **kwargs)
    def test_one_native_image_and_no_second_turn(self):
        fake = FakeRpc([image_event(), image_event()])
        progress = []
        result = self.generate(fake, on_progress=progress.append)
        self.assertEqual(result['images'][0]['bytes'], png())
        self.assertEqual(result['images'][0]['width'], 8)
        self.assertEqual(result['model'], 'available-default')
        self.assertEqual(result['requested_resolution'], 'auto')
        self.assertEqual(result['actual_dimensions'], '8x8')
        self.assertEqual(len([x for x in fake.calls if x[0]=='turn/start']), 1)
        self.assertEqual(fake.events, [image_event()])
        self.assertEqual(fake.factory_flags, {'image_generation': True, 'request_timeout': 15.0})
        self.assertEqual(fake.cancel_count, 1)
        self.assertTrue(fake.closed)
        self.assertEqual([event['phase'] for event in progress], ['starting', 'submitting', 'generating', 'completed'])
        self.assertEqual(progress[1], {'phase': 'submitting', 'threadId': 'thread-image-test'})
        self.assertEqual(progress[2], {'phase': 'generating', 'threadId': 'thread-image-test', 'turnId': 'turn-image-test'})
        turn = next(p for m,p in fake.calls if m=='turn/start')
        self.assertEqual(turn['effort'], 'low')
    def test_native_events_preserve_png_jpeg_and_webp_bytes(self):
        for format, mime in (('PNG', 'image/png'), ('JPEG', 'image/jpeg'), ('WEBP', 'image/webp')):
            with self.subTest(format=format):
                stream = BytesIO()
                Image.new('RGB', (8, 6), '#2468ac').save(stream, format=format)
                data = stream.getvalue()
                encoded = f'data:{mime};base64,' + base64.b64encode(data).decode('ascii')
                event = image_event()
                event['params']['item']['result'] = encoded
                fake = FakeRpc([event])
                result = self.generate(fake)
                self.assertEqual(result['images'][0]['bytes'], data)
                self.assertEqual(result['images'][0]['mime'], mime)
                self.assertEqual(result['actual_dimensions'], '8x6')
                self.assertEqual([method for method, _ in fake.calls].count('turn/start'), 1)

    def test_text_alone_never_counts_as_generated_image(self):
        fake = FakeRpc([{'method':'item/completed','params':{'item':{'type':'agentMessage','text':'Done, see image.png'}}},
                        turn_event()])
        with self.assertRaises(image_rpc.ImageGenerationError) as error:
            self.generate(fake)
        self.assertEqual(error.exception.code, 'image_no_result')
        self.assertTrue(fake.closed)
    def test_usage_limit_does_not_retry(self):
        fake = FakeRpc([image_event(status='failed', failure={'type':'usageLimitExceeded','limitId':'image_gen','resetsAt':123})])
        with self.assertRaises(image_rpc.ImageGenerationError) as error:
            self.generate(fake)
        self.assertEqual(error.exception.code,'image_quota_exceeded')
        self.assertEqual(error.exception.details,{'resetsAt':123})
        self.assertEqual(len([x for x in fake.calls if x[0]=='turn/start']),1)
    def test_structured_native_errors_have_safe_codes_and_details(self):
        cases = [('usageLimitExceeded','image_quota_exceeded'),
                 ('unauthorized','image_auth_required'),
                 ('misalignmentPolicyViolation','image_refused'),
                 ({'responseStreamConnectionFailed':{'httpStatusCode':503}},'image_submission_unknown')]
        for native, code in cases:
            with self.subTest(native=native):
                fake=FakeRpc([{'method':'error','params':{'error':{
                    'message':'private provider text must not escape', 'codexErrorInfo':native},
                    'threadId':'thread-image-test','turnId':'turn-image-test','willRetry':True}}])
                with self.assertRaises(image_rpc.ImageGenerationError) as error:self.generate(fake)
                self.assertEqual(error.exception.code,code)
                self.assertNotIn('private',str(error.exception))
                self.assertNotIn('private',json.dumps(error.exception.details))
                self.assertEqual(len([x for x in fake.calls if x[0]=='turn/start']),1)
                self.assertTrue(fake.closed)
    def test_failed_turn_preserves_structured_auth_classification(self):
        fake=FakeRpc([turn_event('failed', error={'message':'do not expose','codexErrorInfo':'unauthorized'})])
        with self.assertRaises(image_rpc.ImageGenerationError) as error:self.generate(fake)
        self.assertEqual(error.exception.code,'image_auth_required')
        self.assertEqual(error.exception.details,{'nativeErrorInfo':'unauthorized'})
    def test_cancellation_interrupts_and_closes(self):
        cancel = threading.Event()
        delta = {'method': 'item/agentMessage/delta', 'params': {}}
        interrupted = {'method': 'turn/completed', 'params': {'threadId': 'thread-image-test',
                       'turn': {'id': 'turn-image-test', 'status': 'interrupted'}}}
        fake = FakeRpc([delta, interrupted], cancelled=cancel)
        with self.assertRaises(image_rpc.ImageGenerationError) as error:
            self.generate(fake,cancelled=cancel)
        self.assertEqual(error.exception.code,'image_cancelled')
        self.assertEqual(fake.cancel_count,1)
        self.assertTrue(fake.closed)

    def test_cancellation_needs_interrupted_event_for_original_turn(self):
        cancel = threading.Event()
        delta = {'method': 'item/agentMessage/delta', 'params': {}}
        foreign = {'method': 'turn/completed', 'params': {'threadId': 'thread-image-test',
                   'turn': {'id': 'another-turn', 'status': 'interrupted'}}}
        exited = {'method': 'bridge/process/exited', 'params': {}}
        fake = FakeRpc([delta, foreign, exited], cancelled=cancel)
        with self.assertRaises(image_rpc.ImageGenerationError) as error:
            self.generate(fake, cancelled=cancel)
        self.assertEqual(error.exception.code, 'image_submission_unknown')
        self.assertEqual(fake.cancel_count, 1)
        self.assertTrue(fake.closed)
    def test_cancel_during_setup_stops_before_next_rpc_or_inference(self):
        for stage in ('factory','modelProvider/capabilities/read','account/read','model/list','thread/start'):
            with self.subTest(stage=stage):
                cancel=threading.Event()
                fake=FakeRpc([image_event()])
                factory=fake.factory
                request=fake.request
                start_thread=fake.start_thread
                def wrapped_factory(*args,**kwargs):
                    result=factory(*args,**kwargs)
                    if stage=='factory':cancel.set()
                    return result
                def wrapped_request(method,params,**kwargs):
                    result=request(method,params,**kwargs)
                    if method==stage:cancel.set()
                    return result
                def wrapped_thread(*args,**kwargs):
                    result=start_thread(*args,**kwargs)
                    if stage=='thread/start':cancel.set()
                    return result
                fake.request=wrapped_request
                fake.start_thread=wrapped_thread
                with self.assertRaises(image_rpc.ImageGenerationError) as error:
                    image_rpc.generate_image('fake',self.root,'A blue cube',[],cancelled=cancel,
                                              _rpc_factory=wrapped_factory,_generated_root=self.generated)
                self.assertEqual(error.exception.code,'image_cancelled')
                self.assertFalse(any(method=='turn/start' for method,_ in fake.calls))
                if stage=='factory':self.assertEqual(fake.calls,[])
                else:self.assertEqual(fake.calls[-1][0],stage)
                self.assertTrue(fake.closed)
    def test_timeout_interrupts_without_retry(self):
        fake = FakeRpc()
        with self.assertRaises(image_rpc.ImageGenerationError) as error:
            self.generate(fake,timeout=.01)
        self.assertEqual(error.exception.code,'image_timeout')
        self.assertTrue(fake.closed)
        self.assertEqual(len([x for x in fake.calls if x[0]=='turn/start']),1)
    def test_saved_file_is_confined_to_generated_root(self):
        good = self.generated/'thread'/'image.png'; good.parent.mkdir(); good.write_bytes(png())
        result = self.generate(FakeRpc([image_event(result='',fileId='local-file',savedPath=str(good))]))
        self.assertEqual(result['images'][0]['bytes'],png())
        bad = self.root/'outside.png'; bad.write_bytes(png())
        with self.assertRaises(image_rpc.ImageGenerationError) as error:
            self.generate(FakeRpc([image_event(result='',savedPath=str(bad))]))
        self.assertEqual(error.exception.code,'image_output_unavailable')
    def test_file_id_is_not_silently_treated_as_image_bytes(self):
        with self.assertRaises(image_rpc.ImageGenerationError) as error:
            self.generate(FakeRpc([image_event(result='',fileId='file-abc')]))
        self.assertEqual(error.exception.code,'image_output_unavailable')
    def test_invalid_or_oversized_image_is_rejected(self):
        for data in [b'not an image', b'\x89PNG\r\n\x1a\n'+b'0'*32]:
            with self.assertRaises(image_rpc.ImageGenerationError):
                self.generate(FakeRpc([image_event(data)]))
        with patch.object(image_rpc,'MAX_IMAGE_BYTES',8):
            with self.assertRaises(image_rpc.ImageGenerationError) as error:
                self.generate(FakeRpc([image_event()]))
            self.assertEqual(error.exception.code,'image_too_large')
    def test_capability_or_signin_failure_precedes_inference(self):
        for fake in [FakeRpc(capabilities=False),FakeRpc(signed_in=False),FakeRpc(account_type='apiKey')]:
            with self.assertRaises(image_rpc.ImageGenerationError):
                self.generate(fake)
            self.assertFalse(any(m=='turn/start' for m,p in fake.calls))
            self.assertTrue(fake.closed)
    def test_reference_input_and_transparency_are_passed_to_one_tool_request(self):
        reference=self.root/'reference.png';reference.write_bytes(png())
        fake=FakeRpc([image_event()])
        image_rpc.generate_image('fake',self.root,'Edit only the background',[reference],aspect_ratio='9:16',transparent=True,
                                  _rpc_factory=fake.factory,_generated_root=self.generated)
        turn=next(p for m,p in fake.calls if m=='turn/start')
        self.assertEqual(turn['input'][0],{'type':'localImage','path':str(reference.resolve())})
        text=turn['input'][-1]['text']
        self.assertIn('num_last_images_to_include=1',text)
        self.assertIn('9:16',text)
        self.assertIn('transparent_background to true',text)
    def test_resolution_targets_use_prompt_and_report_measured_dimensions(self):
        for resolution, pixels in [('1k',1024),('2k',2048),('4k',4096),('8k',8192)]:
            with self.subTest(resolution=resolution):
                fake=FakeRpc([image_event()])
                result=self.generate(fake,resolution=resolution,aspect_ratio='9:16')
                turn=next(params for method,params in fake.calls if method=='turn/start')
                text=turn['input'][-1]['text']
                self.assertIn(f'longest edge of {pixels} pixels',text)
                self.assertIn('9:16',text)
                self.assertIn('not a guaranteed supported size',text)
                self.assertIn('Do not resize, upscale, downscale',text)
                self.assertEqual(result['requested_resolution'],resolution)
                self.assertEqual(result['actual_dimensions'],'8x8')
                self.assertEqual(result['images'][0]['bytes'],png())
    def test_auto_resolution_preserves_original_prompt_input(self):
        default=image_rpc._request_input('A blue cube',[],'1:1',False)
        explicit=image_rpc._request_input('A blue cube',[],'1:1',False,'auto')
        self.assertEqual(default,explicit)
        self.assertNotIn('longest edge',explicit[-1]['text'])
        self.assertTrue(explicit[-1]['text'].endswith('Creative prompt:\nA blue cube'))
    def test_invalid_resolution_is_rejected_before_native_session(self):
        for resolution in ('3k','2K','',None,2048,[]):
            with self.subTest(resolution=resolution):
                fake=FakeRpc([image_event()])
                with self.assertRaises(image_rpc.ImageGenerationError) as error:
                    self.generate(fake,resolution=resolution)
                self.assertEqual(error.exception.code,'invalid_request')
                self.assertIsNone(fake.factory_flags)
    def test_output_pixel_bound_allows_8192_square_only_within_limit(self):
        class Picture:
            width=8192
            height=8192
            @property
            def size(self):return (self.width,self.height)
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def verify(self):pass
        picture=Picture()
        with patch.object(image_rpc.Image,'open',return_value=picture):
            result=image_rpc._image_bytes(image_event()['params']['item'],self.generated)
            self.assertEqual((result['width'],result['height']),(8192,8192))
            picture.width=8193
            with self.assertRaises(image_rpc.ImageGenerationError) as error:
                image_rpc._image_bytes(image_event()['params']['item'],self.generated)
            self.assertEqual(error.exception.code,'invalid_image')
    def test_cross_thread_images_and_external_tools_are_not_accepted(self):
        wrong=image_event();wrong['params']['threadId']='another-thread'
        fake=FakeRpc([wrong,turn_event()])
        with self.assertRaises(image_rpc.ImageGenerationError) as error:self.generate(fake)
        self.assertEqual(error.exception.code,'image_no_result')
        fake=FakeRpc([{'id':1,'method':'item/tool/call','params':{'tool':'exec_command'}}])
        with self.assertRaises(image_rpc.ImageGenerationError) as error:self.generate(fake)
        self.assertEqual(error.exception.code,'unexpected_tool')

    def test_image_with_missing_turn_identity_is_not_delivered(self):
        event = image_event()
        del event['params']['turnId']
        fake = FakeRpc([event, turn_event()])
        with self.assertRaises(image_rpc.ImageGenerationError) as error:
            self.generate(fake)
        self.assertEqual(error.exception.code, 'image_no_result')

    def test_foreign_completion_and_unscoped_error_cannot_end_original_turn(self):
        unexpected = [turn_event(turn_id='foreign'), turn_event('failed', turn_id='foreign'),
                      {'method': 'error', 'params': {'error': {'codexErrorInfo': 'unauthorized'}}},
                      {'method': 'turn/completed', 'params': {'threadId': 'thread-image-test', 'turn': ['malformed']}}]
        for event in unexpected:
            with self.subTest(event=event):
                result = self.generate(FakeRpc([event, image_event()]))
                self.assertEqual(result['images'][0]['bytes'], png())

    def test_malformed_turn_acceptance_is_unknown_without_retry(self):
        fake = FakeRpc()
        def malformed(*args, **kwargs):
            fake.calls.append(('turn/start', {}))
            raise KeyError('turn')
        fake.start_turn = malformed
        with self.assertRaises(image_rpc.ImageGenerationError) as error:
            self.generate(fake)
        self.assertEqual(error.exception.code, 'image_submission_unknown')
        self.assertEqual([method for method, _ in fake.calls].count('turn/start'), 1)
        self.assertTrue(fake.closed)

    def test_transport_failure_after_submission_is_unknown_without_retry(self):
        for stage in ('turn/start', 'process-exit'):
            with self.subTest(stage=stage):
                fake = FakeRpc([{'method': 'bridge/process/exited', 'params': {}}])
                def lose_response(*args, **kwargs):
                    fake.calls.append(('turn/start', {}))
                    raise RpcError('lost response')
                if stage == 'turn/start':
                    fake.start_turn = lose_response
                with self.assertRaises(image_rpc.ImageGenerationError) as error:
                    self.generate(fake)
                self.assertEqual(error.exception.code, 'image_submission_unknown')
                self.assertEqual([method for method, _ in fake.calls].count('turn/start'), 1)
                self.assertTrue(fake.closed)
    def test_image_opt_in_preserves_other_isolation_and_default_behavior(self):
        env={'CODEX_HOME':str(self.root)}
        default=dict(x.split('=',1) for x in isolated_overrides('catalog',env))
        image=dict(x.split('=',1) for x in isolated_overrides('catalog',env,image_generation=True))
        self.assertEqual(default['features.image_generation'],'false')
        self.assertEqual(image['features.image_generation'],'true')
        image['features.image_generation']='false'
        self.assertEqual(default,image)


if __name__=='__main__':unittest.main()
