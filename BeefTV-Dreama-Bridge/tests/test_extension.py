from tests.fixtures_budget import intent_fields
import base64
import copy
import hashlib
import html
import json
import re
import shutil
import tempfile
import unittest
from html.parser import HTMLParser
from urllib.parse import quote,unquote
from pathlib import Path
from unittest.mock import patch
from bridge import Bridge,BridgeError,PROJECT,SERIES
from tests.fixtures_budget import FundedBridge as Bridge
from enhancement import probe_video
try:
    import extension as e
except ImportError:
    e=None

EID='50347e68-2b4c-49da-8c45-426432bba793'
MODEL='dreama-extend-'+EID
SOURCE='https://jzinnovation.jxbaobian.cn/resourceBucket/video/source.mp4'

def native_mentions(prompt):
    class Mentions(HTMLParser):
        def __init__(self):super().__init__();self.items=[]
        def handle_starttag(self,tag,attrs):
            a=dict(attrs)
            if tag=='span' and (a.get('data-node-type')=='inline-mention' or a.get('data-type')=='mention'):
                self.items.append((a.get('data-value'),json.loads(unquote(a['data-extra-props']))))
    parser=Mentions();parser.feed(prompt);return parser.items
def request():
    return {'model':MODEL,'prompt':'继续原动作，保持机位。\n别改我的文字 & <光>','duration':5,'resolution':'480p',
            'videos':['data:video/mp4;base64,'+base64.b64encode((Path(__file__).parent/'fixtures/source-4s.mp4').read_bytes()).decode()]}

class ExtensionInputTests(unittest.TestCase):
    def setUp(self):self.assertIsNotNone(e,'Extension implementation is missing')
    def test_only_cloud_extend_models_are_routable_and_prompt_stays_verbatim(self):
        for key,res in [('dreama-extend-20492049-caa9-46db-b1bb-89723b409504','720p'),(MODEL,'480p')]:
            data=request();data.update(model=key,resolution=res)
            prompt,duration,raw,images=e.validate_extension_request(data)
            self.assertEqual(prompt,request()['prompt']);self.assertEqual(duration,5);self.assertEqual(images,[])
            self.assertEqual(raw,(Path(__file__).parent/'fixtures/source-4s.mp4').read_bytes())
        self.assertEqual(len(e.extension_models()),2)
        data=request();data['model']='dreama-extend-20e746af-d952-4731-9c54-817b3690ec52'
        with self.assertRaises(BridgeError):e.validate_extension_request(data)
    def test_invalid_inputs_and_model_resolution_fail_before_upload(self):
        for field,value in [('videos',[]),('videos',request()['videos']*2),('duration',31),('duration',-1),
                            ('resolution','1080p'),('audios',['input']),('prompt',''),('videos',['https://example.org/video.mp4'])]:
            data=request();data[field]=value
            with self.subTest(field=field),self.assertRaises(BridgeError):e.validate_extension_request(data)
        for duration in (1,30.1):
            with self.assertRaises(BridgeError):e.validate_source({'duration':duration},e.extension_models()[MODEL])
        for duration in (2,30):e.validate_source({'duration':duration},e.extension_models()[MODEL])

class FakeGateway(e.ExtensionGateway if e else object):
    def __init__(self):
        if e:super().__init__()
        self.calls=[];self.row=None;self.state=1;self.price=76;self.history=[]
        self.media.identity=lambda:{'userId':17921,'teamId':168,'orgId':17}
        self.media.upload_video=lambda path,meta:SOURCE
    def validate_binding(self,binding,spec): return self.media.identity()
    def identity(self): return self.media.identity()
    def verify_project(self,binding): pass
    def save_and_readback(self,prompt,refs,duration,name):
        saved,indexes=self.render_verbatim_prompt(prompt,refs)
        self.row={'id':1001,'projectId':PROJECT,'seriesId':SERIES,'videoPrompt':saved,'duration':duration,'videoTaskId':0,'produceType':5}
        return {'storyboardId':1001,'savedPromptHash':hashlib.sha256(saved.encode()).hexdigest(),'promptPolicy':'verbatim-v1','promptReferenceIndexes':indexes}
    def run(self,*args):
        if args[:2]==('project','get'):return {'id':PROJECT,'promptMode':'drama','proportion':'9:16'}
        if args[:2]==('prompt','assemble'):
            from tests.test_verbatim_prompt import official_tag
            return {'mode':'drama','prompt':'<p>不要带入的附加说明'+official_tag(21)+official_tag(32)+'</p>'}
        raise AssertionError('Unexpected CLI call in fake: '+str(args[:2]))
    def _request(self,path,payload,*,paid=False,job=None):
        self.calls.append((path,copy.deepcopy(payload),paid))
        if path=='/storyboard/api/save':self.row.update(payload);return {}
        if path=='/storyboard/api/list':return {'list':[copy.deepcopy(self.row)]}
        if path=='/storyboard/api/historyList':return {'total':len(self.history),'list':copy.deepcopy(self.history)}
        if path=='/storyboard/endpoint/list':return json.loads((Path(__file__).parents[1]/'extension-catalog.json').read_text('utf-8'))
        if path=='/storyboard/endpoint/calcToken':return {'token':self.price}
        if path=='/storyboard/api/produceVideo':
            self.row['videoTaskId']=987
            self.history.append(dict(copy.deepcopy(self.row),id=7654,extraJson=''))
            return {'taskId':987}
        raise AssertionError(path)
    def task_status(self,task_id):return {'status':self.state,'urls':[SOURCE] if self.state==3 else []}

class ExtensionFlowTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(e,'Extension implementation is missing')
        self.tmp=tempfile.TemporaryDirectory();self.g=FakeGateway();self.b=Bridge(self.tmp.name)
        def download(url,path):shutil.copyfile(Path(__file__).parent/'fixtures/sample.mp4',path);return probe_video(path)
        self.flow=e.ExtensionFlow(self.b,gateway=self.g,download=download)
    def tearDown(self):
        if hasattr(self,'tmp'):self.tmp.cleanup()
    def test_prepare_binds_source_and_preserves_text_without_paid_submit(self):
        job=self.flow.prepare(request(),'ext-1')
        self.assertEqual(job['kind'],'extension');self.assertEqual(job['duration'],5)
        self.assertAlmostEqual(job['sourceVideo']['duration'],4,delta=.01)
        self.assertEqual(job['pointsEstimate'],76);self.assertEqual(job['prompt'],request()['prompt'])
        self.assertEqual(self.g.row['omniReferenceTaskType'],'extend');self.assertEqual(self.g.row['targetVideoUrl'],SOURCE)
        self.assertEqual(self.g.row['videoPrompt'],'<p>继续原动作，保持机位。<br>别改我的文字 &amp; &lt;光&gt;</p>')
        quote=next(p for path,p,_ in self.g.calls if path.endswith('/calcToken'))
        self.assertEqual(quote,{'eid':EID,'type':1,'number':1,'duration':5,'referenceVideoTotalDuration':'4.00'})
        self.assertFalse(any(paid for _,_,paid in self.g.calls))
        self.assertEqual(self.flow.prepare(request(),'ext-1'),job)
    def test_exact_mode_source_prompt_and_duration_required_before_charge(self):
        job=self.flow.prepare(request(),'ext-1')
        original=copy.deepcopy(self.g.row)
        for field,value in [('omniReferenceTaskType','omni'),('targetVideoUrl','https://wrong.example/video.mp4'),('duration',6),('videoPrompt','changed')]:
            self.g.row=copy.deepcopy(original);self.g.row[field]=value
            with self.subTest(field=field),self.assertRaises(BridgeError):self.flow.approve(job['id'],job['quoteId'],76)
        self.assertFalse(any(paid for _,_,paid in self.g.calls))
    def test_native_workspace_restores_target_even_without_video_mention(self):
        job=self.flow.prepare(request(),'draft')
        workspace=json.loads(self.g.row.get('extraJson') or '{}').get('workspaces',{}).get('extend',{})
        self.assertEqual(workspace.get('prompt'),self.g.row['videoPrompt'])
        assets=workspace['referenceAssets'];self.assertEqual(len(assets),1)
        self.assertTrue(assets[0].get('id'),'Native editor requires an asset id to replace/remove the target')
        self.assertEqual(assets[0]['url'],SOURCE);self.assertEqual(assets[0]['fileType'],2)
        self.assertEqual(assets[0]['referenceRole'],'target-video')
        self.assertAlmostEqual(assets[0]['durationSeconds'],4,delta=.01)
        self.assertFalse(any(paid for _,_,paid in self.g.calls))
    def test_native_target_reference_preserves_every_other_character(self):
        data=request();data['prompt']='  延长视频@视频1 ,让它跳舞。\n再看@视频1 & <原文>  '
        job=self.flow.prepare(data,'video-mention')
        mentions=native_mentions(self.g.row['videoPrompt'])
        self.assertEqual(len(mentions),2,'@视频1 must become an actual editor video reference')
        for value,props in mentions:
            self.assertEqual(value,SOURCE);self.assertEqual(props['fileType'],2)
            self.assertEqual(props['referenceRole'],'target-video')
        import re,html
        text=re.sub(r'<span\b[^>]*></span>','@视频1',self.g.row['videoPrompt'])
        self.assertEqual(html.unescape(text[3:-4].replace('<br>','\n')),data['prompt'])
        self.assertEqual(job['prompt'],data['prompt'])
    def test_missing_or_changed_workspace_prevents_paid_submission(self):
        job=self.flow.prepare(request(),'draft-guard')
        self.g.row['extraJson']=''
        with self.assertRaises(BridgeError):self.flow.approve(job['id'],job['quoteId'],76)
        self.assertFalse(any(paid for _,_,paid in self.g.calls))
    def test_target_span_requires_complete_metadata_even_if_draft_and_hash_match(self):
        data=request();data['prompt']='延长 @视频1。'
        job=self.flow.prepare(data,'target-metadata')
        original=copy.deepcopy(self.g.row)
        original_tag=re.search(r'<span\b[^>]*></span>',original['videoPrompt'])[0]
        _,props=native_mentions(original['videoPrompt'])[0]
        for field,value in [('url','https://jzinnovation.jxbaobian.cn/wrong.mp4'),('fileType',3),
                            ('materialId',False),('materialType',1),('fileSizeBytes',1),
                            ('durationSeconds',1),('assetSource','library'),('name','替换视频')]:
            modified={**props,field:value}
            encoded=quote(json.dumps(modified,ensure_ascii=False,separators=(',',':')),safe="~()*!.'-")
            tag=('<span data-value="'+html.escape(modified['url'],quote=True)+'" data-node-type="inline-mention" '
                 'data-extra-props="'+encoded+'" data-draggable="true"></span>')
            self.g.row=copy.deepcopy(original)
            self.g.row['videoPrompt']=original['videoPrompt'].replace(original_tag,tag)
            draft=json.loads(self.g.row['extraJson']);workspace=draft['workspaces']['extend']
            workspace['prompt']=self.g.row['videoPrompt'];workspace['referenceAssets'][0].update(modified)
            self.g.row['extraJson']=json.dumps(draft)
            changed={**job,'savedPromptHash':hashlib.sha256(self.g.row['videoPrompt'].encode()).hexdigest()}
            with self.subTest(field=field),self.assertRaises(BridgeError):self.g.inspect_board(changed)
    def test_target_span_count_matches_original_mentions_and_cannot_be_hidden(self):
        data=request();data['prompt']='延长 @视频1。'
        job=self.flow.prepare(data,'target-count');original=copy.deepcopy(self.g.row)
        tag=re.search(r'<span\b[^>]*></span>',original['videoPrompt'])[0]
        for replacement in ('',tag+tag,tag.replace('</span>','hidden</span>')):
            self.g.row=copy.deepcopy(original)
            self.g.row['videoPrompt']=original['videoPrompt'].replace(tag,replacement)
            draft=json.loads(self.g.row['extraJson']);draft['workspaces']['extend']['prompt']=self.g.row['videoPrompt']
            self.g.row['extraJson']=json.dumps(draft)
            changed={**job,'savedPromptHash':hashlib.sha256(self.g.row['videoPrompt'].encode()).hexdigest()}
            with self.subTest(replacement=replacement),self.assertRaises(BridgeError):self.g.inspect_board(changed)
    def test_unexpected_plain_video_reference_is_not_ignored_by_extension(self):
        job=self.flow.prepare(request(),'unexpected-reference')
        props={'fileType':2,'materialId':0,'materialType':0,'assetSource':'local-upload',
               'name':'其他视频','fileName':'other.mp4','durationSeconds':4,'fileSizeBytes':100}
        tag=('<span data-value="https://jzinnovation.jxbaobian.cn/other.mp4" data-node-type="inline-mention" '
             'data-extra-props="'+quote(json.dumps(props))+'" data-draggable="true"></span>')
        self.g.row['videoPrompt']=self.g.row['videoPrompt'].replace('</p>',tag+'</p>')
        draft=json.loads(self.g.row['extraJson']);draft['workspaces']['extend']['prompt']=self.g.row['videoPrompt']
        self.g.row['extraJson']=json.dumps(draft)
        changed={**job,'savedPromptHash':hashlib.sha256(self.g.row['videoPrompt'].encode()).hexdigest()}
        with self.assertRaises(BridgeError):self.g.inspect_board(changed)
    def test_original_submission_failure_survives_reconciliation(self):
        job=self.flow.prepare(request(),'receipt')
        def fail(job):raise BridgeError('upstream test rejection, traceId=test-123')
        self.g.submit_video=fail
        with self.assertRaises(BridgeError):self.flow.approve(job['id'],job['quoteId'],76)
        self.flow.poll(job['id'])
        self.assertEqual(self.b.get(job['id']).get('submissionError'),'upstream test rejection, traceId=test-123')
    def test_additional_images_keep_mentions_in_place_and_target_video_first(self):
        from tests.test_bridge import request as image_request
        from tests.test_verbatim_prompt import official_tag
        data=request();data['images']=image_request()['images']*2
        data['prompt']='  延续 @视频1：@图片2 看向 @图片1，再看 @图片2。\n保留 & <原文>  '
        refs=[{'imageId':21,'name':'first','url':'https://jzinnovation.jxbaobian.cn/first.png'},
              {'imageId':32,'name':'second','url':'https://jzinnovation.jxbaobian.cn/second.png'}]
        with patch.object(self.g,'upload_image',side_effect=[r['url'] for r in refs]),patch.object(self.g,'create_reference',side_effect=refs):
            job=self.flow.prepare(data,'images')
        self.assertEqual(job['prompt'],data['prompt']);self.assertEqual(job['promptReferenceIndexes'],[1,0,1])
        a,b=official_tag(21),official_tag(32)
        import re
        visible=re.sub(r'<span\b[^>]*data-node-type="inline-mention"[^>]*></span>','@视频1',self.g.row['videoPrompt'])
        self.assertEqual(visible,'<p>  延续 @视频1：'+b+' 看向 '+a+'，再看 '+b+'。<br>保留 &amp; &lt;原文&gt;  </p>')
        mentions=native_mentions(self.g.row['videoPrompt'])
        self.assertEqual([props['fileType'] for _,props in mentions],[2,1,1,1])
        assets=json.loads(self.g.row['extraJson'])['workspaces']['extend']['referenceAssets']
        self.assertTrue(all(a.get('id') for a in assets))
        self.assertEqual(len({a['id'] for a in assets}),len(assets))
        with self.assertRaises(BridgeError):self.g.submit_video(job)
        self.assertFalse(any(paid for _,_,paid in self.g.calls))
        self.flow.approve(job['id'],job['quoteId'],76)
        payload=next(p for _,p,paid in self.g.calls if paid)
        self.assertEqual(payload['images'],[SOURCE,refs[1]['url'],refs[0]['url']])
    def test_submit_deduplicates_identical_reference_urls_in_mention_order(self):
        job={'kind':'extension','status':'submitting','approvedAt':1,'approvedPoints':76,'pointsEstimate':76,
             'model':MODEL,'storyboardId':1001,'duration':5,'sourceVideoUrl':SOURCE,
             'references':[{'url':'https://jzinnovation.jxbaobian.cn/same.png'}]*2,'promptReferenceIndexes':[1,0,1], **intent_fields(76)}
        self.g.row={};self.g.submit_video(job)
        payload=next(p for _,p,paid in self.g.calls if paid)
        self.assertEqual(payload['images'],[SOURCE,'https://jzinnovation.jxbaobian.cn/same.png'])
    def test_paid_submit_once_target_first_and_returns_new_clip_unchanged(self):
        job=self.flow.prepare(request(),'ext-1')
        self.flow.approve(job['id'],job['quoteId'],76)
        with self.assertRaises(BridgeError):self.flow.approve(job['id'],job['quoteId'],76)
        payloads=[p for path,p,paid in self.g.calls if paid]
        self.assertEqual(len(payloads),1)
        self.assertEqual(payloads[0],{'storyBoardId':1001,'projectId':PROJECT,'seriesId':SERIES,'produceType':5,'type':5,
            'duration':5,'LLM':EID,'resolution':'480P','produceVideoFormat':'mp4','omniReferenceTaskType':'extend','images':[SOURCE]})
        self.g.state=3; result=self.flow.poll(job['id']);self.assertEqual(result['status'],'completed')
        self.assertEqual((self.flow.videos/result['mediaKey']).read_bytes(),(Path(__file__).parent/'fixtures/sample.mp4').read_bytes())
    def test_unknown_submission_reconciles_without_resubmission(self):
        job=self.flow.prepare(request(),'ext-1');original=self.g.submit_video
        def lost(job):original(job);raise TimeoutError()
        self.g.submit_video=lost
        with self.assertRaises(BridgeError):self.flow.approve(job['id'],job['quoteId'],76)
        restarted=e.ExtensionFlow(Bridge(self.tmp.name),gateway=self.g)
        self.assertEqual(restarted.poll(job['id'])['taskId'],987)
        self.assertEqual(sum(paid for _,_,paid in self.g.calls),1)
    def test_successful_submit_can_clear_editor_fields_and_return_no_task_id(self):
        job=self.flow.prepare(request(),'native-receipt');original=self.g.submit_video
        def accepted(job):
            original(job)
            self.g.row.update(omniReferenceTaskType='',targetVideoUrl='',extraJson='')
            return {}
        self.g.submit_video=accepted
        result=self.flow.approve(job['id'],job['quoteId'],76)
        self.assertEqual(result['taskId'],987)
        self.g.state=2;self.assertEqual(self.flow.poll(job['id'])['status'],'processing')
        self.assertEqual(sum(paid for _,_,paid in self.g.calls),1)
    def test_unknown_receipt_recovers_from_matching_history_after_editor_changes(self):
        job=self.flow.prepare(request(),'native-lost');original=self.g.submit_video
        def lost(job):
            original(job)
            self.g.row.update(omniReferenceTaskType='',targetVideoUrl='',videoPrompt='new draft',extraJson='')
            raise TimeoutError()
        self.g.submit_video=lost
        with self.assertRaises(BridgeError):self.flow.approve(job['id'],job['quoteId'],76)
        self.g.state=2
        restarted=e.ExtensionFlow(Bridge(self.tmp.name),gateway=self.g)
        result=restarted.poll(job['id'])
        self.assertEqual(result.get('taskId'),987);self.assertEqual(result['status'],'processing')
        self.assertEqual(result['stage'],'poll');self.assertNotIn('error',result)
        self.assertEqual(result['submissionError'],'TimeoutError')
        self.assertEqual(sum(paid for _,_,paid in self.g.calls),1)
    def test_mismatched_or_ambiguous_history_never_adopts_a_task_or_resubmits(self):
        job=self.flow.prepare(request(),'history-guard');original=self.g.submit_video
        def lost(job):original(job);raise TimeoutError()
        self.g.submit_video=lost
        with self.assertRaises(BridgeError):self.flow.approve(job['id'],job['quoteId'],76)
        correct=copy.deepcopy(self.g.history[0])
        for field,value in [('projectId',0),('seriesId',0),('omniReferenceTaskType','omni'),
                            ('targetVideoUrl','https://wrong.example/source.mp4'),('duration',6),('videoPrompt','changed')]:
            self.g.history=[dict(correct,**{field:value})]
            with self.subTest(field=field):
                result=self.flow.poll(job['id'])
                self.assertEqual(result['status'],'submission_unknown');self.assertNotIn('taskId',result)
        self.g.history=[correct,dict(correct,id=7655,videoTaskId=988)]
        result=self.flow.poll(job['id'])
        self.assertEqual(result['status'],'submission_unknown');self.assertNotIn('taskId',result)
        self.assertEqual(sum(paid for _,_,paid in self.g.calls),1)
    def test_source_change_and_quote_change_require_new_preparation_or_confirmation(self):
        job=self.flow.prepare(request(),'ext-1');self.g.price=80
        with self.assertRaises(BridgeError):self.flow.approve(job['id'],job['quoteId'],76)
        current=self.b.get(job['id']);self.assertEqual(current['pointsEstimate'],'80');self.assertNotEqual(current['quoteId'],job['quoteId'])
        (self.flow.inputs/job['sourceKey']).write_bytes(b'changed')
        with self.assertRaises(BridgeError):self.flow.approve(job['id'],current['quoteId'],80)
        self.assertFalse(any(paid for _,_,paid in self.g.calls))
