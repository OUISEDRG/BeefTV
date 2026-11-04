"""Add the preflight channel while BeefTV is CLOSED, preserving its encrypted document.

Format verified against BeefTV v2.1.0 workspace/provider_config.go and localcrypto/secret.go.
Never writes plaintext credentials. Never changes any existing default selection.
"""
import base64
import copy
import datetime
import json
import os
import subprocess
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from bridge import MODEL
from server import ROOT, PORT, local_token
from model_catalog import selected_models

CHANNEL_ID='dreama-local-preflight'

def decode(raw, key):
    envelope=json.loads(raw)
    assert envelope['schemaVersion']==2 and envelope['encryptedConfig'].startswith('enc:v1:')
    cipher=base64.b64decode(envelope['encryptedConfig'][7:]+'===')
    document=json.loads(AESGCM(key).decrypt(cipher[:12],cipher[12:],None))
    assert document['schemaVersion']==1 and isinstance(document['config']['channels'],list)
    return document

def channel(token,specs=None):
    result = {'id':CHANNEL_ID,'name':'剧梦视频生成','scope':'user',
            'enabled':True,'pinned':False,'baseUrl':f'http://127.0.0.1:{PORT}',
            'apiFormat':'openai','interfaceType':'dreama-videos-async','apiKey':token,
            'headers':[],'concurrencyLimit':1,'models':[MODEL],'modelProfiles':[{
                'model':MODEL,'displayName':'九梦 2.0 Pro 720P（参考图预检）',
                'description':'只上传参考图、保存分镜及估算积分；视频生成未启用。',
                'capability':'video','protocol':'dreama-videos-async',
                'referenceMediaTransport':{'image':'inline'},'videoCapabilitiesVersion':'dreama-preflight-v1',
                'capabilityConfig':{'version':1,'video':{
                    'references':{'promptMaxChars':6000,'minImages':1,'maxImages':9,'maxImageBytes':10485760,
                        'minImageWidth':300,'maxImageWidth':6000,'minImageHeight':300,'maxImageHeight':6000,
                        'maxVideos':0,'maxVideoBytes':0,'maxVideoDurationSeconds':0,
                        'maxAudios':0,'maxAudioBytes':0,'maxAudioDurationSeconds':0},
                    'duration':{'selection':'range','min':4,'max':15,'step':1,'default':5},
                    'durationSupported':True,'ratios':[],'defaultRatio':'','ratioSource':'project',
                    'resolutions':['720p'],'defaultResolution':'720p',
                    'generateAudio':{'supported':False,'default':False},
                    'watermark':{'supported':False,'default':False},
                    'operations':['reference_to_video'],'defaultOperation':'reference_to_video',
                    'operationReferences':{'reference_to_video':{'minImages':1,'maxImages':9,'maxVideos':0,'maxAudios':0}}
                }}
            }]}
    profiles=[]
    template=result['modelProfiles'][0]
    for spec in (selected_models() if specs is None else specs).values():
        p=copy.deepcopy(template)
        p.update(model=spec['id'],displayName=spec['label'],description='支持模型目录允许的图片、多视频与音频参考；独立音频限剧梦自由模式项目。准备完成后按当前确认设置提交。',videoCapabilitiesVersion='dreama-video-v5-media-references')
        p['referenceMediaTransport']={'image':'inline','audio':'inline','video':'inline'}
        v=p['capabilityConfig']['video']
        v['references'].update(promptMaxChars=spec['promptMaxChars'],minImages=spec['minImages'],maxImages=spec['maxImages'])
        from model_catalog import audio_capabilities, video_capabilities
        v['references'].update(audio_capabilities(spec.get('limits',{})))
        v['references'].update(video_capabilities(spec.get('limits',{})))
        v['operations']=['image_to_video','reference_to_video']
        image_refs={'minImages':max(1,spec['minImages']),'maxImages':spec['maxImages'],'maxVideos':0,'maxAudios':v['references']['maxAudios']}
        v['operationReferences']={operation:dict(image_refs) for operation in v['operations']}
        v['operationReferences']['reference_to_video']['minImages']=spec['minImages']
        v['operationReferences']['reference_to_video']['maxVideos']=v['references']['maxVideos']
        if (v['references']['maxAudios'] and spec.get('limits',{}).get('allowAudioOnly') is True
                and spec['minImages']==0 and not spec.get('limits',{}).get('videoMin')):
            v['operations'].append('audio_to_video')
            v['operationReferences']['audio_to_video']={'minImages':0,'maxImages':0,'maxVideos':0,'maxAudios':v['references']['maxAudios']}
        if spec['minImages']==0 and not any(spec.get('limits',{}).get(field) for field in ('videoMin','audioMin','totalMin')):
            v['operations'].insert(0,'text_to_video')
            v['operationReferences']['text_to_video']={'minImages':0,'maxImages':0,'maxVideos':0,'maxAudios':0}
            v['defaultOperation']='text_to_video'
        else:
            v['defaultOperation']='image_to_video'
        v['duration']={'selection':'enum','values':spec['durations'],'default':5 if 5 in spec['durations'] else spec['durations'][0]}
        v['resolutions']=spec['resolutions']
        v['resolutionVariants']=spec['resolutionVariants']
        v['promptRequired']=True
        v['durationSemantics']='output'
        v['defaultResolution']=v['resolutions'][0]
        profiles.append(p)
    result['models']=[p['model'] for p in profiles]
    result['modelProfiles']=profiles
    return result

