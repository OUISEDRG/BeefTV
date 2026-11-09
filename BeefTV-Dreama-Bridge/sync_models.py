"""Refresh raw catalog and advertise only the user's four grouped video models."""
import base64
import copy
import datetime
import json
import os
import subprocess
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from dreama_cli import DreamaCliClient
from install_channel import CHANNEL_ID,decode,channel
from model_catalog import models,selected_models
from server import ROOT,PORT,local_token

def merge(document,token):
    doc=copy.deepcopy(document);config=doc['config'];specs=selected_models()
    matches=[(i,c) for i,c in enumerate(config['channels']) if c['id']==CHANNEL_ID]
    if len(matches)!=1:raise RuntimeError('目标渠道不唯一。')
    index,old=matches[0]
    if old.get('baseUrl')!=f'http://127.0.0.1:{PORT}' or old.get('apiKey')!=token:
        raise RuntimeError('渠道不属于当前本地桥接，停止覆盖。')
    fresh=channel(token);updated=copy.deepcopy(old)
    for field in ('name','models','modelProfiles','interfaceType'):updated[field]=fresh[field]
    config['channels'][index]=updated
    for field in ('models','videoModels'):
        config[field]=[m for m in config[field] if not m.startswith(CHANNEL_ID+'::')]+[CHANNEL_ID+'::'+eid for eid in specs]
    if config['videoModel'].startswith(CHANNEL_ID+'::'):
        previous=config['videoModel'].split('::',1)[1]
        canonical=previous if previous in specs else next((s['id'] for s in specs.values() if previous in s['variants'].values()),next(iter(specs)))
        config['videoModel']=CHANNEL_ID+'::'+canonical
        if previous!=canonical:
            resolution=models().get(previous,{}).get('resolution')
            config['vquality']=resolution.lower().removesuffix('p') if resolution in ('480P','720P') else '480'
    check=copy.deepcopy(doc)
    for field in ('channels','models','videoModels','videoModel','vquality'):check['config'][field]=document['config'].get(field)
    assert check==document
    return doc

def sync():
    running=subprocess.run(['tasklist','/FI','IMAGENAME eq BeefTV.exe','/FO','CSV','/NH'],capture_output=True,check=True)
    if b'beeftv.exe' in running.stdout.lower(): raise RuntimeError('请先关闭BeefTV，再同步模型。')
    result=DreamaCliClient().run('capability','list','--kind','video')
    if not result.get('items'): raise RuntimeError('模型目录查询失败。')
    catalog=ROOT/'catalog.json'; previous_catalog=catalog.read_bytes()
    catalog.write_text(json.dumps({'items':result['items']},ensure_ascii=False,indent=2),'utf-8')
    try:
        specs=selected_models()
        pointer=Path(os.environ['APPDATA'])/'BeefTV/storage-location.json'
        data=Path(json.loads(pointer.read_text('utf-8-sig'))['dataDir']).resolve()
        target=data/'local-model-config.json'; original=target.read_bytes(); key=(data/'.settings-key').read_bytes()
        before=decode(original,key);doc=merge(before,local_token())
        if doc==before:print('Grouped video models already current.');return
        doc['revision']+=1
        stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
        (ROOT/'backups'/('before-all-models-'+stamp+'.json.enc')).write_bytes(original)
        (ROOT/'backups'/('catalog-'+stamp+'.json')).write_bytes(previous_catalog)
        nonce=os.urandom(12); payload=nonce+AESGCM(key).encrypt(nonce,json.dumps(doc,ensure_ascii=False).encode(),None)
        output=json.dumps({'schemaVersion':2,'encryptedConfig':'enc:v1:'+base64.b64encode(payload).decode().rstrip('=')}).encode()
        assert decode(output,key)==doc
        if target.read_bytes()!=original: raise RuntimeError('配置并发变化，停止写入。')
        temporary=data/('.dreama-sync-'+stamp+'.tmp')
        with temporary.open('xb') as f: f.write(output); f.flush(); os.fsync(f.fileno())
        os.replace(temporary,target)
        assert decode(target.read_bytes(),key)==doc
        receipt={'modelCount':len(specs),'channel':channel(local_token())['name'],'defaultVideoModel':doc['config']['videoModel'],
                 'revision':doc['revision'],'generationTestsSubmitted':0,'otherSettingsPreserved':True}
        (ROOT/'evidence'/'all-models-installed.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),'utf-8')
        print(json.dumps(receipt,ensure_ascii=False))
    except Exception:
        catalog.write_bytes(previous_catalog); raise

if __name__=='__main__': sync()
