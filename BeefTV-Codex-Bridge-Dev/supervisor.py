"""Keep the local Codex bridge available without replaying any model request.

Only /health is requested. An explicit disabled marker stops this supervisor,
leaving the HTTP server alone so stop.ps1 can shut it down gracefully.
"""
from __future__ import annotations

import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from urllib.request import ProxyHandler, build_opener
import uuid


SERVICE = "beeftv-codex"
LOG_LIMIT = 1024 * 1024
LOG_BACKUPS = 3


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class SingleInstance:
    """An OS-owned lock, automatically released even if the owner crashes."""

    def __init__(self, path):
        self.path = Path(path)
        self.file = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError):
            handle.close()
            return False
        self.file = handle
        return True

    def close(self):
        if self.file is not None:
            self.file.close()
            self.file = None


def process_start_filetime():
    """A verifiable Windows process creation time, not just a reusable PID."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.GetProcessTimes.restype = wintypes.BOOL
    created, exited, system, user = (wintypes.FILETIME() for _ in range(4))
    if not kernel.GetProcessTimes(kernel.GetCurrentProcess(), ctypes.byref(created),
                                  ctypes.byref(exited), ctypes.byref(system), ctypes.byref(user)):
        raise ctypes.WinError(ctypes.get_last_error())
    return (created.dwHighDateTime << 32) | created.dwLowDateTime


def write_atomic(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def probe_health(port, timeout=1.5):
    """Never use a configured proxy or send authentication/account data."""
    try:
        opener = build_opener(ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}/health", timeout=timeout) as response:
            body = response.read(65537)
            if len(body) > 65536:
                return "unavailable"
            status = json.loads(body)
        if not isinstance(status, dict) or status.get("service") != SERVICE:
            return "foreign"
        return "ready" if status.get("ready") is True else "not_ready"
    except (OSError, ValueError):
        return "unavailable"


def port_is_available(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        if os.name == "nt":
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            listener.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def python_executable():
    current = Path(sys.executable)
    if current.name.lower() == "pythonw.exe":
        current = current.with_name("python.exe")
    return str(current)


def rotate_closed_log(path, limit=LOG_LIMIT, backups=LOG_BACKUPS):
    """Rotate only before spawning; never disturb a live child's log handles."""
    path = Path(path)
    if not path.exists() or path.stat().st_size < limit:
        return
    path.with_name(path.name + f".{backups}").unlink(missing_ok=True)
    for number in range(backups - 1, 0, -1):
        older = path.with_name(path.name + f".{number}")
        if older.exists():
            os.replace(older, path.with_name(path.name + f".{number + 1}"))
    os.replace(path, path.with_name(path.name + ".1"))


def make_logger(state, limit=LOG_LIMIT):
    logger = logging.getLogger("beeftv-supervisor-" + uuid.uuid4().hex)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = RotatingFileHandler(state / "supervisor.log", maxBytes=limit,
                                  backupCount=LOG_BACKUPS, encoding="utf-8")
    formatter = logging.Formatter("%(asctime)sZ %(levelname)s %(message)s", "%Y-%m-%dT%H:%M:%S")
    formatter.converter = time.gmtime
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger


