import base64
import copy
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from bridge import Bridge, BridgeError, DreamaGateway
from tests.fixtures_budget import FundedBridge as Bridge
import model_catalog
import server
from tests.test_bridge import request as image_request
from tests.test_verbatim_prompt import official_tag
from tests.test_video_audio_references import OfflineAudioGateway, model, data_url
from video_audio_references import ordered_references


def video_url(raw):
    return 'data:video/mp4;base64,' + base64.b64encode(raw).decode()


def metadata(path):
    raw=Path(path).read_bytes()
    return {'duration':4.0,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),
            'width':640,'height':360,'codec':'mp3'}


class MediaGateway(OfflineAudioGateway):
    def run(self,*args):
        if args[:2]==('prompt','assemble'):
            return {'mode':'drama','prompt':'<p>'+official_tag(200)+'</p>'}
        return super().run(*args)

    def upload_video_reference(self,path,meta):
        self.assert_metadata(path,meta)
        self.writes.append(('upload-video',copy.deepcopy(meta)))
        return 'https://jzinnovation.jxbaobian.cn/test/video/'+meta['sha256']+'.mp4'

    def upload_audio_reference(self,path,meta):
        self.assert_metadata(path,meta)
        self.writes.append(('upload-audio',copy.deepcopy(meta)))
        return 'https://jzinnovation.jxbaobian.cn/test/audio/'+meta['sha256']+'.mp3'

    @staticmethod
    def assert_metadata(path,meta):
        if metadata(path)['sha256']!=meta['sha256']:
            raise AssertionError('Metadata must describe the exact uploaded file')

    def upload_image(self,raw,name):
        self.writes.append(('upload-image',name))
        return 'https://jzinnovation.jxbaobian.cn/test/image/input.png'

    def create_reference(self,name,url):
        self.writes.append(('bind-image',name))
        return {'imageId':200,'name':name,'url':url}


class MultimodalPreparationTests(unittest.TestCase):
    def setUp(self):
        self.limits=model(videoMin=0,videoMax=3,videoSizeMax=200,videoDurationMin=2,
            videoDurationMax=30,totalVideoDurationMax=30,totalAudioDurationMax=30)['limits']
        self.row={'eid':'test-multimodal-eid','name':'Arbitrary catalog model','alias':'720p','isExternal':0,
            'modelLimit':json.dumps(self.limits),'videoTokens':json.dumps([{'duration':5}])}
        for patcher in (patch.object(model_catalog,'LIVE_ITEMS',[self.row]),
                        patch('audio_flow.probe_audio',side_effect=metadata),
                        patch('enhancement.probe_video',side_effect=metadata),
                        patch('requests.sessions.Session.request',side_effect=AssertionError('No external network permitted'))):
            patcher.start(); self.addCleanup(patcher.stop)
        self.data={**image_request(),'model':self.row['eid'],'resolution':'720p',
            'prompt':'  @视频2 后接 @音频1，参考 @图片1\n@视频1 和 @音频2，再次 @视频2 & <原文>。 ',
            'audios':[data_url(b'audio-a'),data_url(b'audio-b')],
            'videos':[video_url(b'video-a'),video_url(b'video-b')],
            'dreama':{'schemaVersion':1,'canvasId':'canvas','nodeId':'node','projectId':91,'seriesId':92,
                'promptMode':'drama','aspectRatio':'9:16','bindingRevision':1}}

    def test_mixed_references_prepare_read_back_and_keep_original_order(self):
        gateway=MediaGateway(self.row)
        with tempfile.TemporaryDirectory() as root:
            bridge=Bridge(root,gateway);job=bridge.prepare(self.data,'mixed')
            self.assertEqual(job['status'],'prepared')
            self.assertFalse(job['generationSubmitted'])
            self.assertEqual(job['videoReferenceSnapshot'],job['videoReferences'])
            self.assertEqual(job['referenceImageIds'],[200])
            self.assertEqual(job['prompt'],self.data['prompt'])
            self.assertEqual(job['promptReferenceOrder'],[['video',1],['audio',0],['image',0],['video',0],['audio',1],['video',1]])
            expected=ordered_references(self.data['prompt'],job['references'],job['audioReferences'],job['videoReferences'])
            DreamaGateway.verify_reference_tags(gateway.board['videoPrompt'],expected)
            self.assertIn('&amp; &lt;原文&gt;',gateway.board['videoPrompt'])
            gateway.check_ready(bridge.get('mixed'))
            writes=copy.deepcopy(gateway.writes)
            self.assertEqual(bridge.prepare(self.data,'mixed'),bridge.get('mixed'))
            self.assertEqual(gateway.writes,writes)

    def test_video_only_works_in_script_project_without_audio_free_mode_gate(self):
        data={**self.data,'images':[],'audios':[],'prompt':'@视频1 接 @视频2'}
        gateway=MediaGateway(self.row,mode_type=1)
        with tempfile.TemporaryDirectory() as root:
            bridge=Bridge(root,gateway);job=bridge.prepare(data,'videos')
            self.assertNotIn('requiresStandaloneAudio',job['bindingSnapshot'])
            self.assertEqual(len(job['videoReferences']),2)
            gateway.check_ready(bridge.get('videos'))

    def test_video_plus_audio_is_not_mistaken_for_forbidden_audio_only(self):
        self.limits['allowAudioOnly']=False;self.row['modelLimit']=json.dumps(self.limits)
        data={**self.data,'images':[],'prompt':'@视频1 @视频2 @音频1 @音频2'}
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(Bridge(root,MediaGateway(self.row)).prepare(data,'video-audio')['status'],'prepared')

    def test_video_duration_budget_fails_before_any_upload(self):
        self.limits['totalVideoDurationMax']=6;self.row['modelLimit']=json.dumps(self.limits)
        gateway=MediaGateway(self.row)
        with tempfile.TemporaryDirectory() as root, self.assertRaises(BridgeError):
            Bridge(root,gateway).prepare(self.data,'over-budget')
        self.assertEqual(gateway.writes,[])

    def test_all_media_size_budget_fails_before_any_upload(self):
        self.limits['totalSizeMax']=0.00001;self.row['modelLimit']=json.dumps(self.limits)
        gateway=MediaGateway(self.row)
        with tempfile.TemporaryDirectory() as root, self.assertRaises(BridgeError):
            Bridge(root,gateway).prepare(self.data,'too-large')
        self.assertEqual(gateway.writes,[])

    def test_ready_rejects_changed_video_runtime_file_or_live_model_limit(self):
        gateway=MediaGateway(self.row)
        with tempfile.TemporaryDirectory() as root:
            bridge=Bridge(root,gateway);bridge.prepare(self.data,'bound')
            changed=bridge.get('bound');changed['videoReferences'][0]['url']+='?changed'
            with self.assertRaises(BridgeError):gateway.check_ready(changed)
            changed=bridge.get('bound');changed['videoReferenceSnapshot'][0]['url']+='?changed'
            with self.assertRaises(BridgeError):bridge.update('bound',changed)
            job=bridge.get('bound');path=Path(job['videoReferences'][0]['path']);raw=path.read_bytes()
            path.write_bytes(b'tampered')
            with self.assertRaises(BridgeError):gateway.check_ready(job)
            path.write_bytes(raw)
            limits=json.loads(gateway.row['modelLimit']);limits['videoMax']=1;gateway.row['modelLimit']=json.dumps(limits)
            with self.assertRaises(BridgeError):gateway.check_ready(job)


