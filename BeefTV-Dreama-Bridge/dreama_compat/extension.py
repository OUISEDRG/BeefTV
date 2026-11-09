"""Dreama cloud video extension: a target video plus verbatim continuation text."""
from job_binding import project_id, series_id, job_scope, verify_frozen

import hashlib
import html
import json
import math
import re
import time
from pathlib import Path
from urllib.parse import quote
from budget_policy import BudgetError, validated_quote
from dreama_video import BridgeError,DreamaGateway,decode_image,image_reference_indexes
from .enhancement import EnhancementGateway,decode_video
from . import http
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
    return build_models(LIVE_ITEMS if LIVE_ITEMS is not None else json.loads((Path(__file__).parents[1]/'extension-catalog.json').read_text('utf-8'))['items'])

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
    READ_PATHS=http.EXTENSION_READS
    def __init__(self):super().__init__();self.media=EnhancementGateway()

    def render_verbatim_prompt(self,prompt,refs,**kwargs):
        kwargs['allow_video_placeholders']=True
        return super().render_verbatim_prompt(prompt,refs,**kwargs)

    def save_and_readback(self,prompt,refs,duration,name,**kwargs):
        kwargs['allow_video_placeholders']=True
        return super().save_and_readback(prompt,refs,duration,name,**kwargs)

    def validate_binding(self,binding,spec):
        identity=self.identity();self.verify_project(binding)
        live=build_models(self.capability_rows()).get(spec['id'])
        if not live or any(live.get(field)!=spec.get(field) for field in ('durations','resolution','maxImages','promptMaxChars','sourceMin','sourceMax')):
            raise BridgeError('延长模型限制已变化，请刷新目录后重新准备。')
        return identity

    def _request(self,path,payload,*,paid=False,job=None):
        if paid:
            if path != '/storyboard/api/produceVideo':
                raise BridgeError('非授权收费接口。')
            result = http.submit(self, 'extension', job, payload)
        else:
            if path not in self.READ_PATHS | {'/storyboard/api/save'}:
                raise BridgeError('延长接口不在已核实范围。')
            result = http.request(self, 'POST', path, payload=payload)
        return result.get('item', result) if isinstance(result, dict) else result or {}

    def capability_rows(self):
        return self._request('/storyboard/endpoint/list',
            {'endpointModes':[5],'type':1,'isMerge':False,'location':100}).get('items',[])

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
        if job.get('status') not in ('submitting','submission_unknown') or not (job.get('submissionIntentId') or job.get('approvedAt')):
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
        try:
            return validated_quote(result.get('token'))
        except BudgetError:
            raise BridgeError('无法确认本次延长积分。') from None

    @job_scope
    def check_ready(self,job):
        verify_frozen(self,job)
        if job.get('extensionBindingVersion')!=2:raise BridgeError('旧延长草稿缺少原生视频绑定，请重新准备。')
        if self.media.identity()!=job['identity']:raise BridgeError('Dreama账号已改变，请重新准备任务。')
        project=self.run('project','get','--id',project_id())
        if project.get('id')!=project_id() or project.get('promptMode')!='drama':raise BridgeError('绑定项目模式已改变。')
        live=build_models(self.capability_rows())
        spec=live.get(job['model'])
        if not spec or job['duration'] not in spec['durations']:raise BridgeError('所选模型已不支持当前延长设置。')
        validate_source(job['sourceVideo'],spec)
        if len(job['references'])>spec['maxImages'] or len(job['prompt'])>spec['promptMaxChars']:raise BridgeError('当前模型输入限制已变化。')
        self.check_character_references(job['references'])
        if self.inspect_board(job).get('videoTaskId'):raise BridgeError('该延长分镜已有视频任务，禁止重提。')

    @job_scope
    def submit_video(self,job):
        from submission import verify_submission_intent
        verify_submission_intent(job)
        if job.get('kind')!='extension':raise BridgeError('视频延长缺少持久化收费意图。')
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
        return self._request('/storyboard/api/produceVideo',payload,paid=True,job=job)
