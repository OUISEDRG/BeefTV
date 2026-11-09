"""Bounded, opt-in cleanup of acknowledged Bridge-owned files."""
from contextlib import closing, ExitStack, nullcontext
import hashlib
import json
import logging
import math
import os
import sqlite3
import stat
import time


HISTORY_ONLY = frozenset(("deleted", "missing"))
MAY_HAVE_REMOVED = HISTORY_ONLY | {"deleting"}
AUDIT_TABLE = """CREATE TABLE media_cleanup_attempts (
    id INTEGER PRIMARY KEY, artifact_id TEXT NOT NULL,
    started_at REAL NOT NULL, finished_at REAL, outcome TEXT)"""


class UnsafeMedia(ValueError):
    pass


def _verify(stream, size, sha):
    info = os.fstat(stream.fileno())
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != size
            or hashlib.file_digest(stream, "sha256").hexdigest() != sha):
        raise UnsafeMedia("media identity changed")


def delete_verified(root, path, size, sha):
    """Check and delete by the same Windows handle, without following a leaf link."""
    if os.name != "nt":
        raise UnsafeMedia("safe handle-based deletion is unavailable")
    relative = path.relative_to(root)
    if not relative.parts or ".." in relative.parts:
        raise UnsafeMedia("outside owned media directory")

    import ctypes
    from ctypes import wintypes as w
    import msvcrt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = (w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID, w.DWORD, w.DWORD, w.HANDLE)
    create.restype = w.HANDLE
    final_path = kernel.GetFinalPathNameByHandleW
    final_path.argtypes = (w.HANDLE, w.LPWSTR, w.DWORD, w.DWORD)
    final_path.restype = w.DWORD
    get_info = kernel.GetFileInformationByHandleEx
    set_info = kernel.SetFileInformationByHandle
    for function in (get_info, set_info):
        function.argtypes = (w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD)
        function.restype = w.BOOL
    close = kernel.CloseHandle
    close.argtypes = (w.HANDLE,)
    close.restype = w.BOOL

    generic_read, delete_access = 0x80000000, 0x10000
    share_read, share_write, open_existing = 1, 2, 3
    open_reparse_point, backup_semantics = 0x00200000, 0x02000000
    read_attributes, file_attribute_tag_info = 0x80, 9
    directory_attribute, reparse_attribute = 0x10, 0x400
    extended_prefix = chr(92) * 2 + "?" + chr(92)
    if not path.is_absolute() or str(path).startswith(chr(92) * 2):
        raise UnsafeMedia("local NTFS media path required")

    def open_path(target, access, sharing, flags):
        handle = create(extended_prefix + str(target), access, sharing, None,
                        open_existing, flags | open_reparse_point, None)
        if handle == ctypes.c_void_p(-1).value:
            error = ctypes.get_last_error()
            if error in (2, 3):
                raise FileNotFoundError(str(target))
            raise ctypes.WinError(error)
        return handle

    def verify_path(handle, target, directory):
        expected = extended_prefix + str(target)
        buffer = ctypes.create_unicode_buffer(32768)
        length = final_path(handle, buffer, len(buffer), 0)
        if not length or length >= len(buffer):
            raise ctypes.WinError(ctypes.get_last_error())
        if buffer.value != expected:
            raise UnsafeMedia("redirected media path")
        tag = (w.DWORD * 2)()
        if not get_info(handle, file_attribute_tag_info, tag, ctypes.sizeof(tag)):
            raise ctypes.WinError(ctypes.get_last_error())
        if tag[0] & reparse_attribute or bool(tag[0] & directory_attribute) != directory:
            raise UnsafeMedia("unexpected media file type")

    with ExitStack() as parents:
        # Deny delete sharing on ancestor directories, including the owned root,
        # while checking and deleting the leaf through its own handle.
        for directory in reversed(path.parents):
            if directory == directory.parent:
                continue  # Volume root cannot be moved.
            parent = open_path(directory, read_attributes, share_read | share_write, backup_semantics)
            parents.callback(close, parent)
            verify_path(parent, directory, directory=True)
        handle = open_path(path, generic_read | delete_access, share_read, 0)
        try:
            verify_path(handle, path, directory=False)
            fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
            handle = None  # The stream owns the handle from this point.
            with os.fdopen(fd, "rb") as stream:
                _verify(stream, size, sha)
                remove = w.BOOL(True)
                file_disposition_info = 4
                if not set_info(msvcrt.get_osfhandle(fd), file_disposition_info,
                                ctypes.byref(remove), ctypes.sizeof(remove)):
                    raise ctypes.WinError(ctypes.get_last_error())
        finally:
            if handle is not None:
                close(handle)


