"""Coaching: the line gate, moments, the line writer and the Coach, with scripted stand-ins for the models."""

import json
import re
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from sel.coach import (ModelCoach, MomentFinder, LineWriter, RuleCoach, act_on_moment, apply_actions, carry_over,
                       check_line, diagnose, gate_moments, skill_candidates)
from sel.cli import _report_for_example
from sel.live import pack_index
from sel.llm import LLMStats
from sel.pack import load_pack
from sel.scoring import score_report
from sel.server import serve
from sel.service import CallService
from sel.simulator import SimulatorHub
from tests.test_service import fixture_factory

ROOT = Path(__file__).resolve().parent.parent
SOLAR = ROOT / "examples/calls/solar-01"
INVERTER = "Inverters carry a 5-year manufacturer warranty, extendable to 10 years with the paid Care Plan."
TEN_YEARS = re.compile(r"\b10\s*-?\s*(?:years?|barsa)\b", re.I)


class ScriptedLLM:
    """Stands in for a model: canned JSON per request name (or a function of the prompt); records prompts."""

    name = "scripted"

    def __init__(self, replies):
        self.replies = replies
        self.prompts = []
        self.stats = LLMStats()

    def complete_json(self, system, user, schema, name, max_tokens=2048):
        self.prompts.append((name, user))
        self.stats.calls += 1
        reply = self.replies[name]
        if isinstance(reply, list):
            reply = reply.pop(0)
        return reply(user) if callable(reply) else reply


def solar_report():
    pack = load_pack(ROOT / "packs/demo-solar")
    report = _report_for_example(SOLAR, ROOT / "packs/demo-solar")
    data = report.to_dict()
    data["score"] = score_report(report, pack)
    return data, pack


def passage_id(user, source):
    """The id the prompt gave a pack source, e.g. P2 for fact:inverter-warranty."""
    return next(line.split()[0] for line in user.splitlines() if re.match(rf"P\d+ \({re.escape(source)}\)", line))


class LineGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.solar = load_pack(ROOT / "packs/demo-solar")
        cls.pos = load_pack(ROOT / "packs/demo-pos")

    def check(self, text, sources, pack=None):
        pack = pack or self.solar
        return check_line(text, sources, pack, pack_index(pack))

    def fact(self, fid, pack=None):
        pack = pack or self.solar
        return {"source": f"fact:{fid}", "quote": pack.facts[fid].statement}

    def test_ten_year_inverter_warranty_without_the_care_plan_is_refused(self):
        for sources in ([self.fact("inverter-warranty")],
                        [{"source": "docs/warranty.md", "quote": "The paid Care Plan extends the inverter warranty to 10 years."}]):
            r = self.check("Hamro inverter ma pani 10 years warranty cha.", sources)
            self.assertEqual(r["check"], "rejected")
            self.assertIn("Care Plan", r["reasons"][0])
            self.assertIn("10 years", r["reasons"][0])

    def test_the_condition_makes_it_right(self):
        r = self.check("Inverter ko warranty 5 years ho, Care Plan linu bhayo bhane 10 years samma.",
                       [self.fact("inverter-warranty")])
        self.assertEqual((r["check"], r["reasons"]), ("ok", []))
        self.assertEqual(len(r["facts"]), 2)

    def test_a_fact_must_cite_the_pack(self):
        r = self.check("Inverter ma 5 years warranty cha.", [])
        self.assertEqual(r["check"], "rejected")
        self.assertIn("cites no passage", r["reasons"][0])
        r = self.check("Inverter ma 5 years warranty cha.", [{"source": "docs/nope.md", "quote": "x"}])
        self.assertEqual(r["check"], "rejected")
        r = self.check("Inverter ma 5 years warranty cha.", [{"source": "fact:inverter-warranty", "quote": "made up words"}])
        self.assertEqual(r["check"], "rejected")

    def test_numbers_must_be_in_what_it_cites(self):
        r = self.check("Hamro 3 kW system installation sahit 3 lakh 60 hajar ho.", [self.fact("price-3kw")])
        self.assertEqual(r["check"], "rejected")
        self.assertIn("3 lakh 60 hajar", r["reasons"][0])
        self.assertIn("4,20,000", r["reasons"][0])  # what the pack says
        self.assertEqual(self.check("Hamro 3 kW system installation sahit 4 lakh 20 hajar ho.",
                                    [self.fact("price-3kw")])["check"], "ok")

    def test_the_passage_must_be_about_the_same_thing(self):
        r = self.check("Panel ma 5 years warranty cha.", [self.fact("inverter-warranty")])
        self.assertEqual(r["check"], "rejected")
        self.assertIn("about something else", r["reasons"][0])

    def test_saying_the_pack_does_what_it_says_it_does_not(self):
        self.assertEqual(self.check("Hamile EMI dinchau.", [self.fact("financing")])["check"], "rejected")
        self.assertEqual(self.check("Partner bank through EMI milcha.", [self.fact("financing")])["check"], "ok")
        # No claim word, but the clause matches a pack fact closely: still checked, and refused.
        r = self.check("Yes, it connects directly to your accounting software.", [self.fact("no-accounting-sync", self.pos)], self.pos)
        self.assertEqual(r["check"], "rejected")
        self.assertIn("opposite", r["reasons"][0])
        r = self.check("Thali POS doesn't integrate with accounting software yet, but you can export sales as CSV any time.",
                       [self.fact("no-accounting-sync", self.pos)], self.pos)
        self.assertEqual(r["check"], "ok")

    def test_a_trailing_qualifier_is_a_condition(self):
        gen = {"source": "docs/generation.md", "quote": self.solar.docs["docs/generation.md"].split("\n\n")[1]}
        r = self.check("3 kW system le mahina ma 390 units generate garcha.", [gen])
        self.assertEqual(r["check"], "rejected")
        self.assertIn("390 units", r["reasons"][0])
        self.assertEqual(self.check("Season anusar mahina ma 330 dekhi 390 units, depending on season.", [gen])["check"], "ok")

    def test_questions_and_promises_state_no_fact(self):
        for text in ("Arko quote ma kun panel, inverter ra warranty include cha?",
                     "Before I quote a price: how much is your monthly bill, and how much roof space do you have?",
                     "Is your bill more than 5,000 a month?",
                     "I'll confirm the inverter warranty with our team and email you today."):
            r = self.check(text, [])
            self.assertEqual((r["check"], r["facts"]), ("ok", []), text)

    def test_a_question_can_still_slip_in_a_claim(self):
        r = self.check("Did you know our inverter has a 10-year warranty?", [])
        self.assertEqual(r["check"], "rejected")


def scripted_coaching_llm(first_inverter_line="Hamro inverter ma pani 10 years warranty cha."):
    """Moment finder + line writer + judge. The first line for the inverter moment claims 10 years."""
    moments = {"moments": [
        {"criterion": "objection-handling", "kind": "weak", "segment": "s04", "why": "Defended the price."},
        {"criterion": "accurate-claims", "kind": "weak", "segment": "[s05]", "why": "10-year inverter warranty."},
        {"criterion": "clear-next-step", "kind": "weak", "segment": "s13", "why": "No day for the visit."},
        {"criterion": "clear-next-step", "kind": "strong", "segment": "s11", "why": "Fixed the email deadline."},
        {"criterion": "accurate-claims", "kind": "strong", "segment": "s05", "why": "Good warranty talk."},
        {"criterion": "discovery", "kind": "weak", "segment": "s77", "why": "Invented line."},
    ]}

    def line(user):
        if "criterion: Only approved product claims" in user:
            pid = passage_id(user, "fact:inverter-warranty")
            if "Sel refused your earlier line" in user:
                return {"line": "Inverter ma 5 years ko warranty cha, ani paid Care Plan le badhauna milcha.", "passages": [pid]}
            return {"line": first_inverter_line, "passages": [pid]}
        if "criterion: Next step" in user:
            return {"line": "Engineer ko visit ko lagi Sunday ki Monday, kun din milcha?", "passages": []}
        return {"line": "Arko quote ma kun inverter ra warranty cha?", "passages": ["P9"]}

    return ScriptedLLM({"moments": moments, "line": line,
                        "verify-lines": {"checks": [{"claim_index": 0, "verdict": "supported", "passage_id": "",
                                                     "explanation": "ok"}]}})


