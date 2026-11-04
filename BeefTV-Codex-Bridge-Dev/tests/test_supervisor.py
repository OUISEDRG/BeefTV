import json
import logging
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import supervisor


FAKE_SERVER = r'''
import argparse, json, os, threading, time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
parser=argparse.ArgumentParser()
parser.add_argument('--root',type=Path,required=True)
parser.add_argument('--port',type=int,required=True)
args=parser.parse_args()
state=args.root/'state'
state.mkdir(exist_ok=True)
launches=state/'fixture-launches.jsonl'
with launches.open('a',encoding='utf-8') as output:
    output.write(json.dumps({'pid':os.getpid(),'time':time.time()})+'\n')
attempt=len(launches.read_text('utf-8').splitlines())
fail=args.root/'fixture-fail-first'
if fail.exists() and attempt<=int(fail.read_text()):
    print('simulated startup failure',flush=True)
    raise SystemExit(7)
delay=args.root/'fixture-start-delay'
if delay.exists(): time.sleep(float(delay.read_text()))
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def do_GET(self):
        with (state/'fixture-requests.jsonl').open('a',encoding='utf-8') as output:
            output.write(json.dumps({'method':'GET','path':self.path,
                                     'authorization':bool(self.headers.get('Authorization'))})+'\n')
        foreign=(args.root/'fixture-foreign').exists()
        value={'service':'foreign' if foreign else 'beeftv-codex','ready':True,'pid':os.getpid()}
        body=json.dumps(value).encode()
        self.send_response(200); self.send_header('Content-Length',str(len(body)))
        self.end_headers(); self.wfile.write(body)
http=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
def watch():
    while True:
        if (state/'fixture-crash').exists():
            (state/'fixture-crash').unlink()
            os._exit(17)
        if (state/'fixture-stop').exists():
            http.shutdown(); return
        time.sleep(.02)
threading.Thread(target=watch,daemon=True).start()
try: http.serve_forever(poll_interval=.02)
finally: http.server_close()
'''


def free_port():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        return listener.getsockname()[1]


def wait_for(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.03)
    raise AssertionError('condition did not become true before timeout')


def process_exited(pid):
    if sys.platform != 'win32':
        try:
            import os
            os.kill(pid, 0)
            return False
        except ProcessLookupError:
            return True
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x00100000, False, pid)
    if not handle:
        return True
    try:
        return kernel.WaitForSingleObject(handle, 0) == 0
    finally:
        kernel.CloseHandle(handle)


