"""Automatic execution after preflight, guarded by the durable budgeted submitter."""
import math
import json
import threading
import time
from bridge import BridgeError

class AutoExecution:
    def __init__(self,bridge,flow_for,policy,now=time.time):
        self.bridge,self.flow_for,self.policy,self.now=bridge,flow_for,policy,now
        self.cancel_requests=set();self.cancel_lock=threading.Lock()

    def eligible(self,job):
        record=self.bridge.tasks.get(job['id'])
        return (self.policy.get('mode')=='automatic' and record and not record['historical']
                and not record['billing'] and job.get('status')=='prepared'
                and not job.get('submissionHold')
                and job.get('generationSubmitted') is False and not job.get('bindingVerificationRequired')
                and bool(job.get('quoteId')) and bool(job.get('model')))

    def request_cancel(self,key):
        raise BridgeError('Dreama 新执行合同不支持预提交取消；已提交任务仅可按上游确证状态展示。')

    def recover_interrupted(self):
        # Only read-only preflight before an intent may resume. Paid intents stay with pollers.
        with self.bridge.db() as db: keys=[row[0] for row in db.execute('SELECT key FROM jobs')]
        for key in keys:
            with self.bridge.task_lock(key):
                job=self.bridge.get(key)
                if (job and self.eligible(job) and job.get('executionAttemptPolicy')
                        and not job.get('executionError')):
                    job.pop('executionAttemptPolicy')
                    self.bridge.update(key,job)

    def ensure_not_cancelled(self,key):
        self.bridge.ensure_not_cancelled(key)
        with self.cancel_lock:
            if key in self.cancel_requests:raise BridgeError('已请求取消，未提交收费任务。')

    def cancel(self,key):
        return self.request_cancel(key)

    def step(self):
        from budget_policy import policy_hash, budget_window, BudgetError
        with self.bridge.db() as db:keys=[r[0] for r in db.execute('SELECT key FROM jobs ORDER BY rowid')]
        for key in keys:
            with self.bridge.task_lock(key):
                job=self.bridge.get(key)
                if not job or not self.eligible(job):continue
                config=self.bridge.budget_config()
                try: window=str(budget_window(config,job.get('kind','video'),self.now())[2])
                except BudgetError: window='unconfigured'
                stamp=policy_hash(config)+':'+window
                if job.get('executionAttemptPolicy')==stamp:continue
                job['executionAttemptPolicy']=stamp
                self.bridge.update(key,job)
                try:
                    self.flow_for(job).submit(key)
                except Exception as error:
                    current=self.bridge.get(key)
                    if current and current['status']=='prepared':
                        current['executionError']=str(error) if isinstance(error,BridgeError) else type(error).__name__
                        self.bridge.update(key,current)