class MediaBodyBudgetTests(unittest.TestCase):
    def handler(self,path,body):
        handler=object.__new__(server.Handler)
        handler.path=path;handler.headers={'Content-Length':str(len(body)),'Content-Type':'application/json'}
        handler.rfile=io.BytesIO(body);handler.authorized=lambda:True
        handler.error=Mock();handler.send_json=Mock()
        handler.server=Mock(execution_policy={'mode':'manual'})
        handler.server.submit.return_value='test-only'
        return handler

    def test_larger_transport_budget_is_limited_to_ordinary_video_endpoint(self):
        body=json.dumps({'model':'test','padding':'a'*120}).encode()
        with patch.object(server,'MAX_BODY',100),patch.object(server,'MAX_VIDEO_REFERENCE_BODY',200):
            ordinary=self.handler('/v1/videos',body);ordinary.do_POST()
            ordinary.server.submit.assert_called_once();ordinary.error.assert_not_called()
            for path in ('/extension/v1/videos','/enhancement/v1/videos','/audio/v1/audio/tasks'):
                restricted=self.handler(path,body);restricted.do_POST()
                restricted.server.submit.assert_not_called();restricted.error.assert_called_once()

    def test_oversize_error_explains_transport_budget_before_reading_payload(self):
        handler=self.handler('/v1/videos',b'{}')
        handler.headers['Content-Length']=str(server.MAX_VIDEO_REFERENCE_BODY+1)
        handler.do_POST()
        handler.server.submit.assert_not_called()
        self.assertIn('base64',handler.error.call_args.args[1])
        self.assertIn('不是模型',handler.error.call_args.args[1])
        self.assertEqual(handler.rfile.tell(),0)


class CharacterMediaBudgetTests(unittest.TestCase):
    def test_two_character_materials_using_same_voice_keep_separate_duration_slots(self):
        from voice_bindings import prepare_video_voices
        bindings=[{'image_index':index,'name':'Character','voice_id':'17'} for index in (1,2)]
        catalog=Mock()
        catalog.resolve.return_value={'id':'17','voiceUrl':'https://jzinnovation.jxbaobian.cn/voice.mp3','source':'catalog'}
        with tempfile.TemporaryDirectory() as root,patch('voice_bindings.catalog',return_value=catalog),\
                patch('voice_bindings.media_info',return_value={'duration':4,'bytes':100}),\
                patch('audio_flow.AudioGateway.upload_audio') as upload:
            with self.assertRaises(BridgeError):
                prepare_video_voices(bindings,model(totalAudioDurationMax=6),Bridge(root))
            upload.assert_not_called()


if __name__=='__main__':unittest.main()
