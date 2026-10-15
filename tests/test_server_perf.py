"""The server's caching, keep-alive, streaming and event-stream behaviour."""

import gzip
import http.client
import json
import re
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path

from sel.adapters import FixtureAnalyzer, FixtureTranscriber
from sel.server import serve
from sel.service import CallService

ROOT = Path(__file__).resolve().parent.parent
POS = ROOT / "examples/calls/pos-01"


def fixture_factory(call, speakers):
    return FixtureTranscriber(POS / "transcript.json"), FixtureAnalyzer(POS / "analysis.json"), None


class ServerPerfTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.service = CallService(cls.tmp.name, ROOT / "packs", analysis_factory=fixture_factory)
        cls.service.create_from_example(ROOT / "examples/calls/solar-01", "demo-solar")
        cls.health_calls = 0

        def health():
            cls.health_calls += 1
            return {"llm": {"ok": False}, "asr": {"ok": False}}

        cls.httpd = serve(cls.service, port=0, quiet=True, health=health)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.service.bus.close()
        cls.httpd.shutdown()
        cls.tmp.cleanup()

    def conn(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        self.addCleanup(c.close)
        return c

    def get(self, path, headers=None, conn=None):
        c = conn or self.conn()
        c.request("GET", path, headers=headers or {})
        r = c.getresponse()
        return r, r.read()

    def test_static_assets_are_pinned_by_hash_and_cached_forever(self):
        r, html = self.get("/")
        self.assertEqual(r.getheader("Cache-Control"), "no-cache")
        url = re.search(rb'/static/app\.js\?v=([0-9a-f]+)"', html)
        self.assertIsNotNone(url, "index.html should pin app.js to its content hash")
        r, body = self.get(f"/static/app.js?v={url.group(1).decode()}")
        self.assertIn("immutable", r.getheader("Cache-Control"))
        r2, body2 = self.get("/static/app.js?v=stale")
        self.assertEqual(r2.getheader("Cache-Control"), "no-cache")
        r3, body3 = self.get("/static/app.js", {"If-None-Match": r.getheader("ETag")})
        self.assertEqual((r3.status, body3), (304, b""))
        r, again = self.get("/", {"If-None-Match": self.get("/")[0].getheader("ETag")})
        self.assertEqual(r.status, 304)

    def test_gzip(self):
        r, raw = self.get("/static/app.js")
        r, gz = self.get("/static/app.js", {"Accept-Encoding": "gzip"})
        self.assertEqual(r.getheader("Content-Encoding"), "gzip")
        self.assertEqual(gzip.decompress(gz), raw)
        self.assertLess(len(gz), len(raw) / 2)
        r, body = self.get("/api/calls/example-solar-01", {"Accept-Encoding": "gzip"})
        self.assertEqual(json.loads(gzip.decompress(body))["id"], "example-solar-01")

    def test_call_view_revalidates_with_etag(self):
        r, body = self.get("/api/calls/example-solar-01")
        etag = r.getheader("ETag")
        self.assertTrue(etag)
        r, body = self.get("/api/calls/example-solar-01", {"If-None-Match": etag})
        self.assertEqual((r.status, body), (304, b""))
        c = self.conn()
        c.request("POST", "/api/calls/example-solar-01/items/f2/dismiss")
        c.getresponse().read()
        r, body = self.get("/api/calls/example-solar-01", {"If-None-Match": etag})
        self.assertEqual(r.status, 200)
        self.assertNotEqual(r.getheader("ETag"), etag)

    def test_keep_alive_and_unread_bodies(self):
        c = self.conn()
        for _ in range(3):  # one connection, several requests
            r, _ = self.get("/api/packs", conn=c)
            self.assertEqual(r.status, 200)
        # Refused before the body is read: the server says it will close, so the body bytes
        # can never be read as the next request.
        c.request("POST", "/api/calls", body=b"x" * 5000)
        r = c.getresponse()
        r.read()
        self.assertEqual((r.status, r.getheader("Connection")), (400, "close"))
        r, _ = self.get("/api/packs")
        self.assertEqual(r.status, 200)

    def test_upload_streams_to_disk(self):
        payload = bytes(range(256)) * 12000  # 3 MB
        c = self.conn()
        c.request("POST", "/api/calls?pack=demo-pos&started_at=2026-10-08T11:30:00%2B05:45&filename=big.wav", body=payload)
        r = c.getresponse()
        created = json.loads(r.read())
        self.assertEqual(r.status, 201)
        self.assertEqual(self.service.audio_path(created["id"]).read_bytes(), payload)
        self.assertEqual(list((Path(self.tmp.name) / ".incoming").iterdir()), [])
        r, body = self.get(f"/api/calls/{created['id']}/audio")
        self.assertEqual(body, payload)
        r, body = self.get(f"/api/calls/{created['id']}/audio", {"If-None-Match": r.getheader("ETag")})
        self.assertEqual(r.status, 304)

    def test_event_stream(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("GET", "/api/events?tab=tab-a")
        r = c.getresponse()
        self.assertEqual(r.getheader("Content-Type"), "text/event-stream; charset=utf-8")
        events = []

        def read():
            ev = None
            try:
                for raw in r:
                    line = raw.decode().rstrip("\n")
                    if line.startswith("event: "):
                        ev = line[7:]
                    elif line.startswith("data: "):
                        events.append((ev, json.loads(line[6:])))
            except (OSError, ValueError):
                pass

        threading.Thread(target=read, daemon=True).start()
        self.wait_for(lambda: any(e == "hello" for e, _ in events))
        hello = next(d for e, d in events if e == "hello")
        self.assertIn("example-solar-01", [x["id"] for x in hello["calls"]])
        # A change made by another tab is pushed; one made by this tab is not echoed back.
        for tab in ("tab-a", "tab-b"):
            k = self.conn()
            k.request("POST", "/api/calls/example-solar-01/items/f3/dismiss", headers={"X-Sel-Tab": tab})
            k.getresponse().read()
        self.wait_for(lambda: any(e == "call-changed" for e, _ in events))
        time.sleep(0.1)
        self.assertEqual(sum(1 for e, _ in events if e == "call-changed"), 1)
        k = self.conn()
        k.request("POST", "/api/calls?pack=demo-pos&started_at=2026-10-08T11:30:00%2B05:45&filename=a.wav", body=b"RIFF")
        new_id = json.loads(k.getresponse().read())["id"]
        self.wait_for(lambda: any(e == "call" and d["id"] == new_id and d["status"] == "done" for e, d in events))
        c.close()

    def test_health_is_cached(self):
        before = self.health_calls
        for _ in range(3):
            self.get("/api/health")
        self.assertLessEqual(self.health_calls - before, 1)

    def wait_for(self, cond, timeout=5.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if cond():
                return
            time.sleep(0.02)
        self.fail("timed out")


class PackCacheTests(unittest.TestCase):
    def test_packs_reload_only_when_files_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            packs = Path(tmp) / "packs"
            shutil.copytree(ROOT / "packs", packs)
            service = CallService(Path(tmp) / "calls", packs)
            first = service.packs()
            self.assertIs(service.packs(), first)
            manifest = packs / "demo-pos/pack.toml"
            manifest.write_text(manifest.read_text().replace('name = "Thali POS (fictional)"', 'name = "Thali POS 2"'))
            self.assertEqual(service.pack("demo-pos").name, "Thali POS 2")


if __name__ == "__main__":
    unittest.main()
