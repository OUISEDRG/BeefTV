"""Independent task contract ledger in this Bridge's SQLite file; short transactions only."""
from contextlib import contextmanager
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import time
import uuid

from budget_policy import BudgetError, budget_window, exact_total, point_text, points, policy_hash
from task_contracts import identity_from_job, legacy_state, transition_allowed, validate_contract, ContractError

class RepositoryError(ValueError):
    pass


@contextmanager
def own_task_ledger(root, *, exclusive):
    """Exclude offline reconciliation while the Bridge's workers own its ledger."""
    owner = sqlite3.connect(Path(root) / 'task-ledger-owner.sqlite3', timeout=0,
                            isolation_level=None, check_same_thread=False)
    try:
        try:
            if owner.execute('PRAGMA journal_mode').fetchone()[0] != 'delete':
                raise RepositoryError('Task ledger ownership requires SQLite rollback journal mode')
            owner.execute('BEGIN EXCLUSIVE' if exclusive else 'BEGIN')
            if not exclusive:
                owner.execute('SELECT name FROM sqlite_schema').fetchall()
        except sqlite3.OperationalError as error:
            raise RepositoryError('Task ledger is owned by a running Bridge or reconciliation') from error
        yield
    finally:
        owner.close()

DDL = (
    'CREATE TABLE IF NOT EXISTS p1_schema(version INTEGER NOT NULL)',
    '''CREATE TABLE IF NOT EXISTS p1_tasks(
        job_id TEXT PRIMARY KEY,operation_id TEXT NOT NULL UNIQUE,fingerprint TEXT NOT NULL,
        identity_json TEXT NOT NULL,state TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 0,
        historical INTEGER NOT NULL DEFAULT 0)''',
    '''CREATE TABLE IF NOT EXISTS p1_events(
        job_id TEXT NOT NULL,revision INTEGER NOT NULL,state TEXT NOT NULL,reason TEXT NOT NULL,
        created_at REAL NOT NULL,PRIMARY KEY(job_id,revision))''',
    '''CREATE TABLE IF NOT EXISTS p1_legacy_records(
        table_name TEXT NOT NULL,row_key TEXT NOT NULL,raw_json TEXT NOT NULL,sha256 TEXT NOT NULL,
        PRIMARY KEY(table_name,row_key))''',
    '''CREATE TABLE IF NOT EXISTS p1_submissions(
        job_id TEXT PRIMARY KEY,intent_id TEXT NOT NULL UNIQUE,kind TEXT NOT NULL,
        estimated_points TEXT,quoted_points TEXT,reserved_points TEXT,actual_points TEXT,
        billing_source TEXT NOT NULL,settlement_status TEXT NOT NULL,policy_json TEXT,
        policy_hash TEXT,window_start REAL,window_end REAL,intent_at REAL NOT NULL,
        upstream_id_json TEXT,evidence TEXT)''',
)

RECONCILIATION_DDL = '''CREATE TABLE p6_billing_evidence(
    job_id TEXT PRIMARY KEY REFERENCES p1_submissions(job_id),
    reference TEXT NOT NULL UNIQUE, proof_json TEXT NOT NULL,
    anomalies_json TEXT NOT NULL, applied_at REAL NOT NULL)'''

