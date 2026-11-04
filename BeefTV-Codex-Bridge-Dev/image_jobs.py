"""Durable, authenticated-by-caller image jobs. Never retry accepted inference.

Pillow is used only to validate image encodings; no image is transformed here.
One ImageJobs owner is allowed per bridge process/data directory.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid
import warnings

from PIL import Image
from completion_bridge import BridgeError


MIMES = {"PNG": ("image/png", ".png"), "JPEG": ("image/jpeg", ".jpg"),
         "WEBP": ("image/webp", ".webp")}
RATIOS = {"auto", "1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "21:9"}
RESOLUTIONS = {"auto", "1k", "2k", "4k", "8k"}
MAX_REFERENCE = 8 * 1024 * 1024
MAX_REFERENCES_TOTAL = 16 * 1024 * 1024
MAX_OUTPUT = 30 * 1024 * 1024
ACTIVE = ("queued", "running")
ERRORS = {
    "image_cancelled": "生图任务已取消。",
    "image_timeout": "Codex 生图超时；任务未自动重试，请先确认原任务结果。",
    "image_unavailable": "Codex 内置生图暂不可用，请检查本机 Codex 服务和账号权限。",
    "image_refused": "Codex 未接受这次生图请求，请调整提示词后重新发起。",
    "image_auth_required": "请先在本机 Codex 登录，再重新发起生图任务。",
    "image_quota_exceeded": "Codex 账号当前生图用量不足或达到限制。",
    "image_no_result": "Codex 本次没有返回可用图片，未将文字回复当作生图成功。",
    "image_generation_failed": "本机 Codex 生图失败；请检查服务状态后重新发起新任务。",
    "image_result_invalid": "Codex 返回的图片格式或大小无效，未保存为成功结果。",
    "image_service_restarted": "服务曾中断，原任务结果未确认；为避免重复生成，本任务未自动重试。",
    "image_service_stopped": "生图服务已停止，本任务未自动重试。",
}


def validate_image(data, declared_mime=None, limit=MAX_OUTPUT, max_pixels=64_000_000):
    if not isinstance(data, (bytes, bytearray)) or not 0 < len(data) <= limit:
        raise BridgeError("图片大小无效或超出限制。", 413, "image_result_invalid")
    data = bytes(data)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as picture:
                if picture.format not in MIMES or not 0 < picture.width * picture.height <= max_pixels:
                    raise ValueError("Unsupported image format or dimensions")
                mime, suffix = MIMES[picture.format]
                picture.verify()
        if declared_mime is not None and declared_mime != mime:
            raise ValueError("Declared MIME differs from image encoding")
    except Exception as error:
        raise BridgeError("只支持有效的 PNG、JPEG 或 WebP 图片。", 400,
                          "image_result_invalid") from error
    return data, mime, suffix


def normalize_request(payload):
    allowed = {"model", "prompt", "images", "aspectRatio", "transparentBackground", "resolution"}
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise BridgeError("生图请求格式无效。")
    if payload.get("model") != "codex-image":
        raise BridgeError("未知的 Codex 生图模型。", 404, "image_model_not_found")
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 30000:
        raise BridgeError("提示词不能为空，且不能超过 30000 字符。")
    ratio = payload.get("aspectRatio", "1:1")
    resolution = payload.get("resolution", "auto")
    transparent = payload.get("transparentBackground", False)
    if not isinstance(ratio, str) or ratio not in RATIOS:
        raise BridgeError("不支持的图片比例。")
    if not isinstance(resolution, str) or resolution not in RESOLUTIONS:
        raise BridgeError("不支持的目标分辨率。")
    if not isinstance(transparent, bool):
        raise BridgeError("透明背景必须为布尔值。")
    images = payload.get("images", [])
    if not isinstance(images, list) or len(images) > 4:
        raise BridgeError("最多支持 4 张参考图。")
    references, descriptors, total = [], [], 0
    for number, value in enumerate(images):
        if not isinstance(value, str) or len(value) > ((MAX_REFERENCE + 2) // 3) * 4 + 64:
            raise BridgeError("参考图格式无效或超过 8 MiB。", 413)
        match = re.fullmatch(r"data:(image/(?:png|jpeg|webp));base64,([A-Za-z0-9+/]*={0,2})", value)
        if not match:
            raise BridgeError("参考图必须以内联 PNG、JPEG 或 WebP 传入，不接受地址或文件路径。")
        try:
            data = base64.b64decode(match[2], validate=True)
        except (ValueError, binascii.Error) as error:
            raise BridgeError("参考图 Base64 编码无效。") from error
        data, mime, suffix = validate_image(data, match[1], MAX_REFERENCE)
        total += len(data)
        if total > MAX_REFERENCES_TOTAL:
            raise BridgeError("参考图总大小不能超过 16 MiB。", 413)
        name = f"reference-{number + 1}{suffix}"
        references.append((name, data))
        descriptors.append({"name": name, "mime": mime, "sha256": hashlib.sha256(data).hexdigest()})
    request = {"model": "codex-image", "prompt": prompt, "aspectRatio": ratio,
               "transparentBackground": transparent, "references": descriptors}
    # Preserve the pre-resolution request JSON and fingerprint for existing keys.
    if resolution != "auto":
        request["resolution"] = resolution
    encoded = json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return request, encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest(), references


def atomic_bytes(path, data):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class ImageJobs:
    def __init__(self, root, generator, max_pending=8):
        if not isinstance(max_pending, int) or max_pending < 1:
            raise ValueError("max_pending must be positive")
        self.root = Path(root).resolve()
        self.directory = self.root / "state" / "image-jobs"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.generator, self.max_pending = generator, max_pending
        self.condition = threading.Condition(threading.RLock())
        self.closed = False
        self._db_closed = False
        self._current = None
        self._events = {}
        # Separate OS-backed SQLite lock: a second owner must not mark live jobs interrupted.
        self.owner = sqlite3.connect(self.root / "state" / "image-jobs-owner.sqlite3",
                                     timeout=0, check_same_thread=False, isolation_level=None)
        try:
            self.owner.execute("BEGIN EXCLUSIVE")
        except sqlite3.OperationalError as error:
            self.owner.close()
            raise BridgeError("此生图任务目录已由另一个服务使用。", 409, "image_store_busy") from error
        self.db = None
        try:
            self.db = sqlite3.connect(self.root / "state" / "image-jobs.sqlite3",
                                      check_same_thread=False, isolation_level=None)
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute("""CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE NOT NULL,
                fingerprint TEXT NOT NULL, request_json TEXT NOT NULL,
                status TEXT NOT NULL, message TEXT NOT NULL, error_code TEXT,
                result_json TEXT, created REAL NOT NULL, updated REAL NOT NULL)""")
            self.db.execute("UPDATE jobs SET status='failed',message=?,error_code=?,updated=? "
                            "WHERE status IN ('queued','running')",
                            (ERRORS["image_service_restarted"], "image_service_restarted", time.time()))
        except Exception:
            if self.db is not None:
                self.db.close()
            self.owner.close()
            raise
        self.worker = threading.Thread(target=self._work, name="codex-image-jobs", daemon=True)
        self.worker.start()

    @property
    def active_count(self):
        with self.condition:
            if self._db_closed:
                return 0
            count = self.db.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
            if self._current:
                row = self.db.execute("SELECT status FROM jobs WHERE id=?", (self._current,)).fetchone()
                count += bool(row and row[0] not in ACTIVE)
            return count

    def _row(self, job_id):
        if not isinstance(job_id, str) or not re.fullmatch(r"[0-9a-f]{32}", job_id):
            raise BridgeError("生图任务不存在。", 404, "image_job_not_found")
        row = self.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise BridgeError("生图任务不存在。", 404, "image_job_not_found")
        return row

    def _snapshot(self, row):
        value = {"id": row["id"], "status": row["status"], "images": [], "message": row["message"]}
        if row["error_code"]:
            value["error"] = {"code": row["error_code"], "message": row["message"]}
        if row["status"] == "succeeded":
            image = json.loads(row["result_json"])
            if image["filename"] not in ("output.png", "output.jpg", "output.webp"):
                raise BridgeError("图片结果记录无效。", 500, "image_result_invalid")
            try:
                path = self.directory / row["id"] / image["filename"]
                if path.stat().st_size > MAX_OUTPUT:
                    raise ValueError("oversized output")
                data = path.read_bytes()
                if hashlib.sha256(data).hexdigest() != image["sha256"]:
                    raise ValueError("output changed")
            except (OSError, ValueError) as error:
                raise BridgeError("已生成图片文件不可用；任务未自动重新生成。", 500,
                                  "image_result_unavailable") from error
            value["images"] = [{"dataUrl": f"data:{image['mime']};base64," + base64.b64encode(data).decode("ascii"),
                                "mimeType": image["mime"]}]
            value["metadata"] = image.get("metadata", {})
        return value

    def create(self, payload, idempotency_key):
        if (not isinstance(idempotency_key, str) or not idempotency_key.strip()
                or len(idempotency_key) > 200 or any(ord(c) < 32 for c in idempotency_key)):
            raise BridgeError("生图请求必须提供稳定的 Idempotency-Key（最多 200 字符）。")
        request, encoded, digest, references = normalize_request(payload)
        with self.condition:
            if self.closed:
                raise BridgeError("生图服务正在关闭。", 503, "image_service_stopped")
            self.db.execute("BEGIN IMMEDIATE")
            try:
                old = self.db.execute("SELECT * FROM jobs WHERE idempotency_key=?", (idempotency_key,)).fetchone()
                if old is not None:
                    if old["fingerprint"] != digest:
                        raise BridgeError("同一幂等键已用于不同的生图请求。", 409, "image_idempotency_conflict")
                    self.db.execute("COMMIT")
                    return self._snapshot(old)
                if self.active_count >= self.max_pending:
                    raise BridgeError("本机生图队列已满，请稍后再试。", 429, "image_queue_full")
                job_id = uuid.uuid4().hex
                directory = self.directory / job_id
                directory.mkdir()
                for name, data in references:
                    atomic_bytes(directory / name, data)
                now = time.time()
                self.db.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?)",
                                (job_id, idempotency_key, digest, encoded, "queued", "等待 Codex 生图。",
                                 None, None, now, now))
                self.db.execute("COMMIT")
            except Exception:
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise
            self._events[job_id] = threading.Event()
            result = self._snapshot(self._row(job_id))
            self.condition.notify_all()
            return result

    def get(self, job_id):
        with self.condition:
            if self._db_closed:
                raise BridgeError("生图服务已关闭。", 503, "image_service_stopped")
            return self._snapshot(self._row(job_id))

    def cancel(self, job_id):
        with self.condition:
            if self._db_closed:
                raise BridgeError("生图服务已关闭。", 503, "image_service_stopped")
            row = self._row(job_id)
            if row["status"] in ACTIVE:
                self._events.setdefault(job_id, threading.Event()).set()
                if row["status"] == "queued":
                    self._events.pop(job_id, None)
                self.db.execute("UPDATE jobs SET status='cancelled',message=?,error_code=?,updated=? WHERE id=?",
                                (ERRORS["image_cancelled"], "image_cancelled", time.time(), job_id))
                self.condition.notify_all()
            return self._snapshot(self._row(job_id))

    def _progress(self, job_id, stage):
        messages = {"rendering": "Codex 正在生成图片。", "finalizing": "正在保存生成图片。"}
        with self.condition:
            if not self.closed:
                self.db.execute("UPDATE jobs SET message=?,updated=? WHERE id=? AND status='running'",
                                (messages.get(stage, "Codex 正在处理生图任务。") if isinstance(stage, str)
                                 else "Codex 正在处理生图任务。", time.time(), job_id))

    def _execute(self, row, cancelled):
        job_id = row["id"]
        request = json.loads(row["request_json"])
        directory = self.directory / job_id
        try:
            result = self.generator(job_dir=directory, prompt=request["prompt"],
                references=[directory / item["name"] for item in request["references"]],
                aspect_ratio=request["aspectRatio"], transparent=request["transparentBackground"],
                resolution=request.get("resolution", "auto"),
                cancelled=cancelled, on_progress=lambda stage: self._progress(job_id, stage))
            if cancelled.is_set():
                return
            images = result.get("images") if isinstance(result, dict) else None
            if not isinstance(images, list) or len(images) != 1 or not isinstance(images[0], dict):
                raise BridgeError(ERRORS["image_no_result"], 502, "image_no_result")
            data, mime, suffix = validate_image(images[0].get("bytes"), images[0].get("mime"),
                                               max_pixels=8192 * 8192)
            filename = "output" + suffix
            atomic_bytes(directory / filename, data)
            metadata = {}
            for field in ("model", "thread_id", "turn_id", "native_image_id", "generation_id",
                          "saved_path_name", "source", "requested_resolution", "actual_dimensions"):
                value = result.get(field)
                if isinstance(value, str) and len(value) <= 512 and not any(ord(c) < 32 for c in value):
                    if field != "saved_path_name" or not any(c in value for c in ("/", "\\", ":")):
                        metadata[field] = value
            message = "图片生成完成。"
            if request.get("resolution", "auto") != "auto":
                with Image.open(io.BytesIO(data)) as picture:
                    metadata["actual_dimensions"] = f"{picture.width}x{picture.height}"
                metadata["requested_resolution"] = request["resolution"]
                message = (f"图片生成完成。请求目标：{request['resolution'].upper()}；"
                           f"实际尺寸：{metadata['actual_dimensions']}。已保留原图，未缩放；目标分辨率不保证达到。")
            saved = json.dumps({"filename": filename, "mime": mime, "size": len(data),
                                "sha256": hashlib.sha256(data).hexdigest(), "metadata": metadata})
            with self.condition:
                if not self.closed and not cancelled.is_set():
                    self.db.execute("UPDATE jobs SET status='succeeded',message=?,result_json=?,updated=? "
                                    "WHERE id=? AND status='running'",
                                    (message, saved, time.time(), job_id))
        except Exception as error:
            code = error.code if isinstance(error, BridgeError) and error.code in ERRORS else "image_generation_failed"
            with self.condition:
                if not self.closed and not cancelled.is_set():
                    self.db.execute("UPDATE jobs SET status='failed',message=?,error_code=?,updated=? "
                                    "WHERE id=? AND status='running'", (ERRORS[code], code, time.time(), job_id))

    def _work(self):
        try:
            while True:
                with self.condition:
                    row = None
                    while not self.closed and row is None:
                        row = self.db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY rowid LIMIT 1").fetchone()
                        if row is None:
                            self.condition.wait()
                    if self.closed:
                        return
                    self._current = row["id"]
                    cancelled = self._events.setdefault(row["id"], threading.Event())
                    self.db.execute("UPDATE jobs SET status='running',message=?,updated=? WHERE id=?",
                                    ("Codex 正在处理生图任务。", time.time(), row["id"]))
                self._execute(row, cancelled)
                with self.condition:
                    self._events.pop(row["id"], None)
                    self._current = None
                    self.condition.notify_all()
        except Exception:
            with self.condition:
                self.closed = True
                for event in self._events.values():
                    event.set()
                try:
                    self.db.execute("UPDATE jobs SET status='failed',message=?,error_code=?,updated=? "
                                    "WHERE status IN ('queued','running')",
                                    (ERRORS["image_unavailable"], "image_unavailable", time.time()))
                except sqlite3.Error:
                    pass
        finally:
            with self.condition:
                if self.closed and not self._db_closed:
                    self.db.close()
                    self.owner.close()
                    self._db_closed = True

    def close(self, timeout=65.0):
        # Image RPC initialization can take 30s catalog + 15s initialize, then
        # up to 6s process cleanup. Do not exit while its owned child is alive.
        if (not isinstance(timeout, (int, float)) or not math.isfinite(timeout)
                or not 0 <= timeout <= 65):
            raise ValueError("close timeout must be finite and between 0 and 65 seconds")
        with self.condition:
            if not self.closed:
                self.closed = True
                for event in self._events.values():
                    event.set()
                self.db.execute("UPDATE jobs SET status='failed',message=?,error_code=?,updated=? "
                                "WHERE status IN ('queued','running')",
                                (ERRORS["image_service_stopped"], "image_service_stopped", time.time()))
                self.condition.notify_all()
        if threading.current_thread() is not self.worker:
            self.worker.join(timeout=timeout)
            if self.worker.is_alive():
                raise BridgeError("生图任务仍在取消清理中，尚未确认关闭；未终止其他进程。", 503,
                                  "image_shutdown_timeout")
