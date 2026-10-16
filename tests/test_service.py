import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from sel.adapters import FixtureAnalyzer, FixtureTranscriber
from sel.server import serve
from sel.service import CallService

ROOT = Path(__file__).resolve().parent.parent
POS = ROOT / "examples/calls/pos-01"


def fixture_factory(call, speakers):
    """Pretend models: the POS fixture transcript and analysis, whatever audio is uploaded."""
    return FixtureTranscriber(POS / "transcript.json"), FixtureAnalyzer(POS / "analysis.json"), None


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.service = CallService(cls.tmp.name, ROOT / "packs", analysis_factory=fixture_factory)
        cls.service.create_from_example(ROOT / "examples/calls/solar-01", "demo-solar")
        cls.service.create_from_example(ROOT / "examples/calls/pos-01", "demo-pos")
        cls.httpd = serve(cls.service, port=0, quiet=True)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.service.wait_idle()
        cls.httpd.shutdown()
        cls.tmp.cleanup()

    def req(self, method, path, body=None, raw=False):
        data = body if isinstance(body, bytes) or body is None else json.dumps(body).encode()
        r = urllib.request.Request(self.base + path, data=data, method=method)
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                content = resp.read()
                return resp.status, content if raw else json.loads(content)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_upload_runs_job_and_report_is_reviewable(self):
        status, body = self.req("POST", "/api/calls?pack=demo-pos&started_at=2026-10-08T11:30:00%2B05:45"
                                        "&filename=call.wav&customer_name=Sunita", b"RIFF....fake")
        self.assertEqual(status, 201)
        call_id = body["id"]
        for _ in range(100):
            _, call = self.req("GET", f"/api/calls/{call_id}")
            if call["status"] in ("done", "error"):
                break
            time.sleep(0.05)
        self.assertEqual(call["status"], "done", call.get("error"))
        self.assertEqual(len(call["report"]["drafts"]), 2)
        self.assertEqual(call["report"]["transcript"]["call_id"], call_id)
        status, audio = self.req("GET", f"/api/calls/{call_id}/audio", raw=True)
        self.assertEqual((status, audio), (200, b"RIFF....fake"))

    def test_upload_validation(self):
        status, body = self.req("POST", "/api/calls?pack=demo-pos&started_at=yesterday&filename=a.wav", b"x")
        self.assertEqual(status, 400)
        status, body = self.req("POST", "/api/calls?pack=demo-pos&started_at=2026-10-08T11:30:00%2B05:45&filename=a.exe", b"x")
        self.assertEqual(status, 400)
        status, body = self.req("POST", "/api/calls?pack=nope&started_at=2026-10-08T11:30:00%2B05:45&filename=a.wav", b"x")
        self.assertEqual(status, 404)

    def test_draft_needs_input_until_filled_then_approves(self):
        cid = "example-solar-01"
        status, body = self.req("POST", f"/api/calls/{cid}/drafts/draft-k3/approve")
        self.assertEqual(status, 409)
        status, draft = self.req("PUT", f"/api/calls/{cid}/drafts/draft-k3", {"fields": {"date": "2026-10-12", "time": "10:00"}})
        self.assertEqual(draft["status"], "ready_for_approval")
        status, draft = self.req("POST", f"/api/calls/{cid}/drafts/draft-k3/approve")
        self.assertEqual(draft["status"], "approved")
        status, ics = self.req("GET", f"/api/calls/{cid}/drafts/draft-k3/ics", raw=True)
        self.assertIn(b"DTSTART:20261012T041500Z", ics)
        # Editing again revokes the approval.
        status, draft = self.req("PUT", f"/api/calls/{cid}/drafts/draft-k3", {"fields": {"time": "10:30"}})
        self.assertEqual(draft["status"], "ready_for_approval")

    def test_fixture_calls_audio_and_retry(self):
        self.assertEqual(self.req("GET", "/api/calls/example-pos-01/audio")[0], 404)  # no recording
        status, head = self.req("GET", "/api/calls/example-solar-01/audio", raw=True)
        self.assertEqual((status, head[:4]), (200, b"OggS"))  # synthesized test recording (Opus)
        self.assertEqual(self.req("POST", "/api/calls/example-solar-01/retry")[0], 400)

    def test_audio_range_request(self):
        r = urllib.request.Request(self.base + "/api/calls/example-solar-01/audio", headers={"Range": "bytes=0-3"})
        with urllib.request.urlopen(r, timeout=10) as resp:
            self.assertEqual((resp.status, resp.read()), (206, b"OggS"))
            self.assertTrue(resp.headers["Content-Range"].startswith("bytes 0-3/"))

    def test_dismiss_and_unknown_routes(self):
        status, review = self.req("POST", "/api/calls/example-solar-01/items/f1/dismiss")
        self.assertIn("f1", review["dismissed"])
        status, review = self.req("POST", "/api/calls/example-solar-01/items/f1/restore")
        self.assertNotIn("f1", review["dismissed"])
        self.assertEqual(self.req("GET", "/api/calls/../../etc")[0], 404)
        self.assertEqual(self.req("GET", "/static/../server.py")[0], 404)

    def test_index_served(self):
        status, html = self.req("GET", "/", raw=True)
        self.assertEqual(status, 200)
        self.assertIn(b"/static/app.js", html)


if __name__ == "__main__":
    unittest.main()
