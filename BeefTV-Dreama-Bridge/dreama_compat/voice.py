"""Account scoped voice catalog and immutable picture/voice request bindings.

Contracts: create-CvFMVb6n.js, VoiceSelectorModal-B339lT2t.js and the official
CLI's read-only prompt assembler. No endpoint here starts inference.
"""
import hashlib
import math
import re
import threading
import time
from pathlib import Path

import requests

from dreama_video import BridgeError, DreamaGateway
from . import http

ROOT = Path(__file__).resolve().parent


class VoiceCatalog:
    def __init__(self, gateway=None, ttl=60):
        self.gateway = gateway or DreamaGateway()
        self.ttl = ttl
        self.lock = threading.Lock()
        self._scope = None
        self._expires = 0
        self._rows = {}

    def _read(self, token, path, payload):
        if path not in http.VOICE_READS:
            raise BridgeError('音色接口不在已核实范围。')
        return http.request(self.gateway, 'POST', path, payload=payload, token=token)

    def get(self, force=False):
        from .audio import cdn_url
        token = http.credential_token(self.gateway)
        scope = hashlib.sha256(token.encode()).hexdigest()
        with self.lock:
            # A different login never receives the previous account's private rows.
            if self._scope != scope:
                self._rows = {}; self._expires = 0; self._scope = scope
            if not force and self._expires > time.monotonic():
                return {key: dict(value) for key, value in self._rows.items()}
            rows = {}
            for source, path, extra in (
                ('public', '/storyboard/voiceTone/list', {'isHumanlike': True}),
                ('public', '/storyboard/voiceTone/list', {'provider': 'douge'}),
                ('personal', '/storyboard/voiceTone/listMine', {'provider': 'custom'})):
                for page in range(1, 101):
                    body = self._read(token, path, {'page': page, 'size': 100, **extra})
                    items = body.get('items', []) if isinstance(body, dict) else body
                    if not isinstance(items, list):
                        raise BridgeError('剧梦音色目录响应无效。')
                    for raw in items:
                        voice_id = str(raw.get('id', ''))
                        url = raw.get('voiceUrl') or raw.get('clipVoiceUrl')
                        if not re.fullmatch(r'[1-9][0-9]*', voice_id) or not cdn_url(url):
                            continue
                        old = rows.get(voice_id)
                        if old and old['source'] != source:
                            raise BridgeError('公共与个人音色编号冲突，未自动选择。')
                        duration = raw.get('duration', raw.get('clipDurationSec'))
                        clip_duration = raw.get('clipDurationSec')
                        rows[voice_id] = {'id': voice_id, 'name': str(raw.get('name') or voice_id),
                            'source': source, 'previewUrl': url, 'voiceUrl': url,
                            'videoUrl': raw.get('clipVoiceUrl') if cdn_url(raw.get('clipVoiceUrl')) else url,
                            'duration': duration if type(duration) in (int, float) and math.isfinite(duration) and duration > 0 else None,
                            'clipDuration': clip_duration if type(clip_duration) in (int, float) and math.isfinite(clip_duration) and clip_duration > 0 else None}
                    total = body.get('totalCount') if isinstance(body, dict) else None
                    if len(items) < 100 or type(total) is int and page * 100 >= total:
                        break
                else:
                    raise BridgeError('音色目录分页未完整返回，请稍后重试。')
            self._rows = rows; self._expires = time.monotonic() + self.ttl
            return {key: dict(value) for key, value in rows.items()}

    def resolve(self, voice_id, force=False):
        row = self.get(force=force).get(str(voice_id))
        if not row:
            raise BridgeError('所选音色已删除或不属于当前 Dreama 账号，请重新选择。')
        return row


_catalog = None
_catalog_lock = threading.Lock()


def catalog():
    global _catalog
    with _catalog_lock:
        if _catalog is None:
            _catalog = VoiceCatalog()
        return _catalog


def media_info(url):
    from audio_flow import cdn_url
    if not cdn_url(url):
        raise BridgeError('音色样本地址无效。')
    response = requests.get(url + ('&' if '?' in url else '?') + 'avinfo',
                            timeout=(10, 30), allow_redirects=False)
    if response.status_code != 200 or len(response.content) > 65536:
        raise BridgeError('无法确认音色样本时长，请选择其他音色。')
    try:
        info = response.json()['format']
        duration, size = info['duration'], info['size']
        if isinstance(duration, bool) or isinstance(size, bool):
            raise ValueError()
        duration = float(duration)
        if isinstance(size, str) and not re.fullmatch(r'[0-9]+', size):
            raise ValueError()
        if type(size) not in (int, str):
            raise ValueError()
        size = int(size)
        if not math.isfinite(duration) or duration <= 0 or size <= 0:
            raise ValueError()
        return {'duration': duration, 'bytes': size}
    except (KeyError, TypeError, ValueError):
        raise BridgeError('音色样本时长或大小无效。') from None
