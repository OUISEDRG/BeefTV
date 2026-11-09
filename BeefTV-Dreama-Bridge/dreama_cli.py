"""The single Dreama CLI 1.0.5 transport for Bridge and maintenance callers."""
import json
import logging
import os
import re
import subprocess
from contextlib import nullcontext
from pathlib import Path

from bridge_errors import BridgeError
from budget_policy import point_text
from submission import checked_before_dispatch, require_paid_intent
from task_contracts import cli_operation_class, validate_contract

_LOG = logging.getLogger(__name__)
_VERSION = 'Dreama cli 1.0.5 env=prod endpoint=https://jzzm.duanju.com/gw'
_EXIT_ACTION = {2: 'fix_params', 3: 'confirm', 10: 'login', 11: 'abort', 20: 'retry', 30: 'abort'}

# Only Bridge-supported commands from the P1 classes and the 1.0.5 help.
# The first two columns are required and optional option names.
_OPTIONS = {
    ('auth', 'whoami'): ('', ''),
    ('project', 'get'): ('id', ''),
    ('project', 'list'): ('', 'page size search include-canvas'),
    ('series', 'get'): ('id', ''),
    ('series', 'list'): ('project-id', 'page size name'),
    ('series', 'create'): ('project-id name yes', ''),
    ('library', 'list'): ('project-id type', 'series-id search page page-size'),
    ('storyboard', 'list'): ('series-id', 'id name project-id page size'),
    ('storyboard', 'create'): ('series-id name yes', ''),
    ('storyboard', 'update'): ('series-id id yes', 'video-prompt image-prompt duration produce-type'),
    ('prompt', 'assemble'): ('project-id kind prompt', 'ref'),
    ('point', 'calc'): ('project-id kind llm', 'duration image-resolution number'),
    ('capability', 'list'): ('kind', 'eid name'),
    ('task', 'status'): ('task-id', 'project-id'),
    ('task', 'get'): ('task-id', ''),
    ('video', 'produce'): ('project-id series-id storyboard-id llm duration produce-video-format yes', 'resolution'),
    ('image', 'produce'): ('project-id series-id storyboard-id llm image-resolution proportion yes', ''),
}
_SWITCHES = {'yes', 'include-canvas'}
_INTS = {'id', 'project-id', 'series-id', 'storyboard-id', 'task-id', 'page', 'size', 'page-size', 'number'}
_REPEATED = {('prompt', 'assemble', 'ref'), ('storyboard', 'list', 'id'), ('task', 'status', 'task-id')}


def _validate_value(name, value):
    if type(value) not in (int, float, str):
        raise BridgeError('Dreama CLI 参数类型无效。')
    value = str(value)
    if '\0' in value:
        raise BridgeError('Dreama CLI 参数含有非法字符。')
    if name in _INTS:
        valid = bool(re.fullmatch(r'[1-9][0-9]{0,18}', value)) and int(value) < 2**63
    elif name == 'duration':
        valid = value == '-1' or bool(re.fullmatch(r'[0-9]{1,6}(?:\.[0-9]{1,9})?', value)) and float(value) > 0
    elif name == 'kind':
        valid = value in ('video', 'image')
    elif name == 'type':
        valid = value in ('1', '2', '3')
    elif name == 'produce-type':
        valid = value in ('3', '5')
    elif name == 'produce-video-format':
        valid = value == 'mp4'
    elif name == 'proportion':
        valid = bool(re.fullmatch(r'[1-9][0-9]{0,4}:[1-9][0-9]{0,4}', value))
    elif name in ('llm', 'eid', 'resolution', 'image-resolution'):
        valid = bool(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,199}', value))
    else:
        valid = bool(value.strip())
    if not valid:
        raise BridgeError('Dreama CLI 参数无效：--' + name)
    return value


def _build(args, *, paid):
    if len(args) < 2 or any(not isinstance(arg, str) for arg in args[:2]):
        raise BridgeError('Dreama CLI 命令无效。')
    pair = tuple(args[:2])
    category = cli_operation_class(pair)
    if pair not in _OPTIONS or (category == 'paid_submission') != paid:
        raise BridgeError('Dreama CLI 命令不在当前操作白名单。')
    required, optional = (set(names.split()) for names in _OPTIONS[pair])
    seen, argv, values = set(), [], {}
    arguments = iter(args[2:])
    for flag in arguments:
        if not isinstance(flag, str) or not flag.startswith('--'):
            raise BridgeError('Dreama CLI 不接受位置参数。')
        name = flag[2:]
        if name not in required | optional or (name in seen and (*pair, name) not in _REPEATED):
            raise BridgeError('Dreama CLI 参数未获准或重复。')
        seen.add(name)
        if name in _SWITCHES:
            argv.append(flag)
        else:
            value = _validate_value(name, next(arguments, None))
            values[name] = value
            # A prompt beginning with --server or --yes stays one flag value.
            argv.append(flag + '=' + value)
    if not required <= seen or (pair == ('point', 'calc') and values.get('kind') == 'video' and 'duration' not in seen):
        raise BridgeError('Dreama CLI 缺少必需参数。')
    if pair == ('storyboard', 'update') and not seen - required:
        raise BridgeError('Dreama CLI 分镜更新缺少待修改字段。')
    validate_contract('cli-command', {'schemaVersion': 1, 'command': pair[0],
                                      'arguments': [pair[1], *argv], 'operationClass': category})
    return pair, category, argv, values