class TaskRepository:
    def __init__(self, path, *, timeout=5):
        self.path, self.timeout = Path(path), timeout
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate()
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=self.timeout)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA busy_timeout=' + str(int(self.timeout*1000)))
        try:
            with db: yield db
        finally: db.close()

    def migrate(self):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            present = {r[0] for r in db.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
            if 'p1_schema' in present:
                versions = [r[0] for r in db.execute('SELECT version FROM p1_schema')]
                if versions == [1]:
                    db.execute(RECONCILIATION_DDL)
                    db.execute('UPDATE p1_schema SET version=2')
                elif versions != [2] or 'p6_billing_evidence' not in present:
                    raise RepositoryError('Unsupported task ledger version')
                return
            for statement in DDL: db.execute(statement)
            if 'jobs' in present:
                columns = {r[1] for r in db.execute('PRAGMA table_info(jobs)')}
                if {'key', 'digest', 'result'} <= columns:
                    if db.execute('PRAGMA user_version').fetchone()[0]>3:
                        raise RepositoryError('Newer Dreama database; refusing a downgrade')
                    for row in db.execute('SELECT key,digest,result FROM jobs').fetchall():
                        job = json.loads(row['result'])
                        self._archive(db, 'jobs', row['key'], json.dumps(list(row), ensure_ascii=False))
                        self._register(db, job, row['digest'], historical=True)
                        paid_status=job.get('status') in ('submitting','submission_unknown','queued','processing','download_failed','completed')
                        paid_evidence=(job.get('taskId') is not None or job.get('approvedAt') is not None
                                       or job.get('approvedPoints') is not None or job.get('generationSubmitted') is True)
                        uncertain=(job.get('status') in ('unknown','failed','cancelled') and job.get('generationSubmitted') is not False)
                        if paid_status or paid_evidence or uncertain:
                            quoted=job.get('approvedPoints')
                            amount=quoted if quoted is not None else job.get('pointsEstimate')
                            amount=point_text(amount) if amount is not None else None
                            estimated=point_text(job['pointsEstimate']) if job.get('pointsEstimate') is not None else None
                            estimated_source=job.get('kind') in ('audio','enhancement')
                            quoted=point_text(quoted) if quoted is not None and not estimated_source else None
                            stamp=job.get('submittedAt') or job.get('submissionIntentAt') or job.get('approvedAt')
                            time_known=type(stamp) in (int,float) and math.isfinite(stamp) and stamp>0
                            metadata=json.dumps({'legacySubmissionTimeKnown':time_known,'legacyStatus':job.get('status')})
                            db.execute('INSERT INTO p1_submissions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                                (job['id'],'legacy:'+job['id'],job.get('kind','video'),estimated,quoted,amount,None,
                                 'estimated' if estimated_source else 'unknown','unknown',metadata,None,None,None,
                                 stamp if time_known else 0,None,'legacy-unsettled; terminal status is not settlement evidence'))
                    db.execute('PRAGMA user_version=3')
                elif {'id','idempotency_key','fingerprint','request_json','status'} <= columns:
                    # Offline migration also accepts a Codex-owned copy. Runtime services
                    # still receive only their own path; no cross-Bridge connection exists.
                    for row in db.execute('SELECT * FROM jobs').fetchall():
                        self._archive(db,'jobs',row['id'],json.dumps(list(row),ensure_ascii=False))
                        job={'id':row['id'],'clientOperationId':row['idempotency_key'] or row['id'],
                             'status':'completed' if row['status']=='succeeded' else row['status'],
                             'kind':'image','requestDigest':row['fingerprint']}
                        self._register(db,job,row['fingerprint'],historical=True)
                else:
                    raise RepositoryError('Unsupported legacy task layout; no partial migration')
            if 'preparation_requests' in present:
                for row in db.execute('SELECT * FROM preparation_requests').fetchall():
                    self._archive(db, 'preparation_requests', row['key'], json.dumps(list(row), ensure_ascii=False))
            db.execute(RECONCILIATION_DDL)
            db.execute('INSERT INTO p1_schema VALUES (2)')

    @staticmethod
    def _archive(db, table, key, raw):
        db.execute('INSERT INTO p1_legacy_records VALUES (?,?,?,?)', (table,key,raw,hashlib.sha256(raw.encode()).hexdigest()))

    def _register(self, db, job, fingerprint, *, historical=False):
        identity = identity_from_job(job)
        row = db.execute('SELECT * FROM p1_tasks WHERE job_id=?', (job['id'],)).fetchone()
        if row:
            if row['fingerprint'] != fingerprint or row['operation_id'] != identity['clientOperationId']:
                raise RepositoryError('Idempotency key reused with different input')
            return
        try:
            state = legacy_state(job['status'])
            db.execute('INSERT INTO p1_tasks VALUES (?,?,?,?,?,0,?)',
                (job['id'], identity['clientOperationId'], fingerprint, json.dumps(identity), state, int(historical)))
            db.execute('INSERT INTO p1_events VALUES (?,0,?,?,?)', (job['id'],state,'legacy-import' if historical else 'received',time.time()))
        except sqlite3.IntegrityError:
            raise RepositoryError('Client operation identity already belongs to another task') from None

    def register(self, job):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._register(db, job, job['requestDigest'])

    def _transition(self, db, key, target, *, expected=None, reason='state-change'):
        row = db.execute('SELECT state,revision FROM p1_tasks WHERE job_id=?', (key,)).fetchone()
        if not row: raise RepositoryError('Task identity missing')
        if expected is not None and row['revision'] != expected: raise RepositoryError('Task revision conflict')
        if not transition_allowed(row['state'], target): raise RepositoryError('Illegal task state transition')
        if row['state'] == target: return
        revision = row['revision'] + 1
        changed = db.execute('UPDATE p1_tasks SET state=?,revision=? WHERE job_id=? AND revision=?', (target,revision,key,row['revision'])).rowcount
        if changed != 1: raise RepositoryError('Task revision conflict')
        db.execute('INSERT INTO p1_events VALUES (?,?,?,?,?)', (key,revision,target,reason,time.time()))

    def transition(self, key, expected_revision, target):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._transition(db, key, target, expected=expected_revision)

    def sync_job(self, db, key, job):
        factory = db.row_factory
        db.row_factory = sqlite3.Row
        try:
            self._sync_job(db,key,job)
        finally:
            db.row_factory = factory

    def _sync_job(self, db, key, job):
        fingerprint = db.execute('SELECT digest FROM jobs WHERE key=?', (key,)).fetchone()[0]
        self._register(db, job, fingerprint)
        target = 'submission_intent' if job['status']=='submitting' else legacy_state(job['status'])
        if job.get('stage') == 'budget_blocked': target='blocked'
        self._transition(db, key, target, reason='provider-projection')
        row = db.execute('SELECT identity_json FROM p1_tasks WHERE job_id=?',(key,)).fetchone()
        identity = json.loads(row[0])
        if job.get('taskId') is not None:
            if identity['upstreamTaskId'] is not None and identity['upstreamTaskId'] != job['taskId']:
                raise RepositoryError('Conflicting upstream task identity')
            identity['upstreamTaskId'] = job['taskId']
        if job.get('mediaKeys'): identity['outputCount']=len(job['mediaKeys'])
        validate_contract('task-identity',identity)
        db.execute('UPDATE p1_tasks SET identity_json=? WHERE job_id=?', (json.dumps(identity),key))

    def get(self, key):
        with self.connect() as db:
            row = db.execute('SELECT * FROM p1_tasks WHERE job_id=?', (key,)).fetchone()
            if not row: return None
            result = dict(row); result['identity']=json.loads(result.pop('identity_json'))
            bill = db.execute('SELECT * FROM p1_submissions WHERE job_id=?',(key,)).fetchone()
            result['billing'] = self._billing(bill) if bill else None
            return result

    @staticmethod
    def _billing(row):
        return {'schemaVersion':1,'intentId':row['intent_id'],'estimatedPoints':row['estimated_points'],
                'quotedPoints':row['quoted_points'],'reservedPoints':row['reserved_points'],
                'actualPoints':row['actual_points'],'billingSource':row['billing_source'],
                'settlementStatus':row['settlement_status']}

    def reserve_intent(self, job, quote, policy, *, now=None, billing_source='cli_quote'):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            now = time.time() if now is None else now
            row = db.execute('SELECT * FROM p1_tasks WHERE job_id=?',(job['id'],)).fetchone()
            if not row or row['historical'] or row['fingerprint'] != job['requestDigest'] or row['state'] not in ('prepared','blocked'):
                raise RepositoryError('Task cannot enter a new paid submission')
            if db.execute('SELECT 1 FROM p1_submissions WHERE job_id=?',(job['id'],)).fetchone():
                raise RepositoryError('A durable intent already exists; reconcile only')
            daily, limit, start, end = budget_window(policy, job.get('kind','video'), now)
            quoted = points(quote)
            if quoted > limit: raise BudgetError('单次预算不足，未提交收费任务。')
            charges = self._budget_charges(db, start, end)
            if exact_total([*charges, quoted]) > daily: raise BudgetError('每日预算不足，未提交收费任务。')
            self._transition(db, job['id'], 'validating', reason='budget-validation')
            intent_id = uuid.uuid4().hex
            db.execute('INSERT INTO p1_submissions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (job['id'],intent_id,job.get('kind','video'),point_text(job.get('estimatedPoints',job['pointsEstimate'])),None if billing_source=='estimated' else point_text(quoted),point_text(quoted),None,
                 billing_source,'reserved',json.dumps(policy,sort_keys=True),policy_hash(policy),start,end,now,None,None))
            self._transition(db, job['id'], 'submission_intent', reason='durable-before-upstream')
            exists = db.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='jobs'").fetchone()
            if exists:
                current = db.execute('SELECT result FROM jobs WHERE key=?',(job['id'],)).fetchone()
                if not current or json.loads(current[0])['status'] != 'prepared': raise RepositoryError('Provider task no longer prepared')
                columns = {r[1] for r in db.execute('PRAGMA table_info(preparation_requests)')}
                if 'cancel_requested' in columns:
                    cancel = db.execute('SELECT cancel_requested FROM preparation_requests WHERE key=?',(job['id'],)).fetchone()
                    if cancel and cancel[0]: raise RepositoryError('Historical cancellation prevents submission')
                job.update(status='submitting',stage='submit',generationSubmitted=None,
                           submissionIntentId=intent_id,quotedPoints=None if billing_source=='estimated' else point_text(quoted),reservedPoints=point_text(quoted))
                db.execute('UPDATE jobs SET result=? WHERE key=?',(json.dumps(job,ensure_ascii=False),job['id']))
            return self._billing(db.execute('SELECT * FROM p1_submissions WHERE job_id=?',(job['id'],)).fetchone())

    @staticmethod
    def _budget_charges(db, start, end):
        charges = []
        for bill in db.execute("SELECT * FROM p1_submissions WHERE settlement_status!='released'"):
            metadata = json.loads(bill['policy_json']) if bill['policy_json'] else {}
            if metadata.get('legacySubmissionTimeKnown') is False:
                raise BudgetError('存在提交时间不明的历史收费意图，禁止新的收费提交。')
            current_day = start <= bill['intent_at'] < end
            unresolved = bill['settlement_status'] in ('reserved', 'unknown') and bill['intent_at'] < end
            if not (current_day or unresolved):
                continue
            amount = bill['actual_points'] if bill['actual_points'] is not None else bill['reserved_points']
            if amount is None:
                raise BudgetError('存在金额不明的收费意图，禁止新的收费提交。')
            charges.append(amount)
        return charges

    @staticmethod
    def _undispatched_intent(db, job):
        row = db.execute('''SELECT b.*,t.state,t.historical,t.identity_json,j.result
                FROM p1_submissions b JOIN p1_tasks t USING(job_id) JOIN jobs j ON j.key=b.job_id
                WHERE b.job_id=?''', (job['id'],)).fetchone()
        if (not row or row['historical'] or row['state'] != 'submission_intent' or row['settlement_status'] != 'reserved'
                or row['intent_id'] != job['submissionIntentId'] or row['upstream_id_json'] is not None
                or row['reserved_points'] != job['reservedPoints']
                or json.loads(row['identity_json'])['upstreamTaskId'] is not None
                or json.loads(row['result']) != job):
            raise RepositoryError('任务或收费意图已变化，禁止提交。')
        return row

    def _release_unsent(self, db, row, job, stage, reason, message):
        self._transition(db, job['id'], 'failed', reason=reason)
        evidence = {'source': 'local_before_dispatch', 'intentId': row['intent_id'], 'accepted': False, 'charged': False}
        db.execute("UPDATE p1_submissions SET settlement_status='released',evidence=? WHERE job_id=?",
                   (json.dumps(evidence, sort_keys=True), job['id']))
        failed = {**job, 'status':'failed', 'stage':stage, 'generationSubmitted':False, 'error':message}
        db.execute('UPDATE jobs SET result=? WHERE key=?', (json.dumps(failed, ensure_ascii=False), job['id']))

    def release_before_dispatch(self, job):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self._undispatched_intent(db, job)
            self._release_unsent(db, row, job, 'preflight_failed', 'preflight-failed-before-dispatch',
                                 '提交前检查失败，未收费。')

    def recheck_before_dispatch(self, job, load_policy, *, now=None):
        denial = None
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self._undispatched_intent(db, job)
            try:
                policy = load_policy()
                daily, limit, start, end = budget_window(policy, row['kind'], time.time() if now is None else now)
                if (row['policy_hash'] != policy_hash(policy) or (row['window_start'], row['window_end']) != (start, end)
                        or points(row['reserved_points']) > limit or exact_total(self._budget_charges(db, start, end)) > daily):
                    raise BudgetError('收费前预算或日界线已变化。')
            except BudgetError as error:
                denial = str(error)
                self._release_unsent(db, row, job, 'budget_revoked', 'budget-changed-before-dispatch', denial)
        if denial is not None:
            raise BudgetError(denial)

    def record_receipt(self, key, upstream_id):
        if upstream_id is None: raise RepositoryError('A confirmed non-null upstream task identity is required')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT identity_json,state FROM p1_tasks WHERE job_id=?',(key,)).fetchone()
            if not row: raise RepositoryError('Task identity missing')
            identity=json.loads(row['identity_json'])
            if identity['upstreamTaskId'] is not None and identity['upstreamTaskId'] != upstream_id:
                raise RepositoryError('Conflicting upstream task identity')
            identity['upstreamTaskId']=upstream_id
            try: validate_contract('task-identity',identity)
            except ContractError: raise RepositoryError('Invalid upstream task identity') from None
            if row['state'] in ('submission_intent','submission_unknown','recovery_required'):
                self._transition(db,key,'submitted',reason='upstream-receipt')
            elif identity['upstreamTaskId'] is None or row['state'] not in ('submitted','queued','processing','artifact_ready','delivery_pending','completed'):
                raise RepositoryError('Task cannot accept a receipt in this state')
            db.execute('UPDATE p1_tasks SET identity_json=? WHERE job_id=?',(json.dumps(identity),key))
            db.execute('UPDATE p1_submissions SET upstream_id_json=? WHERE job_id=?',(json.dumps(upstream_id),key))

    def mark_unknown(self, key):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._transition(db,key,'submission_unknown',reason='uncertain-receipt')
            db.execute("UPDATE p1_submissions SET settlement_status='unknown' WHERE job_id=? AND settlement_status='reserved'",(key,))

    def apply_billing_evidence(self, proof):
        """Apply local operator-verified evidence for one original submission."""
        try:
            validate_contract('billing-evidence', proof)
            if (type(proof['schemaVersion']) is not int or type(proof['upstreamTaskId']) not in (str, int, type(None))
                    or proof['reference'] != proof['reference'].strip()):
                raise ContractError('Invalid billing evidence identity type')
            amount = point_text(proof['actualPoints']) if 'actualPoints' in proof else None
        except (ContractError, BudgetError) as error:
            raise RepositoryError(str(error)) from None
        key, reference = proof['bridgeJobId'], proof['reference']
        encoded = json.dumps(proof, sort_keys=True, separators=(',', ':'))
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('''SELECT b.*,t.state,t.identity_json FROM p1_submissions b
                JOIN p1_tasks t USING(job_id) WHERE b.job_id=?''', (key,)).fetchone()
            if not row or row['intent_id'] != proof['intentId']:
                raise RepositoryError('Billing evidence does not match the original intent')
            old = db.execute('SELECT * FROM p6_billing_evidence WHERE job_id=? OR reference=?',
                             (key, reference)).fetchall()
            if old:
                if len(old) != 1 or old[0]['job_id'] != key or old[0]['proof_json'] != encoded:
                    raise RepositoryError('Conflicting billing evidence')
                return {'billing': self._billing(row), 'anomalies': json.loads(old[0]['anomalies_json'])}
            saved_id = json.loads(row['identity_json'])['upstreamTaskId']
            upstream_id = proof['upstreamTaskId']
            if (type(saved_id) is not type(upstream_id) or saved_id != upstream_id
                    or (row['upstream_id_json'] is not None and json.loads(row['upstream_id_json']) != upstream_id)):
                raise RepositoryError('Billing evidence does not match the original upstream receipt')
            if row['settlement_status'] not in ('reserved', 'unknown'):
                raise RepositoryError('Original billing intent has already been closed')
            if amount is None:
                self._record_confirmed_rejection(db, row, key, encoded)
                anomalies = []
            else:
                expected = row['quoted_points'] if row['quoted_points'] is not None else row['reserved_points']
                variance = expected is None or points(expected) != points(amount)
                state = 'variance' if variance else 'settled'
                db.execute("""UPDATE p1_submissions SET actual_points=?,billing_source='upstream_settlement',
                    settlement_status=?,evidence=? WHERE job_id=?""", (amount, state, encoded, key))
                anomalies = self._settlement_anomalies(db, row, amount, variance)
            db.execute('INSERT INTO p6_billing_evidence VALUES (?,?,?,?,?)',
                       (key, reference, encoded, json.dumps(anomalies), time.time()))
            updated = db.execute('SELECT * FROM p1_submissions WHERE job_id=?', (key,)).fetchone()
            return {'billing': self._billing(updated), 'anomalies': anomalies}

    def _record_confirmed_rejection(self, db, row, key, encoded):
        if row['state'] not in ('submission_unknown', 'recovery_required', 'failed') or row['actual_points'] is not None:
            raise RepositoryError('No uncertain submission eligible for confirmed rejection')
        self._transition(db, key, 'failed', reason='upstream-rejected-without-charge')
        db.execute("UPDATE p1_submissions SET settlement_status='released',evidence=? WHERE job_id=?", (encoded,key))
        if db.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='jobs'").fetchone():
            stored = db.execute('SELECT result FROM jobs WHERE key=?', (key,)).fetchone()
            if stored:
                job = json.loads(stored[0])
                job.update(status='failed', stage='confirmed_no_charge', generationSubmitted=False,
                           error='已核对原提交未被接受，未收费。')
                db.execute('UPDATE jobs SET result=? WHERE key=?', (json.dumps(job,ensure_ascii=False),key))

    def _settlement_anomalies(self, db, row, amount, variance):
        anomalies = ['charge_variance'] if variance else []
        try:
            policy = json.loads(row['policy_json']) if row['policy_json'] else None
            daily, limit, start, end = budget_window(policy, row['kind'], row['intent_at'])
            if (start, end) != (row['window_start'], row['window_end']):
                raise BudgetError('Original budget window unavailable')
        except ValueError:
            return [*anomalies, 'budget_policy_unverified']
        if points(amount) > limit:
            anomalies.append('per_operation_limit_exceeded')
        try:
            if exact_total(self._budget_charges(db, start, end)) > daily:
                anomalies.append('daily_limit_exceeded')
        except (ValueError, TypeError):
            anomalies.append('budget_history_unverified')
        return anomalies