class SupervisorUnitTests(unittest.TestCase):
    def test_live_child_is_not_killed_or_restarted_when_health_times_out(self):
        with tempfile.TemporaryDirectory() as directory:
            guard = supervisor.Supervisor(directory)
            guard.logger = Mock()
            guard.child = Mock()
            guard.child.poll.return_value = None
            with patch.object(supervisor, 'probe_health', return_value='unavailable'), \
                 patch.object(guard, 'spawn') as spawn, \
                 patch.object(supervisor, 'port_is_available') as available:
                for _ in range(4):
                    self.assertTrue(guard.step())
                spawn.assert_not_called()
                available.assert_not_called()
                guard.child.terminate.assert_not_called()
                guard.child.kill.assert_not_called()

    def test_retry_delay_is_capped_and_not_bypassed(self):
        with tempfile.TemporaryDirectory() as directory:
            guard = supervisor.Supervisor(directory, retry_initial=2, retry_max=5)
            guard.logger = Mock()
            with patch.object(supervisor.time, 'monotonic', return_value=100), \
                 patch.object(supervisor, 'probe_health', return_value='unavailable'), \
                 patch.object(supervisor, 'port_is_available', return_value=True), \
                 patch.object(guard, 'spawn', side_effect=OSError(5, 'simulated')) as spawn:
                guard.step()
                self.assertEqual(guard.next_launch, 102)
                guard.step()
                self.assertEqual(spawn.call_count, 1)
                guard.schedule_retry('failure')
                self.assertEqual(guard.next_launch, 104)
                for _ in range(20):
                    guard.schedule_retry('failure')
                self.assertEqual(guard.next_launch, 105)

    def test_occupied_port_is_never_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            guard = supervisor.Supervisor(directory)
            guard.logger = Mock()
            with patch.object(supervisor, 'probe_health', return_value='unavailable'), \
                 patch.object(supervisor, 'port_is_available', return_value=False), \
                 patch.object(guard, 'spawn') as spawn:
                guard.step()
                spawn.assert_not_called()

    def test_log_files_rotate_and_child_log_keeps_prior_content(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            path = state / 'server.log'
            path.write_text('existing output', encoding='utf-8')
            supervisor.rotate_closed_log(path, limit=100)
            self.assertEqual(path.read_text('utf-8'), 'existing output')
            for index in range(5):
                path.write_text(str(index) * 100, encoding='utf-8')
                supervisor.rotate_closed_log(path, limit=100)
            self.assertEqual(len(list(state.glob('server.log.*'))), 3)
            self.assertEqual((state/'server.log.1').read_text(), '4' * 100)
            logger = supervisor.make_logger(state, limit=200)
            try:
                for index in range(100):
                    logger.info('test event %s %s', index, 'x' * 30)
            finally:
                for handler in logger.handlers:
                    handler.close()
            files = list(state.glob('supervisor.log*'))
            self.assertLessEqual(len(files), 4)
            self.assertTrue(all(path.stat().st_size <= 250 for path in files))
            self.assertIn('Z INFO test event', (state/'supervisor.log').read_text())


class SupervisorProcessTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.state = self.root / 'state'
        self.state.mkdir()
        (self.root/'server.py').write_text(FAKE_SERVER, encoding='utf-8')
        self.port = free_port()
        self.processes = []

    def tearDown(self):
        (self.state/'supervisor.disabled').touch()
        (self.state/'fixture-stop').touch()
        for process in self.processes:
            try:
                process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=4)
        # The fake server is intentionally independent of its guardian.
        wait_for(lambda: supervisor.port_is_available(self.port), timeout=5)
        if sys.platform == 'win32':
            for launch in self.launches():
                wait_for(lambda: process_exited(launch['pid']), timeout=5)
        self.temporary.cleanup()

    def launches(self):
        path = self.state/'fixture-launches.jsonl'
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text('utf-8').splitlines()]

    def start_guard(self):
        process = subprocess.Popen(
            [sys.executable, str(Path(supervisor.__file__).resolve()), '--root', str(self.root),
             '--port', str(self.port), '--poll-interval', '.08', '--retry-initial', '.15',
             '--retry-max', '.3', '--health-timeout', '.15'],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.processes.append(process)
        return process

    def wait_ready(self):
        wait_for(lambda: supervisor.probe_health(self.port, .15) == 'ready')

    def test_concurrent_guardians_create_one_child_even_with_slow_start(self):
        (self.root/'fixture-start-delay').write_text('.7')
        first, second = self.start_guard(), self.start_guard()
        self.wait_ready()
        self.assertEqual(len(self.launches()), 1)
        wait_for(lambda: sum(p.poll() is None for p in (first, second)) == 1)
        record = json.loads((self.state/'supervisor.json').read_text('utf-8'))
        self.assertIn(record['pid'], [first.pid, second.pid])
        self.assertEqual(record['root'], str(self.root.resolve()))
        self.assertTrue(record['instanceId'])
        if sys.platform == 'win32':
            self.assertGreater(record['processStartFileTime'], 0)
        requests = [json.loads(line) for line in (self.state/'fixture-requests.jsonl').read_text().splitlines()]
        self.assertTrue(requests)
        self.assertTrue(all(r == {'method':'GET','path':'/health','authorization':False} for r in requests))

    def test_startup_failures_and_subsequent_crash_are_recovered(self):
        (self.root/'fixture-fail-first').write_text('2')
        guard = self.start_guard()
        self.wait_ready()
        self.assertEqual(len(self.launches()), 3)
        (self.state/'fixture-crash').touch()
        wait_for(lambda: len(self.launches()) == 4)
        self.wait_ready()
        self.assertIsNone(guard.poll())
        attempts = self.launches()
        self.assertGreaterEqual(attempts[1]['time'] - attempts[0]['time'], .12)
        self.assertGreaterEqual(attempts[2]['time'] - attempts[1]['time'], .25)
        log = (self.state/'supervisor.log').read_text('utf-8')
        self.assertIn('exited with code 7', log)
        self.assertIn('exited with code 17', log)

    def test_disable_stops_guard_without_killing_server_or_relaunching(self):
        guard = self.start_guard()
        self.wait_ready()
        (self.state/'supervisor.disabled').touch()
        self.assertEqual(guard.wait(timeout=4), 0)
        self.assertEqual(supervisor.probe_health(self.port, .3), 'ready')
        self.assertFalse((self.state/'supervisor.json').exists())
        (self.state/'fixture-stop').touch()
        wait_for(lambda: supervisor.port_is_available(self.port))
        time.sleep(.7)
        self.assertEqual(len(self.launches()), 1)
        disabled_start = self.start_guard()
        self.assertEqual(disabled_start.wait(timeout=3), 0)
        self.assertEqual(len(self.launches()), 1)

    def test_crashed_guard_releases_os_lock_and_new_guard_adopts_existing_server(self):
        first = self.start_guard()
        self.wait_ready()
        old = json.loads((self.state/'supervisor.json').read_text('utf-8'))
        first.kill()
        first.wait(timeout=3)
        self.assertTrue((self.state/'supervisor.json').exists())
        second = self.start_guard()
        def changed():
            value = json.loads((self.state/'supervisor.json').read_text('utf-8'))
            return value['instanceId'] != old['instanceId']
        wait_for(changed)
        time.sleep(.4)
        self.assertIsNone(second.poll())
        self.assertEqual(len(self.launches()), 1)
        self.assertEqual(supervisor.probe_health(self.port, .3), 'ready')

    def test_foreign_listener_is_left_alive_and_no_child_is_started(self):
        (self.root/'fixture-foreign').touch()
        foreign = subprocess.Popen([sys.executable, str(self.root/'server.py'), '--root', str(self.root),
                                    '--port', str(self.port)], stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.processes.append(foreign)
        wait_for(lambda: supervisor.probe_health(self.port, .15) == 'foreign')
        guard = self.start_guard()
        wait_for(lambda: (self.state/'supervisor.json').exists())
        time.sleep(.5)
        self.assertIsNone(foreign.poll())
        self.assertIsNone(guard.poll())
        self.assertEqual(len(self.launches()), 1)
        self.assertIn('foreign service', (self.state/'supervisor.log').read_text('utf-8'))


if __name__ == '__main__':
    unittest.main()
