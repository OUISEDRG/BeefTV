"""Dreama video gateway, bound project reads and verbatim media prompts."""
import base64
import hashlib
import html
import io
import json
import os
import re
from pathlib import Path

from PIL import Image

from bridge_errors import BridgeError
from dreama_cli import DreamaCliClient
from dreama_compat import assets, http as compat_http
from job_binding import job_scope, project_id, series_id, verify_frozen, verify_write_identity
from model_catalog import models

MODEL = '7573d384-0128-4424-9e90-8a0291b9274b'
PROJECT = 412186
SERIES = 3279881
MAX_IMAGE = 10 * 1024 * 1024


def image_reference_indexes(prompt, count):
    available={str(i+1):i for i in range(count)}
    indexes=[]
    for match in re.finditer(r'@图片([0-9]+)',prompt):
        if match[1] not in available:
            raise BridgeError('提示词里的图片编号没有对应参考图，未改写或提交。')
        indexes.append(available[match[1]])
    if set(indexes)!=set(range(count)):
        raise BridgeError('请在原提示词需要的位置引用已连接的图片（如 @图片1）；为保留原文，不再自动添加参考图说明。')
    return indexes


def decode_image(value):
    if not isinstance(value, str) or not value.startswith(('data:image/png;base64,', 'data:image/jpeg;base64,', 'data:image/webp;base64,')):
        raise BridgeError('参考图必须是PNG/JPEG/WebP的data URL；不读取任意网络地址或本地文件路径。')
    head, encoded = value.split(',', 1)
    if len(encoded) > (MAX_IMAGE * 4 // 3 + 8):
        raise BridgeError('单张参考图不得超过10MB。')
    try:
        data = base64.b64decode(encoded, validate=True)
        with Image.open(io.BytesIO(data)) as img:
            expected = {'PNG': 'image/png', 'JPEG': 'image/jpeg', 'WEBP': 'image/webp'}.get(img.format)
            if expected != head[5:].split(';')[0] or not all(300 <= n <= 6000 for n in img.size):
                raise BridgeError('图片格式或尺寸不符：每边需300–6000像素。')
            img.verify()
        if len(data) > MAX_IMAGE:
            raise BridgeError('图片超过10MB。')
        return data
    except BridgeError:
        raise
    except Exception:
        raise BridgeError('参考图数据无效。') from None


class DreamaGateway:
    ALLOWED_PATHS = compat_http.ASSET_WRITES

    def __init__(self):
        self.cli_client = DreamaCliClient()
        self.credentials = Path(os.environ['APPDATA']) / 'Dreama/credentials-prod.json'

    def identity(self):
        account=self._invoke('auth','whoami')
        if type(account.get('userId')) is not int or account['userId']<=0 or account.get('expired'):
            raise BridgeError('无法确认当前Dreama账号。')
        return {k:account[k] for k in ('userId','teamId','orgId') if type(account.get(k)) is int and account[k]>0}

    def verify_project(self,binding):
        project=self.run('project','get','--id',binding['projectId'])
        if (project.get('id')!=binding['projectId'] or project.get('promptMode')!='drama'
                or (project.get('proportion') or project.get('aspectRatio'))!=binding['aspectRatio']):
            raise BridgeError('项目不存在、模式或画幅与绑定不一致。')
        if binding.get('requiresStandaloneAudio'):
            if project.get('modeType') != 3:
                raise BridgeError('独立音频参考仅支持剧梦自由模式项目；当前项目不是自由模式，请改用人物音色或绑定自由模式项目。尚未提交。')
            if binding.get('projectModeType', 3) != project['modeType']:
                raise BridgeError('项目模式与准备任务时不一致，停止提交。')
            binding['projectModeType'] = project['modeType']
        episode=self.run('series','get','--id',binding['seriesId'])
        if episode.get('id')!=binding['seriesId'] or episode.get('projectId')!=binding['projectId']:
            raise BridgeError('剧集不存在或不属于绑定项目。')

    def validate_binding(self,binding,spec):
        identity=self.identity()
        self.verify_project(binding)
        # Only image/video endpoints have a CLI capability contract. Lab engines
        # are allowlisted by their dedicated verified gateway specifications.
        eid=spec.get('eid') or spec.get('id')
        if eid and not eid.startswith('dreama-'):
            kind='image' if 'aspectRatios' in spec else 'video'
            rows=self._invoke('capability','list','--kind',kind).get('items',[])
            if len([r for r in rows if r.get('eid')==eid and r.get('isExternal',0)==0])!=1:
                raise BridgeError('所选模型已不在当前账号可用目录。')
            if kind=='image':
                from dreama_image import build_image_models
                live=build_image_models(rows).get(eid)
                fields=('minImages','maxImages','promptMaxChars','resolutions','aspectRatios')
            else:
                from model_catalog import build_models
                live=build_models(rows).get(eid)
                fields=('minImages','maxImages','promptMaxChars','durations','resolution','limits')
            if not live or any(field in spec and live.get(field)!=spec[field] for field in fields):
                raise BridgeError('模型能力已变化，请刷新目录后重新准备；尚未写入素材。')
        return identity

    def catalogs(self):
        from dreama_compat.extension import ExtensionGateway
        return {'video':self._invoke('capability','list','--kind','video').get('items',[]),
                'image':self._invoke('capability','list','--kind','image').get('items',[]),
                'extension':ExtensionGateway().capability_rows()}

    def post(self, path, payload):
        if path not in self.ALLOWED_PATHS:
            raise BridgeError('此版本禁止调用生成接口。')
        result = compat_http.request(self, 'POST', path, payload=payload)
        if result is None:
            return {}
        if not isinstance(result, dict):
            raise BridgeError('剧梦接口成功回执格式无效。')
        item = result.get('item', result)
        if item is None:
            return {}
        if not isinstance(item, dict):
            raise BridgeError('剧梦接口成功回执格式无效。')
        return item

    def run(self, *args):
        return self.cli_client.run(*args, before_write=lambda: verify_write_identity(self))

    def _invoke(self, *args, job=None):
        if job is None:
            return self.run(*args)
        return self.cli_client.submit(job, *args, before_write=lambda: verify_write_identity(self))

    def upload_image(self, data, name):
        return assets.upload_image(self, data, name)

    def create_reference(self, name, url):
        return assets.create_reference(self, name, url)

    def create_character_reference(self, name, url, voice):
        return assets.create_character_reference(self, name, url, voice)

    def read_reference(self, ref, material_type):
        name = ref.get('assetName', ref['name'])
        result = self.run('library', 'list', '--project-id', project_id(), '--type', material_type, '--search', name)
        matches = [(asset, image) for asset in result.get('items', []) if asset.get('name') == name
                   for image in asset.get('images', []) if ref['url'] in image.get('resourceUrl', [])]
        if len(matches) != 1:
            raise BridgeError('图片绑定回读不唯一或URL不符，未提交。')
        asset, image = matches[0]
        if (not image.get('name') or ref.get('assetId', asset['assetId']) != asset['assetId']
                or ref.get('imageId', image['imageId']) != image['imageId']):
            raise BridgeError('图片绑定已改变，未提交。')
        return asset, image

    def read_character_reference(self, ref):
        asset, image = self.read_reference(ref, 1)
        if (asset.get('voiceId', 0) != ref['voiceId'] or (asset.get('uploadVoiceUrl') or '') != ref['uploadVoiceUrl']):
            raise BridgeError('人物音色或图片绑定已改变，未提交。')
        return asset, image

    def recover_reference(self, name, url, voice=None):
        return assets.recover_reference(self, name, url, voice)

    def check_character_references(self, refs):
        from voice_bindings import catalog
        refreshed = None
        for ref in refs:
            if 'voiceId' not in ref:
                continue
            self.read_character_reference(ref)
            if ref['voiceId']:
                refreshed = refreshed if refreshed is not None else catalog().get(force=True)
                row = refreshed.get(str(ref['voiceId']))
                if not row or (row.get('videoUrl') or row['voiceUrl']) != ref['voiceUrl']:
                    raise BridgeError('人物音色已删除、改变或当前账号无权使用，请重新创建任务。')

    @staticmethod
    def prompt_with_references(prompt, refs):
        return prompt

    def upload_audio_reference(self, path, meta):
        from dreama_compat.audio import AudioGateway
        gateway = AudioGateway()
        gateway.base = self
        return gateway.upload_audio(path, meta)

    def upload_video_reference(self, path, meta):
        from dreama_compat.enhancement import EnhancementGateway
        gateway=EnhancementGateway()
        gateway.base=self
        return gateway.upload_video(path,meta)

    def render_verbatim_prompt(self, prompt, refs, audio_refs=None, video_refs=None, *, allow_video_placeholders=False):
        from video_audio_references import audio_reference_tag, ordered_references, prompt_reference_order
        from video_reference_media import video_reference_tag
        audio_refs = audio_refs or []
        video_refs = video_refs or []
        prompt=self.prompt_with_references(prompt,refs)
        indexes=image_reference_indexes(prompt,len(refs))
        tags=[]
        if refs:
            # Ask the official assembler only for reference metadata. None of
            # its prose or automatic instructions are copied to the user text.
            args = ['prompt', 'assemble', '--project-id', project_id(), '--kind', 'video', '--prompt', '\n'.join(r['name'] for r in refs)]
            for ref in refs:
                args += ['--ref', ref['name']]
            assembled = self.run(*args)
            assembled_text = assembled.get('prompt')
            if assembled.get('failed') or not assembled_text or assembled.get('mode') != 'drama':
                raise BridgeError('参考图标签组装失败或模式不符。')
            image_refs=[{k:v for k,v in ref.items() if k != 'voiceId'} for ref in refs]
            self.verify_reference_tags(assembled_text,image_refs)
            tags=re.findall(r'<span\b[^>]*></span\s*>',assembled_text)
            if len(tags)!=len(refs): raise BridgeError('无法唯一提取官方图片引用标签。')
            self.verify_reference_tags(''.join(tags),image_refs)
            for index, ref in enumerate(refs):
                if 'voiceId' not in ref:
                    continue
                # Only extract the machine-readable audio mention. The CLI's
                # automatically rewritten dialogue/prose never enters the prompt.
                audio = self.run('prompt', 'assemble', '--project-id', project_id(), '--kind', 'video',
                    '--prompt', '参考'+ref['name']+'音色', '--ref', ref['name'])
                if audio.get('failed') or audio.get('mode') != 'drama':
                    raise BridgeError('官方人物音色引用组装失败。')
                audio_tags=re.findall(r'<span\b[^>]*></span\s*>',audio.get('prompt',''))
                audio_ref={**ref, 'fileType':3}; audio_ref.pop('voiceId',None)
                self.verify_reference_tags(''.join(audio_tags),[audio_ref])
                if len(audio_tags)!=1: raise BridgeError('官方人物音色引用不唯一。')
                tags[index] += audio_tags[0]
        def escape(value):
            return html.escape(value,quote=False).replace('\r\n','\n').replace('\r','\n').replace('\n','<br>')
        parts=[]; end=0
        order=prompt_reference_order(prompt,refs,audio_refs,video_refs,allow_video_placeholders=allow_video_placeholders)
        pattern=r'@(图片|音频)([0-9]+)' if allow_video_placeholders else r'@(图片|音频|视频)([0-9]+)'
        for match,(kind,index) in zip(re.finditer(pattern,prompt),order):
            tag=(tags[index] if kind=='image' else audio_reference_tag(audio_refs[index]) if kind=='audio'
                 else video_reference_tag(video_refs[index]))
            parts.extend((escape(prompt[end:match.start()]),tag)); end=match.end()
        parts.append(escape(prompt[end:]))
        text='<p>'+''.join(parts)+'</p>'
        self.verify_reference_tags(text,ordered_references(prompt,refs,audio_refs,video_refs,
            allow_video_placeholders=allow_video_placeholders))
        return text,indexes

    def save_and_readback(self, prompt, refs, duration, name, *, audio_refs=None, video_refs=None, allow_video_placeholders=False):
        from video_audio_references import ordered_references, prompt_reference_order
        audio_refs=audio_refs or []
        video_refs=video_refs or []
        text,indexes=self.render_verbatim_prompt(prompt,refs,
            **({'audio_refs':audio_refs} if audio_refs else {}), **({'video_refs':video_refs} if video_refs else {}),
            **({'allow_video_placeholders':True} if allow_video_placeholders else {}))
        self.run('storyboard', 'create', '--series-id', series_id(), '--name', name, '--yes')
        boards = []
        page = 1
        while True:
            result = self.run('storyboard', 'list', '--series-id', series_id(), '--page', page, '--size', 50)
            boards.extend(row for row in result['items'] if row.get('name') == name)
            if page * 50 >= result['totalCount']:
                break
            page += 1
        if len(boards) != 1:
            raise BridgeError('新分镜回读不唯一。')
        board_id = boards[0]['id']
        return self.save_prompt_to_board(board_id, prompt, refs, duration, text, indexes, audio_refs, video_refs, allow_video_placeholders)

    def save_prompt_to_board(self, board_id, prompt, refs, duration, text, indexes, audio_refs, video_refs, allow_video_placeholders):
        from video_audio_references import ordered_references, prompt_reference_order
        update = ('storyboard', 'update', '--series-id', series_id(), '--id', board_id,
                  '--video-prompt', text, '--duration', duration, '--produce-type', 5, '--yes')
        if self.cli_client.fits(*update):
            self.run(*update)
        else:
            # Long reference HTML can exceed Windows' argv limit. Choose the
            # established JSON save before sending, never as a write retry.
            assets.save_video_prompt(self, board_id, text, duration)
        boards = self.run('storyboard', 'list', '--series-id', series_id(), '--id', board_id).get('items', [])
        if len(boards) != 1:
            raise BridgeError('分镜保存后回读失败。')
        board = boards[0]
        # CLI returns the saved HTML prompt, including machine-readable reference tags.
        saved = board.get('videoPrompt')
        if saved != text:
            raise BridgeError('分镜视频提示词回读与组装原文不一致。')
        self.verify_reference_tags(saved, ordered_references(prompt,refs,audio_refs,video_refs,
            allow_video_placeholders=allow_video_placeholders))
        return {'storyboardId': board_id, 'referenceImageIds': [r['imageId'] for r in refs],
                'promptVerified': True, 'referencesVerified': True,
                'promptPolicy':'verbatim-media-v1' if audio_refs or video_refs else 'verbatim-v1','promptReferenceIndexes':indexes,
                **({'promptReferenceOrder':[list(item) for item in prompt_reference_order(prompt,refs,audio_refs,video_refs)]} if audio_refs or video_refs else {}),
                'savedPromptHash':hashlib.sha256(saved.encode('utf-8')).hexdigest()}

    @staticmethod
    def verify_reference_tags(saved, refs):
        from html.parser import HTMLParser
        from urllib.parse import unquote
        class Tags(HTMLParser):
            def __init__(self):
                super().__init__(); self.values = []; self.mention_open = False
            def handle_starttag(self, tag, attrs):
                if self.mention_open: raise BridgeError('引用标签中不能嵌套其他标签。')
                if len(dict(attrs)) != len(attrs): raise BridgeError('标签存在重复属性。')
                attrs=dict(attrs)
                if tag == 'span' and (attrs.get('data-type') == 'mention' or 'data-node-type-inline-mention' in attrs or attrs.get('data-node-type')=='inline-mention'):
                    if ('data-value' not in attrs or not ('data-node-type-inline-mention' in attrs or attrs.get('data-node-type')=='inline-mention')
                            or attrs.get('data-draggable') != 'true'):
                        raise BridgeError('引用标签结构不符合Dreama官方格式。')
                    self.values.append(attrs); self.mention_open=True
            def handle_endtag(self, tag):
                if self.mention_open:
                    if tag != 'span': raise BridgeError('引用标签闭合无效。')
                    self.mention_open=False
            def handle_data(self, data):
                if self.mention_open and data: raise BridgeError('引用标签必须为空正文。')
            def handle_comment(self, data):
                if self.mention_open: raise BridgeError('引用标签中不能含注释。')
        tags = Tags(); tags.feed(saved); tags.close()
        if tags.mention_open: raise BridgeError('引用标签未闭合。')
        try:
            actual=[json.loads(unquote(tag['data-extra-props'])) for tag in tags.values]
        except (KeyError, ValueError, TypeError):
            raise BridgeError('参考图标签数据无效。') from None
        from video_audio_references import audio_extra_props
        from video_reference_media import video_extra_props
        from audio_flow import cdn_url
        expected=[]; expected_values=[]
        for ref in refs:
            if 'imageId' not in ref and 'url' in ref:
                if not cdn_url(ref['url']): raise BridgeError('参考媒体引用地址无效。')
                expected.append(video_extra_props(ref) if ref.get('fileType')==2 else audio_extra_props(ref)); expected_values.append(ref['url'])
                continue
            expected.append({'fileType':ref.get('fileType',1),'materialId':ref['imageId'],'materialType':ref.get('materialType',3)})
            expected_values.append('')
            if 'voiceId' in ref:
                expected.append({'fileType':3,'materialId':ref['imageId'],'materialType':1})
                expected_values.append('')
        if (actual != expected or [tag.get('data-value') for tag in tags.values] != expected_values
                or any(not isinstance(props,dict) or any(type(value) is not type(expected[index][key])
                    for key,value in props.items()) for index,props in enumerate(actual))):
            raise BridgeError('保存后的参考媒体ID、URL、类型、元数据或顺序不一致。')

    def estimate(self, duration, model=MODEL):
        result = self.run('point', 'calc', '--kind', 'video', '--project-id', project_id(),
                          '--llm', model, '--duration', duration, '--number', 1)
        return result.get('token')

    @job_scope
    def inspect_board(self, job):
        rows=self.run('storyboard','list','--series-id',series_id(),'--id',job['storyboardId']).get('items',[])
        if len(rows)!=1: raise BridgeError('分镜回读不唯一。')
        b=rows[0]
        if b.get('id')!=job['storyboardId'] or b.get('seriesId')!=series_id() or b.get('projectId')!=project_id():
            raise BridgeError('项目、剧集或分镜绑定不一致。')
        if hashlib.sha256(b.get('videoPrompt','').encode()).hexdigest()!=job.get('savedPromptHash'):
            raise BridgeError('分镜提示词已改变，请重新创建任务。')
        refs=job['references']
        if job.get('promptPolicy')=='verbatim-media-v1':
            from video_audio_references import ordered_references, prompt_reference_order
            audio_refs=job.get('audioReferences',[])
            video_refs=job.get('videoReferences',[])
            if job.get('promptReferenceOrder') != [list(value) for value in prompt_reference_order(job['prompt'],refs,audio_refs,video_refs)]:
                raise BridgeError('记录中的媒体引用顺序无效。')
            refs=ordered_references(job['prompt'],refs,audio_refs,video_refs)
        elif job.get('promptPolicy')=='verbatim-v1':
            indexes=job.get('promptReferenceIndexes',[])
            if any(type(i) is not int or i<0 or i>=len(refs) for i in indexes):
                raise BridgeError('记录中的图片引用顺序无效。')
            refs=[refs[i] for i in indexes]
        self.verify_reference_tags(b['videoPrompt'],refs)
        return b

    @job_scope
    def check_ready(self, job):
        verify_frozen(self,job)
        if job.get('bindingSnapshot'):
            rows=self._invoke('capability','list','--kind','video').get('items',[])
            from model_catalog import build_models
            live=build_models(rows).get(job['model'])
            if (not live or job['duration'] not in live['durations'] or len(job['prompt'])>live['promptMaxChars']
                    or not live['minImages']<=len(job['references'])<=live['maxImages']):
                raise BridgeError('模型输入限制已变化，停止提交。')
            from video_audio_references import validate_reference_counts, validate_audio_limits, verify_audio_files
            audios=job.get('audioReferences',[])
            videos=job.get('videoReferences',[])
            voices=[ref for ref in job['references'] if 'voiceId' in ref]
            validate_reference_counts(live,len(job['references']),len(audios)+len(voices),len(videos))
            validate_audio_limits(live,audios,voices)
            if audios:
                if (not job['bindingSnapshot'].get('requiresStandaloneAudio') or job['bindingSnapshot'].get('projectModeType')!=3
                        or audios!=job.get('audioReferenceSnapshot')):
                    raise BridgeError('独立音频引用缺少已核验的项目与内容绑定，停止提交。')
                verify_audio_files(audios)
            if videos:
                from video_reference_media import validate_video_limits, verify_video_files
                if videos!=job.get('videoReferenceSnapshot'):
                    raise BridgeError('参考视频缺少已核验的内容绑定，停止提交。')
                validate_video_limits(live,videos)
                verify_video_files(videos)
        p=self.run('project','get','--id',project_id())
        from job_binding import current
        if p.get('id')!=project_id() or p.get('promptMode')!='drama' or p.get('proportion')!=current()['aspectRatio']:
            raise BridgeError('项目模式或画幅已变化，停止提交。')
        b=self.inspect_board(job)
        self.check_character_references(job['references'])
        if b.get('videoTaskId'): raise BridgeError('该分镜已有视频任务，禁止再次提交。')

    @job_scope
    def submit_video(self, job):
        from submission import verify_submission_intent
        verify_submission_intent(job)
        spec=job.get('modelSnapshot') or models().get(job.get('model'))
        if (job.get('status')!='submitting' or not spec
                or spec.get('id')!=job.get('model')):
            raise BridgeError('缺少单次费用确认。')
        args=['video','produce','--project-id',project_id(),'--series-id',series_id(),
              '--storyboard-id',job['storyboardId'],'--llm',job['model'],
              '--duration',job['duration'],'--produce-video-format','mp4','--yes']
        if spec['resolution']: args+=['--resolution',spec['resolution']]
        return self._invoke(*args, job=job)

    @job_scope
    def task_status(self, task_id, job=None):
        rows=self.run('task','status','--project-id',project_id(),'--task-id',task_id).get('items',[])
        matches=[x for x in rows if x.get('taskId')==task_id]
        if len(matches)!=1: raise BridgeError('任务状态回读不唯一。')
        return matches[0]
