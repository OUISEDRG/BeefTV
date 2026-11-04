"""Offline regression coverage for the CLI series-create acknowledgement."""
import copy
import tempfile
import threading
import unittest

import requests

from bridge import Bridge, BridgeError
from integration import create_series
from server import make_server


class SeriesGateway:
    def __init__(self, receipt):
        self.receipt = receipt
        self.calls = []
        self.items = []

    def run(self, *args):
        self.calls.append(args)
        if args[:2] == ('project', 'get'):
            return {'id': 91, 'promptMode': 'drama', 'proportion': '9:16'}
        if args[:2] == ('series', 'create'):
            self.items.append({'id': 92, 'name': args[5], 'projectId': args[3]})
            return copy.deepcopy(self.receipt)
        if args[:2] == ('series', 'list'):
            return {'items': copy.deepcopy(self.items), 'total': len(self.items)}
        raise AssertionError(f'Unexpected gateway call: {args!r}')

    @property
    def write_count(self):
        return sum(call[:2] == ('series', 'create') for call in self.calls)


class SeriesCreateTests(unittest.TestCase):
    def test_cli_acknowledgement_maps_series_id_without_returning_board_id(self):
        gateway = SeriesGateway({'boardId': 93, 'projectId': 91, 'seriesId': 92})

        result = create_series(gateway, {'projectId': 91, 'name': 'new episode'})

        self.assertEqual(result, {'item': {'id': 92, 'name': 'new episode', 'projectId': 91}})
        self.assertEqual(gateway.write_count, 1)
        self.assertEqual(gateway.calls[-1],
                         ('series', 'create', '--project-id', 91, '--name', 'new episode', '--yes'))

    def test_complete_row_receipts_remain_supported(self):
        row = {'id': 92, 'name': 'server episode', 'projectId': 91}
        for receipt in (row, {'item': row}):
            with self.subTest(receipt=receipt):
                gateway = SeriesGateway(receipt)
                result = create_series(gateway, {'projectId': 91, 'name': 'new episode'})
                self.assertEqual(result, {'item': row})
                self.assertEqual(gateway.write_count, 1)

    def test_invalid_acknowledgement_never_repeats_the_successful_remote_write(self):
        cases = [('missing-series-id', {'boardId': 93, 'projectId': 91})]
        for invalid in (0, -1, None, True, 1.5, [], {}, 'invalid'):
            cases.append((repr(invalid), {'boardId': 93, 'projectId': 91, 'seriesId': invalid}))
        cases.extend([
            ('wrong-project', {'boardId': 93, 'projectId': 99, 'seriesId': 92}),
            ('missing-project', {'boardId': 93, 'seriesId': 92}),
            ('wrong-project-type', {'boardId': 93, 'projectId': [], 'seriesId': 92}),
            ('null-result', None),
            ('list-result', []),
            ('string-result', 'created'),
            ('null-item', {'item': None}),
            ('missing-item-id', {'item': {'name': 'new episode', 'projectId': 91}}),
        ])
        for label, receipt in cases:
            with self.subTest(receipt=label):
                gateway = SeriesGateway(receipt)
                with self.assertRaises(BridgeError):
                    create_series(gateway, {'projectId': 91, 'name': 'new episode'})
                self.assertEqual(gateway.write_count, 1)
                self.assertEqual(len(gateway.items), 1)

    def test_http_create_succeeds_and_refresh_lists_new_episode(self):
        gateway = SeriesGateway({'boardId': 93, 'projectId': 91, 'seriesId': 92})
        with tempfile.TemporaryDirectory() as directory:
            server = make_server(Bridge(directory, gateway), 'offline-test-secret', 0,
                                 policy={'mode': 'manual'})
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f'http://127.0.0.1:{server.server_port}'
            headers = {'Authorization': 'Bearer offline-test-secret'}
            try:
                response = requests.post(base + '/integration/v1/series',
                                         json={'projectId': 91, 'name': 'new episode'},
                                         headers=headers, timeout=5)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json(),
                                 {'item': {'id': 92, 'name': 'new episode', 'projectId': 91}})

                refreshed = requests.get(base + '/integration/v1/series',
                                         params={'projectId': 91}, headers=headers, timeout=5)
                self.assertEqual(refreshed.status_code, 200, refreshed.text)
                self.assertEqual(refreshed.json(),
                                 {'items': [{'id': 92, 'name': 'new episode', 'projectId': 91}]})
                self.assertEqual(gateway.write_count, 1)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == '__main__':
    unittest.main()
