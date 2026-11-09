"""Allowlisted Dreama website transport. Read/prepare and paid routes are disjoint."""
import json
import re

import requests

from bridge_errors import BridgeError
from budget_policy import point_text
from job_binding import verify_write_identity
from submission import checked_before_dispatch, require_paid_intent

_BASE = 'https://jzzm.duanju.com/gw'
ASSET_WRITES = frozenset({
    '/user/resource/getOssToken', '/storyboard/api/createProjectLibraryWithImages',
    '/storyboard/api/updateProjectLibrary', '/storyboard/api/save',
})
EXTENSION_READS = frozenset({
    '/storyboard/api/list', '/storyboard/api/historyList',
    '/storyboard/endpoint/list', '/storyboard/endpoint/calcToken',
})
VOICE_READS = frozenset({'/storyboard/voiceTone/list', '/storyboard/voiceTone/listMine'})
AUDIO_TASK = '/nestjs/ai-lab/voiceovers/task'
_READ_GET = frozenset({AUDIO_TASK, '/nestjs/tasks', '/nestjs/tasks/running-count'})
_PAID = {'extension': '/storyboard/api/produceVideo', 'enhancement': '/nestjs/tasks', 'audio': AUDIO_TASK}


def credential_token(gateway):
    try:
        token = json.loads(gateway.credentials.read_text('utf-8-sig'))['accessToken']
        if isinstance(token, str) and token.strip():
            return token
    except (OSError, ValueError, KeyError, TypeError):
        pass
    raise BridgeError('无法读取当前 Dreama 登录状态。')


def _send(method, path, token, *, payload, params, timeout, success=(200,)):
    try:
        kwargs = dict(headers={'Authorization': token}, json=payload, timeout=timeout, allow_redirects=False)
        if method == 'POST' and params is None:
            response = requests.post(_BASE + path, **kwargs)
        else:
            response = requests.request(method, _BASE + path, params=params, **kwargs)
        if response.status_code not in success:
            raise BridgeError('Dreama 兼容接口 HTTP 失败：' + str(response.status_code))
        body = response.json(parse_float=point_text) if path == '/storyboard/endpoint/calcToken' else response.json()
    except (requests.RequestException, ValueError):
        raise BridgeError('Dreama 兼容接口传输或 JSON 回执失败。') from None
    if not isinstance(body, dict) or str(body.get('code')) != '0':
        raise BridgeError('Dreama 兼容接口业务回执失败。')
    return body.get('result')


def request(gateway, method, path, *, payload=None, params=None, token=None):
    if method == 'POST' and path in ASSET_WRITES:
        verify_write_identity(gateway)
    elif not (method == 'POST' and path in EXTENSION_READS | VOICE_READS
              or method == 'GET' and (path in _READ_GET or re.fullmatch(r'/nestjs/tasks/[0-9a-f-]{36}', path))):
        raise BridgeError('Dreama 兼容接口不在查询或准备白名单。')
    timeout = (10, 30) if path in VOICE_READS else (10, 90)
    return _send(method, path, token or credential_token(gateway),
                 payload=payload, params=params, timeout=timeout)


def submit(gateway, operation, job, payload, *, identity_gateway=None):
    if operation not in _PAID:
        raise BridgeError('Dreama 兼容收费接口未获准。')
    require_paid_intent(operation, job)
    with checked_before_dispatch():
        verify_write_identity(identity_gateway or gateway)
        token = credential_token(gateway)
    require_paid_intent(operation, job, consume=True)
    return _send('POST', _PAID[operation], token, payload=payload, params=None,
                 timeout=(10, 90), success=(200, 201) if operation == 'audio' else (200,))
