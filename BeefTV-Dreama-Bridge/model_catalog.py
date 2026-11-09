"""The complete video catalog returned by Dreama; no inferred model IDs."""
import json
import re
from pathlib import Path

LIVE_ITEMS=None

def models():
    items=LIVE_ITEMS if LIVE_ITEMS is not None else json.loads((Path(__file__).parent/'catalog.json').read_text('utf-8'))['items']
    return build_models(items)

def build_models(items):
    result={}
    for item in items:
        limits=json.loads(item.get('modelLimit') or '{}')
        if item.get('isExternal',0) not in (None,0):
            continue
        durations=sorted({x['duration'] for x in json.loads(item['videoTokens']) if type(x.get('duration')) is int and x['duration']>0})
        if not durations or item['eid'] in result: raise ValueError('Invalid catalog')
        alias=item.get('alias') or ''
        resolution=alias.upper() if re.fullmatch(r'\d+[pP]',alias) else None
        result[item['eid']]={'id':item['eid'],'name':item['name'],'alias':alias,
            'label':item['name']+' · '+(alias or '供应商默认')+' · '+item['eid'][:6],
            'resolution':resolution,'durations':durations,
            'minImages':int(limits.get('imageMin',1)),
            'maxImages':min(30,int(limits.get('imageMax',item.get('imageMax',1)))),
            'promptMaxChars':min(15000,int(limits.get('modelTextLimit',6000))), 'limits':limits,
            **audio_capabilities(limits), **video_capabilities(limits)}
    return result


def audio_capabilities(limits):
    maximum = max(0, int(limits.get('audioMax', 0)))
    return {'maxAudios': maximum,
            'maxAudioBytes': int(min(20, limits.get('audioSizeMax', 0)) * 1024 * 1024) if maximum else 0,
            'minAudioDurationSeconds': limits.get('audioDurationMin', 0) if maximum else 0,
            'maxAudioDurationSeconds': limits.get('audioDurationMax', 0) if maximum else 0,
            'maxAudioTotalDurationSeconds': (limits.get('totalAudioDurationMax') or limits.get('audioDurationMax', 0)) if maximum else 0}


def video_capabilities(limits):
    maximum=max(0,int(limits.get('videoMax',0)))
    return {'maxVideos':maximum,'maxVideoBytes':int(limits.get('videoSizeMax',0)*1024*1024) if maximum else 0,
            'minVideoDurationSeconds':limits.get('videoDurationMin',0) if maximum else 0,
            'maxVideoDurationSeconds':limits.get('videoDurationMax',0) if maximum else 0,
            'maxVideoTotalDurationSeconds':(limits.get('totalVideoDurationMax') or limits.get('videoDurationMax',0)) if maximum else 0}

SELECTED_NAMES=('九梦 2.5 Pro','九梦 2.0 Mini','九梦 2.0 Fast','九梦 2.0 Pro')

def group_models(catalog,names,*,label_prefix=''):
    """Stable family IDs with every implemented resolution's independent limits."""
    result={}
    for name in names:
        candidates=[s for s in catalog.values() if s['name']==name]
        if not candidates: continue
        variants={(s.get('resolution') or 'default').lower():s for s in candidates}
        if len(variants)!=len(candidates): raise ValueError('模型分辨率条目不唯一：'+name)
        resolutions=sorted(variants,key=lambda r:int(r[:-1]) if re.fullmatch(r'\d+p',r) else 99999)
        low=variants.get('480p',variants[resolutions[0]])
        result[low['id']]={**low,'label':label_prefix+name,'resolutions':resolutions,
                           'variants':{res:variants[res]['id'] for res in resolutions},
                           'resolutionVariants':{res:{'modelId':variants[res]['id'],
                               'duration':{'selection':'enum','values':variants[res]['durations'],
                                           'default':5 if 5 in variants[res]['durations'] else variants[res]['durations'][0]},
                               'references':{**{k:variants[res][k] for k in ('promptMaxChars','minImages','maxImages',
                                   'maxAudios','maxAudioBytes','minAudioDurationSeconds','maxAudioDurationSeconds','maxAudioTotalDurationSeconds') if k in variants[res]},
                                   **{k:variants[res][k] for k in ('maxVideos','maxVideoBytes','minVideoDurationSeconds','maxVideoDurationSeconds','maxVideoTotalDurationSeconds') if k in variants[res]},
                                   **({'minVideoDurationSeconds':variants[res]['sourceMin'],'maxVideoDurationSeconds':variants[res]['sourceMax']} if 'sourceMin' in variants[res] else {})}}
                               for res in resolutions}}
    return result

def selected_models():return group_models(models(),SELECTED_NAMES)

def available_models(catalog=None):
    catalog=models() if catalog is None else catalog
    result=group_models(catalog,SELECTED_NAMES)
    favorites={variant for group in result.values() for variant in group['variants'].values()}
    for name in dict.fromkeys(s['name'] for s in catalog.values() if s['id'] not in favorites):
        candidates={key:spec for key,spec in catalog.items() if spec['name']==name}
        try: result.update(group_models(candidates,[name]))
        except ValueError:
            # Ambiguous aliases are kept separate; never pick a concrete EID by
            # overwriting another variant with the same presentation label.
            for key,spec in candidates.items():
                entry=group_models({key:spec},[name])[key]
                entry['label']=spec['label'];result[key]=entry
    return result

def resolve_variant(catalog,groups,model_id,resolution=None):
    if not isinstance(model_id,str) or model_id not in catalog:raise ValueError('模型不在当前Dreama目录中。')
    spec=catalog[model_id]
    chosen=str(resolution if resolution is not None else spec['resolution'] or 'default').strip().lower()
    if chosen.isdigit():chosen+='p'
    group=groups.get(model_id)
    if group:
        target=group['variants'].get(chosen)
        if not target:raise ValueError('所选分辨率不在该模型目录中。')
        return catalog[target]
    if chosen!=(spec['resolution'] or 'default').lower():raise ValueError('旧模型条目与分辨率不符，请重新选择模型。')
    return spec

def resolve_model(model_id,resolution=None):return resolve_variant(models(),available_models(),model_id,resolution)
