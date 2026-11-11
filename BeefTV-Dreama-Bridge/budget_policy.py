"""Fail-closed budget policy and exact decimal arithmetic, without defaults for money."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from task_contracts import validate_contract, ContractError

KINDS = ('video', 'image', 'extension', 'enhancement', 'audio')

class BudgetError(ValueError):
    pass

def unset_policy():
    return {'schemaVersion': 1, 'enabled': True, 'dailyLimitPoints': None,
            'perOperationLimits': {k: None for k in KINDS}, 'timezone': None, 'dayStart': None}

def points(value):
    if type(value) not in (str, int, float, Decimal) or isinstance(value, bool):
        raise BudgetError('积分缺失或格式无效，禁止新的收费提交。')
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0 or abs(amount.as_tuple().exponent) > 10000:
            raise ValueError()
    except (InvalidOperation, ValueError):
        raise BudgetError('积分必须为有限非负数，禁止新的收费提交。') from None
    return amount

def point_text(value):
    amount = points(value)
    if amount == 0: return '0'
    return format(amount, 'f')

def validated_quote(value, *, maximum=None):
    amount = points(value)
    if maximum is not None and amount >= maximum:
        raise BudgetError('积分报价超出可核对范围。')
    return value if type(value) in (int, float) else point_text(amount)

def exact_total(values):
    amounts = [points(x) for x in values]
    if not amounts: return Decimal(0)
    scale = max(max(0, -x.as_tuple().exponent) for x in amounts)
    integer_digits = max(max(1, x.adjusted()+1) for x in amounts)
    with localcontext() as ctx:
        ctx.prec = integer_digits + scale + len(str(len(amounts))) + 2
        return sum(amounts, Decimal(0))

def unique_json_object(items):
    obj = dict(items)
    if len(obj) != len(items):
        raise ValueError('Duplicate JSON key')
    return obj

def read_policy(path):
    try:
        return json.loads(Path(path).read_text('utf-8'), parse_float=point_text,
                          object_pairs_hook=unique_json_object,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (OSError, ValueError):
        return unset_policy()

def policy_hash(value):
    try:
        return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()
    except (TypeError, ValueError):
        return 'invalid-policy'

def direct_authorized(policy):
    try:
        validate_contract('budget', policy)
    except ContractError:
        return False
    return policy.get('mode') == 'direct-authorized' and policy['enabled'] is True

def budget_window(policy, kind, now):
    try:
        validate_contract('budget', policy)
        if policy['enabled'] is not True or kind not in KINDS:
            raise ValueError()
        daily, per_job = points(policy['dailyLimitPoints']), points(policy['perOperationLimits'][kind])
        if not policy['timezone'] or not policy['dayStart']: raise ValueError()
        zone = ZoneInfo(policy['timezone'])
        hour, minute = map(int, policy['dayStart'].split(':'))
        local = datetime.fromtimestamp(now, timezone.utc).astimezone(zone)
        day = local.date()
        if (local.hour, local.minute) < (hour, minute): day -= timedelta(days=1)
        def boundary(date):
            stamp = datetime(date.year, date.month, date.day, hour, minute, tzinfo=zone)
            if stamp.utcoffset() != stamp.replace(fold=1).utcoffset(): raise ValueError()
            if stamp.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != stamp.replace(tzinfo=None): raise ValueError()
            return stamp.timestamp()
        start, end = boundary(day), boundary(day + timedelta(days=1))
    except (ContractError, KeyError, TypeError, ValueError, ZoneInfoNotFoundError):
        raise BudgetError('预算、时区或日界线尚未正确配置，禁止新的收费提交。') from None
    return daily, per_job, start, end
