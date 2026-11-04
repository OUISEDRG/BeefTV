"""Dreama cloud video extension: a target video plus verbatim continuation text."""
from job_binding import project_id, series_id, job_scope, preparation_scope, freeze, verify_frozen, validate_metadata, verify_write_identity

import hashlib
import html
import json
import math
import os
import re
import time
import uuid
from pathlib import Path
from urllib.parse import quote
import requests
from bridge import BridgeError,DreamaGateway,decode_image,image_reference_indexes
from enhancement import EnhancementGateway,decode_video,probe_video,file_digest
from generation import Generation,download_video
from model_catalog import group_models,resolve_variant
LIVE_ITEMS=None

def build_models(items):
    result={}
    for item in items:
        if item.get('hasVideoExtendEdit')!=1 or item.get('isExternal')!=0:continue
        limits=json.loads(item.get('modelLimit') or '{}')
        durations=sorted({x['duration'] for x in json.loads(item['videoTokens']) if type(x.get('duration')) is int and x['duration']>0})
        resolution=item.get('alias','').upper()
        if not durations or not re.fullmatch(r'\d+P',resolution):continue
        key='dreama-extend-'+item['eid']
        result[key]={'id':key,'eid':item['eid'],'name':item['name'],'label':'延长 · '+item['name']+' · '+resolution,
            'resolution':resolution,'durations':durations,'maxImages':min(30,int(limits.get('imageMax',0))),
            'promptMaxChars':min(15000,int(limits.get('modelTextLimit',6000))),
            'sourceMin':float(limits['videoDurationMin']),'sourceMax':float(limits['videoDurationMax']),'limits':limits}
    return result

def extension_models():
    return build_models(LIVE_ITEMS if LIVE_ITEMS is not None else json.loads((Path(__file__).parent/'extension-catalog.json').read_text('utf-8'))['items'])

def selected_extension_models(catalog=None):
    return group_models(extension_models() if catalog is None else catalog,('九梦 2.5 Pro',),label_prefix='延长 · ')

def resolve_extension_model(data):
    try:return resolve_variant(extension_models(),selected_extension_models(),data.get('model'),data.get('resolution'))
    except ValueError as error:raise BridgeError(str(error)) from None

def validate_source(meta,spec):
    duration=meta.get('duration')
    if type(duration) not in (int,float) or not math.isfinite(duration) or not spec['sourceMin']<=duration<=spec['sourceMax']:
        raise BridgeError(f"该延长模型的原视频需为{spec['sourceMin']:g}–{spec['sourceMax']:g}秒；不会自动裁剪。")

def validate_extension_request(data):
    if not isinstance(data,dict) or data.get('model') not in extension_models():raise BridgeError('视频延长模型无效。')
    allowed={'model','prompt','videos','images','audios','duration','seconds','aspect_ratio','resolution','character_voices', 'dreama'}
    if set(data)-allowed or data.get('audios'):raise BridgeError('延长不接收独立音频或扩展参数。')
    spec=resolve_extension_model(data);prompt=data.get('prompt','')
    if not isinstance(prompt,str) or not prompt.strip() or len(prompt)>spec['promptMaxChars']:raise BridgeError('续写提示词为空或超出模型字数限制。')
    if any(m[1]!='1' for m in re.finditer(r'@视频([0-9]+)',prompt)):raise BridgeError('只连接一个原视频，请核对提示词中的视频引用编号。')
    duration=data.get('duration',data.get('seconds',5))
    if type(duration) not in (int,float) or duration not in spec['durations']:raise BridgeError('新增时长不在延长模型支持范围内。')
    videos=data.get('videos')
    if not isinstance(videos,list) or len(videos)!=1:raise BridgeError('视频延长需要且只接受一个原视频。')
    images=data.get('images',[])
    if not isinstance(images,list) or len(images)>spec['maxImages']:raise BridgeError('参考图片数量超出延长模型限制。')
    image_reference_indexes(prompt,len(images))
    from voice_bindings import validate_video_bindings
    validate_video_bindings(data,len(images),spec)
    return prompt,int(duration),decode_video(videos[0]),[decode_image(v) for v in images]

def extension_target_props(job):
    return {'url':job['sourceVideoUrl'],'fileType':2,'materialType':0,'materialId':0,
            'referenceRole':'target-video','assetSource':'local-upload','name':'视频1',
            'fileName':'视频1.mp4','durationSeconds':job['sourceVideo']['duration'],
            'fileSizeBytes':job['sourceVideo']['bytes']}

