"""Immutable task bindings. Context is per thread/call, never gateway instance state."""
import copy
import functools
import inspect
import re
from contextvars import ContextVar

CURRENT = ContextVar('dreama_job_binding', default=None)


def historical(job=None):
    from bridge import PROJECT, SERIES
    job=job or {}
    return dict(schemaVersion=1, projectId=job.get('projectId',PROJECT), seriesId=job.get('seriesId',SERIES),
                promptMode='drama', aspectRatio='9:16', bindingRevision=0, canvasId='',nodeId='',
                source='legacy', identity=copy.deepcopy(job.get('identity')))


def current():
    return CURRENT.get() or historical()


def project_id(): return current()['projectId']
def series_id(): return current()['seriesId']


def job_scope(fn):
    signature=inspect.signature(fn)
    @functools.wraps(fn)
    def wrapped(*args,**kwargs):
        bound=signature.bind(*args,**kwargs); job=bound.arguments.get('job')
        if job is None and 'key' in bound.arguments:
            owner=args[0]; bridge=getattr(owner,'bridge',owner)
            job=bridge.get(bound.arguments['key'])
        snapshot=(job or {}).get('bindingSnapshot')
        token=CURRENT.set(copy.deepcopy(snapshot or (CURRENT.get() if job is None else None) or historical(job)))
        try: return fn(*args,**kwargs)
        finally: CURRENT.reset(token)
    return wrapped


def preparation_scope(fn):
    @functools.wraps(fn)
    def wrapped(*args,**kwargs):
        token=CURRENT.set(None)
        try: return fn(*args,**kwargs)
        finally: CURRENT.reset(token)
    return wrapped


def validate_metadata(data):
    from bridge import BridgeError
    if 'dreama' not in data: return historical()
    value=data['dreama']
    fields={'schemaVersion','canvasId','nodeId','projectId','seriesId','promptMode','aspectRatio','bindingRevision'}
    if not isinstance(value,dict) or set(value)!=fields or value.get('schemaVersion')!=1:
        raise BridgeError('画布绑定格式或版本无效。')
    if any(type(value.get(k)) is not int or value[k]<=0 for k in ('projectId','seriesId')):
        raise BridgeError('项目与剧集必须为真实正整数ID。')
    if type(value['bindingRevision']) is not int or value['bindingRevision']<0:
        raise BridgeError('画布绑定修订号无效。')
    if any(not isinstance(value[k],str) or not value[k].strip() or len(value[k])>200 for k in ('canvasId','nodeId')):
        raise BridgeError('画布或节点标识无效。')
    # This validates shape only. The actual allowed ratio is the project
    # proportion read by verify_project, never an invented model-wide list.
    if (value['promptMode']!='drama' or not isinstance(value['aspectRatio'],str)
            or not re.fullmatch(r'[1-9][0-9]{0,4}:[1-9][0-9]{0,4}',value['aspectRatio'])):
        raise BridgeError('仅支持已经接通的剧本模式与画幅。')
    return {**copy.deepcopy(value),'source':'canvas'}


def freeze(data,gateway,spec,*,requires_standalone_audio=False):
    from bridge import BridgeError
    snapshot=validate_metadata(data)
    if requires_standalone_audio:
        snapshot['requiresStandaloneAudio']=True
    checker=getattr(gateway,'validate_binding',None) or getattr(getattr(gateway,'base',None),'validate_binding',None)
    if checker:
        snapshot['identity']=checker(snapshot,spec)
    elif snapshot['source']=='canvas':
        raise BridgeError('此执行器不能核验新的项目绑定。')
    else:
        identity=getattr(gateway,'identity',None)
        snapshot['identity']=identity() if identity else None
    if requires_standalone_audio and snapshot.get('projectModeType') != 3:
        raise BridgeError('独立音频参考需要核验剧梦自由模式项目（modeType=3）；尚未上传或提交。')
    CURRENT.set(copy.deepcopy(snapshot))
    return dict(schemaVersion=2,bindingSnapshot=snapshot,modelSnapshot=copy.deepcopy(spec),projectId=snapshot['projectId'],
                seriesId=snapshot['seriesId'],canvasId=snapshot['canvasId'],nodeId=snapshot['nodeId'])


def verify_frozen(gateway,job):
    from bridge import BridgeError
    snapshot=job.get('bindingSnapshot')
    if not snapshot: return
    if not snapshot.get('identity'):
        raise BridgeError('历史任务缺少可验证的账号绑定；请保留账本并核对原账号，不能自动提交。')
    if gateway.identity()!=snapshot['identity']:
        raise BridgeError('Dreama账号或组织已改变，未提交。')
    gateway.verify_project(snapshot)


def verify_write_identity(gateway):
    from bridge import BridgeError
    snapshot=CURRENT.get()
    if snapshot and snapshot.get('identity') and gateway.identity()!=snapshot['identity']:
        raise BridgeError('准备期间Dreama账号或组织已改变，停止后续写入。')
