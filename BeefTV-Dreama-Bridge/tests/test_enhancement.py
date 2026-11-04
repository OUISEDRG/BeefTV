import base64
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from bridge import BridgeError

try:
    import enhancement as e
except ImportError:
    e=None

TASK='1570a254-f2cf-40ff-97d1-4396d6ed0532'
SOURCE='https://jzinnovation.jxbaobian.cn/resourceBucket/video/source.mp4'

def source_request(model='dreama-enhance-a'):
    data=(Path(__file__).parent/'fixtures/sample.mp4').read_bytes()
    return {'model':model,'prompt':'这段文字不用于增强','duration':5,'aspect_ratio':'9:16','resolution':'480p',
            'videos':['data:video/mp4;base64,'+base64.b64encode(data).decode()]}

def prepared_job(model='dreama-enhance-a'):
    return {'id':'enhance-test','kind':'enhancement','model':model,'duration':5,
            'sourceDuration':4.01,'sourceVideoUrl':SOURCE,'clientRequestId':'beeftv-enhance:unique',
            'identity':{'userId':17921,'teamId':168,'orgId':17},
            'sourceVideo':{'width':320,'height':480,'duration':4.01,'fps':24,'hasAudio':False},
            'status':'submitting','approvedAt':1,'pointsEstimate':5,'approvedPoints':5}

def task_row(job=None,status='COMPLETED'):
    job=job or prepared_job()
    return {'id':TASK,'taskId':TASK,'userId':'17921','type':'video-enhancement-aliyun','status':status,
            'payload':{'clientRequestId':job['clientRequestId'],'videoUrl':SOURCE},
            'resultUrl':'https://jzinnovation.jxbaobian.cn/result.mp4'}

class EnhancementInputTests(unittest.TestCase):
    def setUp(self): self.assertIsNotNone(e,'Enhancement implementation is missing')
    def test_four_models_accept_one_video_and_preserve_its_bytes(self):
        self.assertEqual(set(e.enhancement_models()),{'dreama-enhance-a','dreama-enhance-b','dreama-enhance-c-low','dreama-enhance-c-high'})
        original=(Path(__file__).parent/'fixtures/sample.mp4').read_bytes()
        for model in e.enhancement_models(): self.assertEqual(e.validate_enhancement_request(source_request(model)),original)
    def test_invalid_or_multiple_inputs_are_rejected(self):
        for field,value in [('videos',[]),('videos',source_request()['videos']*2),('videos',['https://example.com/v.mp4']),
                            ('images',['unexpected']),('audios',['unexpected']),('model','wrong')]:
            data=source_request(); data[field]=value
            with self.subTest(field=field),self.assertRaises(BridgeError):e.validate_enhancement_request(data)
    def test_probe_uses_real_video_and_source_duration_for_quote(self):
        meta=e.probe_video(Path(__file__).parent/'fixtures/sample.mp4')
        self.assertEqual((meta['width'],meta['height']),(320,480))
        self.assertAlmostEqual(meta['duration'],1,places=1)
        meta.update(duration=4.01,fps=24)
        self.assertEqual(e.estimate_points(meta,'dreama-enhance-a'),5)
        self.assertEqual(e.estimate_points(meta,'dreama-enhance-c-high'),10)
        meta['fps']=31
        with self.assertRaises(BridgeError):e.estimate_points(meta,'dreama-enhance-c-low')
        meta.update(fps=24,duration=121)
        with self.assertRaises(BridgeError):e.estimate_points(meta,'dreama-enhance-a')
    def test_nonvideo_file_is_rejected_by_actual_probe(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'fake.mp4';path.write_text('<html>not a video</html>')
            with self.assertRaises(BridgeError):e.probe_video(path)

class EnhancementGatewayTests(unittest.TestCase):
    def test_official_status_and_result_url_variants(self):
        gateway=e.EnhancementGateway(); job=prepared_job()
        variants=[{'outputUrl':SOURCE},{'result':{'MediaUrl':SOURCE}},
                  {'result':{'mediaUrl':SOURCE}},{'result':{'outputUrl':SOURCE}},
                  {'result':json.dumps({'MediaUrl':SOURCE})}]
        for fields in variants:
            row=task_row(job); row.pop('resultUrl',None); row['status']='completed'; row.update(fields)
            gateway._request=lambda *a,**kw:row
            result=gateway.task_status(TASK,job)
            self.assertEqual(result['status'],3); self.assertEqual(result['urls'],[SOURCE])
    def setUp(self): self.assertIsNotNone(e,'Enhancement implementation is missing')
    def test_paid_post_requires_confirmed_intent_and_uses_frozen_source(self):
        gateway=e.EnhancementGateway();job=prepared_job()
        with patch.object(gateway,'_request',return_value={'id':TASK}) as http:
            invalid=dict(job,status='prepared')
            with self.assertRaises(BridgeError):gateway.submit_video(invalid)
            http.assert_not_called()
            result=gateway.submit_video(job)
            self.assertEqual(result,{'taskId':TASK})
            args,kwargs=http.call_args
            self.assertEqual(args,('POST','/nestjs/tasks'))
            payload=kwargs['payload']
            self.assertEqual(payload['videoUrl'],SOURCE)
            self.assertEqual(payload['duration'],5)
            self.assertEqual(payload['clientRequestId'],job['clientRequestId'])
            self.assertTrue(payload['enablePointDeduction'])
            self.assertNotIn('storyBoardId',payload)
            self.assertNotIn('prompt',payload)
    def test_status_requires_exact_task_identity_and_source(self):
        gateway=e.EnhancementGateway();job=prepared_job()
        with patch.object(gateway,'_request',return_value=task_row()):
            result=gateway.task_status(TASK,job)
            self.assertEqual(result['status'],3)
            self.assertEqual(result['urls'],['https://jzinnovation.jxbaobian.cn/result.mp4'])
        for field,value in [('taskId','wrong'),('userId','999'),('type','video-enhancement')]:
            row=task_row();row[field]=value
            with patch.object(gateway,'_request',return_value=row),self.assertRaises(BridgeError):gateway.task_status(TASK,job)
        row=task_row();row['payload']['videoUrl']='https://example.com/wrong.mp4'
        with patch.object(gateway,'_request',return_value=row),self.assertRaises(BridgeError):gateway.task_status(TASK,job)
    def test_unknown_submission_looks_up_client_id_without_post(self):
        gateway=e.EnhancementGateway();job=prepared_job()
        with patch.object(gateway,'_request',return_value={'items':[task_row()],'totalCount':1}) as http:
            self.assertEqual(gateway.find_task(job),TASK)
            self.assertTrue(all(call.args[0]=='GET' for call in http.call_args_list))
        row=task_row();row['payload']['clientRequestId']='unrelated'
        with patch.object(gateway,'_request',return_value={'items':[row],'totalCount':1}):
            self.assertIsNone(gateway.find_task(job))
