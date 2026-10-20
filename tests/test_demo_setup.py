"""demo-setup against a real server: rules instead of models, the solar-01 transcript instead of Whisper."""

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from sel.adapters import FixtureTranscriber
from sel.demo_setup import NEXT_FILE, SetupError, next_transcript, run
from sel.live import RuleAnalyzer
from sel.server import serve
from sel.service import CallService

ROOT = Path(__file__).resolve().parent.parent
SOLAR = ROOT / "examples/calls/solar-01"

HEALTHY = {"ok": True, "problems": [], "provider": "local", "speech": {"installed": True},
           "llm": {"model": "qwen2.5:7b", "licence": "Apache-2.0", "runs_on": "this machine", "health": {"ok": True}},
           "chat": {"model": "qwen2.5:1.5b", "licence": "Apache-2.0", "runs_on": "this machine", "health": {"ok": True}}}


class DemoSetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.next_json = Path(self.tmp.name) / "next.json"
        self.next_json.write_text(json.dumps(next_transcript()), encoding="utf-8")

        def factory(call, speakers):
            # The follow-up call is the variant where the rep asks before defending the price.
            source = self.next_json if call.get("original_filename") == NEXT_FILE else SOLAR / "transcript.json"
            return FixtureTranscriber(source), RuleAnalyzer(), None

        self.doctor = dict(HEALTHY)
        self.service = CallService(Path(self.tmp.name) / "calls", ROOT / "packs", analysis_factory=factory)
        self.httpd = serve(self.service, port=0, quiet=True, doctor=lambda: self.doctor)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.audio = Path(self.tmp.name) / "next.wav"
        self.audio.write_bytes(b"RIFF....fake")
        self.said = []

    def tearDown(self):
        self.httpd.shutdown()
        self.service.wait_idle()
        self.service.bus.close()
        self.tmp.cleanup()

    def go(self, **kw):
        return run(self.url, say=self.said.append, sleep=lambda s: time.sleep(0.05), out_dir=Path(self.tmp.name),
                   **kw)

    def test_sets_up_the_team_the_first_call_and_the_follow_up(self):
        out = self.go(next_audio=self.audio)
        self.assertEqual((out["rep"]["role"], out["manager"]["role"]), ("rep", "manager"))
        first = self.service.view(out["first_call"])
        self.assertEqual((first["rep_id"], first["lead_id"]), (out["rep"]["id"], out["lead"]["id"]))
        self.assertEqual(first["status"], "done")
        self.assertEqual(out["focus"]["criterion"], "objection-handling")
        self.assertTrue(out["next_call"])
        # The follow-up is coached after the focus was set, so its transfer check runs and passes.
        deadline = time.monotonic() + 20
        transfer = []
        while not transfer and time.monotonic() < deadline:
            self.service.wait_idle()
            transfer = self.service.coach_view(out["rep"]["id"])["transfer"]
            time.sleep(0.05)
        self.assertTrue(transfer, self.said)
        self.assertEqual(transfer[-1]["call_id"], out["next_call"])
        self.assertEqual(transfer[-1]["verdict"], "pass")

    def test_running_again_reuses_everything(self):
        first = self.go()
        again = self.go()
        self.assertEqual(again["first_call"], first["first_call"])
        self.assertEqual(len(self.service.people.list()), 2)
        self.assertEqual(len([c for c in self.service.list() if c.get("source") == "audio"]), 1)
        self.assertIn("First call: already filed", " ".join(self.said))

    def test_stops_on_a_proprietary_service_or_missing_models(self):
        self.doctor = {**HEALTHY, "ok": False, "problems": ["https://api.openai.com/v1 is a proprietary model service"]}
        with self.assertRaisesRegex(SetupError, "proprietary"):
            self.go()

    def test_missing_models_need_allow_rules(self):
        self.doctor = {**HEALTHY, "chat": {**HEALTHY["chat"], "health": {"ok": False, "error": "connection refused"}}}
        with self.assertRaisesRegex(SetupError, "allow-rules"):
            self.go()
        self.assertEqual(self.service.people.list(), [])  # nothing was changed
        self.go(allow_rules=True)
        self.assertIn("Continuing on Sel's rules", " ".join(self.said))

    def test_no_server(self):
        with self.assertRaisesRegex(SetupError, "python -m sel serve"):
            run("http://127.0.0.1:9", say=self.said.append)


if __name__ == "__main__":
    unittest.main()
