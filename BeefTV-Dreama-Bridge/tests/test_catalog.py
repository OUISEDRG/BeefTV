import unittest
from tests.test_bridge import request
from bridge import validate_request, BridgeError

class CatalogTests(unittest.TestCase):
    def test_all_catalog_entries_are_routable_with_their_duration_and_resolution(self):
        import json
        from pathlib import Path
        entries=json.loads((Path(__file__).resolve().parents[1]/'catalog.json').read_text('utf-8'))['items']
        self.assertEqual(len(entries),22)
        for entry in entries:
            with self.subTest(model=entry['eid']):
                data=request(); data['model']=entry['eid']
                data['duration']=max(t['duration'] for t in json.loads(entry['videoTokens']) if t['duration']>0)
                alias=entry.get('alias','')
                data['resolution']=alias.lower() if alias.lower().endswith('p') else 'default'
                validate_request(data)
                data['duration']=999
                with self.assertRaises(BridgeError): validate_request(data)