class Supervisor:
    def __init__(self, root, port=18766, *, poll_interval=3.0, retry_initial=2.0,
                 retry_max=60.0, health_timeout=1.5, stable_seconds=30.0):
        self.root = Path(root).resolve()
        self.state = self.root / "state"
        self.port = port
        self.poll_interval = poll_interval
        self.retry_initial = retry_initial
        self.retry_max = retry_max
        self.health_timeout = health_timeout
        self.stable_seconds = stable_seconds
        self.instance_id = uuid.uuid4().hex
        self.child = None
        self.failures = 0
        self.next_launch = 0.0
        self.ready_since = None
        self.last_status = None
        self.last_status_at = 0.0
        self.logger = None

    @property
    def disabled(self):
        return (self.state / "supervisor.disabled").exists()

    def log_status(self, message):
        now = time.monotonic()
        if message != self.last_status or now - self.last_status_at >= 60:
            self.logger.info(message)
            self.last_status, self.last_status_at = message, now

    def schedule_retry(self, message):
        self.failures += 1
        delay = min(self.retry_max, self.retry_initial * (2 ** min(self.failures - 1, 12)))
        self.next_launch = time.monotonic() + delay
        self.ready_since = None
        self.logger.warning("%s; retry in %.2f seconds", message, delay)

    def spawn(self):
        script = self.root / "server.py"
        if not script.is_file():
            raise FileNotFoundError("server.py is missing")
        stdout = self.state / "server.log"
        stderr = self.state / "server-error.log"
        for path in (stdout, stderr):
            rotate_closed_log(path)
        # Direct append handles allow the server to survive supervisor shutdown.
        with stdout.open("ab", buffering=0) as output, stderr.open("ab", buffering=0) as error:
            marker = f"\n[{utc_now()}] supervisor starting bridge\n".encode("utf-8")
            output.write(marker)
            error.write(marker)
            return subprocess.Popen(
                [python_executable(), "-u", "-X", "utf8", str(script), "--root", str(self.root),
                 "--port", str(self.port)],
                cwd=self.root, stdin=subprocess.DEVNULL, stdout=output, stderr=error,
                close_fds=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def step(self):
        """One observation cycle. Never terminate a child or replay a request."""
        if self.disabled:
            return False
        if self.child is not None:
            result = self.child.poll()
            if result is not None:
                self.child = None
                self.schedule_retry(f"Bridge process exited with code {result}")
        status = probe_health(self.port, self.health_timeout)
        if status == "ready":
            if self.ready_since is None:
                self.ready_since = time.monotonic()
            if time.monotonic() - self.ready_since >= self.stable_seconds:
                self.failures = 0
            self.log_status("Bridge is ready; monitoring existing service")
            return True
        self.ready_since = None
        if self.child is not None:
            self.log_status("Bridge process is alive but health is not ready; leaving it running")
            return True
        # A live listener, including an unresponsive or unrelated one, is never replaced.
        if status == "foreign" or not port_is_available(self.port):
            self.log_status("Bridge port is occupied or has a foreign service; no process started")
            return True
        if time.monotonic() < self.next_launch:
            return True
        if self.disabled:
            return False
        try:
            self.child = self.spawn()
            self.logger.info("Started bridge child pid=%s", self.child.pid)
        except OSError as error:
            # Error class/code are sufficient; avoid logging arbitrary exception payloads.
            self.schedule_retry(f"Bridge launch failed ({type(error).__name__}, errno={error.errno})")
        return True

    def run(self):
        self.state.mkdir(parents=True, exist_ok=True)
        lock = SingleInstance(self.state / "supervisor.lock")
        if not lock.acquire():
            return 0
        record_path = self.state / "supervisor.json"
        try:
            if self.disabled:
                return 0
            self.logger = make_logger(self.state)
            record = {"pid": os.getpid(), "instanceId": self.instance_id, "startedAt": utc_now(),
                      "processStartFileTime": process_start_filetime(), "root": str(self.root),
                      "scriptPath": str(Path(__file__).resolve()), "executable": sys.executable,
                      "port": self.port}
            write_atomic(record_path, record)
            self.logger.info("Supervisor started pid=%s instance=%s", os.getpid(), self.instance_id)
            while self.step():
                # Check the marker promptly even during a long retry delay.
                deadline = time.monotonic() + self.poll_interval
                while not self.disabled and time.monotonic() < deadline:
                    time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))
            self.logger.info("Supervisor disabled; leaving any running bridge process alone")
            return 0
        except Exception as error:
            if self.logger:
                self.logger.error("Supervisor stopped unexpectedly (%s, errno=%s)",
                                  type(error).__name__, getattr(error, "errno", None))
            raise
        finally:
            try:
                record = json.loads(record_path.read_text("utf-8"))
                if record.get("instanceId") == self.instance_id:
                    record_path.unlink(missing_ok=True)
            except (OSError, ValueError):
                pass
            if self.logger:
                for handler in list(self.logger.handlers):
                    handler.close()
                    self.logger.removeHandler(handler)
            lock.close()


def positive_float(value):
    parsed = float(value)
    if not 0 < parsed <= 3600:
        raise argparse.ArgumentTypeError("must be positive and at most 3600")
    return parsed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--port", type=int, default=18766)
    parser.add_argument("--poll-interval", type=positive_float, default=3.0)
    parser.add_argument("--retry-initial", type=positive_float, default=2.0)
    parser.add_argument("--retry-max", type=positive_float, default=60.0)
    parser.add_argument("--health-timeout", type=positive_float, default=1.5)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    return Supervisor(args.root, args.port, poll_interval=args.poll_interval,
                      retry_initial=args.retry_initial, retry_max=args.retry_max,
                      health_timeout=args.health_timeout).run()


if __name__ == "__main__":
    raise SystemExit(main())
