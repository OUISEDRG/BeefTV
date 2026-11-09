import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from bridge import Bridge, BridgeError
from tests.fixtures_budget import FundedBridge as Bridge
from tests.test_enhancement import source_request, TASK, SOURCE
from enhancement import EnhancementGateway, probe_video, MEDIA_TOOLS

class FakeEnhancer(EnhancementGateway):
    def __init__(self): self.calls=[]; self.task=None; self.state=1
    def verify_project(self,binding): pass
    def identity(self): return {'userId':17921,'teamId':168,'orgId':17}
    def upload_video(self,path,meta): self.calls.append('upload'); return SOURCE
    def counts(self): return {'video-enhancement-c-priority':0,'video-enhancement-c-leisure':0}
    def submit_video(self,job): self.calls.append('submit'); self.task=TASK; return {'taskId':TASK}
    def find_task(self,job): self.calls.append('lookup'); return self.task
    def task_status(self,task_id,job): return {'status':self.state,'urls':[SOURCE] if self.state==3 else []}

class EnhancementFlowTests(unittest.TestCase):
    def setUp(self):
        from enhancement import EnhancementFlow
        self.tmp=tempfile.TemporaryDirectory(); self.b=Bridge(self.tmp.name); self.g=FakeEnhancer()
        def download(url,path):
            shutil.copyfile(Path(__file__).parent/'fixtures/sample.mp4',path)
            return probe_video(path)
        self.flow=EnhancementFlow(self.b,gateway=self.g,download=download)
    def tearDown(self): self.tmp.cleanup()
    def test_prepare_reads_source_not_generation_controls_and_deduplicates(self):
        job=self.flow.prepare(source_request(),'enhance-1')
        self.assertEqual(job['kind'],'enhancement'); self.assertEqual(job['duration'],1)
        self.assertEqual(job['pointsEstimate'],1); self.assertEqual(job['sourceVideo']['width'],320)
        self.assertNotIn('storyboardId',job); self.assertNotIn('submit',self.g.calls)
        self.assertEqual(self.flow.prepare(source_request(),'enhance-1'),job)
        self.assertEqual(self.g.calls.count('upload'),1)
        with self.assertRaises(BridgeError): self.flow.prepare(source_request('dreama-enhance-b'),'enhance-1')
    def test_confirmation_submits_once_and_returns_uuid_result(self):
        job=self.flow.prepare(source_request(),'enhance-1')
        with self.assertRaises(BridgeError): self.flow.approve(job['id'],'wrong',1)
        self.assertNotIn('submit',self.g.calls)
        self.flow.approve(job['id'],job['quoteId'],1)
        with self.assertRaises(BridgeError): self.flow.approve(job['id'],job['quoteId'],1)
        self.assertEqual(self.g.calls.count('submit'),1)
        self.g.state=3; result=self.flow.poll(job['id'])
        self.assertEqual(result['status'],'completed'); self.assertEqual(result['taskId'],TASK)
        self.assertTrue((self.flow.videos/result['mediaKey']).is_file())
    def test_unknown_submission_reconciles_after_restart_without_resubmit(self):
        job=self.flow.prepare(source_request(),'enhance-1')
        def lost(job): self.g.calls.append('submit'); self.g.task=TASK; raise TimeoutError()
        self.g.submit_video=lost
        with self.assertRaises(BridgeError): self.flow.approve(job['id'],job['quoteId'],1)
        from enhancement import EnhancementFlow
        restarted=EnhancementFlow(Bridge(self.tmp.name),gateway=self.g)
        self.assertEqual(restarted.poll(job['id'])['taskId'],TASK)
        self.assertEqual(self.g.calls.count('submit'),1)
    def test_changed_source_blocks_charge(self):
        job=self.flow.prepare(source_request(),'enhance-1')
        (self.flow.inputs/job['sourceKey']).write_bytes(b'changed')
        with self.assertRaises(BridgeError): self.flow.approve(job['id'],job['quoteId'],1)
        self.assertNotIn('submit',self.g.calls)
        self.assertEqual(self.b.get(job['id'])['status'],'prepared')
    def test_missing_upstream_audio_is_restored_from_source(self):
        import base64
        source=Path(self.tmp.name)/'sound.mp4'
        subprocess.run([str(MEDIA_TOOLS/'ffmpeg.exe'),'-v','error','-i',str(Path(__file__).parent/'fixtures/sample.mp4'),
            '-f','lavfi','-i','sine=frequency=440:duration=1','-map','0:v:0','-map','1:a:0','-c:v','copy','-c:a','aac','-shortest',str(source)],check=True,capture_output=True)
        data=source_request(); data['videos']=['data:video/mp4;base64,'+base64.b64encode(source.read_bytes()).decode()]
        job=self.flow.prepare(data,'sound'); self.flow.approve(job['id'],job['quoteId'],job['pointsEstimate'])
        self.g.state=3; result=self.flow.poll(job['id'])
        self.assertEqual(result['status'],'completed',result.get('error'))
        self.assertTrue(probe_video(self.flow.videos/result['mediaKey'])['hasAudio'])
        self.assertTrue(result['video']['audioRestored'])

    def test_short_audio_does_not_truncate_longer_video(self):
        import base64
        source=Path(self.tmp.name)/'short-audio.mp4'
        subprocess.run([str(MEDIA_TOOLS/'ffmpeg.exe'),'-v','error','-stream_loop','-1',
            '-i',str(Path(__file__).parent/'fixtures/sample.mp4'),'-f','lavfi','-i','sine=frequency=440:duration=1',
            '-map','0:v:0','-map','1:a:0','-c:v','copy','-c:a','aac','-t','4',str(source)],check=True,capture_output=True)
        def silent_download(url,path):
            subprocess.run([str(MEDIA_TOOLS/'ffmpeg.exe'),'-v','error','-i',str(source),'-an','-c:v','copy',str(path)],check=True,capture_output=True)
            return probe_video(path)
        self.flow.download=silent_download
        data=source_request(); data['videos']=['data:video/mp4;base64,'+base64.b64encode(source.read_bytes()).decode()]
        job=self.flow.prepare(data,'short-audio'); self.flow.approve(job['id'],job['quoteId'],job['pointsEstimate'])
        self.g.state=3; result=self.flow.poll(job['id'])
        self.assertEqual(result['status'],'completed',result.get('error'))
        self.assertAlmostEqual(result['video']['duration'],4,delta=.1)
        self.assertTrue(result['video']['hasAudio'])

    def test_mov_disguised_as_mp4_is_rejected_before_upload(self):
        import base64
        source=Path(self.tmp.name)/'disguised.mp4'
        subprocess.run([str(MEDIA_TOOLS/'ffmpeg.exe'),'-v','error','-i',str(Path(__file__).parent/'fixtures/sample.mp4'),
            '-c','copy','-f','mov',str(source)],check=True,capture_output=True)
        data=source_request(); data['videos']=['data:video/mp4;base64,'+base64.b64encode(source.read_bytes()).decode()]
        with self.assertRaises(BridgeError): self.flow.prepare(data,'mov-input')
        self.assertEqual(self.g.calls,[])

    def test_finalize_failure_does_not_accumulate_downloaded_files(self):
        job=self.flow.prepare(source_request(),'bad-result'); self.flow.approve(job['id'],job['quoteId'],1)
        self.g.state=3
        def reject(*args): raise BridgeError('invalid result')
        self.flow.finalize_video=reject
        for _ in range(3):
            result=self.flow.poll(job['id'])
            self.assertEqual(result['status'],'download_failed')
            self.assertEqual(list(self.flow.videos.glob('*.mp4')),[])
        self.assertEqual(self.g.calls.count('submit'),1)

    def test_silent_upstream_track_is_replaced_by_original_audio(self):
        import base64
        source=Path(self.tmp.name)/'tone.mp4'; fixture=Path(__file__).parent/'fixtures/sample.mp4'
        subprocess.run([str(MEDIA_TOOLS/'ffmpeg.exe'),'-v','error','-i',str(fixture),'-f','lavfi','-i','sine=frequency=440:duration=1',
            '-map','0:v:0','-map','1:a:0','-c:v','copy','-c:a','aac',str(source)],check=True,capture_output=True)
        def silent_download(url,path):
            subprocess.run([str(MEDIA_TOOLS/'ffmpeg.exe'),'-v','error','-i',str(fixture),'-f','lavfi','-i','anullsrc=r=44100:cl=mono',
                '-map','0:v:0','-map','1:a:0','-c:v','copy','-c:a','aac','-t','1',str(path)],check=True,capture_output=True)
            return probe_video(path)
        def audio_hash(path):
            return subprocess.run([str(MEDIA_TOOLS/'ffmpeg.exe'),'-v','error','-i',str(path),'-map','0:a:0','-c','copy',
                '-f','hash','-hash','sha256','-'],check=True,capture_output=True).stdout
        self.flow.download=silent_download
        data=source_request(); data['videos']=['data:video/mp4;base64,'+base64.b64encode(source.read_bytes()).decode()]
        job=self.flow.prepare(data,'upstream-silence'); self.flow.approve(job['id'],job['quoteId'],job['pointsEstimate'])
        self.g.state=3; result=self.flow.poll(job['id'])
        self.assertEqual(result['status'],'completed',result.get('error'))
        self.assertEqual(audio_hash(self.flow.videos/result['mediaKey']),audio_hash(source))
