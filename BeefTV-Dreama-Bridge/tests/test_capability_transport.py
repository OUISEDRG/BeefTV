import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from bridge import BridgeError, DreamaGateway
from catalog_cache import build_channels


class CapabilityTransportTests(unittest.TestCase):
    def test_catalog_refresh_preserves_raw_external_and_vendor_metadata(self):
        root = Path(__file__).parents[1]
        images = json.loads((root / 'image-catalog.json').read_text('utf-8'))['items']
        videos = json.loads((root / 'extension-catalog.json').read_text('utf-8'))['items']

        def cli(command, **kwargs):
            if command[1:] == ['version']:
                return SimpleNamespace(returncode=0, stdout='Dreama cli 1.0.5 env=prod endpoint=https://jzzm.duanju.com/gw')
            self.assertEqual(command[1:3], ['capability', 'list'])
            kind = next(arg.split('=',1)[1] for arg in command if arg.startswith('--kind='))
            rows = images if kind == 'image' else videos
            if '--raw' in command:
                body = {'code': '0', 'msg': 'success', 'result': {'items': rows, 'totalCount': len(rows)}, 'traceId': 'test'}
            else:
                # CLI 1.0.5's default projection omits these policy fields.
                projected = [{k: v for k, v in row.items() if k not in ('isExternal', 'vendorGroupCode')}
                             for row in rows]
                body = {'ok': True, 'code': '0', 'result': {'items': projected}}
            return SimpleNamespace(returncode=0, stdout=json.dumps(body))

        with patch('dreama_cli.subprocess.run', side_effect=cli), patch('extension.ExtensionGateway._request', return_value={'items': videos}):
            snapshots = DreamaGateway().catalogs()
            channels = {channel['id']: channel for channel in build_channels(snapshots)}
        profiles = channels['dreama-image-generation']['modelProfiles']
        self.assertEqual(len(profiles), 21)
        published = {profile['model'] for profile in profiles}
        external = {row['eid'] for row in images if row['isExternal'] == 1}
        self.assertTrue(published.isdisjoint(external))
        self.assertTrue(all('isExternal' in row for row in snapshots['video']))
        self.assertEqual(snapshots['image'][0]['vendorGroupCode'], 'Vendor_JiuWan')

    def test_raw_capability_failure_or_malformed_success_is_rejected(self):
        invalid = [
            {'code': '401', 'ok': True, 'result': {'items': []}},
            {'code': '0', 'ok': True, 'result': None},
            {'code': '0', 'ok': True, 'result': {'items': 'not-a-list'}},
        ]
        for body in invalid:
            with self.subTest(body=body), patch('dreama_cli.DreamaCliClient.check_version'), patch('dreama_cli.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout=json.dumps(body))):
                with self.assertRaises(BridgeError):
                    DreamaGateway()._invoke('capability', 'list', '--kind', 'image')

    def test_non_capability_commands_keep_agent_envelope(self):
        def cli(command, **kwargs):
            if command[1:] == ['version']:
                return SimpleNamespace(returncode=0, stdout='Dreama cli 1.0.5 env=prod endpoint=https://jzzm.duanju.com/gw')
            self.assertNotIn('--raw', command)
            return SimpleNamespace(returncode=0, stdout=json.dumps({'ok': True, 'code': '0', 'result': {'userId': 17}}))
        with patch('dreama_cli.subprocess.run', side_effect=cli):
            self.assertEqual(DreamaGateway().identity(), {'userId': 17})