class DiagnoseTests(unittest.TestCase):
    def setUp(self):
        self.report, self.pack = solar_report()
        self.index = pack_index(self.pack)

    def test_model_moments_are_gated_and_quote_the_transcript(self):
        llm = scripted_coaching_llm()
        c = diagnose(self.report, self.pack, self.index, MomentFinder(llm), LineWriter(llm, self.index), judge=llm)
        segs = {s["id"]: s["text"] for s in self.report["transcript"]["segments"]}
        for m in c["moments"]:
            self.assertIn(m["evidence"]["segment_ids"][0], segs)
            self.assertEqual(m["evidence"]["quote"], segs[m["evidence"]["segment_ids"][0]])
        found = {(m["kind"], m["criterion"], m["evidence"]["segment_ids"][0]) for m in c["moments"]}
        # Cited on the customer's objection (s04): moved to the rep's reply.
        self.assertIn(("weak", "objection-handling", "s05"), found)
        self.assertEqual(next(m for m in c["moments"] if m["criterion"] == "objection-handling")["customer_said"]["segment_id"], "s04")
        refused = {r["segment"]: r["reasons"][0] for r in c["rejected"]}
        self.assertIn("unknown segment", refused["s77"])
        self.assertIn("contradicts", refused["s05"])  # no praise for the line with the wrong warranty

    def test_inverter_line_never_claims_ten_years_and_the_refused_line_is_kept(self):
        llm = scripted_coaching_llm()
        c = diagnose(self.report, self.pack, self.index, MomentFinder(llm), LineWriter(llm, self.index), judge=llm)
        inverter = next(m for m in c["moments"] if m["criterion"] == "accurate-claims" and m["kind"] == "weak")
        self.assertEqual(inverter["better_line"]["check"], "ok")
        self.assertNotRegex(inverter["better_line"]["text"], TEN_YEARS)
        self.assertEqual(inverter["better_line"]["sources"][0]["quote"], INVERTER)  # the pack's own words
        self.assertEqual(inverter["better_line"]["checked_by"], ["code", "model"])
        self.assertEqual(len(inverter["rejected_lines"]), 1)
        self.assertRegex(inverter["rejected_lines"][0]["text"], TEN_YEARS)
        self.assertIn("Care Plan", inverter["rejected_lines"][0]["reasons"][0])
        # The retry told the model why.
        retry = [u for n, u in llm.prompts if n == "line" and "Sel refused your earlier line" in u]
        self.assertEqual(len(retry), 1)
        self.assertIn("leaves out the pack's condition", retry[0])
        # A passage id the model was never given doesn't count as a source.
        objection = next(m for m in c["moments"] if m["criterion"] == "objection-handling")
        self.assertEqual(objection["better_line"]["sources"], [])
        self.assertEqual(c["counts"]["lines_rejected"], 1)

    def test_twice_wrong_leaves_no_line_only_reasons(self):
        llm = scripted_coaching_llm()
        other = llm.replies["line"]
        llm.replies["line"] = lambda user: {"line": "Inverter ma 10 years warranty cha.",
                                            "passages": [passage_id(user, "fact:inverter-warranty")]} \
            if "criterion: Only approved product claims" in user else other(user)
        c = diagnose(self.report, self.pack, self.index, MomentFinder(llm), LineWriter(llm, self.index))
        inverter = next(m for m in c["moments"] if m["criterion"] == "accurate-claims" and m["kind"] == "weak")
        self.assertIsNone(inverter["better_line"])
        self.assertEqual(len(inverter["rejected_lines"]), 2)

    def test_a_looping_or_failing_model_gives_refused_attempts_not_a_crash(self):
        from sel.llm import LLMError

        llm = scripted_coaching_llm()

        def line(user):
            if "criterion: Next step" in user:
                raise LLMError("model hit max_tokens on line")
            return {"line": "Can you tell me your bill first? [s02] [P2] [P4] [s03] [P1]", "passages": []}

        llm.replies["line"] = line
        c = diagnose(self.report, self.pack, self.index, MomentFinder(llm), LineWriter(llm, self.index))
        nxt = next(m for m in c["moments"] if m["criterion"] == "clear-next-step" and m["kind"] == "weak")
        self.assertIsNone(nxt["better_line"])
        self.assertIn("was not finished", nxt["rejected_lines"][0]["reasons"][0])
        other = next(m for m in c["moments"] if m["criterion"] == "objection-handling")
        self.assertEqual(other["rejected_lines"][0]["reasons"],
                         ["contains segment or passage ids ([s02], [P2], [P4], [s03]) instead of only the words to say"])

    def test_the_model_judge_can_only_refuse_more(self):
        llm = scripted_coaching_llm()
        llm.replies["verify-lines"] = {"checks": [{"claim_index": 0, "verdict": "contradicted", "passage_id": "",
                                                   "explanation": "The warranty terms differ."}]}
        c = diagnose(self.report, self.pack, self.index, MomentFinder(llm), LineWriter(llm, self.index), judge=llm)
        inverter = next(m for m in c["moments"] if m["criterion"] == "accurate-claims" and m["kind"] == "weak")
        self.assertIsNone(inverter["better_line"])
        self.assertIn("model checking it", inverter["rejected_lines"][-1]["reasons"][0])

    def test_rules_without_a_model_write_no_line(self):
        c = diagnose(self.report, self.pack, self.index)
        self.assertTrue(all(m["better_line"] is None for m in c["moments"]))
        self.assertEqual(c["by"]["lines"], None)
        kinds = {(m["kind"], m["criterion"]) for m in c["moments"]}
        self.assertIn(("weak", "objection-handling"), kinds)
        self.assertIn(("strong", "clear-next-step"), kinds)

    def test_fixture_mistakes_are_refused(self):
        fixture = json.loads((SOLAR / "coaching.json").read_text(encoding="utf-8"))
        c = diagnose(self.report, self.pack, self.index, fixture=fixture)
        self.assertEqual(len(c["rejected"]), 2)
        for m in c["moments"]:
            if m["better_line"]:
                self.assertFalse(TEN_YEARS.search(m["better_line"]["text"]) and "Care Plan" not in m["better_line"]["text"])

    def test_actions_on_moments(self):
        fixture = json.loads((SOLAR / "coaching.json").read_text(encoding="utf-8"))
        c = diagnose(self.report, self.pack, self.index, fixture=fixture)
        inverter = next(m for m in c["moments"] if m["criterion"] == "accurate-claims" and m["kind"] == "weak")
        act_on_moment(c, inverter["id"], "endorse", {}, self.pack, self.index, "asha")
        self.assertEqual(inverter["state"], "endorsed")
        # A person's edit is checked like the model's: the 10-year claim is refused, with code-found passages.
        act_on_moment(c, inverter["id"], "edit", {"text": "Inverter ma 10 years warranty cha."}, self.pack, self.index, "asha")
        self.assertEqual((inverter["state"], inverter["better_line"]["check"]), ("edited", "rejected"))
        with self.assertRaises(ValueError):
            act_on_moment(c, inverter["id"], "endorse", {}, self.pack, self.index, "asha")
        self.assertIn("Care Plan", inverter["original_line"]["text"])
        act_on_moment(c, inverter["id"], "edit", {"text": "Inverter ma 5 years ko warranty cha."}, self.pack, self.index, "asha")
        self.assertEqual(inverter["better_line"]["check"], "ok")
        self.assertEqual(inverter["better_line"]["by"], "person:asha")
        act_on_moment(c, inverter["id"], "dismiss", {}, self.pack, self.index, "asha")
        self.assertEqual(inverter["state"], "dismissed")
        act_on_moment(c, inverter["id"], "dismiss", {"on": False}, self.pack, self.index, "asha")
        self.assertEqual(inverter["state"], "edited")
        act_on_moment(c, inverter["id"], "note", {"note": "  Practise the Care Plan wording. "}, self.pack, self.index, "asha")
        self.assertEqual(inverter["note"], "Practise the Care Plan wording.")
        with self.assertRaises(KeyError):
            act_on_moment(c, "m99", "note", {}, self.pack, self.index, None)
        # Coached again later: the person's work is kept.
        again = carry_over(diagnose(self.report, self.pack, self.index, fixture=fixture), c)
        kept = next(m for m in again["moments"] if m["id"] == inverter["id"])
        self.assertEqual((kept["state"], kept["note"], kept["better_line"]["by"]), ("edited", inverter["note"], "person:asha"))


