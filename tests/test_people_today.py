import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from sel.people import PeopleStore
from sel.server import serve
from sel.service import CallService
from sel.simulator import SimulatorHub
from tests.test_service import fixture_factory

ROOT = Path(__file__).resolve().parent.parent


class PeopleStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = PeopleStore(Path(self.tmp.name) / "people.json")

    def test_add_update_and_ids(self):
        self.assertEqual(self.store.list(), [])
        asha = self.store.add("  Asha   Gurung ", "rep")
        self.assertEqual((asha["id"], asha["name"]), ("asha-gurung", "Asha Gurung"))
        # Same slug, different person: a numbered id.
        other = self.store.add("Asha-Gurung", "manager")
        self.assertEqual(other["id"], "asha-gurung-2")
        self.store.update("asha-gurung", role="manager")
        self.assertEqual(self.store.get("asha-gurung")["role"], "manager")
        self.assertEqual(len(PeopleStore(self.store.path).list()), 2)  # persisted

    def test_validation(self):
        with self.assertRaises(ValueError):
            self.store.add("", "rep")
        with self.assertRaises(ValueError):
            self.store.add("Bikash", "admin")
        self.store.add("Bikash", "rep")
        with self.assertRaises(ValueError):
            self.store.add("bikash", "rep")
        with self.assertRaises(KeyError):
            self.store.update("nobody", name="x")


class TodayApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.service = CallService(cls.tmp.name, ROOT / "packs", analysis_factory=fixture_factory)
        cls.service.create_from_example(ROOT / "examples/calls/solar-01", "demo-solar")
        cls.service.create_from_example(ROOT / "examples/calls/pos-01", "demo-pos")
        cls.hub = SimulatorHub(cls.service, speech_status=lambda: (False, "off"))
        cls.httpd = serve(cls.service, port=0, quiet=True, hub=cls.hub)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.service.wait_idle()
        cls.httpd.shutdown()
        cls.hub.close()
        cls.tmp.cleanup()

    def req(self, method, path, body=None):
        data = body if isinstance(body, bytes) or body is None else json.dumps(body).encode()
        r = urllib.request.Request(self.base + path, data=data, method=method)
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def person(self, name, role="rep"):
        status, body = self.req("POST", "/api/people", {"name": name, "role": role})
        if status == 400 and "already" in body["error"]:
            return next(p for p in self.req("GET", "/api/people")[1] if p["name"] == name)
        self.assertEqual(status, 201, body)
        return body

    def test_people_api(self):
        p = self.person("Mina Rai")
        status, people = self.req("GET", "/api/people")
        self.assertIn(p, people)
        status, body = self.req("PUT", f"/api/people/{p['id']}", {"role": "manager"})
        self.assertEqual((status, body["role"]), (200, "manager"))
        self.assertEqual(self.req("POST", "/api/people", {"name": "X", "role": "boss"})[0], 400)
        self.assertEqual(self.req("PUT", "/api/people/nobody", {"name": "Y"})[0], 404)

    def test_a_person_and_a_lead_can_choose_a_portrait(self):
        p = self.person("Portrait Person")
        status, body = self.req("PUT", f"/api/people/{p['id']}", {"portrait": 3})
        self.assertEqual((status, body["portrait"], body["name"]), (200, 3, "Portrait Person"))
        # A name change leaves the portrait alone; null goes back to the automatic one.
        self.assertEqual(self.req("PUT", f"/api/people/{p['id']}", {"name": "Portrait P"})[1]["portrait"], 3)
        self.assertNotIn("portrait", self.req("PUT", f"/api/people/{p['id']}", {"portrait": None})[1])
        for bad in (6, -1, "2", True, 1.5):
            self.assertEqual(self.req("PUT", f"/api/people/{p['id']}", {"portrait": bad})[0], 400, bad)
        status, lead = self.req("POST", "/api/leads", {"name": "Portrait Lead"})
        self.assertEqual(status, 201)
        status, lead = self.req("PUT", f"/api/leads/{lead['id']}", {"portrait": 5})
        self.assertEqual((status, lead["portrait"]), (200, 5))
        self.assertEqual(self.req("PUT", f"/api/leads/{lead['id']}", {"portrait": 9})[0], 400)
        self.assertIsNone(self.req("PUT", f"/api/leads/{lead['id']}", {"portrait": None})[1]["portrait"])

    def test_rep_assignment_and_today_for_a_rep(self):
        asha = self.person("Asha")
        status, summary = self.req("PUT", "/api/calls/example-solar-01/rep", {"rep_id": asha["id"]})
        self.assertEqual((status, summary["rep_id"]), (200, asha["id"]))
        self.assertEqual(self.req("PUT", "/api/calls/example-solar-01/rep", {"rep_id": "ghost"})[0], 404)
        listed = {c["id"]: c for c in self.req("GET", "/api/calls")[1]}
        self.assertEqual(listed["example-solar-01"]["rep_id"], asha["id"])

        status, today = self.req("GET", f"/api/today?person={asha['id']}")
        self.assertEqual(today["scope"], "mine")
        self.assertEqual([c["id"] for c in today["recent"]], ["example-solar-01"])
        # The solar call's site visit has no day: a follow-up waiting for input, listed first.
        self.assertTrue(today["follow_ups"])
        self.assertEqual(today["follow_ups"][0]["status"], "needs_input")
        self.assertEqual(today["counts"]["needs_input"], sum(f["status"] == "needs_input" for f in today["follow_ups"]))
        for skill in today["skills"]:
            self.assertGreater(skill["short"], 0)
            self.assertTrue(all(c["id"] == "example-solar-01" for c in skill["calls"]))
        self.assertNotIn("team", today)

    def test_today_for_a_manager_covers_the_team(self):
        bikash = self.person("Bikash")
        self.person("Sita Manager", "manager")
        self.req("PUT", "/api/calls/example-pos-01/rep", {"rep_id": bikash["id"]})
        boss = next(p for p in self.req("GET", "/api/people")[1] if p["name"] == "Sita Manager")
        status, today = self.req("GET", f"/api/today?person={boss['id']}")
        self.assertEqual(today["scope"], "team")
        row = next(t for t in today["team"] if t["id"] == bikash["id"])
        self.assertEqual(row["calls"], 1)
        self.assertIsNotNone(row["average"])
        self.assertEqual(self.req("GET", "/api/today?person=ghost")[0], 404)

    def test_upload_with_rep_id_uses_the_persons_name(self):
        nima = self.person("Nima")
        status, body = self.req("POST", "/api/calls?pack=demo-pos&started_at=2026-10-08T11:30:00%2B05:45"
                                        f"&filename=call.wav&rep_id={nima['id']}", b"RIFF....fake")
        self.assertEqual(status, 201, body)
        for _ in range(100):
            _, call = self.req("GET", f"/api/calls/{body['id']}")
            if call["status"] in ("done", "error"):
                break
            time.sleep(0.05)
        self.assertEqual((call["rep_id"], call["rep_name"]), (nima["id"], "Nima"))
        bad = self.req("POST", "/api/calls?pack=demo-pos&started_at=2026-10-08T11:30:00%2B05:45"
                               "&filename=call.wav&rep_id=ghost", b"RIFF")
        self.assertEqual(bad[0], 404)

    def test_simulated_call_is_filed_under_the_reps_person(self):
        rita = self.person("Rita")
        status, made = self.req("POST", "/api/sim/rooms", {"pack_id": "demo-pos", "role": "rep", "name": "Rita",
                                                           "person_id": rita["id"]})
        self.assertEqual(status, 201, made)
        code = made["room"]["code"]
        _, joined = self.req("POST", f"/api/sim/rooms/{code}/join", {"role": "customer", "name": "Kiran"})
        self.req("POST", f"/api/sim/rooms/{code}/start", {"client": made["client"]})
        self.req("POST", f"/api/sim/rooms/{code}/lines", {"client": made["client"], "text": "Namaste, this is Rita."})
        status, room = self.req("POST", f"/api/sim/rooms/{code}/finish?client={made['client']}")
        self.assertEqual(status, 200, room)
        call = self.req("GET", f"/api/calls/{room['call_id']}")[1]
        self.assertEqual(call["rep_id"], rita["id"])
        self.assertEqual(self.req("POST", "/api/sim/rooms", {"pack_id": "demo-pos", "person_id": "ghost"})[0], 404)

    def test_pack_detail_lists_skills_facts_and_docs(self):
        status, pack = self.req("GET", "/api/packs/demo-solar")
        self.assertEqual(status, 200)
        self.assertTrue(pack["scorecard"] and pack["facts"] and pack["docs"])
        self.assertIn("statement", pack["facts"][0])
        self.assertEqual(self.req("GET", "/api/packs/nope")[0], 404)


if __name__ == "__main__":
    unittest.main()
