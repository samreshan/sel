"""Playground objection drills over the API, without models: the pack's objections and those heard on calls,
practised with the AI customer, debriefed, and kept out of the Coach."""

import json
import tempfile
import threading
import unittest
from pathlib import Path

from sel.server import serve
from sel.service import CallService
from sel.simulator import SimulatorHub
from tests import test_practice as practice  # the module, so its test class doesn't run twice

ROOT = Path(__file__).resolve().parent.parent


class PlaygroundDrillTests(unittest.TestCase):
    req = practice.PracticeLoopTests.req
    until = practice.PracticeLoopTests.until
    say = practice.PracticeLoopTests.say
    finish = practice.PracticeLoopTests.finish

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.service = CallService(self.tmp.name, ROOT / "packs", analysis_factory=practice.fixture_factory)
        self.service.create_from_example(practice.SOLAR, "demo-solar")
        self.hub = SimulatorHub(self.service, speech_status=lambda: (False, "off"))
        self.httpd = serve(self.service, port=0, quiet=True, hub=self.hub)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.rep = self.service.add_person("Playground Asha", "rep")["id"]

    def tearDown(self):
        self.service.wait_idle()
        self.httpd.shutdown()
        self.hub.close()
        self.service.bus.close()
        self.tmp.cleanup()

    def objections(self, person=None):
        status, data = self.req("GET", f"/api/playground/objections?pack=demo-solar{f'&person={person}' if person else ''}")
        self.assertEqual(status, 200, data)
        return data

    def start(self, **source):
        status, d = self.req("POST", "/api/playground/objections", {"person_id": self.rep, "pack_id": "demo-solar", **source})
        self.assertEqual(status, 201, d)
        self.assertEqual(d["origin"], "playground")
        return self.until(lambda: (x := self.req("GET", f"/api/drills/{d['id']}")[1])["status"] == "ready" and x)

    def room(self, drill_id):
        status, out = self.req("POST", f"/api/drills/{drill_id}/start", {"by": self.rep})
        self.assertEqual(status, 201, out)
        code, client = out["room"]["code"], out["client"]
        status, room = self.req("POST", f"/api/sim/rooms/{code}/start", {"client": client})
        return code, client, room

    def test_lists_the_packs_objections_and_those_heard_on_calls(self):
        data = self.objections()
        self.assertEqual([o["id"] for o in data["pack"]], ["competitor-cheaper", "inverter-worry", "wait-for-subsidy"])
        self.assertEqual(data["pack"][2]["criterion"], "discovery")
        self.assertIsNone(data["pack"][0]["drill"])
        # Heard: objections on reviewed real calls of the pack (the example call is one), anonymised.
        heard = data["heard"]
        self.assertEqual(len(heard), 1)
        self.assertIn("a different price", heard[0]["says"])  # anonymised: the competitor's NPR 3,60,000 is gone
        self.assertNotIn("3,60,000", json.dumps(heard))
        self.assertEqual(self.req("GET", "/api/playground/objections")[0], 400)
        self.assertEqual(self.req("GET", "/api/playground/objections?pack=nope")[0], 404)

    def test_a_pack_objection_drill_runs_and_is_debriefed_with_the_hint(self):
        drill = self.start(objection_id="competitor-cheaper")
        self.assertEqual(drill["criterion"], "objection-handling")
        self.assertEqual(drill["persona"]["opening"], "Another company quoted me much less for the same size. Why are you so expensive?")
        self.assertIsNone(drill["moment"]["quote"])  # no real answer to compare
        again = self.start(objection_id="competitor-cheaper")
        self.assertEqual(again["id"], drill["id"])  # practising again reuses the drill
        self.assertEqual(self.objections(self.rep)["pack"][0]["drill"]["id"], drill["id"])

        code, client, room = self.room(drill["id"])
        self.assertEqual(room["lines"][0]["text"], drill["persona"]["opening"])  # the AI customer raises it
        self.say(code, client, "What does the other quote include: which panels and inverter, and what warranty?")
        d = self.finish(code, client, drill["id"], 1)
        debrief = d["attempts"][0]["debrief"]
        self.assertEqual(debrief["verdict"], "pass")
        self.assertIsNone(debrief["real_answer"])
        self.assertIn("Ask what the other quote includes", debrief["hint"])

    def test_an_objection_without_cue_words_is_still_judged(self):
        """"Inverters break all the time…" has no cue word; in its drill the opening is the objection anyway."""
        drill = self.start(objection_id="inverter-worry")
        code, client, _ = self.room(drill["id"])
        self.say(code, client, "Our inverter has a 5-year manufacturer warranty, and the panels 25 years.")
        failed = self.finish(code, client, drill["id"], 1)["attempts"][0]["debrief"]
        self.assertEqual(failed["verdict"], "fail")  # defended before asking
        code, client, _ = self.room(drill["id"])
        self.say(code, client, "I understand. What worries you most: the repair cost, or being without power?")
        passed = self.finish(code, client, drill["id"], 2)["attempts"][1]["debrief"]
        self.assertEqual(passed["verdict"], "pass")

    def test_a_heard_objection_drill_is_anonymised(self):
        heard = self.objections()["heard"][0]
        drill = self.start(heard_id=heard["id"])
        self.assertEqual(drill["source"]["heard_id"], heard["id"])
        self.assertNotIn("3,60,000", json.dumps(drill))
        self.assertNotIn("Ramesh", json.dumps(drill["persona"]))
        self.assertEqual(self.req("POST", "/api/playground/objections",
                                  {"person_id": self.rep, "pack_id": "demo-solar", "heard_id": "hnope"})[0], 404)

    def test_playground_drills_never_reach_the_coach(self):
        drill = self.start(objection_id="competitor-cheaper")
        for n in range(1, 4):  # three fails would escalate a call drill to a manager
            code, client, _ = self.room(drill["id"])
            self.say(code, client, "Our panels have a 25-year performance warranty.")
            d = self.finish(code, client, drill["id"], n)
            self.assertEqual(d["attempts"][-1]["verdict"], "fail")
        self.service.wait_idle()
        coach = self.service.coach_view(self.rep)
        self.assertEqual(coach["escalations"], [])
        self.assertIsNone(coach["focus"])  # this rep has no real call: practice sets nothing
        self.assertNotIn(drill["id"], [x["id"] for x in self.service.coach_state(self.rep)["drills"]])
        self.assertEqual(self.service.drills.get(drill["id"])["difficulty"], 1)

    def test_only_reps_and_one_source(self):
        manager = self.service.add_person("Playground Manager", "manager")["id"]
        for body in ({"person_id": manager, "objection_id": "competitor-cheaper"},
                     {"person_id": self.rep},
                     {"person_id": self.rep, "objection_id": "competitor-cheaper", "heard_id": "h1"}):
            with self.subTest(body=body):
                self.assertEqual(self.req("POST", "/api/playground/objections", {"pack_id": "demo-solar", **body})[0], 400)
        self.assertEqual(self.req("POST", "/api/playground/objections",
                                  {"person_id": self.rep, "pack_id": "demo-solar", "objection_id": "nope"})[0], 404)

    def test_the_typed_objection_exercise_is_retired(self):
        status, out = self.req("POST", "/api/playground", {"person_id": self.rep, "pack_id": "demo-solar", "mode": "objection",
                                                          "topic": "pricing", "response": "What are you comparing?"})
        self.assertEqual(status, 400)
        self.assertIn("AI customer", out["error"])


if __name__ == "__main__":
    unittest.main()
