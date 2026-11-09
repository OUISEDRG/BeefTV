"""Persist Dreama media identity and acknowledgements separately from billing."""
import hashlib
import json
import os
import re
import stat
import time

from bridge_errors import BridgeError
from media_cleanup import AUDIT_TABLE, HISTORY_ONLY, MAY_HAVE_REMOVED, MediaCleanup, UnsafeMedia, receipts
from task_contracts import ContractError, validate_contract


LEDGER_SCHEMA_VERSION = 6


class DeliveryError(BridgeError):
    def __init__(self, code, message, status=409):
        super().__init__(message)
        self.code, self.status = code, status


def identifier(value):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 200 and all(ord(c) >= 32 for c in value)


def media_path(root, key):
    match = re.fullmatch(r'[0-9a-f]{48}\.(mp4|mp3|img)', key) if isinstance(key, str) else None
    if not match:
        raise DeliveryError('media_result_unavailable', '媒体文件名不在允许范围内。')
    directory = {'mp4': 'videos', 'mp3': 'audios', 'img': 'images'}[match[1]]
    return root / directory / key


def verified_hash(path, size):
    try:
        if path.resolve(strict=True) != path:
            raise ValueError('Redirected media path')
        with path.open('rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size != size:
                raise ValueError('Unexpected media size or type')
            return hashlib.file_digest(stream, 'sha256').hexdigest()
    except (OSError, ValueError, RuntimeError) as error:
        raise DeliveryError('media_result_unavailable', '媒体不可读取或大小已变化。') from error


class MediaDelivery:
    def __init__(self, bridge):
        self.bridge = bridge
        self.root = bridge.root.resolve()
        with bridge.db() as db:
            db.execute('BEGIN IMMEDIATE')
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version < 4:
                db.execute('CREATE TABLE delivery_bindings (job_id TEXT PRIMARY KEY REFERENCES jobs(key), task_id TEXT NOT NULL)')
                db.execute('''CREATE TABLE delivery_artifacts (artifact_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL REFERENCES delivery_bindings(job_id), output_index INTEGER NOT NULL,
                    media_key TEXT NOT NULL, mime TEXT NOT NULL, size_bytes INTEGER NOT NULL, sha256 TEXT NOT NULL,
                    UNIQUE(job_id,output_index), UNIQUE(media_key))''')
                db.execute('''CREATE TABLE delivery_acks (artifact_id TEXT PRIMARY KEY REFERENCES delivery_artifacts(artifact_id),
                    idempotency_key TEXT UNIQUE NOT NULL, payload_json TEXT NOT NULL, received_at REAL NOT NULL)''')
                db.execute('''CREATE TABLE delivery_cleanup (artifact_id TEXT PRIMARY KEY REFERENCES delivery_acks(artifact_id),
                    state TEXT NOT NULL, eligible_after REAL)''')
            if version < 5:
                db.execute(AUDIT_TABLE)
            if version < 6:
                db.execute('ALTER TABLE delivery_bindings ADD COLUMN bound_on_admission INTEGER NOT NULL DEFAULT 0')
                db.execute(f'PRAGMA user_version={LEDGER_SCHEMA_VERSION}')
        self.cleanup = MediaCleanup(bridge.db_path, self.root, 'delivery_cleanup', '''
            SELECT f.*, b.task_id, a.idempotency_key, a.payload_json, a.received_at, j.result
            FROM delivery_cleanup k JOIN delivery_acks a USING(artifact_id)
            JOIN delivery_artifacts f USING(artifact_id)
            JOIN delivery_bindings b USING(job_id) JOIN jobs j ON j.key=f.job_id
            WHERE b.bound_on_admission=1 AND NOT EXISTS (
                SELECT 1 FROM delivery_artifacts sibling
                LEFT JOIN delivery_acks sibling_ack ON sibling_ack.artifact_id=sibling.artifact_id
                WHERE sibling.job_id=f.job_id AND sibling_ack.artifact_id IS NULL)''',
            self._cleanup_file, bridge.task_lock)

    @staticmethod
    def _outputs(job):
        if job.get('kind') == 'image':
            return [(item['mediaKey'], item['mimeType'], item['bytes'], item.get('sha256')) for item in job['images']]
        if job.get('kind') == 'audio':
            return [(job['mediaKey'], 'audio/mpeg', job['audio']['bytes'], job['audio'].get('sha256'))]
        return [(job['mediaKey'], 'video/mp4', job['video']['bytes'], job['video'].get('sha256'))]

    def _cleanup_file(self, db, row):
        try:
            payload = json.loads(row['payload_json'])
            validate_contract('media-ack', payload)
            if not identifier(row['idempotency_key']) or not identifier(payload['resourceId']):
                raise UnsafeMedia('saved ACK identity changed')
        except ContractError as error:
            raise UnsafeMedia('saved ACK contract changed') from error
        job = json.loads(row['result'])
        if job['id'] != row['job_id'] or job['status'] != 'completed':
            raise UnsafeMedia('job identity or completion changed')
        outputs = self._outputs(job)
        indexed = self._artifacts(db, row['job_id'])
        if len(outputs) != len(indexed) or any(
                tuple(item[1:5]) != (index, *output[:3]) or (output[3] is not None and item[5] != output[3])
                for index, (item, output) in enumerate(zip(indexed, outputs))):
            raise UnsafeMedia('job output index changed')
        try:
            return media_path(self.root, row['media_key'])
        except DeliveryError as error:
            raise UnsafeMedia('invalid owned file name') from error

    def receipts(self, job_id):
        with self.bridge.db() as db:
            return receipts(db, '''SELECT f.artifact_id,a.payload_json,a.received_at,k.state
                FROM delivery_artifacts f JOIN delivery_acks a USING(artifact_id)
                JOIN delivery_cleanup k USING(artifact_id) WHERE f.job_id=? ORDER BY f.output_index''', job_id)

    @staticmethod
    def history_only(delivery_receipts, *, bytes_missing=False):
        states = MAY_HAVE_REMOVED if bytes_missing else HISTORY_ONLY
        return any(item['cleanupState'] in states for item in delivery_receipts)

    def bind(self, db, job_id, task_id, *, on_admission=False):
        if task_id is None:
            return
        old = db.execute('SELECT task_id FROM delivery_bindings WHERE job_id=?', (job_id,)).fetchone()
        if old is None:
            db.execute('INSERT INTO delivery_bindings (job_id,task_id,bound_on_admission) VALUES (?,?,?)',
                       (job_id, task_id, int(on_admission)))
        elif old[0] != task_id:
            raise DeliveryError('image_task_identity_conflict', '此作业已关联另一个 BeefTV 任务。')

    @staticmethod
    def _descriptor(task_id, row):
        artifact_id, index, _key, mime, size, sha = row
        return {'schemaVersion': 1, 'taskId': task_id, 'artifactId': artifact_id, 'outputIndex': index,
                'mimeType': mime, 'sizeBytes': size, 'sha256': sha}

    @staticmethod
    def _artifacts(db, job_id):
        return db.execute('''SELECT artifact_id,output_index,media_key,mime,size_bytes,sha256
            FROM delivery_artifacts WHERE job_id=? ORDER BY output_index''', (job_id,)).fetchall()

    def publish(self, job):
        job_id = job['id']
        with self.bridge.db() as db:
            binding = db.execute('SELECT task_id FROM delivery_bindings WHERE job_id=?', (job_id,)).fetchone()
            if binding is None:
                return []
            old = self._artifacts(db, job_id)
        outputs = self._outputs(job)
        if old:
            if [(row[2], row[3], row[4]) for row in old] != [item[:3] for item in outputs]:
                raise DeliveryError('media_artifact_changed', '已发布的原产物列表发生变化。')
            return [self._descriptor(binding[0], row) for row in old]

        rows = []
        for index, (key, mime, size, expected) in enumerate(outputs):
            digest = verified_hash(media_path(self.root, key), size)
            if expected is not None and expected != digest:
                raise DeliveryError('media_artifact_changed', '已完成媒体的 SHA256 校验失败。')
            row = (f'dreama-{hashlib.sha256(job_id.encode()).hexdigest()}-{index}', index, key, mime, size, digest)
            validate_contract('media-artifact', self._descriptor(binding[0], row))
            rows.append(row)
        with self.bridge.db() as db:
            db.execute('BEGIN IMMEDIATE')
            prior = self._artifacts(db, job_id)
            if prior and prior != rows:
                raise DeliveryError('media_artifact_changed', '已发布的原产物列表发生变化。')
            if not prior:
                db.executemany('''INSERT INTO delivery_artifacts
                    (artifact_id,output_index,media_key,mime,size_bytes,sha256,job_id) VALUES (?,?,?,?,?,?,?)''',
                               [(*row, job_id) for row in rows])
        return [self._descriptor(binding[0], row) for row in rows]

    def acknowledge(self, job_id, payload, key):
        try:
            validate_contract('media-ack', payload)
            if (not identifier(key)
                    or any(type(payload[field]) is not int for field in ('schemaVersion', 'outputIndex', 'sizeBytes'))
                    or any(not identifier(payload[field]) for field in ('taskId', 'artifactId', 'resourceId'))):
                raise ContractError('Invalid ACK identity')
        except ContractError as error:
            raise DeliveryError('invalid_media_ack', '媒体交付 ACK 或幂等键无效。', 400) from error
        encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'))
        artifact_id = payload['artifactId']
        receipt = {'acknowledged': True, 'artifactId': artifact_id, 'cleanupState': 'held'}
        with self.bridge.task_lock(job_id), self.bridge.db() as db:
            if db.execute('SELECT 1 FROM jobs WHERE key=?', (job_id,)).fetchone() is None:
                raise DeliveryError('media_job_not_found', '未找到对应媒体任务。', 404)
            binding = db.execute('SELECT task_id FROM delivery_bindings WHERE job_id=?', (job_id,)).fetchone()
            row = next((item for item in self._artifacts(db, job_id) if item[0] == artifact_id), None)
            if binding is None or row is None:
                raise DeliveryError('media_ack_mismatch', '媒体交付 ACK 与原任务或产物不匹配。')
            try:
                validate_contract('media-ack', payload, artifact=self._descriptor(binding[0], row))
            except ContractError as error:
                raise DeliveryError('media_ack_mismatch', '媒体交付 ACK 与原任务或产物不匹配。') from error
            if self._prior_ack(db, artifact_id, key, encoded):
                return receipt
            if verified_hash(media_path(self.root, row[2]), row[4]) != row[5]:
                raise DeliveryError('media_result_unavailable', '媒体 SHA256 已变化。')
            db.execute('BEGIN IMMEDIATE')
            job = db.execute('SELECT result FROM jobs WHERE key=?', (job_id,)).fetchone()
            if job is None or json.loads(job[0]).get('status') != 'completed':
                raise DeliveryError('media_ack_mismatch', '任务尚未完成。')
            if not self._prior_ack(db, artifact_id, key, encoded):
                db.execute('INSERT INTO delivery_acks VALUES (?,?,?,?)', (artifact_id, key, encoded, time.time()))
                db.execute("INSERT INTO delivery_cleanup VALUES (?, 'held', NULL)", (artifact_id,))
        return receipt

    @staticmethod
    def _prior_ack(db, artifact_id, key, encoded):
        saved = db.execute('''SELECT artifact_id,idempotency_key,payload_json FROM delivery_acks
            WHERE artifact_id=? OR idempotency_key=?''', (artifact_id, key)).fetchall()
        if saved and saved != [(artifact_id, key, encoded)]:
            raise DeliveryError('media_ack_conflict', '此媒体或 ACK 幂等键已用于不同交付。')
        return bool(saved)
