"""Versioned task/media contracts; no network, credentials, or database access."""
from datetime import datetime
from functools import lru_cache
import json
from pathlib import Path

from jsonschema import Draft202012Validator

class ContractError(ValueError):
    pass

READ_COMMANDS = {
    ('auth','status'),('auth','whoami'),('project','list'),('project','get'),('project','members'),
    ('series','list'),('series','get'),('library','list'),('storyboard','list'),('prompt','assemble'),
    ('capability','list'),('point','calc'),('task','status'),('task','get'),('task','queue')}
PREPARE_COMMANDS = {('series','create'),('storyboard','create'),('storyboard','update'),('storyboard','set-video-prompt')}
PAID_COMMANDS = {('image','produce'),('video','produce'),('video','batch')}

def cli_operation_class(pair):
    if pair in READ_COMMANDS: return 'read'
    if pair in PREPARE_COMMANDS: return 'prepare'
    if pair in PAID_COMMANDS: return 'paid_submission'
    return None

@lru_cache
def schema(name):
    if name not in {'task-identity', 'task-state', 'billing', 'billing-evidence', 'budget',
                    'media-artifact', 'media-ack', 'cli-command'}:
        raise ContractError('Unknown contract')
    value = json.loads((Path(__file__).parent / 'schemas' / (name + '.schema.json')).read_text('utf-8'))
    Draft202012Validator.check_schema(value)
    return value

def validate_contract(name, value, *, artifact=None):
    errors = list(Draft202012Validator(schema(name)).iter_errors(value))
    if errors:
        # Paths and schema rules only; never include payload values in errors.
        raise ContractError('Invalid ' + name + ': ' + '/'.join(str(x) for x in errors[0].path) + ' (' + errors[0].validator + ')')
    if name == 'task-identity' and value['outputIndex'] >= value['outputCount']:
        raise ContractError('Output index outside batch')
    if name == 'cli-command':
        args=value['arguments']; pair=(value['command'],args[0] if args else None)
        category=cli_operation_class(pair)
        if category is None or value['operationClass'] != category:
            raise ContractError('CLI operation is not in the declared command class')
        if any(arg.split('=',1)[0] in {'--server','--env','--config','--raw'} for arg in args):
            raise ContractError('CLI runtime/environment overrides are not allowed')
    if name == 'media-ack':
        try:
            stamp = datetime.fromisoformat(value['acknowledgedAt'].replace('Z', '+00:00'))
            if stamp.tzinfo is None: raise ValueError()
        except ValueError:
            raise ContractError('ACK requires an offset-aware timestamp') from None
        if artifact is not None:
            validate_contract('media-artifact', artifact)
            if any(value[k] != artifact[k] for k in ('taskId', 'artifactId', 'outputIndex', 'sha256', 'sizeBytes')):
                raise ContractError('ACK artifact ownership or byte identity mismatch')
    return value

def validate_artifacts(items, *, task_id, output_count):
    if type(output_count) is not int or output_count < 1 or not isinstance(items,list) or len(items) != output_count:
        raise ContractError('Incomplete artifact batch')
    for item in items: validate_contract('media-artifact',item)
    if (any(item['taskId'] != task_id for item in items)
            or {item['outputIndex'] for item in items} != set(range(output_count))
            or len({item['artifactId'] for item in items}) != output_count):
        raise ContractError('Artifact task, index, or identity mismatch')
    return sorted(items,key=lambda item:item['outputIndex'])

def transition_allowed(previous, target):
    states = schema('task-state')['properties']['state']['enum']
    return previous in states and target in states and (previous == target or target in schema('task-state')['x-transitions'][previous])

def legacy_state(status):
    return {'preparation_queued': 'received', 'preparing': 'preparing', 'prepared': 'prepared',
            'submitting': 'submission_unknown', 'submission_unknown': 'submission_unknown',
            'queued': 'queued', 'processing': 'processing', 'completed': 'artifact_ready',
            'failed': 'failed', 'cancelled': 'blocked', 'unknown': 'recovery_required',
            'download_failed': 'recovery_required'}.get(status, 'recovery_required')

def identity_from_job(job):
    value = {'schemaVersion': 1, 'clientOperationId': job.get('clientOperationId') or job['id'],
             'bridgeJobId': job['id'], 'beefTaskId': job.get('beefTaskId'),
             'providerRequestId': job.get('providerRequestId'), 'upstreamTaskId': job.get('taskId'),
             'resourceId': job.get('resourceId'), 'sourceNodeId': job.get('nodeId') or None,
             'artifactId': job.get('artifactId'), 'outputIndex': job.get('outputIndex', 0),
             'outputCount': job.get('outputCount', 1)}
    return validate_contract('task-identity', value)
