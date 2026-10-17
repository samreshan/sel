import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from sel.service import CallService
from sel.server import serve
from sel.playground import Playground

ROOT = Path(__file__).resolve().parent.parent


class PlaygroundTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.service = CallService(self.tmp.name, ROOT / 'packs', chat_factory=lambda: None)
        self.rep = self.service.add_person('Playground Rep', 'rep')['id']
        self.pg = Playground(self.service)
        self.ref = next(iter(self.service.pack('demo-solar').docs))
        self.body = dict(person_id=self.rep, pack_id='demo-solar', mode='document', topic='pricing',
                         ref=self.ref, response='Let me explain the conditions first. What matters to you?')

    def tearDown(self):
        self.service.wait_idle()
        self.service.bus.close()
        self.tmp.cleanup()

    def test_persistence_source_snapshot_and_isolation(self):
        result = self.pg.submit(self.body)
        self.assertEqual(Playground(self.service).list(self.rep, 'demo-solar')[0], result)
        self.assertEqual(result['source_text'], self.service.pack('demo-solar').docs[self.ref])
        other = self.service.add_person('Another Rep', 'rep')['id']
        self.assertEqual(self.pg.list(other, 'demo-solar'), [])
        self.assertEqual(self.pg.list(self.rep, 'demo-pos'), [])
        self.assertEqual(self.service.list_drills(self.rep), [])
        self.assertNotIn('verdict', result)
        self.assertFalse((self.service.root / 'coach' / f'{self.rep}.json').exists())

    def test_invalid_response_source_and_manager(self):
        manager = self.service.add_person('Manager', 'manager')['id']
        for change in ({'response': ''}, {'response': 'x' * 4001}, {'ref': '../secret'},
                       {'mode': 'invented'}, {'person_id': manager}, {'ref': []}):
            with self.subTest(change=change), self.assertRaises((ValueError, KeyError)):
                self.pg.submit({**self.body, **change})

    def test_model_quote_gate(self):
        class Model:
            def complete_json(self, *args, **kwargs):
                return {'tip': 'source', 'response_quote': 'Never said this', 'source_quote': 'Invented'}
        self.service._chat_factory = lambda: Model()
        result = self.pg.submit(self.body)
        self.assertEqual(result['feedback']['by'], "Sel's rules")
        self.assertIn('could not be grounded', result['feedback']['model_status'])

    def test_grounded_model_tip(self):
        response = self.body['response']
        source = self.service.pack('demo-solar').docs[self.ref][:80]
        class Model:
            def complete_json(self, *args, **kwargs):
                return {'tip': 'source', 'response_quote': response, 'source_quote': source}
        self.service._chat_factory = lambda: Model()
        feedback = self.pg.submit(self.body)['feedback']
        self.assertEqual(feedback['by'], 'AI-selected coaching tip')
        self.assertEqual(feedback['source_quote'], source)

    def test_api_roundtrip(self):
        server = serve(self.service, port=0, quiet=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f'http://127.0.0.1:{server.server_address[1]}'
        try:
            request = Request(base + '/api/playground', json.dumps(self.body).encode(), {'Content-Type': 'application/json'})
            with urlopen(request) as r:
                self.assertEqual(r.status, 201)
            with urlopen(base + f'/api/playground?person={self.rep}&pack=demo-solar') as r:
                self.assertEqual(len(json.load(r)), 1)
        finally:
            server.shutdown()
            server.server_close()