def install():
    running=subprocess.run(['tasklist','/FI','IMAGENAME eq BeefTV.exe','/FO','CSV','/NH'],capture_output=True)
    if b'beeftv.exe' in running.stdout.lower(): raise RuntimeError('请先关闭BeefTV，再安装渠道。')
    pointer=Path(os.environ['APPDATA'])/'BeefTV/storage-location.json'
    data=Path(json.loads(pointer.read_text('utf-8-sig'))['dataDir']).resolve()
    target=data/'local-model-config.json'
    original=target.read_bytes(); key=(data/'.settings-key').read_bytes()
    document=decode(original,key); before=copy.deepcopy(document)
    channels=document['config']['channels']
    existing=[x for x in channels if x.get('id')==CHANNEL_ID]
    if existing:
        if existing != [channel(local_token())]: raise RuntimeError('同名渠道已有不同设置；停止覆盖。')
        print('Channel already installed.'); return
    channels.append(channel(local_token()))
    document['revision']+=1
    nonce=os.urandom(12)
    ciphertext=nonce+AESGCM(key).encrypt(nonce,json.dumps(document,ensure_ascii=False).encode(),None)
    output=json.dumps({'schemaVersion':2,'encryptedConfig':'enc:v1:'+base64.b64encode(ciphertext).decode().rstrip('=')}).encode()
    assert decode(output,key)==document
    # Verify that removing this one addition restores the complete original document.
    check=copy.deepcopy(document); check['config']['channels'].pop(); check['revision']-=1
    assert check==before
    backup=ROOT/'backups'/('local-model-config-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'.json.enc')
    backup.write_bytes(original)
    if target.read_bytes()!=original: raise RuntimeError('配置发生并发修改，已停止。')
    temporary=target.with_name('.dreama-channel-config.tmp')
    with temporary.open('xb') as file:
        file.write(output); file.flush(); os.fsync(file.fileno())
    os.replace(temporary,target)
    assert decode(target.read_bytes(),key)==document
    print(json.dumps({'installed':True,'channelId':CHANNEL_ID,'dataDir':str(data),'backup':str(backup),
                      'defaultVideoModelUnchanged':True,'generationEnabled':True,'requiresPerJobConfirmation':True},ensure_ascii=False))

if __name__=='__main__': install()
