"""Durable five-second opt-out execution, independent of browser lifetime."""
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
        created=job.get('createdAt');points=job.get('pointsEstimate');effective=self.policy.get('effectiveAt')
        return (self.policy.get('mode')=='background-countdown' and self.policy.get('delaySeconds')==5 and
                type(effective) in (int,float) and math.isfinite(effective) and
                type(created) in (int,float) and math.isfinite(created) and created>=effective and
                job.get('status')=='prepared' and job.get('generationSubmitted') is False and
                not job.get('bindingVerificationRequired') and
                bool(job.get('quoteId')) and bool(job.get('model')) and
                type(points) in (int,float) and math.isfinite(points) and points>=0)

    def request_cancel(self,key):
        # Does not wait behind a remote pre-submit check holding bridge.lock.
        with self.bridge.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT result FROM jobs WHERE key=?',(key,)).fetchone()
            job=json.loads(row[0]) if row else None
            if not job or job.get('status') not in ('preparation_queued','preparing','prepared') or job.get('generationSubmitted') is not False:
                raise BridgeError('只能取消尚未提交的本地任务；远端已提交任务不能假装取消。')
            db.execute("INSERT INTO preparation_requests(key,payload,cancel_requested) VALUES (?,'',1) "
                       'ON CONFLICT(key) DO UPDATE SET cancel_requested=1',(key,))
            if job['status']!='preparing':
                job.update(status='cancelled',stage='cancelled_before_submit',cancelRequested=True)
                db.execute('UPDATE jobs SET result=? WHERE key=?',(json.dumps(job,ensure_ascii=False),key))
        with self.cancel_lock:self.cancel_requests.add(key)

    def recover_interrupted(self):
        # Called only after the listening socket has been acquired on startup.
        # Show an interrupted readiness check honestly instead of an eternal
        # active spinner. A cancellation request may also have been interrupted.
        # Paid submitting/unknown jobs stay on the existing reconciliation path.
        with self.bridge.lock:
            with self.bridge.db() as db:keys=[r[0] for r in db.execute('SELECT key FROM jobs')]
            for key in keys:
                job=self.bridge.get(key)
                if (job and self.eligible(job) and job.get('autoAttemptedQuoteId')==job['quoteId'] and
                    not job.get('autoExecuteError')):
                    job['autoExecuteError']='上次提交前核验中断，自动执行已暂停；可手动立即执行或取消。'
                    job['autoRecoveredAt']=self.now();self.bridge.update(key,job)

    def ensure_not_cancelled(self,key):
        self.bridge.ensure_not_cancelled(key)
        with self.cancel_lock:
            if key in self.cancel_requests:raise BridgeError('已请求取消，未提交收费任务。')

    def cancel(self,key):
        return self.request_cancel(key)

    def step(self):
        with self.bridge.db() as db:keys=[r[0] for r in db.execute('SELECT key FROM jobs ORDER BY rowid')]
        for key in keys:
            with self.bridge.lock:
                job=self.bridge.get(key)
                if not job or not self.eligible(job):continue
                with self.cancel_lock:
                    if key in self.cancel_requests:continue
                quote=job['quoteId']
                if job.get('autoAttemptedQuoteId')==quote:continue
                if job.get('autoExecuteQuoteId')!=quote:
                    job.update(autoExecuteQuoteId=quote,autoExecuteAt=self.now()+5)
                    job.pop('autoExecuteError',None);self.bridge.update(key,job)
                if self.now()<job['autoExecuteAt']:continue
                # Persist before readiness checks so errors/restarts cannot loop.
                job.update(autoAttemptedQuoteId=quote,autoAttemptedAt=self.now())
                self.bridge.update(key,job)
            try:
                self.flow_for(job).approve(key,quote,job['pointsEstimate'],before_submit=lambda:self.ensure_not_cancelled(key))
            except Exception as error:
                with self.bridge.lock:
                    current=self.bridge.get(key)
                    if current and current['status']=='prepared':
                        current['autoExecuteError']=str(error) if isinstance(error,BridgeError) else type(error).__name__
                        self.bridge.update(key,current)