def extension_target_tag(job):
    target=extension_target_props(job)
    props=quote(json.dumps(target,ensure_ascii=False,separators=(',',':')),safe="~()*!.'-")
    return ('<span data-value="'+html.escape(target['url'],quote=True)+'" data-node-type="inline-mention" '
            'data-extra-props="'+props+'" data-draggable="true"></span>')

def without_verified_target_mentions(job,prompt):
    # The saved prompt hash is already checked. Preserve the exact native atom
    # contract, including repeated mentions of the one frozen target resource.
    tag=extension_target_tag(job)
    expected_count=len(re.findall(r'@视频1(?![0-9])',job['prompt']))
    if prompt.count(tag)!=expected_count:
        raise BridgeError('延长目标视频的引用标签、元数据或引用次数已改变，未提交。')
    return prompt.replace(tag,'')

def extension_draft(job, row):
    """Use the website's inline-mention and schemaVersion=1 workspace formats.

    Grounding: dist-BZlou8Kg.js (editor serializer), referenceAssetLimit-UQTlfRuY.js
    (workspace sanitizer) and index-Bsshbw-t.js (tI, QP, PP, zF).
    Image tags still come unchanged from Dreama's official CLI assembler.
    """
    target=extension_target_props(job)
    tag=extension_target_tag(job)
    text=re.sub(r'@视频1(?![0-9])',lambda _:tag,row['videoPrompt'])
    try:
        draft=json.loads(row.get('extraJson') or '{"schemaVersion":1,"workspaces":{}}')
        if draft.get('schemaVersion')!=1 or not isinstance(draft.get('workspaces'),dict):raise ValueError()
    except (ValueError,TypeError,AttributeError):raise BridgeError('已有编辑器草稿格式未知，未覆盖。') from None
    target['id']='beeftv-target-'+hashlib.sha256(target['url'].encode()).hexdigest()[:16]
    assets=[target]+[{'id':'beeftv-image-'+str(r['imageId']),'url':r['url'],'fileType':1,'materialType':r.get('materialType',3),'materialId':r['imageId'],'name':r['name']}
                     for r in job['references']]
    assets += [{'id':'beeftv-voice-'+str(r['imageId']),'url':r['voiceUrl'],'fileType':3,
                'materialType':1,'materialId':r['imageId'],'name':r['voiceName'],
                'voiceId':r['voiceId'],'uploadVoiceUrl':r['uploadVoiceUrl'],'durationSeconds':r['voiceDuration']}
               for r in job['references'] if 'voiceId' in r]
    draft['workspaces']['extend']={'category':'extend','prompt':text,'referenceAssets':assets,
                                   'source':'programmatic','updatedAt':int(time.time()*1000)}
    return text,json.dumps(draft,ensure_ascii=False,separators=(',',':'))

