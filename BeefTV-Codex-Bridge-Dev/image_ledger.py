"""Versioned SQLite schema and immutable identity for Codex image output."""
from datetime import datetime
import json
import re

from completion_bridge import BridgeError
from media_cleanup import AUDIT_TABLE


SCHEMA_VERSION = 5
OUTPUT_NAMES = {"image/png": "output.png", "image/jpeg": "output.jpg", "image/webp": "output.webp"}
ACK_FIELDS = {"schemaVersion", "taskId", "artifactId", "resourceId", "outputIndex", "sha256", "sizeBytes", "acknowledgedAt"}


def valid_identifier(value):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 200 and all(ord(char) >= 32 for char in value)


def validate_ack(payload, key):
    if (not valid_identifier(key) or not isinstance(payload, dict) or payload.keys() != ACK_FIELDS
            or type(payload["schemaVersion"]) is not int or payload["schemaVersion"] != 1
            or any(not valid_identifier(payload[field]) for field in ("taskId", "artifactId", "resourceId"))
            or type(payload["outputIndex"]) is not int or payload["outputIndex"] < 0
            or type(payload["sizeBytes"]) is not int or payload["sizeBytes"] <= 0
            or not isinstance(payload["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", payload["sha256"])):
        raise BridgeError("媒体交付 ACK 或幂等键无效。", 400, "invalid_media_ack")
    try:
        stamp = payload["acknowledgedAt"]
        if (not isinstance(stamp, str)
                or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T[0-9:.]+(?:Z|[+-]\d{2}:\d{2})", stamp)
                or datetime.fromisoformat(stamp.replace("Z", "+00:00")).utcoffset() is None):
            raise ValueError("Missing ACK offset")
    except ValueError as error:
        raise BridgeError("媒体交付 ACK 必须包含带时区的时间。", 400, "invalid_media_ack") from error
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def record_artifact(db, job_id, image):
    db.execute("""INSERT INTO image_artifacts (id, job_id, output_index, filename, mime, sha256, size_bytes)
                  VALUES (?, ?, 0, ?, ?, ?, ?)""",
               (f"codex-image-{job_id}-0", job_id, image["filename"], image["mime"], image["sha256"], image["size"]))


def record_native_receipt(db, job_id, thread_id, turn_id):
    if not valid_identifier(thread_id) or (turn_id is not None and not valid_identifier(turn_id)):
        raise BridgeError("原生生图回执标识无效。", 502, "image_submission_unknown")
    changed = db.execute("""INSERT INTO image_native_receipts (job_id, thread_id, turn_id) VALUES (?,?,?)
        ON CONFLICT(job_id) DO UPDATE SET turn_id=coalesce(image_native_receipts.turn_id, excluded.turn_id)
        WHERE image_native_receipts.thread_id=excluded.thread_id AND
        (image_native_receipts.turn_id IS NULL OR excluded.turn_id IS NULL OR image_native_receipts.turn_id=excluded.turn_id)""",
                         (job_id, thread_id, turn_id)).rowcount
    if changed != 1:
        raise BridgeError("原生生图回执与已保存的任务不匹配。", 409, "image_submission_unknown")


def _legacy_image(result_json):
    try:
        image = json.loads(result_json)
    except (TypeError, ValueError):
        return None
    if not isinstance(image, dict):
        return None
    mime, filename, size, sha = (image.get(key) for key in ("mime", "filename", "size", "sha256"))
    if (not isinstance(mime, str) or not isinstance(filename, str) or OUTPUT_NAMES.get(mime) != filename
            or type(size) is not int or size <= 0 or not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha)):
        return None
    return image


def migrate(db):
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version == SCHEMA_VERSION:
        return
    if version not in (0, 1, 2, 3, 4):
        raise RuntimeError("Unsupported Codex image ledger schema")
    db.execute("BEGIN IMMEDIATE")
    try:
        if version == 0:
            db.execute("""CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE NOT NULL,
                fingerprint TEXT NOT NULL, request_json TEXT NOT NULL,
                status TEXT NOT NULL, message TEXT NOT NULL, error_code TEXT,
                result_json TEXT, created REAL NOT NULL, updated REAL NOT NULL)""")
            db.execute("""CREATE TABLE image_artifacts (
                id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), output_index INTEGER NOT NULL,
                filename TEXT NOT NULL, mime TEXT NOT NULL, sha256 TEXT NOT NULL, size_bytes INTEGER NOT NULL,
                UNIQUE (job_id, output_index))""")
            db.execute("CREATE INDEX image_artifacts_sha256 ON image_artifacts(sha256)")
            for job_id, result in db.execute("SELECT id, result_json FROM jobs WHERE status='succeeded'"):
                image = _legacy_image(result)
                if image is not None:
                    record_artifact(db, job_id, image)
        if version < 2:
            db.execute("""CREATE TABLE image_job_bindings (
                job_id TEXT PRIMARY KEY REFERENCES jobs(id), task_id TEXT NOT NULL)""")
            db.execute("""CREATE TABLE image_delivery_acks (
                artifact_id TEXT PRIMARY KEY REFERENCES image_artifacts(id),
                idempotency_key TEXT UNIQUE NOT NULL, payload_json TEXT NOT NULL, received_at REAL NOT NULL)""")
            db.execute("""CREATE TABLE image_cleanup_queue (
                artifact_id TEXT PRIMARY KEY REFERENCES image_delivery_acks(artifact_id),
                state TEXT NOT NULL, eligible_after REAL)""")
        if version < 3:
            db.execute("""CREATE TABLE image_native_receipts (
                job_id TEXT PRIMARY KEY REFERENCES jobs(id), thread_id TEXT NOT NULL, turn_id TEXT)""")
        if version < 4:
            db.execute(AUDIT_TABLE)
        db.execute("ALTER TABLE image_job_bindings ADD COLUMN bound_on_admission INTEGER NOT NULL DEFAULT 0")
        db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        db.execute("COMMIT")
    except Exception:
        db.execute("ROLLBACK")
        raise
