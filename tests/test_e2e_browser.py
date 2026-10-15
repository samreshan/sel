"""End-to-end tests in a real browser (Chromium through Playwright).

Skipped unless Playwright and its Chromium are installed::

    pip install playwright && playwright install chromium
    python -m unittest tests.test_e2e_browser

Covers the review screen (evidence, keyboard, transcript edits, optimistic
dismiss, drafts), uploads with live updates pushed over the event stream, and
a whole simulated call between two browser sessions: fake microphones,
WebRTC audio, typed lines, live score, hang-up, the stereo recording upload
and the filed call's next actions.
"""

import json
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

from sel.adapters import FixtureTranscriber
from sel.live import RuleAnalyzer
from sel.server import serve
from sel.service import CallService
from sel.simulator import SimulatorHub

ROOT = Path(__file__).resolve().parent.parent
SOLAR = ROOT / "examples/calls/solar-01"

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - optional dependency
    sync_playwright = None


def rules_factory(call, speakers):
    """No models: the solar transcript for any upload, and the rules analyzer."""
    return FixtureTranscriber(SOLAR / "transcript.json"), RuleAnalyzer(), None


class FakeSpeech:
    """Stands in for Whisper: every utterance window becomes one short line."""

    def transcribe_pcm(self, pcm, sample_rate=16000):
        return [(0.0, max(0.5, len(pcm) / 32000), "testing one two", 0.9)]


