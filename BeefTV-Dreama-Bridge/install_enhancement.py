"""Install a separate enhancement channel without replacing BeefTV or user defaults."""
import base64
import copy
import datetime
import json
import os
import subprocess
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from install_channel import decode
from enhancement import enhancement_models, MAX_VIDEO, MAX_SECONDS
from server import ROOT, PORT, local_token

CHANNEL_ID='dreama-video-enhancement'

def channel(token):
    profiles=[]
    for model in enhancement_models().values():
        references={'promptMaxChars':6000,'minImages':0,'maxImages':0,'maxImageBytes':0,
                    'maxVideos':1,'maxVideoBytes':MAX_VIDEO,'maxVideoDurationSeconds':MAX_SECONDS,
                    'maxAudios':0,'maxAudioBytes':0,'maxAudioDurationSeconds':0}
        profiles.append({'model':model['id'],'displayName':model['label'],
            'description':'连接一个MP4原视频（≤40MiB、≤120秒）；增强C限30fps。整段增强，按原视频时长预估积分，需在Dreama任务页逐次确认。提示词不参与增强。',
            'capability':'video','protocol':'dreama-enhancement-async',
            'referenceMediaTransport':{'video':'inline'},'videoCapabilitiesVersion':'dreama-enhance-v1',
            'capabilityConfig':{'version':1,'video':{'references':references,
                'duration':{'selection':'enum','values':[5],'default':5},'durationSupported':False,
                'ratios':[],'defaultRatio':'','resolutions':[],'defaultResolution':'',
                'generateAudio':{'supported':False,'default':False},'watermark':{'supported':False,'default':False},
                'operations':['upscale'],'defaultOperation':'upscale','promptRequired':False,'durationSemantics':'output','standaloneOutput':True,
                'operationReferences':{'upscale':{'minImages':0,'maxImages':0,'maxVideos':1,'maxAudios':0}}
            }}})
    return {'id':CHANNEL_ID,'name':'剧梦视频增强（连接一个视频）','scope':'user','enabled':True,'pinned':False,
            'baseUrl':f'http://127.0.0.1:{PORT}/enhancement','apiFormat':'openai','interfaceType':'dreama-enhancement-async',
            'apiKey':token,'headers':[],'concurrencyLimit':1,'models':list(enhancement_models()),'modelProfiles':profiles}

def merge(document,token):
    doc=copy.deepcopy(document); config=doc['config']; fresh=channel(token)
    matches=[i for i,c in enumerate(config['channels']) if c.get('id')==CHANNEL_ID]
    if len(matches)>1: raise RuntimeError('增强渠道不唯一。')
    if matches:
        old=config['channels'][matches[0]]
        if old.get('baseUrl') not in (fresh['baseUrl'],f'http://127.0.0.1:{PORT}') or old.get('apiKey')!=token: raise RuntimeError('同名渠道不属于当前转接服务。')
        updated=copy.deepcopy(old)
        for field in ('name','models','modelProfiles','interfaceType','baseUrl'): updated[field]=fresh[field]
        config['channels'][matches[0]]=updated
    else: config['channels'].append(fresh)
    for field in ('models','videoModels'):
        config[field]=[m for m in config[field] if not m.startswith(CHANNEL_ID+'::')]+[CHANNEL_ID+'::'+m for m in fresh['models']]
    check=copy.deepcopy(doc)
    for field in ('channels','models','videoModels'): check['config'][field]=document['config'][field]
    assert check==document
    return doc

def install():
    running=subprocess.run(['tasklist','/FI','IMAGENAME eq BeefTV.exe','/FO','CSV','/NH'],capture_output=True,check=True)
    if b'beeftv.exe' in running.stdout.lower(): raise RuntimeError('请先关闭BeefTV。')
    pointer=Path(os.environ['APPDATA'])/'BeefTV/storage-location.json'
    data=Path(json.loads(pointer.read_text('utf-8-sig'))['dataDir']).resolve()
    target=data/'local-model-config.json'; original=target.read_bytes(); key=(data/'.settings-key').read_bytes()
    before=decode(original,key); doc=merge(before,local_token())
    if doc==before: print('Enhancement channel already current.'); return
    doc['revision']+=1; stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    (ROOT/'backups'/('before-enhancement-channel-'+stamp+'.json.enc')).write_bytes(original)
    nonce=os.urandom(12); payload=nonce+AESGCM(key).encrypt(nonce,json.dumps(doc,ensure_ascii=False).encode(),None)
    output=json.dumps({'schemaVersion':2,'encryptedConfig':'enc:v1:'+base64.b64encode(payload).decode().rstrip('=')}).encode()
    assert decode(output,key)==doc
    if target.read_bytes()!=original: raise RuntimeError('配置并发变化，停止写入。')
    temporary=data/('.dreama-enhancement-'+stamp+'.tmp')
    with temporary.open('xb') as stream: stream.write(output); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary,target)
    assert decode(target.read_bytes(),key)==doc
    receipt={'installed':True,'enhancementModels':4,'defaultVideoModel':doc['config']['videoModel'],
             'revision':doc['revision'],'otherSettingsPreserved':True,'paidTestsSubmitted':0}
    (ROOT/'evidence'/'enhancement-installed.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps(receipt,ensure_ascii=False))

if __name__=='__main__': install()
