"""One budgeted submitter per durable intent; retries of unknown outcomes are read-only."""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
import sqlite3
from threading import get_ident
import uuid
from bridge_errors import BridgeError
from budget_policy import BudgetError, points, point_text
from task_repository import RepositoryError

_ACTIVE_INTENT = ContextVar('active_dreama_submission', default=None)


class BudgetWithdrawn(BridgeError):
    """The original transport did not start and the reservation is released."""


class PreDispatchFailure(BridgeError):
    """A local preflight guard prevented the paid call."""


@contextmanager
def checked_before_dispatch():
    try:
        yield
    except BridgeError as error:
        raise PreDispatchFailure(str(error)) from error


def require_paid_intent(operation, job, *, consume=False):
    """A committed intent authorizes only its original, in-process send."""
    active = _ACTIVE_INTENT.get()
    if (active is None or active['thread'] != get_ident() or active['used'] or job != active['job']
            or job.get('kind', 'video') != operation):
        raise BridgeError('缺少本次持久化收费意图，禁止调用收费传输。')
    if not consume:
        return
    bridge = active['bridge']
    try:
        bridge.tasks.recheck_before_dispatch(job, bridge.budget_config)
    except BudgetError as error:
        raise BudgetWithdrawn(str(error)) from None
    active['used'] = True

def verify_submission_intent(job):
    try:
        valid = (job.get('status') == 'submitting' and isinstance(job.get('submissionIntentId'),str)
                 and len(job['submissionIntentId']) == 32
                 and points(job.get('reservedPoints')) == points(job.get('pointsEstimate')))
    except BudgetError:
        valid = False
    if not valid: raise BridgeError('缺少持久化收费意图及预算预留，禁止提交。')

class SubmissionService:
    def __init__(self, flow):
        self.flow, self.bridge = flow, flow.bridge

    def execute(self, key, *, before_submit=None, expected_quote=None, expected_points=None):
        bridge, flow = self.bridge, self.flow
        with bridge.task_lock(key):
            job = bridge.get(key)
            if not job or job.get('status') != 'prepared': raise BridgeError('任务不可新提交；已有意图只能查询或对账。')
            if job.get('submissionHold'): raise BridgeError('任务已按用户要求暂停，未提交生成。')
            record = bridge.tasks.get(key)
            if not record or record['historical'] or record['billing']:
                raise BridgeError('历史任务或已有收费意图，不能重新提交。')
            if expected_quote is not None:
                try: same = expected_quote==job.get('quoteId') and points(expected_points)==points(job.get('pointsEstimate'))
                except BudgetError: same=False
                if not same: raise BridgeError('任务报价已失效。')
            bridge.ensure_not_cancelled(key)
            if before_submit: before_submit()
            flow.check_ready(job)
            fresh = flow.estimate_job(job)
            try:
                fresh = points(fresh)
                estimated = point_text(job['pointsEstimate'])
                job.setdefault('estimatedPoints', estimated)
                if fresh != points(job['pointsEstimate']):
                    job.update(pointsEstimate=point_text(fresh), quoteId=uuid.uuid4().hex)
                    bridge.update(key,job)
                    if expected_quote is not None: raise BridgeError('积分已变化，请刷新任务；自动执行会重新核验预算。')
                bridge.ensure_not_cancelled(key)
                if before_submit: before_submit()
                source = getattr(flow,'billing_source','cli_quote')
                bill = bridge.tasks.reserve_intent(job,fresh,bridge.budget_config(),billing_source=source)
            except BudgetError as error:
                job.update(stage='budget_blocked',executionError=str(error))
                bridge.update(key,job)
                raise BridgeError(str(error)) from None
            except RepositoryError as error:
                raise BridgeError(str(error)) from None
            # Intent and provider JSON were committed in one transaction before this boundary.
            job.update(reservedPoints=bill['reservedPoints'])
            try:
                active = {'bridge': bridge, 'job': deepcopy(job), 'used': False, 'thread': get_ident()}
                token = _ACTIVE_INTENT.set(active)
                try:
                    receipt = flow.gateway.submit_video(job)
                finally:
                    _ACTIVE_INTENT.reset(token)
                task_id = receipt.get('taskId') if isinstance(receipt,dict) else receipt
                if not flow.valid_task_id(task_id): task_id=flow.reconcile_task_id(job)
                if not flow.valid_task_id(task_id): raise BridgeError('提交回执没有唯一上游任务身份。')
                bridge.tasks.record_receipt(key,task_id)
                job.update(taskId=task_id,status='queued',stage='poll',generationSubmitted=True)
                job.pop('executionError',None)
                bridge.update(key,job)
                return job
            except BudgetWithdrawn:
                raise
            except Exception as error:
                if isinstance(error, PreDispatchFailure) and not active['used']:
                    try:
                        bridge.tasks.release_before_dispatch(job)
                    except (sqlite3.Error, RepositoryError):
                        raise BridgeError('收费未发送，预留释放记录失败；保留原意图以供核对。') from None
                    raise BridgeError(str(error)) from None
                detail = str(error) if isinstance(error,BridgeError) else type(error).__name__
                job.update(status='submission_unknown',error=detail,submissionError=detail)
                try:
                    bridge.tasks.mark_unknown(key)
                    bridge.update(key,job)
                except (sqlite3.Error,RepositoryError):
                    raise BridgeError('收费意图已持久化，回执存储失败；只能只读对账，禁止重提。') from None
                raise BridgeError('提交回执不明，只读对账；不会自动再次扣费。') from None
