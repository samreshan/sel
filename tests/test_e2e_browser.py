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
        cls.service.wait_idle()
        cls.service.bus.close()
        cls.httpd.shutdown()
        cls.tmp.cleanup()

    def page(self):
        ctx = self.browser.new_context()
        page = ctx.new_page()
        page.errors = []
        page.on("pageerror", lambda e: page.errors.append(str(e)))
        page.on("console", lambda m: m.type == "error" and page.errors.append(m.text))
        page.on("response", lambda r: r.status >= 400 and page.errors.append(f"{r.status} {r.request.method} {r.url}"))
        self.addCleanup(ctx.close)
        return page

    def call_json(self, call_id):
        import urllib.request
        with urllib.request.urlopen(f"{self.base}/api/calls/{call_id}", timeout=10) as r:
            return json.loads(r.read())

    # -- people, Today and Playbook ------------------------------------------------

    def test_calls_page_lists_all_calls(self):
        page = self.page()
        page.goto(f"{self.base}/#calls")
        page.wait_for_selector(".calls-table tbody tr")
        self.assertEqual(page.locator(".sidebar #call-list").count(), 0)
        self.assertEqual(page.locator('.primary-nav [data-nav="practice"]').count(), 0)
        expected = len(json.loads(self.get("/api/calls")))
        self.assertEqual(page.locator(".calls-table tbody tr").count(), expected)
        self.assertEqual(page.locator('.primary-nav [data-nav="calls"]').get_attribute("aria-current"), "page")
        page.locator(".calls-table tbody tr").first.locator("td").last.click()
        page.wait_for_selector(".transcript")
        page.locator('.primary-nav [data-nav="calls"]').click()
        page.wait_for_selector(".calls-table tbody tr")
        page.locator(".calls-table-link").first.focus()
        page.keyboard.press("Enter")
        page.wait_for_selector(".transcript")
        page.locator('.primary-nav [data-nav="calls"]').click()
        page.wait_for_selector(".calls-table tbody tr")
        page.locator('label[for="calls-view-card"]').click()
        self.assertEqual(page.locator(".calls-page-list .call-row").count(), expected)
        page.reload()
        page.wait_for_selector(".calls-page-list .call-row")
        self.assertTrue(page.locator("#calls-view-card").is_checked())
        page.locator(".calls-page-list .call-link").first.click()
        page.wait_for_selector(".transcript")
        page.locator('.primary-nav [data-nav="calls"]').click()
        page.locator('label[for="calls-view-table"]').click()
        self.assertEqual(page.locator(".calls-table tbody tr").count(), expected)
        self.assertEqual(page.errors, [])

    def test_today_follows_the_viewer(self):
        page = self.page()
        page.goto(f"{self.base}/")
        # No one chosen yet: Today asks who this is (or to add someone first).
        page.wait_for_selector(".welcome h1")
        page.goto(f"{self.base}/#people")
        for name, role in (("Asha E2E", "rep"), ("Sita E2E", "manager")):
            page.fill("#p-name", name)
            page.select_option("#p-role", role)
            page.click(".person-form button[type=submit]")
            page.wait_for_function(f"() => [...document.querySelectorAll('.person-row input')].some(i => i.value === {json.dumps(name)})")
        asha = next(p for p in json.loads(self.get("/api/people")) if p["name"] == "Asha E2E")
        page.select_option("#viewer", asha["id"])

        # Assign the solar example to Asha from the call page; it becomes her call.
        page.goto(f"{self.base}/#call/example-solar-01")
        page.select_option("#call-rep", asha["id"])
        page.wait_for_selector(".rep-picker :text('Saved.')")
        page.goto(f"{self.base}/#today")
        page.wait_for_selector("text=Today, Asha")
        page.wait_for_selector(".skill .chip-link")  # skills she fell short on, linked to the call
        page.wait_for_selector(".page-row:has-text('Needs input')")

        # A manager sees the team, and calls without a rep.
        sita = next(p for p in json.loads(self.get("/api/people")) if p["name"] == "Sita E2E")
        page.select_option("#viewer", sita["id"])
        page.wait_for_selector("text=Your team today")
        # The per-rep table lives on the Team page (managers only), not on Today.
        page.click("a[data-nav=team]")
        page.wait_for_selector(".team-table :text('Asha E2E')")

        page.goto(f"{self.base}/#playbook/demo-solar")
        page.get_by_role("button", name="Library", exact=True).click()
        page.wait_for_selector("text=Approved facts")
        page.locator(".doc-row button").first.click()
        page.wait_for_selector(".doc-row .doc-text")
        self.assertEqual(page.errors, [])

    def test_coaching_moments_lines_and_focus(self):
        page = self.page()
        page.goto(f"{self.base}/#call/example-solar-01")
        page.wait_for_selector(".moment")
        card = page.locator(".moment", has_text="Only approved product claims").first
        # The accepted line keeps the Care Plan condition; the 10-year line is listed as refused, with the reason.
        self.assertIn("Care Plan", card.locator(".better-line-text").inner_text())
        card.locator("summary").click()
        refused = card.locator(".refused-lines li").first
        self.assertIn("10-year", refused.inner_text())
        self.assertIn("Care Plan", refused.locator(".reasons").inner_text())
        # A person's line goes through the same check.
        card.locator("[data-action=edit]").click()
        page.fill(".moment-form textarea", "The inverter has a 10-year warranty.")
        page.keyboard.press("Tab")
        card.locator(".moment-form button[type=submit]").click()
        page.wait_for_selector(".moment .form-status.is-error")
        self.assertIn("Refused", card.locator(".better-line .better-line-head").inner_text())
        # Keyboard: J moves between cards, including moments.
        page.locator("body").click(position={"x": 5, "y": 5})
        page.keyboard.press("j")
        page.wait_for_selector(".panel .card.is-focused")

        # Assigning the call to a rep gives them a focus; Today shows it.
        person = json.loads(self.post("/api/people", {"name": "Focus E2E", "role": "rep"}))
        page.evaluate(f"localStorage.setItem('sel.viewer', JSON.stringify('{person['id']}'))")
        page.goto(f"{self.base}/#call/example-pos-01")
        page.reload()
        page.select_option("#call-rep", person["id"])
        page.wait_for_selector(".rep-picker :text('Saved.')")
        page.goto(f"{self.base}/#today")
        page.wait_for_selector(".focus-card .focus-title", timeout=15000)
        self.assertEqual(page.errors, [])

    def test_drill_with_the_ai_customer_and_debrief(self):
        person = json.loads(self.post("/api/people", {"name": "Drill E2E", "role": "rep"}))
        self.post(f"/api/calls/example-solar-01/rep", {"rep_id": person["id"]}, method="PUT")
        for _ in range(200):  # the Coach sets a focus and makes a drill for it
            drills = json.loads(self.get(f"/api/drills?person={person['id']}"))
            if drills and drills[0]["status"] == "ready":
                break
            time.sleep(0.05)
        page = self.page()
        page.add_init_script(f"localStorage.setItem('sel.viewer', JSON.stringify('{person['id']}'))")
        page.goto(f"{self.base}/#practice")
        page.locator(".page-row-link").first.click()
        page.click("text=Start the drill")
        page.click("text=Start the call")
        page.wait_for_selector(".sim-line.role-customer .lang:text('AI')")  # the AI customer opens, labelled
        page.fill("#drill-say", "What does the other quote include: which panels and inverter?")
        page.press("#drill-say", "Enter")
        page.wait_for_function("document.querySelectorAll('.sim-line.role-customer').length >= 2")
        page.click("text=End and get feedback")
        page.wait_for_selector(".debrief :text('Passed')", timeout=15000)
        self.assertIn("What does the other quote include", page.locator(".debrief .compare").inner_text())
        self.assertEqual(page.errors, [])

    def test_drill_recovers_a_line_missed_while_the_stream_reconnects(self):
        """A line published while the tab's event stream is down comes back with the reconnect snapshot."""
        from sel.events import _end

        person = json.loads(self.post("/api/people", {"name": "Reconnect E2E", "role": "rep"}))
        self.post(f"/api/calls/example-solar-01/rep", {"rep_id": person["id"]}, method="PUT")
        for _ in range(200):
            drills = json.loads(self.get(f"/api/drills?person={person['id']}"))
            if drills and drills[0]["status"] == "ready":
                break
            time.sleep(0.05)
        page = self.page()
        page.add_init_script(f"localStorage.setItem('sel.viewer', JSON.stringify('{person['id']}'))")
        page.goto(f"{self.base}/#practice")
        page.locator(".page-row-link").first.click()
        page.click("text=Start the drill")
        page.click("text=Start the call")
        page.wait_for_selector(".sim-line.role-customer .lang:text('AI')")
        room = [r for r in self.hub.rooms.values() if r.drill_id and r.state == "live"][-1]
        bus = self.service.bus
        with bus._lock:  # end this tab's stream, as the bus does to a subscriber that falls behind
            subs = [x for x in bus._subs if f"room:{room.code}" in x.topics]
            for x in subs:
                bus._subs.discard(x)
        for x in subs:
            _end(x)
        with room.lock:  # published while the browser waits to reconnect (about 1.5 s)
            now = self.hub._elapsed(room)
            self.hub._add(room, "customer", now, now + 1, "Said while your connection was down.", "ai")
            self.hub._broadcast(room)
        page.wait_for_selector(".sim-line .text:has-text('while your connection was down')", timeout=15000)
        self.assertEqual(page.errors, [])

    def test_flywheel_approve_then_prep_then_rehearse(self):
        """A manager approves a proposed team answer; the lead's Prep shows it; Rehearse opens a drill."""
        svc = self.service
        manager = svc.add_person("Flywheel Manager", "manager")["id"]
        rep = svc.add_person("Flywheel Rep", "rep")["id"]
        svc.set_rep("example-solar-01", rep)
        svc.wait_idle()
        lead = svc.add_lead("Flywheel Lead", None, rep)["id"]
        svc.set_lead("example-solar-01", lead)
        weak = next(m for m in svc.coaching("example-solar-01")["moments"]
                    if m["criterion"] == "objection-handling" and m["kind"] == "weak")
        svc.moment_action("example-solar-01", weak["id"], "endorse", {"by": rep})
        svc.run_librarian("demo-solar")
        answer = weak["better_line"]["text"]

        boss = self.page()
        boss.add_init_script(f"localStorage.setItem('sel.viewer', JSON.stringify('{manager}'))")
        boss.goto(f"{self.base}/#playbook/demo-solar")
        boss.get_by_role("button", name="Library", exact=True).click()
        boss.wait_for_selector("a[data-nav=team]:visible")  # Team is in the navigation for a manager
        item = boss.locator(".skill", has_text=answer).filter(has=boss.locator("text=Approve for the team"))
        item.locator("text=Approve for the team").click()
        boss.wait_for_selector("text=Approved for the team.")
        boss.wait_for_function("(t) => [...document.querySelectorAll('.playbook-side .skill')].some(e => e.textContent.includes(t))", arg=answer[:40])
        self.assertEqual(boss.errors, [])

        page = self.page()
        page.add_init_script(f"localStorage.setItem('sel.viewer', JSON.stringify('{rep}'))")
        page.goto(f"{self.base}/#prep/{lead}")
        page.wait_for_selector("text=Likely objections")
        self.assertFalse(page.locator("a[data-nav=team]").is_visible())
        objection = page.locator(".skill").first
        self.assertIn("Team answer", objection.inner_text())
        self.assertIn(answer[:40], objection.inner_text())
        page.click("text=Rehearse this call")
        page.wait_for_url("**/#practice/*")
        page.wait_for_selector("text=Start the drill", timeout=15000)
        self.assertEqual(page.errors, [])

    def post(self, path, body, method="POST"):
        import urllib.request
        r = urllib.request.Request(f"{self.base}{path}", data=json.dumps(body).encode(), method=method,
                                   headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.read()

    def get(self, path):
        import urllib.request
        with urllib.request.urlopen(f"{self.base}{path}", timeout=10) as r:
            return r.read()

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
        page.click("text=More about this call")  # names live in a disclosure
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
        # Waiting: the empty tile is the invite; there is no transcript to show yet.
        self.assertEqual(rep.inner_text(".person.is-invite .invite-code"), code)
        self.assertTrue(rep.is_hidden(".sim-transcript"))

        cust.goto(f"{self.base}/#sim")
        cust.fill("#sim-name", "Ramesh")
        cust.fill("#sim-email", "ramesh@example.com")
        cust.locator(".sim-room-item", has_text=code).locator("button").click()
        cust.wait_for_url(f"**/#sim/{code}")

        for p in (rep, cust):
            p.click("text=Turn on microphone")
            p.wait_for_selector("text=Mute")
        rep.wait_for_selector(".sim-ready:has-text('Ramesh is here')")
        self.assertEqual(rep.locator(".person.is-invite").count(), 0)
        rep.click("text=Start the call")
        for p in (rep, cust):
            p.wait_for_selector(".sim-head .pill-bad:has-text('Live')")
            p.wait_for_selector(".sim-transcript", state="visible")
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
