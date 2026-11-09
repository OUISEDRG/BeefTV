import unittest
from types import SimpleNamespace

from bridge import BridgeError
from integration import get_integration


class WorkbenchExceptionTests(unittest.TestCase):
    def test_exception_projection_preserves_reconciliation_without_showing_formal_tasks(self):
        rows = [
            {'id': 'completed', 'canvasId': 'a', 'status': 'completed', 'mediaKey': 'private-path'},
            {'id': 'ordinary', 'canvasId': 'a', 'status': 'processing'},
            {'id': 'old', 'canvasId': 'a', 'status': 'submission_unknown', 'pointsEstimate': 67,
             'prompt': 'private prompt', 'error': 'https://signed-url.invalid', 'autoExecuteAt': 100},
            {'id': 'blocked', 'canvasId': 'a', 'status': 'prepared', 'stage': 'budget_blocked',
             'generationSubmitted': False, 'executionError': 'private details'},
            {'id': 'binding', 'canvasId': 'a', 'status': 'prepared', 'bindingVerificationRequired': True},
            {'id': 'different', 'canvasId': 'b', 'status': 'download_failed'},
        ]
        server = SimpleNamespace(bridge=SimpleNamespace(gateway=object()), list_jobs=lambda: rows)
        query = {'canvasId': ['a'], 'scope': ['exceptions']}
        result = get_integration(server, '/tasks', query)['items']
        self.assertEqual([task['id'] for task in result], ['old', 'blocked', 'binding'])
        self.assertEqual(result[0]['pointsEstimate'], 67)
        self.assertEqual(result[1]['stage'], 'budget_blocked')
        self.assertEqual(result[2]['status'], 'binding_verification_required')
        self.assertTrue(all(not task['cancelable'] for task in result))
        self.assertNotIn('signed-url', str(result))
        self.assertNotIn('prompt', str(result))
        self.assertNotIn('autoExecuteAt', str(result))
        self.assertEqual(len(get_integration(server, '/tasks', {'canvasId': ['a']})['items']), 5)
        with self.assertRaises(BridgeError):
            get_integration(server, '/tasks', {**query, 'scope': ['exceptions', 'all']})