class DreamaCliClient:
    def __init__(self, executable=None):
        self.executable = Path(executable or Path(os.environ['LOCALAPPDATA']) / 'Programs/Dreama/Dreama.exe')
        self._version_checked = False

    def _process(self, argv, *, timeout, operation):
        if not self.executable.is_absolute() or self.executable.suffix.lower() != '.exe':
            raise BridgeError('Dreama CLI 必须使用固定的本机可执行文件。')
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith('DREAMA_')}
        try:
            return subprocess.run([str(self.executable), *argv], shell=False, stdin=subprocess.DEVNULL,
                                  capture_output=True, text=True, encoding='utf-8', timeout=timeout,
                                  env=env, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except subprocess.TimeoutExpired:
            _LOG.warning('dreama_cli operation=%s outcome=timeout', operation)
            raise BridgeError('Dreama CLI 超时。') from None
        except (OSError, UnicodeError):
            _LOG.warning('dreama_cli operation=%s outcome=process_error', operation)
            raise BridgeError('Dreama CLI 启动或读取失败。') from None

    def check_version(self):
        if not self._version_checked:
            result = self._process(['version'], timeout=10, operation='version')
            if result.returncode or result.stdout.strip() != _VERSION:
                raise BridgeError('Dreama CLI 版本或生产端点与已验证合同不一致。')
            self._version_checked = True

    def fits(self, *args):
        pair, _, argv, _ = _build(args, paid=False)
        command = [str(self.executable), *pair, *argv, '--timeout=90s']
        return len(subprocess.list2cmdline(command).encode('utf-16-le')) // 2 < 32767

    def run(self, *args, before_write=None):
        return self._execute(args, before_write)

    def submit(self, job, *args, before_write=None):
        if job is None:
            raise BridgeError('缺少本次持久化收费意图。')
        return self._execute(args, before_write, job=job)

    @staticmethod
    def _check_paid_parameters(pair, values, job):
        from job_binding import historical
        require_paid_intent(pair[0], job)
        binding = job.get('bindingSnapshot') or historical(job)
        expected = {'project-id': binding['projectId'], 'series-id': binding['seriesId'],
                    'storyboard-id': job['storyboardId'], 'llm': job['model']}
        if pair[0] == 'image':
            expected.update(proportion=job['aspect_ratio'], **{'image-resolution': job['resolution']})
        else:
            expected.update(duration=job['duration'], resolution=(job.get('modelSnapshot') or {}).get('resolution'))
        if any(values.get(key) != (None if value is None else str(value)) for key, value in expected.items()):
            raise BridgeError('Dreama CLI 收费参数与已保存任务不匹配。')

    def _execute(self, args, before_write, *, job=None):
        with checked_before_dispatch() if job is not None else nullcontext():
            pair, category, argv, values = _build(args, paid=job is not None)
            if job is not None:
                self._check_paid_parameters(pair, values, job)
            self.check_version()
            if category != 'read' and before_write:
                before_write()
        raw = pair == ('capability', 'list')
        operation = ' '.join(pair)
        if category == 'paid_submission':
            require_paid_intent(pair[0], job, consume=True)
        result = self._process([*pair, *argv, *(['--raw'] if raw else []), '--timeout=90s'],
                               timeout=100, operation=operation)
        _LOG.info('dreama_cli operation=%s class=%s exit_code=%d', operation, category, result.returncode)
        try:
            body = (json.loads(result.stdout, parse_float=point_text) if pair == ('point', 'calc')
                    else json.loads(result.stdout))
        except ValueError:
            body = None
        if result.returncode:
            action = _EXIT_ACTION.get(result.returncode, 'abort')
            raise BridgeError(f'Dreama CLI 操作失败，exit={result.returncode} action={action}。')
        if not isinstance(body, dict):
            raise BridgeError('Dreama CLI 回执不是有效的 JSON 对象。')
        payload = body.get('result')
        if raw:
            if str(body.get('code')) != '0' or not isinstance(payload, dict) or not isinstance(payload.get('items'), list):
                raise BridgeError('Dreama 实时能力目录回执无效。')
        elif body.get('ok') is not True:
            action = body.get('action')
            if action not in ('retry', 'login', 'fix_params', 'abort', 'confirm'):
                action = 'abort'
            raise BridgeError('Dreama CLI 操作失败，action=' + action + '。')
        elif payload is None and category in ('prepare', 'paid_submission'):
            # Write acknowledgements may be empty. Paid callers reconcile through P1.
            return {}
        elif not isinstance(payload, dict):
            raise BridgeError('Dreama CLI 回执缺少有效的 result。')
        return payload
