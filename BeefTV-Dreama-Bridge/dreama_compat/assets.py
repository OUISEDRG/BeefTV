"""Content-addressed uploads for reference images, video and audio."""
import hashlib
import io
import re
from pathlib import Path

import requests
from PIL import Image

from bridge_errors import BridgeError
from job_binding import project_id, series_id

def upload_image(gateway, data, name):
    with Image.open(io.BytesIO(data)) as img:
        ext, mime = {'PNG': ('png', 'image/png'), 'JPEG': ('jpg', 'image/jpeg'), 'WEBP': ('webp', 'image/webp')}[img.format]
    auth = gateway.post('/user/resource/getOssToken', {'category': 'resource'})
    bucket = auth.get('bucketName', '')
    if not bucket or '/' in bucket or '\\' in bucket:
        raise BridgeError('上传授权中的bucket无效。')
    digest = hashlib.sha256(data).hexdigest()
    key = bucket + '/image/' + digest + '.' + ext
    url = 'https://jzinnovation.jxbaobian.cn/' + key
    # Official uploader deduplicates content-addressed objects. Verify bytes,
    # rather than attempting an overwrite when a canvas reuses an image.
    existing = requests.get(url, timeout=(10, 60), allow_redirects=False)
    if existing.status_code == 200:
        if hashlib.sha256(existing.content).hexdigest() != digest:
            raise BridgeError('已存在图片的字节校验失败。')
        return url
    if existing.status_code != 404:
        raise BridgeError('无法核实已有图片，HTTP=' + str(existing.status_code))
    r = requests.post('https://up-z2.qiniup.com', data={'token': auth['token'], 'key': key},
                      files={'file': (name + '.' + ext, data, mime)}, timeout=(10, 90), allow_redirects=False)
    if r.status_code != 200:
        raise BridgeError('图片上传失败，HTTP=' + str(r.status_code))
    uploaded = r.json()
    if uploaded.get('key') != key:
        raise BridgeError('上传回执资源路径不匹配。')
    check = requests.get(url, timeout=(10, 60), allow_redirects=False)
    if check.status_code != 200 or hashlib.sha256(check.content).hexdigest() != digest:
        raise BridgeError('上传后的图片字节校验失败。')
    return url


def upload_video(gateway,path,meta):
    auth=gateway.post('/user/resource/getOssToken',{'category':'resource'})
    bucket=auth.get('bucketName','')
    if not re.fullmatch(r'[A-Za-z0-9_-]+',bucket): raise BridgeError('视频上传授权bucket无效。')
    key=bucket+'/video/'+meta['sha256']+'.mp4'; url='https://jzinnovation.jxbaobian.cn/'+key
    def verify(response):
        if response.status_code!=200: return False
        digest=hashlib.sha256(); size=0
        for chunk in response.iter_content(1024*1024):
            size+=len(chunk)
            if size>meta['bytes']: raise BridgeError('云端视频大小与原文件不一致。')
            digest.update(chunk)
        if size!=meta['bytes'] or digest.hexdigest()!=meta['sha256']: raise BridgeError('云端视频字节校验失败。')
        return True
    with requests.get(url,stream=True,timeout=(10,60),allow_redirects=False) as existing:
        if verify(existing): return url
        if existing.status_code!=404: raise BridgeError('无法核实云端已有视频。')
    with Path(path).open('rb') as source:
        uploaded=requests.post('https://up-z2.qiniup.com',data={'token':auth['token'],'key':key},
            files={'file':('source.mp4',source,'video/mp4')},timeout=(10,120),allow_redirects=False)
    if uploaded.status_code!=200 or uploaded.json().get('key')!=key: raise BridgeError('视频上传回执不匹配。')
    with requests.get(url,stream=True,timeout=(10,60),allow_redirects=False) as saved:
        if not verify(saved): raise BridgeError('上传后视频无法回读。')
    return url


def upload_audio(gateway, path, meta):
    auth = gateway.post('/user/resource/getOssToken', {'category': 'resource'})
    bucket = auth.get('bucketName', '')
    if not re.fullmatch(r'[A-Za-z0-9_-]+', bucket):
        raise BridgeError('音频上传bucket无效。')
    key = bucket + '/audio/' + meta['sha256'] + '.mp3'
    url = 'https://jzinnovation.jxbaobian.cn/' + key
    def verify(response):
        if response.status_code != 200:
            return False
        digest, size = hashlib.sha256(), 0
        for chunk in response.iter_content(1024 * 1024):
            size += len(chunk)
            if size > meta['bytes']:
                raise BridgeError('云端音频大小不一致。')
            digest.update(chunk)
        if size != meta['bytes'] or digest.hexdigest() != meta['sha256']:
            raise BridgeError('云端音频校验失败。')
        return True
    with requests.get(url, stream=True, timeout=(10, 60), allow_redirects=False) as response:
        if verify(response):
            return url
        if response.status_code != 404:
            raise BridgeError('无法核实云端参考音频。')
    with Path(path).open('rb') as source:
        response = requests.post('https://up-z2.qiniup.com', data={'token': auth['token'], 'key': key},
            files={'file': ('reference.mp3', source, 'audio/mpeg')}, timeout=(10, 120), allow_redirects=False)
    if response.status_code != 200 or response.json().get('key') != key:
        raise BridgeError('音频上传回执不匹配。')
    with requests.get(url, stream=True, timeout=(10, 60), allow_redirects=False) as response:
        if not verify(response):
            raise BridgeError('音频上传后无法回读。')
    return url


def create_reference(gateway, name, url):
    gateway.post('/storyboard/api/createProjectLibraryWithImages', {
        'name': name, 'projectId': project_id(), 'seriesId': series_id(), 'type': 3,
        'description': 'BeefTV画布参考图原图导入；不触发生图。',
        'images': [{'name': name, 'isMain': 1, 'description': 'BeefTV原图', 'resourceUrl': [url]}]})
    return gateway.recover_reference(name, url)


def create_character_reference(gateway, name, url, voice):
    gateway.post('/storyboard/api/createProjectLibraryWithImages', {
        'name': name, 'projectId': project_id(), 'seriesId': series_id(), 'type': 1,
        'voiceId': voice['voiceId'], 'uploadVoiceUrl': voice['uploadVoiceUrl'],
        'description': 'BeefTV画布人物图与用户选定音色；不触发生成。',
        'images': [{'name': name, 'isMain': 1, 'resourceUrl': [url]}]})
    return gateway.recover_reference(name, url, voice)


def recover_reference(gateway, name, url, voice=None):
    # Reconcile an existing asset; never create another asset on recovery.
    ref = {'name': name, 'assetName': name, 'url': url}
    if voice is not None:
        ref.update(materialType=1, **voice)
        asset, image = gateway.read_character_reference(ref)
    else:
        asset, image = gateway.read_reference(ref, 3)
    ref.update(assetId=asset['assetId'], imageId=image['imageId'], name=image['name'])
    if voice is not None:
        # Official save propagates the verified parent voice to its child.
        gateway.post('/storyboard/api/updateProjectLibrary', {
            'id': asset['assetId'], 'name': name, 'projectId': project_id(),
            'voiceId': voice['voiceId'], 'uploadVoiceUrl': voice['uploadVoiceUrl']})
        gateway.read_character_reference(ref)
    return ref


def save_video_prompt(gateway, board_id, text, duration):
    return gateway.post('/storyboard/api/save', {'id': board_id, 'seriesId': series_id(),
        'videoPrompt': text, 'duration': duration, 'produceType': 5})
