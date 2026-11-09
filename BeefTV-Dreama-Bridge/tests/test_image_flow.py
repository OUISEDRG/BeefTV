from tests.fixtures_budget import intent_fields
import base64
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from bridge import Bridge, BridgeError, PROJECT, SERIES
from tests.fixtures_budget import FundedBridge as Bridge
from tests.test_verbatim_prompt import official_tag
from image_flow import ImageFlow, ImageGateway, image_models, validate_image_request, download_image

MODEL='7fa38b0a-d222-4a8b-a605-7b55a8bc807a'
WAN='b4618c22‑0e91‑4050‑8255‑d128603ef375'
URL='https://jzinnovation.jxbaobian.cn/example/result.png'

def png(size=(320,320)):
    data=io.BytesIO(); Image.new('RGB',size,'navy').save(data,format='PNG'); return data.getvalue()

def request(**overrides):
    return {'model':MODEL,'prompt':'  原始提示词 <保留> & 标点\n第二行  ','images':[],
            'aspect_ratio':'1:1','resolution':'3K','n':1,**overrides}

class FakeImageGateway:
    def __init__(self): self.calls=[]; self.task=None; self.status=1; self.quote=3
    def identity(self): return {'userId':17921,'teamId':168,'orgId':17}
    def upload_image(self,data,name): self.calls.append('upload'); return URL
    def create_reference(self,name,url):
        self.calls.append('reference'); return {'name':name,'url':url,'imageId':21,'assetId':20}
    def prepare_board(self,job):
        self.calls.append('save'); job.update(storyboardId=300,savedPromptHash='hash',promptPolicy='verbatim-v1',promptReferenceIndexes=[])
    def estimate_image(self,job): self.calls.append('quote'); return self.quote
    def check_ready(self,job): self.calls.append('check')
    def inspect_board(self,job): return {'imageTaskId':self.task}
    def submit_video(self,job): self.calls.append('submit'); self.task=701; return {'taskId':701}
    def task_status(self,task_id): return {'taskId':task_id,'status':self.status,'urls':[URL,URL+'?second=1'] if self.status==3 else []}

class ImageValidationTests(unittest.TestCase):
    def test_all_26_preserved_but_external_5_not_runnable(self):
        all_models=image_models(include_disabled=True)
        self.assertEqual(len(all_models),26); self.assertEqual(len(image_models()),21)
        self.assertIn(WAN,all_models); self.assertNotIn(WAN.replace('‑','-'),all_models)
        disabled=[m for m in all_models.values() if not m['enabled']]
        self.assertEqual(len(disabled),5)
        for model in disabled:
            self.assertTrue(model['disabledReason'])
            with self.assertRaises(BridgeError): validate_image_request(request(model=model['id'],resolution='2K'))

    def test_text_only_allowed_and_model_resolutions_preserved(self):
        validated=validate_image_request(request())
        self.assertEqual(validated['prompt'],request()['prompt']); self.assertEqual(validated['images'],[])
        self.assertEqual(validated['resolution'],'3K')
        with self.assertRaises(BridgeError): validate_image_request(request(resolution='8K'))
        with self.assertRaises(BridgeError): validate_image_request(request(aspect_ratio='8:11'))
        with self.assertRaises(BridgeError): validate_image_request(request(n=2))
        with self.assertRaises(BridgeError): validate_image_request(request(n=True))

    def test_image_model_limits_replace_video_minimum_and_cap(self):
        image='data:image/png;base64,'+base64.b64encode(png((240,240))).decode()
        validated=validate_image_request(request(model=WAN,prompt='@图片1 原文',images=[image],resolution='2K'))
        self.assertEqual(validated['images'][0],png((240,240)))
        with self.assertRaises(BridgeError): validate_image_request(request(model=WAN,prompt=' '.join('@图片'+str(i) for i in range(1,5)),images=[image]*4,resolution='2K'))
        with self.assertRaises(BridgeError): validate_image_request(request(images=[image]))

class ImageFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.bridge=Bridge(self.tmp.name); self.gateway=FakeImageGateway()
        def download(url,path):
            raw=png(); path.write_bytes(raw)
            return {'mimeType':'image/png','width':320,'height':320,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
        self.flow=ImageFlow(self.bridge,gateway=self.gateway,download=download)
    def tearDown(self): self.tmp.cleanup()

    def test_prepare_is_durable_deduplicated_and_does_not_generate(self):
        job=self.flow.prepare(request(),'image-1')
        self.assertEqual(job['kind'],'image'); self.assertEqual(job['prompt'],request()['prompt'])
        self.assertEqual(job['pointsEstimate'],3); self.assertNotIn('submit',self.gateway.calls)
        self.assertEqual(job['identity'],self.gateway.identity())
        self.assertEqual(self.flow.prepare(request(),'image-1'),job)
        self.assertEqual(self.gateway.calls.count('save'),1)
        with self.assertRaises(BridgeError): self.flow.prepare(request(prompt='changed'),'image-1')

    def test_approve_once_then_return_all_images(self):
        job=self.flow.prepare(request(),'image-1'); self.flow.approve(job['id'],job['quoteId'],3)
        with self.assertRaises(BridgeError): self.flow.approve(job['id'],job['quoteId'],3)
        self.gateway.status=3; result=self.flow.poll(job['id'])
        self.assertEqual(result['status'],'completed'); self.assertEqual(len(result['images']),2)
        self.assertEqual(len(result['mediaKeys']),2); self.assertEqual(self.gateway.calls.count('submit'),1)
        for key in result['mediaKeys']: self.assertTrue((self.flow.images/key).is_file())

    def test_quote_change_prevents_submit(self):
        job=self.flow.prepare(request(),'image-1'); self.gateway.quote=5
        with self.assertRaises(BridgeError): self.flow.approve(job['id'],job['quoteId'],3)
        self.assertNotIn('submit',self.gateway.calls)
        fresh=self.bridge.get(job['id']); self.assertEqual(fresh['pointsEstimate'],'5')
        self.assertNotEqual(fresh['quoteId'],job['quoteId'])

    def test_lost_response_reconciles_image_task_after_restart(self):
        job=self.flow.prepare(request(),'image-1')
        def lost(job): self.gateway.calls.append('submit'); self.gateway.task=701; raise TimeoutError()
        self.gateway.submit_video=lost
        with self.assertRaises(BridgeError): self.flow.approve(job['id'],job['quoteId'],3)
        restarted=ImageFlow(Bridge(self.tmp.name),gateway=self.gateway)
        self.assertEqual(restarted.poll(job['id'])['taskId'],701)
        self.assertEqual(self.gateway.calls.count('submit'),1)

    def test_partial_download_failure_cleans_files_without_resubmit(self):
        job=self.flow.prepare(request(),'image-1'); self.flow.approve(job['id'],job['quoteId'],3); self.gateway.status=3
        initial=self.flow.download
        def failing(url,path):
            if '?' in url: raise BridgeError('download failed')
            return initial(url,path)
        self.flow.download=failing
        self.assertEqual(self.flow.poll(job['id'])['status'],'download_failed')
        self.assertEqual(list(self.flow.images.iterdir()),[])
        self.flow.download=initial; self.assertEqual(self.flow.poll(job['id'])['status'],'completed')
        self.assertEqual(self.gateway.calls.count('submit'),1)

    def test_failed_upstream_task_is_terminal_without_download(self):
        job=self.flow.prepare(request(),'image-1'); self.flow.approve(job['id'],job['quoteId'],3)
        self.gateway.status=4
        with patch.object(self.flow,'download') as download:
            self.assertEqual(self.flow.poll(job['id'])['status'],'failed')
            self.assertEqual(self.flow.poll(job['id'])['status'],'failed'); download.assert_not_called()

    def test_invalid_estimate_never_becomes_prepared(self):
        self.gateway.quote=float('nan')
        with self.assertRaises(BridgeError): self.flow.prepare(request(),'image-1')
        self.assertEqual(self.bridge.get('image-1')['status'],'unknown')
        self.assertNotIn('submit',self.gateway.calls)

class ImageGatewayTests(unittest.TestCase):
    def test_live_external_or_unclassified_model_blocks_prepared_image_readiness(self):
        rows=json.loads((Path(__file__).parents[1]/'image-catalog.json').read_text('utf-8'))['items']
        original=next(row for row in rows if row['eid']==MODEL)
        gateway=ImageGateway()
        job={'kind':'image','model':MODEL,'modelSnapshot':image_models()[MODEL],
             'identity':{'userId':17},'resolution':'3K','aspect_ratio':'1:1','references':[]}
        for external in (1,None,'0','missing'):
            live=dict(original)
            if external=='missing': live.pop('isExternal')
            else: live['isExternal']=external
            with self.subTest(isExternal=external),patch.object(gateway,'identity',return_value={'userId':17}), \
                 patch.object(gateway,'run',return_value={'id':PROJECT,'promptMode':'drama'}), \
                 patch.object(gateway,'_invoke',return_value={'items':[live]}), \
                 patch.object(gateway,'inspect_board',return_value={}) as inspect:
                with self.assertRaises(BridgeError): gateway.check_ready(job)
                inspect.assert_not_called()

    def test_identity_checks_live_auth_without_leaking_other_account_fields(self):
        gateway=ImageGateway()
        with patch.object(gateway,'_invoke',return_value={'userId':17921,'teamId':168,'orgId':17,'quota':123}) as cli:
            self.assertEqual(gateway.identity(),{'userId':17921,'teamId':168,'orgId':17})
            cli.assert_called_once_with('auth','whoami')
        for response in ({'userId':17921,'expired':True},{'userId':0},{'userId':True},{'userId':'17921'}):
            with patch.object(gateway,'_invoke',return_value=response),self.assertRaises(BridgeError):
                gateway.identity()

    def test_changed_account_blocks_before_any_project_check_or_paid_submit(self):
        gateway=ImageGateway()
        job={'kind':'image','model':MODEL,'identity':{'userId':17921,'teamId':168,'orgId':17}}
        for current in ({'userId':17922,'teamId':168,'orgId':17},
                        {'userId':17921,'teamId':169,'orgId':17},
                        {'userId':17921,'teamId':168,'orgId':18}):
            with patch.object(gateway,'identity',return_value=current),patch.object(gateway,'run') as cli, \
                 patch.object(gateway,'_invoke') as invoke,self.assertRaises(BridgeError):
                gateway.check_ready(job)
            cli.assert_not_called(); invoke.assert_not_called()

    def test_image_prompt_uses_official_image_assembler_and_exact_readback(self):
        gateway=ImageGateway(); tag=official_tag(21)
        job={'name':'test','prompt':'  @图片1 <保留> &\n原文  ','references':[{'imageId':21,'name':'ref'}]}
        expected='<p>  '+tag+' &lt;保留&gt; &amp;<br>原文  </p>'
        responses=[{'mode':'drama','prompt':'<p>不要复制说明'+tag+'</p>'},{},{'items':[{'id':300}]},{},
                   {'items':[{'id':300,'projectId':PROJECT,'seriesId':SERIES,'imagePrompt':expected}]}]
        with patch.object(gateway,'run',side_effect=responses) as cli: gateway.prepare_board(job)
        self.assertEqual(cli.call_args_list[0].args[cli.call_args_list[0].args.index('--kind')+1],'image')
        update=cli.call_args_list[3].args
        self.assertEqual(update[update.index('--image-prompt')+1],expected)
        self.assertEqual(job['savedPromptHash'],hashlib.sha256(expected.encode()).hexdigest())

    def test_native_submit_uses_image_resolution_and_aspect_ratio(self):
        gateway=ImageGateway(); job={'kind':'image','model':MODEL,'status':'submitting','approvedAt':1,
          'approvedPoints':3,'pointsEstimate':3,'storyboardId':300,'resolution':'3K','aspect_ratio':'1:1', **intent_fields(3)}
        with patch.object(gateway,'_invoke',return_value={'taskId':701}) as cli:
            self.assertEqual(gateway.submit_video(job),{'taskId':701})
        args=cli.call_args.args; self.assertEqual(args[:2],('image','produce'))
        self.assertEqual(args[args.index('--image-resolution')+1],'3K')
        self.assertEqual(args[args.index('--proportion')+1],'1:1')

    def test_reference_and_prompt_change_blocks_readiness(self):
        gateway=ImageGateway(); saved='<p>original</p>'
        job={'kind':'image','storyboardId':300,'references':[],'promptReferenceIndexes':[],
             'savedPromptHash':hashlib.sha256(saved.encode()).hexdigest()}
        board={'id':300,'projectId':PROJECT,'seriesId':SERIES,'imagePrompt':'changed'}
        with patch.object(gateway,'run',return_value={'items':[board]}):
            with self.assertRaises(BridgeError): gateway.inspect_board(job)

    def test_download_rejects_untrusted_url_before_network(self):
        with patch('dreama_image.requests.get') as get:
            for url in ('http://127.0.0.1/secret','https://jzinnovation.jxbaobian.cn:invalid/image'):
                with self.assertRaises(BridgeError): download_image(url,Path('ignored.img'))
            get.assert_not_called()

    def test_download_validates_actual_image_bytes_and_preserves_source(self):
        class Response:
            status_code=200
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def iter_content(self,size): yield png()
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'result.img'
            with patch('dreama_image.requests.get',return_value=Response()) as get:
                result=download_image(URL,path)
            self.assertEqual(path.read_bytes(),png()); self.assertEqual(result['mimeType'],'image/png')
            self.assertEqual(result['sha256'],hashlib.sha256(png()).hexdigest())
            self.assertFalse(get.call_args.kwargs['allow_redirects'])

    def test_download_rejects_successful_http_html_and_removes_partial(self):
        class Response:
            status_code=200
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def iter_content(self,size): yield b'<html>not an image</html>'
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'result.img'
            with patch('dreama_image.requests.get',return_value=Response()):
                with self.assertRaises(BridgeError): download_image(URL,path)
            self.assertEqual(list(Path(root).iterdir()),[])