@unittest.skipIf(sync_playwright is None, "playwright is not installed")
class BrowserE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.service = CallService(cls.tmp.name, ROOT / "packs", analysis_factory=rules_factory)
        cls.service.create_from_example(SOLAR, "demo-solar")
        cls.service.create_from_example(ROOT / "examples/calls/pos-01", "demo-pos")
        cls.hub = SimulatorHub(cls.service, speech_factory=FakeSpeech, speech_status=lambda: (True, None))
        cls.audio_chunks = 0
        add_audio = cls.hub.add_audio

        def counting(*a, **k):
            cls.audio_chunks += 1
            return add_audio(*a, **k)

        cls.hub.add_audio = counting
        cls.httpd = serve(cls.service, port=0, quiet=True, hub=cls.hub)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        try:
            cls.pw = sync_playwright().start()
            cls.browser = cls.pw.chromium.launch(args=[
                "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
                "--autoplay-policy=no-user-gesture-required"])
        except Exception as e:  # noqa: BLE001 - no browser binary
            cls.httpd.shutdown()
            raise unittest.SkipTest(f"Chromium is not available: {e}") from e

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.service.bus.close()
        cls.httpd.shutdown()
        cls.tmp.cleanup()

    def page(self):
        ctx = self.browser.new_context()
        page = ctx.new_page()
        page.errors = []
        page.on("pageerror", lambda e: page.errors.append(str(e)))
        page.on("console", lambda m: m.type == "error" and page.errors.append(m.text))
        self.addCleanup(ctx.close)
        return page

    def call_json(self, call_id):
        import urllib.request
        with urllib.request.urlopen(f"{self.base}/api/calls/{call_id}", timeout=10) as r:
            return json.loads(r.read())

    # -- review screen -------------------------------------------------------

    def test_review_flows(self):
        page = self.page()
        page.goto(f"{self.base}/#call/example-solar-01")
        page.wait_for_function("document.querySelectorAll('.transcript .seg').length === 15")
        self.assertEqual(page.locator(".scorecard .score-row").count(), 4)

        page.locator(".panel .ref-btn").first.click()
        page.wait_for_selector(".transcript .seg.is-evidence")

        page.locator("body").click(position={"x": 5, "y": 5})
        page.keyboard.press("2")
        page.wait_for_selector("#tab-claims[aria-selected='true']")
        page.keyboard.press("3")
        page.wait_for_selector("#tab-followups[aria-selected='true']")
        page.keyboard.press("1")
        page.keyboard.press("j")
        page.wait_for_selector(".panel .card.is-focused")

        row = page.locator('.seg[data-id="s04"]')
        row.hover()
        row.locator(".edit-btn").click()
        page.fill(".edit-text", "Ali mahango bhayo. Arko company le 3 lakh 60 ma dinchu bhaneko thiyo, dai.")
        page.keyboard.press("Control+Enter")
        page.wait_for_selector(".notice:not([hidden])")
        self.assertIn("dai.", page.locator('.seg[data-id="s04"] .text').inner_text())
        self.assertEqual(page.locator(".transcript .seg").count(), 15)

        dismiss = page.locator(".panel .finding button", has_text="Dismiss").first
        item = dismiss.locator("xpath=ancestor::article").get_attribute("data-item")
        dismiss.click()
        page.wait_for_selector(f'.panel .finding.is-dismissed[data-item="{item}"]')
        for _ in range(50):
            if item in self.call_json("example-solar-01")["review"]["dismissed"]:
                break
            time.sleep(0.05)
        self.assertIn(item, self.call_json("example-solar-01")["review"]["dismissed"])

        page.click("#tab-followups")
        draft = page.locator("form.draft").filter(has=page.locator("#d-draft-k3-date"))
        draft.locator("#d-draft-k3-date").fill("2026-10-12")
        draft.locator("#d-draft-k3-time").fill("10:00")
        draft.locator("button[type=submit]").click()
        page.wait_for_function("document.querySelector('#d-draft-k3-date') && !document.querySelector('#d-draft-k3-date').closest('.field').classList.contains('is-missing')")
        page.locator("form.draft").filter(has=page.locator("#d-draft-k3-date")).locator(".approve-btn").click()
        page.wait_for_selector("text=Approved. Sel does not send it; you do.")
        drafts = {d["id"]: d["status"] for d in self.call_json("example-solar-01")["report"]["drafts"]}
        self.assertEqual(drafts["draft-k3"], "approved")
        self.assertEqual(page.errors, [])

    def test_upload_updates_arrive_without_polling(self):
        page = self.page()
        requests = []
        page.on("request", lambda r: requests.append(r.url))
        page.goto(f"{self.base}/#new")
        page.wait_for_selector("#upload-form")
        page.set_input_files("#f-audio", str(SOLAR / "audio.ogg"))
        page.select_option("#f-pack", "demo-solar")
        page.fill("#f-cust", "Ramesh")
        page.click("#upload-submit")
        page.wait_for_url("**/#call/2*")
        page.wait_for_function("document.querySelectorAll('.transcript .seg').length === 15", timeout=20000)
        self.assertIn("rules (no language model)", page.locator(".call-head").inner_text())
        # The list and the call view were updated by pushed events, not by polling the list.
        self.assertLessEqual(sum(1 for u in requests if u.endswith("/api/calls")), 1)
        self.assertEqual(page.errors, [])

    # -- call simulator ---------------------------------------------------------

    def test_two_sessions_call_scored_recorded_and_filed(self):
        rep, cust = self.page(), self.page()
        rep.goto(f"{self.base}/#sim")
        rep.fill("#sim-name", "Asha")
        rep.select_option("#sim-pack", "demo-solar")
        rep.click("text=Open a call")
        rep.wait_for_url("**/#sim/*")
        code = rep.url.rsplit("/", 1)[1]

        cust.goto(f"{self.base}/#sim")
        cust.fill("#sim-name", "Ramesh")
        cust.fill("#sim-email", "ramesh@example.com")
        cust.locator(".sim-room-item", has_text=code).locator("button").click()
        cust.wait_for_url(f"**/#sim/{code}")

        for p in (rep, cust):
            p.click("text=Turn on microphone")
            p.wait_for_selector("text=Mute")
        rep.click("text=Start the call")
        for p in (rep, cust):
            p.wait_for_selector(".sim-head .pill-bad:has-text('Live')")
            p.wait_for_selector("text=Audio link: connected", timeout=15000)

        script = [
            (rep, "Hamro 3 kW grid-tie system installation sahit 4 lakh 20 hajar parcha."),
            (cust, "Ali mahango bhayo. Arko company le 3 lakh 60 ma dinchu bhaneko thiyo."),
            (rep, "Hamro panel ma 25 years performance warranty cha, ani inverter ma pani 10 years warranty cha."),
            (cust, "EMI ko option cha?"),
            (rep, "Huncha, ma bholi beluka samma quotation ra EMI details email garchu."),
            (rep, "Sometime next week hamro engineer pathaula."),
        ]
        for who, text in script:
            who.fill("#sim-say", text)
            who.press("#sim-say", "Enter")
            who.wait_for_selector(f".sim-line .text:has-text({json.dumps(text[:30])})")
        for p in (rep, cust):
            p.wait_for_function(f"document.querySelectorAll('.sim-line').length >= {len(script)}")
            p.wait_for_selector(".sim-feed-item:has-text('Objection')")
            p.wait_for_selector(".sim-action")
        self.assertIsNotNone(rep.locator(".gauge-num").inner_text())

        rep.click("text=Hang up")
        cust.wait_for_selector(".sim-head :text('Ended'), .sim-head :text('Saving'), .sim-head :text('Saved')")
        for p in (rep, cust):
            p.wait_for_selector("text=Done by Sel", timeout=30000)
            p.wait_for_selector(".next-item:has-text('Email')")
        self.assertGreater(self.audio_chunks, 0, "no microphone audio reached the server")

        room_call = self.hub.rooms[code].call_id
        view = self.call_json(room_call)
        self.assertEqual(view["source"], "simulator")
        self.assertEqual(view["status"], "done")
        self.assertTrue(view["audio_file"], "the rep's recording was not saved")
        drafts = view["report"]["drafts"]
        email = next(d for d in drafts if d["kind"] == "email")
        self.assertEqual(email["fields"]["to"], "ramesh@example.com")
        self.assertEqual(email["status"], "ready_for_approval")
        self.assertTrue(any(d["status"] == "needs_input" for d in drafts if d["kind"] == "calendar"))
        if shutil.which("ffprobe"):
            out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
                                  "stream=channels", "-of", "json", str(self.service.audio_path(room_call))],
                                 capture_output=True, text=True, check=True).stdout
            self.assertEqual(json.loads(out)["streams"][0]["channels"], 2, "the recording should be stereo")

        rep.click("text=Open the full review")
        rep.wait_for_function("document.querySelectorAll('.transcript .seg').length >= 6")
        rep.wait_for_selector(".call-head :text('Recorded in the call simulator')")
        self.assertEqual(rep.errors, [])
        self.assertEqual(cust.errors, [])


if __name__ == "__main__":
    unittest.main()
