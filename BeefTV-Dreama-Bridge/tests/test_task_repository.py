import datetime as dt
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor

try:
    from task_repository import TaskRepository, RepositoryError
    from budget_policy import BudgetError
except ImportError:
    TaskRepository = None

NOW = dt.datetime(2026, 11, 7, 4, tzinfo=dt.timezone.utc).timestamp()

def policy(daily='2.50', limit='1.25'):
    return {'schemaVersion': 1, 'enabled': True, 'dailyLimitPoints': daily,
            'perOperationLimits': {k: limit for k in ('video', 'image', 'extension', 'enhancement', 'audio')},
            'timezone': 'UTC', 'dayStart': '00:00'}

def job(key='j', **changes):
    return {'id': key, 'clientOperationId': 'op-' + key, 'status': 'prepared', 'kind': 'video',
            'pointsEstimate': 1.25, 'requestDigest': 'digest-' + key, **changes}

class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(TaskRepository, 'P1 repository is not implemented')
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'jobs.sqlite3'
        self.repo = TaskRepository(self.path)

    def test_idempotency_and_operation_identity_survive_reopen(self):
        self.repo.register(job())
        self.repo.register(job())
        with self.assertRaises(RepositoryError): self.repo.register(job(requestDigest='different'))
        with self.assertRaises(RepositoryError): self.repo.register(job('other', clientOperationId='op-j'))
        self.assertEqual(TaskRepository(self.path).get('j')['identity']['bridgeJobId'], 'j')

    def test_state_transition_is_compare_and_swap_and_replay_idempotent(self):
        self.repo.register(job())
        self.repo.transition('j', 0, 'validating')
        with self.assertRaises(RepositoryError): self.repo.transition('j', 0, 'failed')
        self.repo.transition('j', 1, 'validating')
        self.assertEqual(self.repo.get('j')['revision'], 1)

    def test_receipt_identity_is_idempotent_and_rejects_conflicting_upstream(self):
        self.repo.register(job()); self.repo.reserve_intent(job(),1.25,policy(),now=NOW)
        self.repo.record_receipt('j',700)
        self.assertEqual(self.repo.get('j')['identity']['upstreamTaskId'],700)
        revision=self.repo.get('j')['revision']; self.repo.transition('j',revision,'queued')
        self.repo.record_receipt('j',700)
        self.assertEqual(self.repo.get('j')['state'],'queued')
        with self.assertRaises(RepositoryError): self.repo.record_receipt('j',701)

    def test_missing_receipt_id_preserves_intent_state_and_revision(self):
        self.repo.register(job()); self.repo.reserve_intent(job(),1.25,policy(),now=NOW)
        before=self.repo.get('j')
        with self.assertRaises(RepositoryError): self.repo.record_receipt('j',None)
        after=self.repo.get('j')
        self.assertEqual((before['state'],before['revision']),(after['state'],after['revision']))

    def test_exact_decimal_math_does_not_round_at_default_context_precision(self):
        from budget_policy import exact_total, budget_window
        from decimal import Decimal
        self.assertEqual(exact_total(['9999999999999999999999999999.99','.01']),Decimal('10000000000000000000000000000.00'))
        config=policy(); config.update(timezone='Asia/Shanghai',dayStart='04:00')
        daily,cap,start,end=budget_window(config,'video',NOW)
        self.assertEqual(dt.datetime.fromtimestamp(start,dt.timezone.utc).hour,20)
        self.assertEqual(end-start,86400)
        config.update(timezone='America/New_York',dayStart='01:30')
        ambiguous=dt.datetime(2026,11,1,12,tzinfo=dt.timezone.utc).timestamp()
        with self.assertRaises(BudgetError): budget_window(config,'video',ambiguous)

    def test_unconfigured_disabled_invalid_policies_never_persist_intent(self):
        self.repo.register(job())
        for value in (None, {}, {**policy(), 'enabled': False}, {**policy(), 'dailyLimitPoints': None},
                      {**policy(), 'timezone': None}, {**policy(), 'dayStart': None},
                      {**policy(), 'dailyLimitPoints': float('nan')},
                      {**policy(), 'dailyLimitPoints': True}, {**policy(), 'timezone': 'invented/timezone'}):
            with self.subTest(value=value), self.assertRaises(BudgetError):
                self.repo.reserve_intent(job(), 1.25, value, now=NOW)
        self.assertIsNone(self.repo.get('j')['billing'])

    def test_policy_file_never_rounds_a_limit_up_to_a_charge(self):
        from budget_policy import read_policy
        config = Path(self.tmp.name) / 'budget-policy.json'
        encoded = json.dumps(policy(daily='LIMIT', limit='2')).replace('"LIMIT"', '0.9999999999999999999999999999')
        config.write_text(encoded, encoding='utf-8')
        self.repo.register(job())
        with self.assertRaises(BudgetError):
            self.repo.reserve_intent(job(), 1, read_policy(config), now=NOW)
        self.assertIsNone(self.repo.get('j')['billing'])
        config.write_text(encoded.replace('"dailyLimitPoints": ', '"dailyLimitPoints": null, "dailyLimitPoints": '),
                          encoding='utf-8')
        with self.assertRaises(BudgetError):
            self.repo.reserve_intent(job(), '0.5', read_policy(config), now=NOW)
        self.assertIsNone(self.repo.get('j')['billing'])

    def test_decimal_reservation_concurrency_never_overspends(self):
        for n in range(8): self.repo.register(job(str(n)))
        def reserve(n):
            try:
                TaskRepository(self.path).reserve_intent(job(str(n)), 1.25, policy(), now=NOW)
                return True
            except BudgetError: return False
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(reserve, range(8))), 2)

    def test_single_limit_and_duplicate_intent_guard(self):
        self.repo.register(job())
        with self.assertRaises(BudgetError): self.repo.reserve_intent(job(), 1.26, policy(), now=NOW)
        intent = self.repo.reserve_intent(job(), 1.25, policy(), now=NOW)
        self.assertTrue(intent['intentId'])
        self.assertEqual(intent['reservedPoints'], '1.25')
        with self.assertRaises(RepositoryError): self.repo.reserve_intent(job(), 1.25, policy(), now=NOW)

    def test_unknown_holds_across_midnight_and_cannot_resubmit(self):
        self.repo.register(job())
        self.repo.reserve_intent(job(), 1.25, policy(daily='1.25'), now=NOW)
        self.repo.mark_unknown('j')
        self.repo.register(job('tomorrow'))
        with self.assertRaises(BudgetError):
            self.repo.reserve_intent(job('tomorrow'), 1.25, policy(daily='1.25'), now=NOW+86400)
        with self.assertRaises(RepositoryError): self.repo.reserve_intent(job(), 1.25, policy(), now=NOW+86400)
        self.assertEqual(self.repo.get('j')['billing']['settlementStatus'], 'unknown')

    def test_settlement_variance_and_confirmed_no_charge_evidence(self):
        self.repo.register(job())
        bill = self.repo.reserve_intent(job(), 1.25, policy(), now=NOW)
        self.repo.mark_unknown('j')
        proof = {'schemaVersion': 1, 'bridgeJobId': 'j', 'intentId': bill['intentId'],
                 'upstreamTaskId': 700, 'source': 'official_statement', 'reference': 'official-receipt-1',
                 'outcome': 'charge_confirmed', 'actualPoints': '2.75'}
        with self.assertRaises(RepositoryError): self.repo.apply_billing_evidence(proof)
        self.repo.record_receipt('j', 700)
        with self.assertRaises(RepositoryError):
            self.repo.apply_billing_evidence({**proof, 'upstreamTaskId': 701})
        report = self.repo.apply_billing_evidence(proof)
        self.assertEqual(report['anomalies'], ['charge_variance', 'per_operation_limit_exceeded', 'daily_limit_exceeded'])
        self.assertEqual(report['billing']['actualPoints'], '2.75')
        self.assertEqual(TaskRepository(self.path).apply_billing_evidence(proof), report)
        self.assertEqual(self.repo.get('j')['billing']['settlementStatus'], 'variance')
        self.repo.register(job('later'))
        with self.assertRaises(BudgetError): self.repo.reserve_intent(job('later'), 1.25, policy(), now=NOW)
        with self.assertRaises(RepositoryError):
            self.repo.apply_billing_evidence({**proof, 'actualPoints': '1.25'})

    def test_confirmed_unaccepted_and_uncharged_releases_without_resubmission(self):
        self.repo.register(job())
        bill = self.repo.reserve_intent(job(), 1.25, policy(), now=NOW)
        self.repo.mark_unknown('j')
        proof = {'schemaVersion': 1, 'bridgeJobId': 'j', 'intentId': bill['intentId'],
                 'upstreamTaskId': None, 'source': 'official_statement', 'reference': 'verified-official-rejection',
                 'outcome': 'rejected_without_charge', 'accepted': False, 'charged': False}
        for changes in ({'intentId': 'wrong-intent'}, {'accepted': True}, {'charged': True}, {'upstreamTaskId': 700}):
            with self.subTest(changes=changes), self.assertRaises(RepositoryError):
                self.repo.apply_billing_evidence({**proof, **changes})
        self.assertEqual(self.repo.get('j')['billing']['settlementStatus'], 'unknown')
        result = self.repo.apply_billing_evidence(proof)
        self.assertEqual(result['billing']['settlementStatus'], 'released')
        self.assertIsNone(result['billing']['actualPoints'])
        self.assertEqual(self.repo.get('j')['state'], 'failed')
        self.assertEqual(self.repo.get('j')['billing']['settlementStatus'], 'released')
        with self.assertRaises(RepositoryError): self.repo.reserve_intent(job(), 1.25, policy(), now=NOW)
        self.repo.register(job('other'))
        second = self.repo.reserve_intent(job('other'), 1.25, policy(), now=NOW)
        self.repo.mark_unknown('other')
        with self.assertRaises(RepositoryError):
            self.repo.apply_billing_evidence({**proof, 'bridgeJobId':'other', 'intentId':second['intentId']})
        self.assertEqual(self.repo.get('other')['billing']['settlementStatus'], 'unknown')

    def test_migration_preserves_exact_raw_rows_and_quarantines_unknown(self):
        path = Path(self.tmp.name) / 'legacy.sqlite3'
        raw = json.dumps({'id': 'old', 'kind': 'extension', 'status': 'submission_unknown',
                          'pointsEstimate': 67, 'approvedPoints': 67, 'approvedAt': 1, 'storyboardId': 411981})
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('CREATE TABLE jobs(key TEXT PRIMARY KEY,digest TEXT,result TEXT)')
            db.execute('CREATE TABLE preparation_requests(key TEXT PRIMARY KEY,payload TEXT,kind TEXT,cancel_requested INTEGER)')
            db.execute('INSERT INTO jobs VALUES (?,?,?)', ('old', 'original-digest', raw))
            db.execute('INSERT INTO preparation_requests VALUES (?,?,?,?)', ('old', 'original-request', 'extension', 0))
            db.execute('PRAGMA user_version=2')
        repo = TaskRepository(path)
        with closing(sqlite3.connect(path)) as db, db:
            self.assertEqual(db.execute('SELECT result FROM jobs').fetchone()[0], raw)
            self.assertEqual(db.execute('SELECT payload FROM preparation_requests').fetchone()[0], 'original-request')
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 3)
        old = repo.get('old')
        self.assertTrue(old['historical'])
        self.assertEqual(old['state'], 'submission_unknown')
        self.assertIsNone(old['identity']['upstreamTaskId'])
        with self.assertRaises(RepositoryError): repo.reserve_intent(job('old'), 1, policy(), now=NOW)

    def test_sqlite_lock_and_migration_error_leave_no_partial_intent(self):
        self.repo.register(job())
        with closing(sqlite3.connect(self.path)) as blocker, blocker:
            blocker.execute('BEGIN IMMEDIATE')
            repo = TaskRepository.__new__(TaskRepository); repo.path=self.path; repo.timeout=.01
            with self.assertRaises(sqlite3.OperationalError): repo.reserve_intent(job(), 1, policy(), now=NOW)
        self.assertIsNone(self.repo.get('j')['billing'])

if __name__ == '__main__': unittest.main()