class CoachAgentTests(unittest.TestCase):
    def setUp(self):
        report, self.pack = solar_report()
        fixture = json.loads((SOLAR / "coaching.json").read_text(encoding="utf-8"))
        coaching = diagnose(report, self.pack, pack_index(self.pack), fixture=fixture)
        self.skills = skill_candidates([({"id": "c1", "pack_id": "demo-solar"}, coaching)], {"demo-solar": self.pack}.get)
        self.state = {"person_id": "asha", "name": "Asha", "focus": None, "skills": self.skills, "calls_seen": 1}

    def test_rules_count_calls_and_prefer_a_skill_practice_can_train(self):
        # Every weak skill fell short on the one call (claims twice in it); the tie goes to objections.
        claims = next(s for s in self.skills if s["criterion"] == "accurate-claims")
        self.assertEqual((claims["weak"], claims["weak_calls"]), (2, ["c1"]))
        action = RuleCoach().decide(self.state)[0]
        self.assertEqual((action["action"], action["skill"]), ("set_focus", "demo-solar/objection-handling"))
        self.assertEqual(action["reason"], "Fell short on this in 1 of your last 1 reviewed call.")

    def test_model_actions_are_validated(self):
        llm = ScriptedLLM({"focus": {"action": "set_focus", "skill": "demo-solar/objection-handling",
                                     "reason": "Two calls lost on price."}})
        actions = ModelCoach(llm).decide(self.state)
        coach_file = {"focus": None}
        log = apply_actions(actions, self.state, coach_file, "coach:model")
        self.assertTrue(log[0]["applied"])
        self.assertEqual(coach_file["focus"]["criterion"], "objection-handling")
        self.assertEqual(coach_file["focus"]["reason"], "Two calls lost on price.")
        # Already focused: no new focus is proposed (a drill for it is), and a forced one is refused.
        later = ModelCoach(llm).decide({**self.state, "focus": coach_file["focus"]})
        self.assertNotIn("set_focus", [a["action"] for a in later])
        self.assertIn("create_drill", [a["action"] for a in later])
        log = apply_actions([{"action": "set_focus", "skill": "demo-solar/discovery"}], self.state, coach_file, "x")
        self.assertIn("already active", log[0]["refused"])

    def test_unknown_skills_and_actions_are_refused(self):
        coach_file = {"focus": None}
        log = apply_actions([{"action": "send_email", "to": "ramesh@example.com"},
                             {"action": "set_focus", "skill": "demo-solar/closing"},
                             {"action": "escalate_to_manager", "reason": "low score"}], self.state, coach_file,
                            "coach:model")
        self.assertEqual([e["applied"] for e in log], [False, False, False])
        self.assertIn("not an action", log[0]["refused"])
        self.assertIn("not a skill", log[1]["refused"])
        self.assertIsNone(coach_file["focus"])
        self.assertEqual(len(coach_file["actions"]), 3)  # refused actions are logged too


class CoachingServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.service = CallService(cls.tmp.name, ROOT / "packs", analysis_factory=fixture_factory)
        cls.service.create_from_example(SOLAR, "demo-solar")
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
        data = None if body is None else json.dumps(body).encode()
        r = urllib.request.Request(self.base + path, data=data, method=method)
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_example_call_is_coached(self):
        status, c = self.req("GET", "/api/calls/example-solar-01/coaching")
        self.assertEqual(status, 200)
        self.assertEqual(c["by"]["moments"], "fixture (hand-written, not model output)")
        view = self.req("GET", "/api/calls/example-solar-01")[1]
        self.assertEqual(view["coaching_status"], "done")
        self.assertEqual(len(view["coaching"]["moments"]), len(c["moments"]))

    def test_moment_actions_over_the_api(self):
        c = self.req("GET", "/api/calls/example-pos-01/coaching")[1]
        m = next(x for x in c["moments"] if x["kind"] == "weak" and x["better_line"])
        status, out = self.req("POST", f"/api/calls/example-pos-01/moments/{m['id']}/note", {"note": "good one"})
        self.assertEqual((status, out["note"]), (200, "good one"))
        status, out = self.req("POST", f"/api/calls/example-pos-01/moments/{m['id']}/edit", {"text": ""})
        self.assertEqual(status, 400)
        self.assertEqual(self.req("POST", f"/api/calls/example-pos-01/moments/{m['id']}/launch", {})[0], 404)
        self.assertEqual(self.req("POST", "/api/calls/example-pos-01/moments/m99/note", {})[0], 404)
        self.assertEqual(self.req("POST", f"/api/calls/example-pos-01/moments/{m['id']}/note", {"by": "nobody"})[0], 404)

    def test_a_reps_call_gets_a_focus_and_today_shows_it(self):
        rep = self.service.add_person("Asha Coach", "rep")
        boss = self.service.add_person("Sita Coach", "manager")
        self.req("PUT", "/api/calls/example-solar-01/rep", {"rep_id": rep["id"]})
        for _ in range(200):
            focus = self.service.coach_store.get(rep["id"]).get("focus")
            if focus:
                break
            time.sleep(0.02)
        self.assertEqual(focus["set_by"], "coach:rules")
        self.assertEqual(focus["status"], "active")
        today = self.req("GET", f"/api/today?person={rep['id']}")[1]
        self.assertEqual(today["focus"]["criterion"], focus["criterion"])
        self.assertEqual(today["focus"]["latest"]["call_id"], "example-solar-01")
        self.assertEqual(today["focus"]["set_by_name"], "Sel's coach")
        coach = self.req("GET", f"/api/coach/{rep['id']}")[1]
        self.assertTrue(coach["skills"])
        self.assertTrue(any(a["applied"] for a in coach["actions"]))
        # The manager chooses another focus; the Coach leaves a focus alone once one is active.
        status, coach = self.req("PUT", f"/api/coach/{rep['id']}/focus",
                                 {"pack_id": "demo-solar", "criterion": "clear-next-step", "by": boss["id"]})
        self.assertEqual(status, 200)
        self.assertEqual((coach["focus"]["criterion"], coach["focus"]["set_by_name"]), ("clear-next-step", "Sita Coach"))
        self.assertEqual(coach["history"][-1]["status"], "replaced")
        log = self.service.run_coach(rep["id"])
        self.assertNotIn("set_focus", [e["action"] for e in log if e["applied"]])
        self.assertEqual(self.service.coach_store.get(rep["id"])["focus"]["criterion"], "clear-next-step")
        team = self.req("GET", f"/api/today?person={boss['id']}")[1]["team"]
        self.assertEqual(next(r for r in team if r["id"] == rep["id"])["focus"]["criterion"], "clear-next-step")
        self.assertEqual(self.req("PUT", f"/api/coach/{rep['id']}/focus", {"pack_id": "demo-solar", "criterion": "nope"})[0], 400)


if __name__ == "__main__":
    unittest.main()
