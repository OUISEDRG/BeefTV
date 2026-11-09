import copy
import unittest

try:
    from task_contracts import validate_contract, ContractError, transition_allowed, legacy_state
except ImportError:
    validate_contract = None

class ContractTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(validate_contract, 'P1 contracts are not implemented')

    def identity(self):
        return {'schemaVersion': 1, 'clientOperationId': 'caller-1', 'bridgeJobId': 'bridge-1',
                'beefTaskId': None, 'providerRequestId': None, 'upstreamTaskId': None,
                'resourceId': None, 'sourceNodeId': 'node-1', 'artifactId': None,
                'outputIndex': 0, 'outputCount': 2}

    def test_identity_keeps_system_ids_separate_and_upstream_unknown(self):
        value = self.identity()
        self.assertEqual(validate_contract('task-identity', value), value)
        self.assertIsNone(value['upstreamTaskId'])

    def test_missing_wrong_types_and_batch_bounds_rejected(self):
        for changes in ({'clientOperationId': ''}, {'bridgeJobId': 2}, {'schemaVersion': True},
                        {'outputIndex': 2}, {'outputCount': 0}, {'outputIndex': -1}):
            with self.subTest(changes=changes), self.assertRaises(ContractError):
                validate_contract('task-identity', {**self.identity(), **changes})
        value = self.identity(); del value['clientOperationId']
        with self.assertRaises(ContractError): validate_contract('task-identity', value)

    def test_state_machine_unknown_never_reenters_paid_submission(self):
        self.assertTrue(transition_allowed('submission_unknown', 'submitted'))
        self.assertTrue(transition_allowed('submission_unknown', 'submission_unknown'))
        self.assertFalse(transition_allowed('submission_unknown', 'submission_intent'))
        self.assertFalse(transition_allowed('completed', 'preparing'))
        self.assertFalse(transition_allowed('invented', 'prepared'))

    def test_history_is_not_relabelled_as_new_execution_or_delivery(self):
        self.assertEqual(legacy_state('unknown'), 'recovery_required')
        self.assertEqual(legacy_state('submission_unknown'), 'submission_unknown')
        self.assertEqual(legacy_state('completed'), 'artifact_ready')
        self.assertEqual(legacy_state('cancelled'), 'blocked')

    def test_media_and_ack_validate_ownership_bytes_order_and_time(self):
        artifact = {'schemaVersion': 1, 'taskId': 't', 'artifactId': 'a', 'outputIndex': 0,
                    'sha256': 'a' * 64, 'sizeBytes': 1, 'mimeType': 'image/png'}
        ack = {k: v for k, v in artifact.items() if k != 'mimeType'}
        ack.update(resourceId='r', acknowledgedAt='2026-11-07T00:00:00Z')
        self.assertEqual(validate_contract('media-artifact', artifact), artifact)
        self.assertEqual(validate_contract('media-ack', ack, artifact=artifact), ack)
        for changes in ({'taskId': 'wrong'}, {'artifactId': 'wrong'}, {'sha256': 'b'*64},
                        {'sizeBytes': 2}, {'outputIndex': 1}, {'acknowledgedAt': 'yesterday'}):
            with self.subTest(changes=changes), self.assertRaises(ContractError):
                validate_contract('media-ack', {**ack, **changes}, artifact=artifact)

    def test_billing_unknown_is_not_zero_and_estimate_is_not_quote(self):
        bill = {'schemaVersion': 1, 'estimatedPoints': '1.25', 'quotedPoints': None,
                'reservedPoints': None, 'actualPoints': None, 'billingSource': 'estimated',
                'settlementStatus': 'unsettled'}
        self.assertEqual(validate_contract('billing', bill), bill)
        for value in ('-1', 'NaN', 'Infinity', True):
            with self.assertRaises(ContractError):
                validate_contract('billing', {**bill, 'actualPoints': value})

    def test_cli_contract_rejects_environment_overrides_and_wrong_operation_class(self):
        value={'schemaVersion':1,'command':'task','arguments':['status','--task-id','123'],'operationClass':'read'}
        self.assertEqual(validate_contract('cli-command',value),value)
        for changes in ({'arguments':['status','--server=https://elsewhere']},
                        {'command':'video','arguments':['produce','--yes']},
                        {'arguments':['unknown']},{'command':'auth','arguments':['login']}):
            with self.assertRaises(ContractError): validate_contract('cli-command',{**value,**changes})

    def test_artifact_batch_has_complete_unique_order_and_one_task(self):
        from task_contracts import validate_artifacts
        first={'schemaVersion':1,'taskId':'t','artifactId':'a','outputIndex':0,
               'sha256':'a'*64,'sizeBytes':1,'mimeType':'image/png'}
        second={**first,'artifactId':'b','outputIndex':1}
        self.assertEqual(validate_artifacts([second,first],task_id='t',output_count=2),[first,second])
        for items in ([first], [first,first], [first,{**second,'taskId':'other'}], [first,{**second,'artifactId':'a'}]):
            with self.assertRaises(ContractError): validate_artifacts(items,task_id='t',output_count=2)

if __name__ == '__main__': unittest.main()
