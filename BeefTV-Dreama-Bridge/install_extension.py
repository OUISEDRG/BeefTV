"""Install extension as its own channel, preserving existing models and defaults."""
import base64
import copy
import datetime
import json
import os
import subprocess
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from install_channel import decode
from extension import extension_models,selected_extension_models
from enhancement import MAX_VIDEO
from server import ROOT, PORT, local_token

CHANNEL_ID='dreama-video-extension'

def channel(token,specs=None):
    specs=selected_extension_models() if specs is None else specs
    profiles=[]
    for spec in specs.values():
        references={'promptMaxChars':spec['promptMaxChars'],'minImages':0,'maxImages':spec['maxImages'],
            'maxImageBytes':10485760,'minImageWidth':300,'maxImageWidth':6000,'minImageHeight':300,'maxImageHeight':6000,
            'maxVideos':1,'maxVideoBytes':MAX_VIDEO,'minVideoDurationSeconds':spec['sourceMin'],'maxVideoDurationSeconds':spec['sourceMax'],
            'maxAudios':0,'maxAudioBytes':0,'maxAudioDurationSeconds':0}
        profiles.append({'model':spec['id'],'displayName':spec['label'],
            'description':'连接一个2–30秒MP4原视频（≤40MiB），填写续写提示词。在视频设置中选择480P/720P和新增秒数；仅返回续写片段。准备完成5秒后自动提交。',
            'capability':'video','protocol':'dreama-extension-async',
            'referenceMediaTransport':{'image':'inline','video':'inline'},'videoCapabilitiesVersion':'dreama-extend-v2-grouped',
            'capabilityConfig':{'version':1,'video':{'references':references,
                'duration':{'selection':'enum','values':spec['durations'],'default':5 if 5 in spec['durations'] else spec['durations'][0]},
                'durationSupported':True,'ratios':[],'defaultRatio':'',
                'resolutions':spec['resolutions'],'defaultResolution':'480p',
                'generateAudio':{'supported':False,'default':False},'watermark':{'supported':False,'default':False},
                'operations':['extend'],'defaultOperation':'extend','promptRequired':True,'durationSemantics':'additional','standaloneOutput':True,'resolutionVariants':spec['resolutionVariants'],
                'operationReferences':{'extend':{'minImages':0,'maxImages':spec['maxImages'],'maxVideos':1,'maxAudios':0}}
            }}})
    return {'id':CHANNEL_ID,'name':'剧梦视频延长（连接一个视频）','scope':'user','enabled':True,'pinned':False,
        'baseUrl':f'http://127.0.0.1:{PORT}/extension','apiFormat':'openai','interfaceType':'dreama-extension-async',
        'apiKey':token,'headers':[],'concurrencyLimit':1,'models':list(specs),'modelProfiles':profiles}

def merge(document,token):
    doc=copy.deepcopy(document);config=doc['config'];fresh=channel(token)
    matches=[i for i,c in enumerate(config['channels']) if c.get('id')==CHANNEL_ID]
    if len(matches)>1:raise RuntimeError('延长渠道不唯一。')
    if matches:
        old=config['channels'][matches[0]]
        if old.get('baseUrl')!=fresh['baseUrl'] or old.get('apiKey')!=token:raise RuntimeError('同名渠道不属于当前转接服务。')
        updated=copy.deepcopy(old)
        for field in ('name','models','modelProfiles','interfaceType','baseUrl'):updated[field]=fresh[field]
        config['channels'][matches[0]]=updated
    else:config['channels'].append(fresh)
    for field in ('models','videoModels'):
        config[field]=[m for m in config[field] if not m.startswith(CHANNEL_ID+'::')]+[CHANNEL_ID+'::'+m for m in fresh['models']]
    if config.get('videoModel','').startswith(CHANNEL_ID+'::'):
        old_id=config['videoModel'].split('::',1)[1]
        for spec in selected_extension_models().values():
            if old_id in spec['variants'].values() and old_id!=spec['id']:
                config['videoModel']=CHANNEL_ID+'::'+spec['id']
                config['vquality']=extension_models()[old_id]['resolution'].lower().removesuffix('p')
    check=copy.deepcopy(doc)
    for field in ('channels','models','videoModels','videoModel','vquality'):
        if field in document['config']:check['config'][field]=document['config'][field]
    assert check==document
    return doc

def install():
    running=subprocess.run(['tasklist','/FI','IMAGENAME eq BeefTV.exe','/FO','CSV','/NH'],capture_output=True,check=True)
    if b'beeftv.exe' in running.stdout.lower():raise RuntimeError('请先关闭BeefTV。')
    pointer=Path(os.environ['APPDATA'])/'BeefTV/storage-location.json'
    data=Path(json.loads(pointer.read_text('utf-8-sig'))['dataDir']).resolve()
    target=data/'local-model-config.json';original=target.read_bytes();key=(data/'.settings-key').read_bytes()
    before=decode(original,key);doc=merge(before,local_token())
    if doc==before:print('Extension channel already current.');return
    doc['revision']+=1;stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    (ROOT/'backups'/('before-extension-channel-'+stamp+'.json.enc')).write_bytes(original)
    nonce=os.urandom(12);payload=nonce+AESGCM(key).encrypt(nonce,json.dumps(doc,ensure_ascii=False).encode(),None)
    output=json.dumps({'schemaVersion':2,'encryptedConfig':'enc:v1:'+base64.b64encode(payload).decode().rstrip('=')}).encode()
    assert decode(output,key)==doc
    if target.read_bytes()!=original:raise RuntimeError('配置并发变化，停止写入。')
    temporary=data/('.dreama-extension-'+stamp+'.tmp')
    with temporary.open('xb') as stream:stream.write(output);stream.flush();os.fsync(stream.fileno())
    os.replace(temporary,target)
    assert decode(target.read_bytes(),key)==doc
    receipt={'installed':True,'extensionModels':len(selected_extension_models()),'defaultVideoModel':doc['config']['videoModel'],
        'revision':doc['revision'],'otherSettingsPreserved':True,'paidTestsSubmitted':0}
    (ROOT/'evidence'/'extension-installed.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps(receipt,ensure_ascii=False))

if __name__=='__main__':install()