class ExtensionGateway(DreamaGateway):
    READ_PATHS={'/storyboard/api/list','/storyboard/api/historyList','/storyboard/endpoint/list','/storyboard/endpoint/calcToken'}
    def __init__(self):super().__init__();self.media=EnhancementGateway()

    def render_verbatim_prompt(self,prompt,refs,**kwargs):
        kwargs['allow_video_placeholders']=True
        return super().render_verbatim_prompt(prompt,refs,**kwargs)

    def save_and_readback(self,prompt,refs,duration,name,**kwargs):
        kwargs['allow_video_placeholders']=True
        return super().save_and_readback(prompt,refs,duration,name,**kwargs)

    def validate_binding(self,binding,spec):
        identity=self.identity();self.verify_project(binding)
        live=build_models(self._request('/storyboard/endpoint/list',
            {'endpointModes':[5],'type':1,'isMerge':False,'location':100}).get('items',[])).get(spec['id'])
        if not live or any(live.get(field)!=spec.get(field) for field in ('durations','resolution','maxImages','promptMaxChars','sourceMin','sourceMax')):
            raise BridgeError('延长模型限制已变化，请刷新目录后重新准备。')
        return identity

    def _request(self,path,payload,*,paid=False):
        if paid or path=='/storyboard/api/save': verify_write_identity(self)
        if paid:
            if path!='/storyboard/api/produceVideo':raise BridgeError('非授权收费接口。')
        elif path not in self.READ_PATHS|{'/storyboard/api/save'}:raise BridgeError('延长接口不在已核实范围。')
        token=json.loads(self.credentials.read_text('utf-8-sig'))['accessToken']
        r=requests.post('https://jzzm.duanju.com/gw'+path,headers={'Authorization':token},json=payload,timeout=(10,90),allow_redirects=False)
        if r.status_code!=200:raise BridgeError('视频延长接口HTTP失败：'+str(r.status_code))
        data=r.json()
        if str(data.get('code'))!='0':
            detail=str(data.get('msg') or data.get('message') or '').replace(token,'[redacted]')[:500]
            trace=str(data.get('traceId') or '')[:100]
            raise BridgeError(f"视频延长接口业务失败，code={data.get('code')}：{detail}；traceId={trace}")
        result=data.get('result',{})
        return result.get('item',result) if isinstance(result,dict) else result

    @job_scope
    def prepare_board(self,job):
        job.update(self.save_and_readback(job['prompt'],job['references'],job['duration'],job['name']))
        row=self.read_board(job)
        self.save_extension_draft(job,row)
        self.inspect_board(job)

    @job_scope
    def save_extension_draft(self,job,row):
        if row.get('id')!=job['storyboardId'] or row.get('seriesId')!=series_id() or row.get('projectId')!=project_id():
            raise BridgeError('延长草稿绑定不一致。')
        if hashlib.sha256(row.get('videoPrompt','').encode()).hexdigest()!=job['savedPromptHash']:
            raise BridgeError('续写提示词已改变，未覆盖。')
        text,draft=extension_draft(job,row)
        self._request('/storyboard/api/save',{'id':job['storyboardId'],'seriesId':series_id(),'produceType':5,
            'duration':job['duration'],'omniReferenceTaskType':'extend','targetVideoUrl':job['sourceVideoUrl'],
            'videoPrompt':text,'extraJson':draft})
        job.update(savedPromptHash=hashlib.sha256(text.encode()).hexdigest(),extensionBindingVersion=2)

    @job_scope
    def read_board(self,job):
        rows=self._request('/storyboard/api/list',{'storyBoardIds':[job['storyboardId']],'seriesId':series_id(),'page':1,'size':10}).get('list',[])
        if len(rows)!=1:raise BridgeError('延长分镜回读不唯一。')
        return rows[0]

    @job_scope
    def inspect_board(self,job):
        row=self.read_board(job)
        expected={'id':job['storyboardId'],'seriesId':series_id(),'projectId':project_id(),'produceType':5,
                  'omniReferenceTaskType':'extend','targetVideoUrl':job['sourceVideoUrl'],'duration':job['duration']}
        if any(row.get(k)!=v for k,v in expected.items()):raise BridgeError('延长分镜的绑定、模式、目标视频或时长已改变。')
        if hashlib.sha256(row.get('videoPrompt','').encode()).hexdigest()!=job['savedPromptHash']:raise BridgeError('续写提示词已改变，未提交。')
        refs=job['references'];indexes=job['promptReferenceIndexes']
        reference_prompt=row['videoPrompt']
        if job.get('extensionBindingVersion')==2:
            try:
                draft=json.loads(row.get('extraJson') or '{}')
                workspace=draft['workspaces']['extend'];assets=workspace['referenceAssets']
                if not all(a.get('id') for a in assets) or len({a['id'] for a in assets})!=len(assets):raise ValueError()
                targets=[a for a in assets if a.get('referenceRole')=='target-video']
                target=extension_target_props(job)
                target['id']='beeftv-target-'+hashlib.sha256(target['url'].encode()).hexdigest()[:16]
                if (draft['schemaVersion']!=1 or workspace['prompt']!=row['videoPrompt'] or len(targets)!=1 or
                    targets[0]!=target or any(type(targets[0][key]) is not type(value) for key,value in target.items())):raise ValueError()
                actual=[(a.get('materialId'),a.get('url')) for a in assets if a.get('fileType')==1]
                if actual!=[(r['imageId'],r['url']) for r in refs]:raise ValueError()
                audio=[(a.get('materialId'),a.get('materialType'),a.get('url'),a.get('voiceId'),a.get('uploadVoiceUrl'),a.get('durationSeconds'))
                       for a in assets if a.get('fileType')==3]
                expected_audio=[(r['imageId'],1,r['voiceUrl'],r['voiceId'],r['uploadVoiceUrl'],r['voiceDuration'])
                                for r in refs if 'voiceId' in r]
                if audio!=expected_audio:raise ValueError()
            except (ValueError,KeyError,TypeError,AttributeError):
                raise BridgeError('延长编辑器的目标视频或参考素材丢失或已改变，未提交。') from None
            reference_prompt=without_verified_target_mentions(job,reference_prompt)
        self.verify_reference_tags(reference_prompt,[refs[i] for i in indexes])
        return row

    @job_scope
    def submitted_task_id(self,job):
        # Successful produceVideo moves the submitted parameters into history
        # and clears targetVideoUrl / omniReferenceTaskType on the editable board.
        # Reconcile the immutable submission, never re-run draft readiness checks.
        if job.get('status') not in ('submitting','submission_unknown') or not job.get('approvedAt'):
            raise BridgeError('缺少已提交意图，不能接管历史任务。')
        result=self._request('/storyboard/api/historyList',{'seriesId':series_id(),'storyBoardId':job['storyboardId'],
                            'page':1,'size':100,'isAll':True})
        rows=result.get('list');total=result.get('total')
        if not isinstance(rows,list) or type(total) is not int or total!=len(rows):
            raise BridgeError('延长提交历史不完整，继续只读核对。')
        expected={'projectId':project_id(),'seriesId':series_id(),'produceType':5,'duration':job['duration'],
                  'omniReferenceTaskType':'extend','targetVideoUrl':job['sourceVideoUrl']}
        ids=set()
        for row in rows:
            if not isinstance(row,dict) or any(row.get(k)!=v for k,v in expected.items()):continue
            prompt=row.get('videoPrompt');task_id=row.get('videoTaskId')
            if (isinstance(prompt,str) and hashlib.sha256(prompt.encode()).hexdigest()==job['savedPromptHash']
                    and type(task_id) is int and task_id>0):ids.add(task_id)
        if len(ids)>1:raise BridgeError('存在多个相同延长历史任务，不能自动选择或重提。')
        return next(iter(ids),None)

    @job_scope
    def estimate_extension(self,job):
        spec=job.get('modelSnapshot') or extension_models()[job['model']]
        result=self._request('/storyboard/endpoint/calcToken',{'eid':spec['eid'],'type':1,'number':1,
            'duration':job['duration'],'referenceVideoTotalDuration':f"{job['sourceVideo']['duration']:.2f}"})
        points=result.get('token')
        if type(points) not in (int,float) or not math.isfinite(points) or points<0:raise BridgeError('无法确认本次延长积分。')
        return points

    @job_scope
    def check_ready(self,job):
        verify_frozen(self,job)
        if job.get('extensionBindingVersion')!=2:raise BridgeError('旧延长草稿缺少原生视频绑定，请重新准备。')
        if self.media.identity()!=job['identity']:raise BridgeError('Dreama账号已改变，请重新准备任务。')
        project=self.run('project','get','--id',project_id())
        if project.get('id')!=project_id() or project.get('promptMode')!='drama':raise BridgeError('绑定项目模式已改变。')
        live=build_models(self._request('/storyboard/endpoint/list',{'endpointModes':[5],'type':1,'isMerge':False,'location':100}).get('items',[]))
        spec=live.get(job['model'])
        if not spec or job['duration'] not in spec['durations']:raise BridgeError('所选模型已不支持当前延长设置。')
        validate_source(job['sourceVideo'],spec)
        if len(job['references'])>spec['maxImages'] or len(job['prompt'])>spec['promptMaxChars']:raise BridgeError('当前模型输入限制已变化。')
        self.check_character_references(job['references'])
        if self.inspect_board(job).get('videoTaskId'):raise BridgeError('该延长分镜已有视频任务，禁止重提。')

    @job_scope
    def submit_video(self,job):
        if (job.get('kind')!='extension' or job.get('status')!='submitting' or not job.get('approvedAt') or
            job.get('approvedPoints')!=job.get('pointsEstimate')):raise BridgeError('视频延长缺少本次费用确认。')
        spec=job.get('modelSnapshot') or extension_models()[job['model']]
        # Match the website's inline-mention traversal and URL deduplication.
        # The target video is always first, independent of its prompt position.
        urls=[job['sourceVideoUrl']]
        for index in job['promptReferenceIndexes']:
            ref=job['references'][index];urls.append(ref['url'])
            if 'voiceId' in ref:urls.append(ref['voiceUrl'])
        urls=list(dict.fromkeys(urls))
        payload={'storyBoardId':job['storyboardId'],'projectId':project_id(),'seriesId':series_id(),'produceType':5,'type':5,
                 'duration':job['duration'],'LLM':spec['eid'],'resolution':spec['resolution'],'produceVideoFormat':'mp4',
                 'omniReferenceTaskType':'extend','images':urls}
        return self._request('/storyboard/api/produceVideo',payload,paid=True)

