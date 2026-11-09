"""Versioned, validated capability snapshots survive executor restart."""
import hashlib
import json
import os
import threading
import uuid
from pathlib import Path

LOCK=threading.RLock()
CACHE_NAME='capability-cache-v1.json'


def encode(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')


def build_channels(snapshots=None):
    """Build real consumers against detached candidates, without global writes."""
    import model_catalog,image_flow,extension
    from install_channel import channel as video_channel
    from install_extension import channel as extension_channel
    from install_enhancement import channel as enhancement_channel
    from install_modalities import channels
    video=model_catalog.models() if snapshots is None else model_catalog.build_models(snapshots['video'])
    image=image_flow.image_models() if snapshots is None else image_flow.build_image_models(snapshots['image'])
    extend=extension.extension_models() if snapshots is None else extension.build_models(snapshots['extension'])
    if not video or not image or not extend:raise ValueError('no implemented models')
    return [video_channel('',specs=model_catalog.available_models(video)),
            extension_channel('',specs=extension.selected_extension_models(extend)),
            enhancement_channel(''),*channels('',image_specs=image,text_specs={})]


def prepare(snapshots):
    from bridge import BridgeError
    if not isinstance(snapshots,dict) or set(snapshots)!={'video','image','extension'} or any(not isinstance(rows,list) or not rows for rows in snapshots.values()):
        raise BridgeError('实时能力目录不完整，请稍后刷新。')
    try:
        snapshots=json.loads(encode(snapshots))
        fresh=build_channels(snapshots)
        encode(fresh)
    except (ValueError,KeyError,TypeError,IndexError,AttributeError):
        raise BridgeError('能力目录数据无效，未覆盖已验证缓存。') from None
    return snapshots,fresh


def apply(snapshots):
    import dreama_image
    import model_catalog
    from dreama_compat import extension
    model_catalog.LIVE_ITEMS=snapshots['video']
    dreama_image.LIVE_ITEMS=snapshots['image']
    extension.LIVE_ITEMS=snapshots['extension']


def persist(root,snapshots):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    body={'schemaVersion':1,'sha256':hashlib.sha256(encode(snapshots)).hexdigest(),'catalogs':snapshots}
    pending=root/(CACHE_NAME+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with pending.open('xb') as stream:
            stream.write(encode(body));stream.flush();os.fsync(stream.fileno())
        os.replace(pending,root/CACHE_NAME)
    finally:
        pending.unlink(missing_ok=True)


def restore(root):
    from bridge import BridgeError
    path=Path(root)/CACHE_NAME
    if not path.exists():return False
    with LOCK:
        try:
            body=json.loads(path.read_text('utf-8'))
            if (type(body.get('schemaVersion')) is not int or body['schemaVersion']!=1
                    or hashlib.sha256(encode(body.get('catalogs'))).hexdigest()!=body.get('sha256')):
                raise ValueError('unsupported or inconsistent cache')
            snapshots,_=prepare(body['catalogs'])
        except (BridgeError,ValueError,KeyError,TypeError,AttributeError,OSError):
            raise BridgeError('持久能力目录无法验证，停止启动以保护已保存模型选择；请恢复有效缓存。') from None
        apply(snapshots)
    return True
