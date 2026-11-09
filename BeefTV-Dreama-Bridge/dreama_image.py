"""Catalog-backed Dreama images with dedicated storyboard reconciliation."""
from job_binding import project_id, series_id, job_scope, verify_frozen

import base64
import hashlib
import html
import io
import json
import os
import re
import warnings
from pathlib import Path
from urllib.parse import urlsplit

import requests
from PIL import Image
from budget_policy import BudgetError, validated_quote
from dreama_video import BridgeError, DreamaGateway, image_reference_indexes

MAX_RESULT_BYTES=64*1024*1024
MAX_RESULT_PIXELS=64*1024*1024
MEDIA_KEY_PATTERN=r'[0-9a-f]{48}\.img'
LIVE_ITEMS=None


def image_models(include_disabled=False):
    """Exact EIDs, independent resolution choices, and reference limits."""
    items=LIVE_ITEMS if LIVE_ITEMS is not None else json.loads((Path(__file__).parent/'image-catalog.json').read_text('utf-8'))['items']
    return build_image_models(items,include_disabled)

def build_image_models(items,include_disabled=False):
    result={}
    for item in items:
        limits=json.loads(item.get('modelLimit') or '{}')
        enabled=item.get('isExternal')==0
        if not enabled and not include_disabled: continue
        resolutions=[r['resolution'] for r in item.get('resolutionTokens',[]) if isinstance(r.get('resolution'),str)]
        ratios=item.get('proportionList',[])
        result[item['eid']]={'id':item['eid'],'eid':item['eid'],'name':item['name'],'label':item['name'],
            'resolutions':resolutions,'aspectRatios':ratios,'proportions':ratios,
            'defaultResolution':'2K' if '2K' in resolutions else resolutions[0],
            'minImages':int(limits.get('imageMin',0)),'maxImages':int(limits.get('imageMax',0)),
            'maxOutputs':1,'promptMaxChars':5000 if item.get('vendorGroupCode')=='Vendor_JiuWan' else 10000,
            'enabled':enabled,'disabledReason':item.get('disabledReason',''),
            'vendorGroupCode':item.get('vendorGroupCode'),'limits':limits}
    return result


def _positive_points(value):
    try:
        return validated_quote(value, maximum=1000000)
    except BudgetError:
        raise BridgeError('无法确认图片生成积分。') from None


