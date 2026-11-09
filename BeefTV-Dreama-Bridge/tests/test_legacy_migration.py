from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from task_repository import TaskRepository, RepositoryError
from budget_policy import BudgetError
from tests.test_task_repository import policy,job,NOW

class MigrationTests(unittest.TestCase):
    def legacy_paid(self,path,status,**changes):
        value={'id':'old','kind':'video','status':status,'taskId':700,'generationSubmitted':True,
               'pointsEstimate':10,'approvedPoints':10,'approvedAt':NOW,**changes}
        with closing(sqlite3.connect(path)) as db,db:
            db.execute('CREATE TABLE jobs(key TEXT PRIMARY KEY,digest TEXT,result TEXT)')
            db.execute('INSERT INTO jobs VALUES(?,?,?)',('old','old-digest',json.dumps(value)))
        return TaskRepository(path)

    def new_job(self,repo,amount=1.25):
        value=job('new',pointsEstimate=amount)
        with repo.connect() as db:
            db.execute('INSERT INTO jobs VALUES(?,?,?)',('new',value['requestDigest'],json.dumps(value)))
        repo.register(value)
        return value

    def test_all_accepted_legacy_states_keep_unsettled_budget_occupancy(self):
        for status in ('submitting','submission_unknown','queued','processing','download_failed','completed','failed','cancelled'):
            with self.subTest(status=status),tempfile.TemporaryDirectory() as temp:
                repo=self.legacy_paid(Path(temp)/'jobs.sqlite3',status)
                bill=repo.get('old')['billing']
                self.assertIsNotNone(bill,'Accepted historical charge disappeared from budget accounting')
                self.assertEqual(bill['reservedPoints'],'10')
                self.assertIsNone(bill['actualPoints'])
                value=self.new_job(repo,10)
                with self.assertRaises(BudgetError):
                    repo.reserve_intent(value,10,policy(daily='10',limit='10'),now=NOW)

    def test_missing_legacy_amount_or_submission_time_fails_closed(self):
        for changes in ({'pointsEstimate':None,'approvedPoints':None},{'approvedAt':None,'createdAt':NOW}):
            with self.subTest(changes=changes),tempfile.TemporaryDirectory() as temp:
                repo=self.legacy_paid(Path(temp)/'jobs.sqlite3','processing',**changes)
                value=self.new_job(repo)
                with self.assertRaises(BudgetError): repo.reserve_intent(value,1.25,policy(daily='100',limit='100'),now=NOW)
                self.assertEqual(repo.get('old')['state'],'processing')

    def test_proven_local_cancellation_does_not_invent_a_paid_charge(self):
        with tempfile.TemporaryDirectory() as temp:
            repo=self.legacy_paid(Path(temp)/'jobs.sqlite3','cancelled',stage='cancelled_before_submit',
                                  taskId=None,generationSubmitted=False,approvedAt=None,approvedPoints=None)
            self.assertIsNone(repo.get('old')['billing'])
            value=self.new_job(repo)
            repo.reserve_intent(value,1.25,policy(),now=NOW)

    def test_dreama_additive_migration_fences_older_writers(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'dreama.sqlite3'
            with closing(sqlite3.connect(path)) as db, db:
                db.execute('CREATE TABLE jobs(key TEXT PRIMARY KEY,digest TEXT,result TEXT)')
                db.execute('PRAGMA user_version=2')
            TaskRepository(path)
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],3,
                                 'Old Bridge writers must reject the P1 database')

    def test_codex_own_database_layout_is_archived_without_touching_job_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'codex.sqlite3'
            with closing(sqlite3.connect(path)) as db, db:
                db.execute('CREATE TABLE jobs(id TEXT PRIMARY KEY,idempotency_key TEXT,fingerprint TEXT,request_json TEXT,status TEXT,result_json TEXT)')
                db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?)',('image-1','caller-1','sha','{"prompt":"fixture"}','succeeded','{}'))
                original=db.execute('SELECT * FROM jobs').fetchall()
            repo=TaskRepository(path)
            self.assertIsNotNone(repo.get('image-1'),'Codex layout was silently skipped')
            self.assertEqual(repo.get('image-1')['state'],'artifact_ready')
            self.assertEqual(repo.get('image-1')['identity']['clientOperationId'],'caller-1')
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(db.execute('SELECT * FROM jobs').fetchall(),original)
                self.assertEqual(db.execute('SELECT count(*) FROM p1_legacy_records').fetchone()[0],1)

    def test_malformed_record_rolls_back_additive_schema_and_preserves_legacy(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'broken.sqlite3'
            with closing(sqlite3.connect(path)) as db, db:
                db.execute('CREATE TABLE jobs(key TEXT PRIMARY KEY,digest TEXT,result TEXT)')
                db.execute('INSERT INTO jobs VALUES (?,?,?)',('j','sha','not-json'))
            with self.assertRaises(ValueError): TaskRepository(path)
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(db.execute('SELECT result FROM jobs').fetchone()[0],'not-json')
                self.assertFalse(db.execute("SELECT 1 FROM sqlite_schema WHERE name='p1_schema'").fetchone())

    def test_newer_ledger_schema_is_not_downgraded(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'future.sqlite3'
            with closing(sqlite3.connect(path)) as db, db:
                db.execute('CREATE TABLE p1_schema(version INTEGER)')
                db.execute('INSERT INTO p1_schema VALUES(3)')
            with self.assertRaises(RepositoryError): TaskRepository(path)
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(db.execute('SELECT version FROM p1_schema').fetchone()[0],3)

if __name__=='__main__': unittest.main()