def validate_saved_ack(row):
    ack = json.loads(row["payload_json"])
    if (not isinstance(ack, dict) or type(ack.get("schemaVersion")) is not int
            or ack["schemaVersion"] != 1 or not isinstance(ack.get("resourceId"), str)
            or not ack["resourceId"].strip() or type(ack.get("sizeBytes")) is not int
            or type(ack.get("outputIndex")) is not int
            or (ack.get("taskId"), ack.get("artifactId"), ack.get("outputIndex"), ack.get("sizeBytes"), ack.get("sha256"))
            != (row["task_id"], row["artifact_id"], row["output_index"], row["size_bytes"], row["sha256"])):
        raise UnsafeMedia("saved ACK identity changed")


class MediaCleanup:
    def __init__(self, database, root, queue, selection, media_file, task_lock=None):
        self.database, self.root, self.queue = database, root, queue
        self.policy = database.parent / "media-retention.json"
        self.selection, self.media_file = selection, media_file
        self.task_lock = task_lock

    def _retention(self):
        try:
            with self.policy.open("rb") as stream:
                encoded = stream.read(4097)
            if len(encoded) > 4096:
                return None
            policy = json.loads(encoded)
            if (isinstance(policy, dict) and policy.keys() == {"schemaVersion", "retentionSeconds"}
                    and type(policy["schemaVersion"]) is int and policy["schemaVersion"] == 1
                    and type(policy["retentionSeconds"]) is int and 0 < policy["retentionSeconds"] < 2**53):
                return policy["retentionSeconds"]
        except (OSError, ValueError):
            pass
        return None

    def _claim(self, db, now, retention):
        with db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(self.selection + """ AND k.state IN ('held','deleting')
                AND (k.eligible_after IS NULL OR k.eligible_after<=?)
                AND a.received_at>0 AND a.received_at<=?
                ORDER BY a.received_at,k.artifact_id LIMIT 1""", (now, now-retention)).fetchone()
            if row is None:
                return None
            artifact = row["artifact_id"]
            db.execute("""UPDATE media_cleanup_attempts SET finished_at=?,outcome='interrupted'
                WHERE artifact_id=? AND finished_at IS NULL""", (now, artifact))
            attempt = db.execute("INSERT INTO media_cleanup_attempts(artifact_id,started_at) VALUES (?,?)",
                                 (artifact, now)).lastrowid
            db.execute(f"UPDATE {self.queue} SET state='deleting',eligible_after=? WHERE artifact_id=?",
                       (now + 600, artifact))
            return row, attempt

    def sweep(self, *, now=None, stop=None):
        retention = self._retention()
        if retention is None or os.name != "nt":
            return 0
        now = time.time() if now is None else now
        if not math.isfinite(now):
            raise ValueError("cleanup time must be finite")
        deleted = 0
        with closing(sqlite3.connect(self.database.resolve().as_uri() + "?mode=rw", uri=True, timeout=2)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            for _ in range(8):
                if (stop is not None and stop.is_set()) or self._retention() != retention:
                    break
                claimed = self._claim(db, now, retention)
                if claimed is None:
                    break
                row, attempt = claimed
                artifact = row["artifact_id"]
                with self.task_lock(row["job_id"]) if self.task_lock else nullcontext():
                    outcome = self._remove(db, row, retention)
                with db:
                    changed = db.execute("""UPDATE media_cleanup_attempts SET finished_at=?,outcome=?
                        WHERE id=? AND finished_at IS NULL""", (max(now, time.time()), outcome, attempt)).rowcount
                    if changed:
                        db.execute(f"UPDATE {self.queue} SET state=?,eligible_after=? WHERE artifact_id=?",
                                   ("deleting" if outcome == "retry" else outcome,
                                    max(now, time.time()) + 30 if outcome == "retry" else None, artifact))
                        deleted += outcome == "deleted"
        return deleted

    def _remove(self, db, claimed, retention):
        try:
            row = db.execute(self.selection + " AND k.artifact_id=?", (claimed["artifact_id"],)).fetchone()
            if row is None or tuple(row) != tuple(claimed):
                raise UnsafeMedia("job or artifact identity changed")
            validate_saved_ack(row)
            path = self.media_file(db, row)
            if self._retention() != retention:
                return "retry"
            delete_verified(self.root, path, row["size_bytes"], row["sha256"])
        except FileNotFoundError:
            return "missing"
        except (ValueError, TypeError, KeyError, IndexError):
            return "blocked"
        except OSError:
            return "retry"
        return "deleted"

    def loop(self, stop):
        while not stop.wait(30):
            try:
                self.sweep(stop=stop)
            except (OSError, sqlite3.Error):
                logging.warning("Media cleanup could not access the local ledger. Will retry.")


def receipts(db, selection, job_id):
    result = []
    for artifact, payload, received, state in db.execute(selection, (job_id,)):
        attempts = db.execute("""SELECT started_at,finished_at,outcome FROM media_cleanup_attempts
            WHERE artifact_id=? ORDER BY id DESC LIMIT 8""", (artifact,)).fetchall()
        result.append({"ack": json.loads(payload), "receivedAt": received, "cleanupState": state,
                       "cleanupAttempts": [{"startedAt": start, "finishedAt": end, "outcome": outcome}
                                           for start, end, outcome in attempts]})
    return result