def _decode_reference(value,spec):
    allowed={'image/png':'PNG','image/jpeg':'JPEG','image/jpg':'JPEG'}
    if not isinstance(value,str) or not value.startswith('data:') or ';base64,' not in value:
        raise BridgeError('图片参考需为PNG/JPEG的内联data URL，不读取任意网址或本地路径。')
    head,encoded=value.split(',',1); mime=head[5:].split(';')[0]
    if head!='data:'+mime+';base64' or mime not in allowed:
        raise BridgeError('当前剧梦图像参考仅接收PNG/JPEG，请先转换图片格式。')
    maximum=min(30,float(spec['limits'].get('imageSizeMax',30)))*1024*1024
    if len(encoded)>4*(int(maximum+2)//3): raise BridgeError('参考图超过该模型单图大小限制。')
    try:
        raw=base64.b64decode(encoded,validate=True)
        if not raw or len(raw)>maximum: raise BridgeError('参考图为空或超过该模型单图大小限制。')
        with warnings.catch_warnings():
            warnings.simplefilter('error',Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as picture:
                if picture.format!=allowed[mime] or getattr(picture,'n_frames',1)!=1:
                    raise BridgeError('参考图格式与声明不符或包含多帧。')
                width,height=picture.size
                if min(width,height)<=0 or max(width,height)>32768 or width*height>MAX_RESULT_PIXELS:
                    raise BridgeError('参考图尺寸超出本地处理范围。')
                vendor=spec.get('vendorGroupCode')
                if vendor=='Vendor_JiuWan' and (min(width,height)<240 or max(width,height)>8000 or max(width/height,height/width)>8):
                    raise BridgeError('九万参考图每边需240–8000像素，宽高比需在1:8至8:1。')
                if vendor=='Vendor_DouBao_Seedream' and (min(width,height)<=14 or width*height>6000*6000 or max(width/height,height/width)>16):
                    raise BridgeError('九梦参考图每边须大于14像素、总像素不超过3600万、宽高比在1:16至16:1。')
                picture.verify()
            with Image.open(io.BytesIO(raw)) as picture: picture.load()
        return raw
    except BridgeError: raise
    except Exception: raise BridgeError('参考图编码或图片内容无效。') from None


def validate_image_request(data):
    if not isinstance(data,dict): raise BridgeError('图片生成请求必须为JSON对象。')
    if set(data)-{'model','prompt','images','resolution','aspect_ratio','n','dreama'}:
        raise BridgeError('图片请求包含尚未支持的参数。')
    model=data.get('model')
    if not isinstance(model,str): raise BridgeError('图片模型无效。')
    spec=image_models(include_disabled=True).get(model)
    if not spec: raise BridgeError('图片模型不在当前剧梦目录中。')
    if not spec['enabled']: raise BridgeError(spec['disabledReason'])
    prompt=data.get('prompt')
    if not isinstance(prompt,str) or not prompt.strip() or len(prompt)>spec['promptMaxChars']:
        raise BridgeError('图片提示词为空或超过当前长度限制。')
    resolution=data.get('resolution') or spec['defaultResolution']
    ratio=data.get('aspect_ratio') or '1:1'
    if resolution not in spec['resolutions']: raise BridgeError('分辨率不在该图像模型支持范围。')
    if ratio not in spec['aspectRatios']: raise BridgeError('画面比例不在该图像模型支持范围。')
    if type(data.get('n',1)) is not int or data.get('n',1)!=1:
        raise BridgeError('当前原生接口每次提交一项任务，n必须为1。')
    images=data.get('images',[])
    if not isinstance(images,list) or not spec['minImages']<=len(images)<=spec['maxImages']:
        raise BridgeError('参考图数量超出该图像模型限制。')
    image_reference_indexes(prompt,len(images))
    decoded=[_decode_reference(image,spec) for image in images]
    if sum(map(len,decoded))>float(spec['limits'].get('totalSizeMax',420))*1024*1024:
        raise BridgeError('参考图合计大小超出该模型限制。')
    return {'model':spec,'prompt':prompt,'images':decoded,'resolution':resolution,'aspect_ratio':ratio,'n':1}


def download_image(url,path):
    try:
        parsed=urlsplit(url)
        allowed=(parsed.scheme=='https' and parsed.hostname=='jzinnovation.jxbaobian.cn'
                 and parsed.port in (None,443) and not parsed.username and not parsed.password)
    except ValueError: raise BridgeError('图像结果URL无效。') from None
    if not allowed:
        raise BridgeError('图像结果地址不在已核实的剧梦CDN中。')
    path=Path(path); temporary=path.with_suffix('.part')
    try:
        with requests.get(url,stream=True,timeout=(10,60),allow_redirects=False) as response:
            if response.status_code!=200: raise BridgeError('图像结果下载失败。')
            count=0; digest=hashlib.sha256()
            with temporary.open('wb') as output:
                for chunk in response.iter_content(1024*1024):
                    count+=len(chunk)
                    if count>MAX_RESULT_BYTES: raise BridgeError('图像结果超过64MiB缓存限制。')
                    output.write(chunk); digest.update(chunk)
        with warnings.catch_warnings():
            warnings.simplefilter('error',Image.DecompressionBombWarning)
            with Image.open(temporary) as picture:
                mime={'PNG':'image/png','JPEG':'image/jpeg','WEBP':'image/webp'}.get(picture.format)
                width,height=picture.size
                if not mime or min(width,height)<=0 or width*height>MAX_RESULT_PIXELS or getattr(picture,'n_frames',1)!=1:
                    raise BridgeError('任务结果不是支持的静态图像。')
                picture.verify()
            with Image.open(temporary) as picture: picture.load()
        os.replace(temporary,path)
        return {'bytes':count,'sha256':digest.hexdigest(),'mimeType':mime,'width':width,'height':height}
    except BridgeError: raise
    except Exception: raise BridgeError('图像结果下载或内容校验失败。') from None
    finally: temporary.unlink(missing_ok=True)


class ImageGateway(DreamaGateway):
    def identity(self):
        account=self._invoke('auth','whoami')
        if type(account.get('userId')) is not int or account['userId']<=0 or account.get('expired'):
            raise BridgeError('无法确认当前Dreama账号。')
        return {key:account[key] for key in ('userId','teamId','orgId')
                if type(account.get(key)) is int and account[key]>0}

    def render_image_prompt(self,prompt,refs):
        indexes=image_reference_indexes(prompt,len(refs)); tags=[]
        if refs:
            args=['prompt','assemble','--project-id',project_id(),'--kind','image','--prompt','\n'.join(r['name'] for r in refs)]
            for ref in refs: args+=['--ref',ref['name']]
            assembled=self.run(*args); assembled_text=assembled.get('prompt','')
            if assembled.get('failed') or assembled.get('mode')!='drama' or not assembled_text:
                raise BridgeError('官方图像引用标签组装失败。')
            self.verify_reference_tags(assembled_text,refs)
            tags=re.findall(r'<span\b[^>]*></span\s*>',assembled_text)
            if len(tags)!=len(refs): raise BridgeError('图像引用标签数量不一致。')
        def escape(value):
            return html.escape(value,quote=False).replace('\r\n','\n').replace('\r','\n').replace('\n','<br>')
        parts=[]; end=0
        for match,index in zip(re.finditer(r'@图片([0-9]+)',prompt),indexes):
            parts.extend((escape(prompt[end:match.start()]),tags[index])); end=match.end()
        parts.append(escape(prompt[end:])); text='<p>'+''.join(parts)+'</p>'
        self.verify_reference_tags(text,[refs[index] for index in indexes])
        return text,indexes

    @job_scope
    def prepare_board(self,job):
        text,indexes=self.render_image_prompt(job['prompt'],job['references'])
        self.run('storyboard','create','--series-id',series_id(),'--name',job['name'],'--yes')
        rows=self.run('storyboard','list','--series-id',series_id(),'--name',job['name']).get('items',[])
        if len(rows)!=1 or type(rows[0].get('id')) is not int: raise BridgeError('图像分镜回读不唯一。')
        board_id=rows[0]['id']
        self.run('storyboard','update','--series-id',series_id(),'--id',board_id,'--image-prompt',text,'--produce-type',3,'--yes')
        rows=self.run('storyboard','list','--series-id',series_id(),'--id',board_id).get('items',[])
        if (len(rows)!=1 or rows[0].get('id')!=board_id or rows[0].get('projectId')!=project_id()
                or rows[0].get('seriesId')!=series_id() or rows[0].get('imagePrompt')!=text):
            raise BridgeError('图像分镜提示词或项目绑定回读不一致。')
        self.verify_reference_tags(rows[0]['imagePrompt'],[job['references'][index] for index in indexes])
        job.update(storyboardId=board_id,promptPolicy='verbatim-v1',promptReferenceIndexes=indexes,
                   savedPromptHash=hashlib.sha256(text.encode()).hexdigest(),promptVerified=True,referencesVerified=True)

    @job_scope
    def inspect_board(self,job):
        rows=self.run('storyboard','list','--series-id',series_id(),'--id',job['storyboardId']).get('items',[])
        if len(rows)!=1: raise BridgeError('图像分镜回读不唯一。')
        board=rows[0]
        if board.get('id')!=job['storyboardId'] or board.get('seriesId')!=series_id() or board.get('projectId')!=project_id():
            raise BridgeError('图像分镜项目或剧集绑定已改变。')
        text=board.get('imagePrompt','')
        if not isinstance(text,str) or hashlib.sha256(text.encode()).hexdigest()!=job.get('savedPromptHash'):
            raise BridgeError('图像提示词已改变，请重新准备任务。')
        indexes=job.get('promptReferenceIndexes',[]); refs=job['references']
        if any(type(i) is not int or not 0<=i<len(refs) for i in indexes): raise BridgeError('图像引用顺序无效。')
        self.verify_reference_tags(text,[refs[index] for index in indexes])
        return board

    @job_scope
    def estimate_image(self,job):
        return _positive_points(self.run('point','calc','--kind','image','--project-id',project_id(),
            '--llm',job['model'],'--image-resolution',job['resolution'],'--number',1).get('token'))

    @job_scope
    def check_ready(self,job):
        verify_frozen(self,job)
        spec=job.get('modelSnapshot') or image_models().get(job.get('model'))
        if job.get('kind')!='image' or not spec or spec.get('id')!=job.get('model') or not spec.get('enabled'):
            raise BridgeError('图像模型不可用于云端生成。')
        if self.identity()!=job.get('identity'):
            raise BridgeError('Dreama账号已改变，请重新创建图像任务。')
        project=self.run('project','get','--id',project_id())
        if project.get('id')!=project_id() or project.get('promptMode')!='drama': raise BridgeError('绑定项目模式已改变。')
        items=self._invoke('capability','list','--kind','image').get('items',[])
        matches=[item for item in items if item.get('eid')==job['model']]
        if len(matches)!=1: raise BridgeError('图像模型已不在当前可用目录。')
        if job['model'] not in build_image_models(matches):
            raise BridgeError('图像模型已不属于当前已接通的云端能力，未提交。')
        model=matches[0]; limits=json.loads(model.get('modelLimit') or '{}')
        if job['resolution'] not in [r.get('resolution') for r in model.get('resolutionTokens',[])]:
            raise BridgeError('图像模型已不支持所选分辨率。')
        if job['aspect_ratio'] not in model.get('proportionList',[]) or len(job['references'])>limits.get('imageMax',0):
            raise BridgeError('图像模型画幅或参考图限制已变化。')
        if self.inspect_board(job).get('imageTaskId'): raise BridgeError('该分镜已有图像任务，禁止重复提交。')

    @job_scope
    def submit_video(self,job):
        from submission import verify_submission_intent
        verify_submission_intent(job)
        # Generation.approve calls this gateway method once after durable intent.
        spec=job.get('modelSnapshot') or image_models().get(job.get('model'))
        if (job.get('kind')!='image' or job.get('status')!='submitting' or not spec
                or spec.get('id')!=job.get('model') or not spec.get('enabled')):
            raise BridgeError('图像生成缺少本次费用确认。')
        return self._invoke('image','produce','--project-id',project_id(),'--series-id',series_id(),
            '--storyboard-id',job['storyboardId'],'--llm',job['model'],'--image-resolution',job['resolution'],
            '--proportion',job['aspect_ratio'],'--yes',job=job)
