"""Authenticated workbench API; only whitelisted public fields leave the ledger."""
import hashlib
import json
import re
from bridge import BridgeError
import catalog_cache
CATALOG_LOCK=catalog_cache.LOCK


def positive(value):
    if isinstance(value,str) and re.fullmatch(r'[1-9][0-9]*',value): value=int(value)
    if type(value) is not int or value<=0: raise BridgeError('项目ID无效。')
    return value


def public_task(job):
    fields=('id','kind','status','stage','pointsEstimate','projectId','seriesId','canvasId','nodeId','taskId')
    result={key:job.get(key) for key in fields}
    result['kind']=result['kind'] or 'video'
    result['cancelable']=False
    result['resultReady']=job.get('status')=='completed'
    # Upstream errors can embed signed URLs or headers. This API uses a stable
    # action-oriented message; detailed ledger remains local to the executor.
    result['error']=('任务需要核对，请查看本地执行器记录。'
                     if job.get('error') or job.get('autoExecuteError') or job.get('executionError') else None)
    if job.get('bindingVerificationRequired') and job.get('status') not in ('completed','failed','cancelled'):
        result['status']='binding_verification_required'
    return result


def needs_task_reconciliation(task):
    return (task['status'] in ('unknown','submission_unknown','download_failed','binding_verification_required','recovery_required')
            or task['status']=='prepared' and (task['stage']=='budget_blocked' or bool(task['error'])))


def project_item(row):
    return {'id':positive(row['id']),'name':str(row.get('name','')),'promptMode':row.get('promptMode'),
            'aspectRatio':row.get('proportion') or row.get('aspectRatio') or ''}


def project(gateway,project_id):
    row=gateway.run('project','get','--id',positive(project_id))
    if row.get('id')!=project_id or row.get('promptMode')!='drama':
        raise BridgeError('当前只开放已验证的剧本模式项目。')
    return project_item(row)


def paged(gateway,*args):
    rows=[]
    for page in range(1,101):
        result=gateway.run(*args,'--page',page,'--size',100)
        items=result.get('items')
        if not isinstance(items,list): raise BridgeError('目录回执格式无效。')
        rows.extend(items)
        total=result.get('total')
        if len(items)<100 or type(total) is int and len(rows)>=total: return rows
    raise BridgeError('目录超过本次读取范围，请缩小查询。')


def series_item(row,pid):
    if row.get('projectId',pid)!=pid: raise BridgeError('剧集归属与项目不一致。')
    return {'id':positive(row['id']),'name':str(row.get('name','')),'projectId':pid}


def create_series(gateway,data):
    if not isinstance(data,dict) or set(data)!={'projectId','name'}:
        raise BridgeError('新建剧集需要projectId与name。')
    pid=positive(data['projectId']); name=data['name']
    if not isinstance(name,str) or not name.strip() or len(name)>100:
        raise BridgeError('剧集名称无效。')
    project(gateway,pid)
    row=gateway.run('series','create','--project-id',pid,'--name',name,'--yes')
    if not isinstance(row,dict):
        raise BridgeError('剧梦已返回创建成功，但剧集回执无法识别；请刷新剧集列表核对，勿重复创建。')
    # CLI 1.0.5 returns {boardId, projectId, seriesId}, not a series object.
    # boardId identifies the initial storyboard; only seriesId is the episode ID.
    if 'seriesId' in row:
        if positive(row.get('projectId'))!=pid:
            raise BridgeError('新建剧集回执的项目归属不一致；请刷新列表核对，勿重复创建。')
        row={'id':positive(row['seriesId']),'projectId':pid,'name':name}
    row=row.get('item',row)
    if not isinstance(row,dict) or 'id' not in row:
        raise BridgeError('剧梦已返回创建成功，但缺少剧集编号；请刷新剧集列表核对，勿重复创建。')
    return {'item':series_item(row,pid)}


def catalog(pid=None,gateway=None,state_root=None):
    with CATALOG_LOCK:
        return _catalog(pid,gateway,state_root)


def _catalog(pid=None,gateway=None,state_root=None):
    snapshots=None
    if gateway is not None and hasattr(gateway,'catalogs'):
        snapshots,fresh=catalog_cache.prepare(gateway.catalogs())
    else:
        fresh=catalog_cache.build_channels()
    ratio=project(gateway,pid)['aspectRatio'] if pid is not None else None
    result=[]
    for channel in fresh:
        profiles=channel['modelProfiles']
        if ratio and channel['id']=='dreama-local-preflight':
            for profile in profiles:
                profile['capabilityConfig']['video'].update(ratios=[ratio],defaultRatio=ratio)
        result.append({key:channel[key] for key in ('id','models','modelProfiles')})
    encoded=json.dumps(result,sort_keys=True,ensure_ascii=False).encode()
    response={'schemaVersion':1,'catalogVersion':hashlib.sha256(encoded).hexdigest(),
              'channels':result,'models':[p for c in result for p in c['modelProfiles']]}
    # No fallible parsing, profile building or project lookup may follow the
    # disk commit. All consumers were staged without exposing candidate globals.
    if snapshots is not None:
        if state_root is not None:catalog_cache.persist(state_root,snapshots)
        catalog_cache.apply(snapshots)
    return response


def get_integration(server,path,query):
    gateway=server.bridge.gateway
    if path=='/status':
        from ledger_status import inspect_ledger
        return {'schemaVersion':1,'service':'dreama-preflight','integrationVersion':'1.0',
                'executionMode':server.execution_policy.get('mode'),'delaySeconds':0,
                'ledger':inspect_ledger(server.bridge.root)}
    if path=='/projects':
        return {'items':[project_item(row) for row in paged(gateway,'project','list') if row.get('promptMode')=='drama']}
    if path=='/series':
        pid=positive(query.get('projectId',[None])[0]); project(gateway,pid)
        return {'items':[series_item(row,pid) for row in paged(gateway,'series','list','--project-id',pid)]}
    if path=='/catalog':
        pid=positive(query['projectId'][0]) if 'projectId' in query else None
        return catalog(pid,gateway,server.bridge.root)
    if path=='/tasks':
        canvas=query.get('canvasId',[None])[0]
        scope=query.get('scope',['all'])
        if scope not in (['all'],['exceptions']):
            raise BridgeError('任务查询范围无效。')
        tasks=[public_task(row) for row in server.list_jobs() if canvas is None or row.get('canvasId')==canvas]
        return {'items':tasks if scope==['all'] else [task for task in tasks if needs_task_reconciliation(task)]}
    raise BridgeError('集成接口不存在。')