class ExtensionFlow(Generation):
    def __init__(self,bridge,gateway=None,download=download_video):
        super().__init__(bridge,download,gateway=gateway or ExtensionGateway())
        self.inputs=bridge.root/'extension-inputs';self.inputs.mkdir(exist_ok=True)

    @preparation_scope
    def prepare(self,data,key):
        prompt,duration,raw,images=validate_extension_request(data)
        spec=resolve_extension_model(data)
        if not isinstance(key,str) or not 1<=len(key)<=150:raise BridgeError('请求ID无效。')
        digest=hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        frozen=freeze(data,self.gateway,spec)
        with self.bridge.lock:
            with self.bridge.db() as db:
                db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT digest,result FROM jobs WHERE key=?',(key,)).fetchone()
                if row and json.loads(row[1]).get('status')=='preparation_queued':
                    if row[0]!=digest: raise BridgeError('同一请求ID对应不同内容。')
                    self.bridge.ensure_not_cancelled(key)
                    row=None
                if row:
                    if row[0]!=digest:raise BridgeError('同一请求ID对应不同内容。')
                    prior=json.loads(row[1])
                    if prior['status']!='prepared':raise BridgeError('已有记录需先对账，不重复准备。')
                    return prior
                job={'id':key,'kind':'extension','model':spec['id'],'selectedModel':data['model'],
                     'resolution':spec['resolution'],'modelLabel':spec['label'],'duration':duration,'prompt':prompt,
                     'status':'preparing','stage':'probe','createdAt':time.time(),'requestDigest':digest,
                     'name':'BeefTV_Ext_'+uuid.uuid4().hex[:12],'generationSubmitted':False,
                     'projectId':project_id(),'seriesId':series_id(),'references':[]}
                job.update(frozen)
                db.execute('INSERT INTO jobs VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET result=excluded.result',(key,digest,json.dumps(job)))
            try:
                from voice_bindings import validate_video_bindings, prepare_video_voices
                bindings=validate_video_bindings(data,len(images),spec)
                character_voices=prepare_video_voices(bindings,spec,self.bridge) if bindings else {}
                source_key=hashlib.sha256(raw).hexdigest()+'.mp4';source=self.inputs/source_key
                if not source.exists():
                    tmp=source.with_suffix('.tmp');tmp.write_bytes(raw);os.replace(tmp,source)
                if file_digest(source)!=source_key[:-4]:raise BridgeError('原视频缓存校验失败。')
                meta=probe_video(source);validate_source(meta,spec)
                job.update(sourceKey=source_key,sourceVideo=meta,identity=frozen['bindingSnapshot']['identity'] or self.gateway.media.identity(),stage='upload')
                self.bridge.update(key,job)
                job['sourceVideoUrl']=self.gateway.media.upload_video(source,meta);self.bridge.update(key,job)
                for index,image in enumerate(images,1):
                    name=job['name']+'_参考图'+str(index);job['stage']='reference:'+str(index);self.bridge.update(key,job)
                    url=self.gateway.upload_image(image,name)
                    ref=(self.gateway.create_character_reference(name,url,character_voices[index])
                         if index in character_voices else self.gateway.create_reference(name,url))
                    job['references'].append(ref);self.bridge.update(key,job)
                job['stage']='save';self.bridge.update(key,job)
                self.gateway.prepare_board(job);self.bridge.update(key,job)
                job['pointsEstimate']=self.estimate_job(job)
                job.update(status='prepared',stage='complete',quoteId=uuid.uuid4().hex)
                self.bridge.update(key,job);return job
            except Exception as error:
                job.update(status='unknown',error=str(error) if isinstance(error,BridgeError) else type(error).__name__)
                self.bridge.update(key,job)
                raise BridgeError('延长预检中止，未提交收费任务。'+job['error']) from None

    @job_scope
    def estimate_job(self,job):return self.gateway.estimate_extension(job)

    @job_scope
    def reconcile_task_id(self,job):return self.gateway.submitted_task_id(job)

    @job_scope
    def check_ready(self,job):
        key=job.get('sourceKey','')
        if not re.fullmatch(r'[0-9a-f]{64}\.mp4',key):raise BridgeError('原视频缓存标识无效。')
        source=self.inputs/key
        if not source.is_file() or file_digest(source)!=job['sourceVideo']['sha256']:raise BridgeError('原视频已改变或缺失，请重新准备。')
        self.gateway.check_ready(job)
