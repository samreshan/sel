"""Phase 4: leads, Prep, rehearsal, the team view, escalations, the playbook overlay and the flywheel.

Over the HTTP API, with scripted stand-ins for the analysis and chat models where a model is involved.
"""

import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from sel.adapters import FixtureAnalyzer, FixtureTranscriber
from sel.coach import ESCALATION_REASONS, LineWriter, diagnose
from sel.live import pack_index
from sel.llm import LLMStats
from sel.models import Transcript
from sel.pack import load_pack
from sel.playbook import candidates
from sel.server import serve
from sel import service as service_mod
from sel.service import CallService
from sel.simulator import SimulatorHub

ROOT = Path(__file__).resolve().parent.parent
SOLAR = ROOT / "examples/calls/solar-01"


class ScriptedLLM:
    name = "scripted"

    def __init__(self, replies):
        self.replies = replies
        self.prompts = []
        self.stats = LLMStats()

    def complete_json(self, system, user, schema, name, max_tokens=2048):
        self.prompts.append((name, user))
        reply = self.replies[name]
        if isinstance(reply, list):
            reply = reply.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply(user) if callable(reply) else reply


def solar_factory(call, speakers):
    """No models: the solar transcript and its hand-written analysis for any upload; coaching by rules."""
    return FixtureTranscriber(SOLAR / "transcript.json"), FixtureAnalyzer(SOLAR / "analysis.json"), None


class TeamApi(unittest.TestCase):
    """A fresh calls folder and server per test: the example solar call, a manager and two reps."""

    analysis_llm = None
    chat_llm = None

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.doctor_calls = 0

        def doctor():
            self.doctor_calls += 1
            return {"ok": True, "problems": [], "llm": {"model": "qwen2.5:7b"}}

        self.service = CallService(self.tmp.name, ROOT / "packs", analysis_factory=solar_factory,
                                   analysis_llm_factory=lambda: self.analysis_llm, chat_factory=lambda: self.chat_llm)
        self.service.create_from_example(SOLAR, "demo-solar")
        self.hub = SimulatorHub(self.service, speech_status=lambda: (False, "off"))
        self.httpd = serve(self.service, port=0, quiet=True, hub=self.hub, doctor=doctor)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.events = self.service.bus.subscribe({"calls"})
        self.manager = self.service.add_person("Mina", "manager")["id"]
        self.rep = self.service.add_person("Asha", "rep")["id"]
        self.other = self.service.add_person("Bikash", "rep")["id"]

    def tearDown(self):
        self.service.wait_idle()
        self.events.close()
        self.httpd.shutdown()
        self.hub.close()
        self.tmp.cleanup()

    def req(self, method, path, body=None, raw=None):
        data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
        r = urllib.request.Request(self.base + path, data=data, method=method)
        try:
            with urllib.request.urlopen(r, timeout=20) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def ok(self, method, path, body=None, status=200, raw=None):
        got, out = self.req(method, path, body, raw)
        self.assertEqual(got, status, out)
        return out

    def published(self, type_):
        out = []
        while True:
            try:
                ev = self.events.get(timeout=0.01)
            except Exception:  # noqa: BLE001 - queue.Empty
                return out
            if ev is not None and ev.type == type_:
                out.append(ev.data)

    def assign(self, call_id="example-solar-01", rep=None):
        self.ok("PUT", f"/api/calls/{call_id}/rep", {"rep_id": rep or self.rep})
        self.service.wait_idle()

    def upload(self, rep=None, lead=None, transcript=None, started="2026-10-20T10:00:00%2B05:45"):
        if transcript is not None:
            path = Path(self.tmp.name) / f"t-{len(self.service.list())}.json"
            path.write_text(json.dumps(transcript), encoding="utf-8")
            self.service._factory = lambda call, speakers: (FixtureTranscriber(path), FixtureAnalyzer(SOLAR / "analysis.json"), None)
        q = f"pack=demo-solar&started_at={started}&filename=c.wav&rep_id={self.rep if rep is None else rep}" + (f"&lead_id={lead}" if lead else "")
        out = self.ok("POST", f"/api/calls?{q}", raw=b"RIFF", status=201)
        self.service.wait_idle()
        self.service._factory = solar_factory
        return out["id"]

    def endorse_and_approve(self):
        """A rep endorses the better line for the price objection; the Librarian proposes it; a manager approves."""
        coaching = self.ok("GET", "/api/calls/example-solar-01/coaching")
        m = next(m for m in coaching["moments"] if m["criterion"] == "objection-handling" and m["kind"] == "weak")
        self.ok("POST", f"/api/calls/example-solar-01/moments/{m['id']}/endorse", {"by": self.rep})
        self.ok("POST", "/api/playbook/demo-solar/answers", {"action": "propose", "by": self.manager}, status=202)
        self.service.wait_idle()
        answer = next(a for a in self.ok("GET", "/api/playbook/demo-solar")["proposed"]
                      if a["criterion"] == "objection-handling")
        self.ok("POST", "/api/playbook/demo-solar/answers", {"action": "approve", "id": answer["id"], "by": self.manager})
        return answer


class LeadTests(TeamApi):
    def test_leads_crud_and_linking_calls(self):
        lead = self.ok("POST", "/api/leads", {"name": "Ramesh Thapa", "phone": "9841", "rep_id": self.rep}, 201)
        self.ok("POST", "/api/leads", {"name": "Sita", "rep_id": self.other}, 201)
        self.assertEqual([x["id"] for x in self.ok("GET", f"/api/leads?rep={self.rep}")], [lead["id"]])
        self.assertEqual(len(self.ok("GET", "/api/leads")), 2)
        self.assertEqual(self.req("POST", "/api/leads", {"name": ""})[0], 400)
        self.assertEqual(self.req("POST", "/api/leads", {"name": "X", "rep_id": "nobody"})[0], 404)
        self.assertEqual(self.ok("PUT", f"/api/leads/{lead['id']}", {"notes": "Wants 5 kW"})["notes"], "Wants 5 kW")

        summary = self.ok("PUT", "/api/calls/example-solar-01/lead", {"lead_id": lead["id"]})
        self.assertEqual(summary["lead_id"], lead["id"])
        view = self.ok("GET", f"/api/leads/{lead['id']}")
        self.assertEqual([c["id"] for c in view["calls"]], ["example-solar-01"])
        self.assertEqual(view["rep_name"], "Asha")
        self.assertTrue(self.published("lead"))
        # Moving the call to another lead unlinks it from the first.
        self.ok("PUT", "/api/calls/example-solar-01/lead", {"lead_id": "sita"})
        self.assertEqual(self.ok("GET", f"/api/leads/{lead['id']}")["call_ids"], [])
        self.assertEqual(self.req("PUT", "/api/calls/example-solar-01/lead", {"lead_id": "nobody"})[0], 404)
        self.assertEqual(self.req("GET", "/api/leads/nobody")[0], 404)
        # An upload can name its lead.
        call_id = self.upload(lead=lead["id"])
        self.assertIn(call_id, self.ok("GET", f"/api/leads/{lead['id']}")["call_ids"])

    def test_prep_rules_then_model_on_the_agent_queue(self):
        lead = self.ok("POST", "/api/leads", {"name": "Ramesh", "rep_id": self.rep}, 201)
        self.assertEqual(self.ok("GET", f"/api/leads/{lead['id']}/prep")["calls"], [])
        self.ok("PUT", "/api/calls/example-solar-01/lead", {"lead_id": lead["id"]})
        brief = self.ok("GET", f"/api/leads/{lead['id']}/prep")
        self.assertEqual(brief["by"], "rules (no language model)")
        self.assertNotIn("model_pending", brief)
        self.assertEqual(brief["likely_objections"][0]["answer"]["from"], "better line")

        self.analysis_llm = ScriptedLLM({"prep": {"likely_objections": ["O1"], "gaps": ["G1"],
                                                  "summary": "Price is the concern; confirm the site visit day."}})
        brief = self.ok("GET", f"/api/leads/{lead['id']}/prep")
        self.assertTrue(brief["model_pending"])  # rules at once; the model brief is queued
        self.service.wait_idle()
        self.assertTrue(any(e.get("prep") for e in self.published("lead")))
        brief = self.ok("GET", f"/api/leads/{lead['id']}/prep")
        self.assertEqual(brief["summary"]["by"], "model:scripted")
        self.assertEqual(len(self.analysis_llm.prompts), 1)
        self.ok("GET", f"/api/leads/{lead['id']}/prep")
        self.assertEqual(len(self.analysis_llm.prompts), 1)  # cached until the lead's calls change

    def test_rehearse_makes_a_drill_from_the_latest_weak_objection(self):
        lead = self.ok("POST", "/api/leads", {"name": "Ramesh", "rep_id": self.rep}, 201)
        self.assertEqual(self.req("POST", f"/api/leads/{lead['id']}/rehearse", {"by": self.rep})[0], 400)
        self.assign()
        self.ok("PUT", "/api/calls/example-solar-01/lead", {"lead_id": lead["id"]})
        drill = self.ok("POST", f"/api/leads/{lead['id']}/rehearse", {"by": self.rep}, 201)
        self.assertEqual((drill["criterion"], drill["person_id"]), ("objection-handling", self.rep))
        self.assertEqual(drill["source"]["call_id"], "example-solar-01")
        self.assertEqual(self.ok("POST", f"/api/leads/{lead['id']}/rehearse", {"by": self.rep}, 201)["id"], drill["id"])


class TeamViewTests(TeamApi):
    def test_matrix_patterns_escalations_and_review_queue(self):
        self.assign()
        self.upload(rep=self.other)
        unassigned = self.upload(rep="")
        team = self.ok("GET", "/api/team")
        reps = {r["id"]: r for r in team["reps"]}
        self.assertEqual(set(reps), {self.rep, self.other})
        self.assertEqual(reps[self.rep]["skills"]["demo-solar/objection-handling"], {"scored": 1, "short": 1,
                                                                                   "calls": ["example-solar-01"]})
        self.assertIn("demo-solar/discovery", [c["key"] for c in team["criteria"]])
        # Both reps fell short on the price objection: a team pattern, and the objection cue across the team.
        skill = next(s for s in team["patterns"]["skills"] if s["criterion"] == "objection-handling")
        self.assertEqual((skill["reps_short"], skill["reps_scored"]), (2, 2))
        cue = team["patterns"]["objections"][0]
        self.assertEqual((cue["cue"], cue["count"], cue["reps"]), ("expensive", 2, 2))
        self.assertIn("expensive", cue["examples"][0]["quote"])
        self.assertEqual(self.ok("GET", "/api/team/patterns"), team["patterns"])
        self.assertEqual({e["reason"] for e in team["escalations"]}, {"contradicted_claim"})
        self.assertEqual([c["id"] for c in team["review"]["unassigned"]], [unassigned])

    def test_a_rep_for_the_manager(self):
        self.assign()
        self.upload()  # a second real call, later
        r = self.ok("GET", f"/api/team/{self.rep}")
        tl = next(t for t in r["timelines"] if t["criterion"] == "objection-handling")
        self.assertEqual(len(tl["points"]), 2)
        self.assertLess(tl["points"][0]["started_at"], tl["points"][1]["started_at"])  # oldest first
        self.assertEqual(tl["points"][0]["moment"]["kind"], "weak")
        self.assertEqual(r["focus"]["criterion"], "objection-handling")
        self.assertTrue(r["drills"])
        kinds = [p["kind"] for p in r["brief"]["points"]]
        self.assertIn("focus", kinds)
        self.assertIn("escalation", kinds)
        self.assertIsNone(r["brief"]["summary"])
        self.assertEqual(self.req("GET", "/api/team/nobody")[0], 404)

    def test_one_on_one_summary_is_number_checked(self):
        self.assign()
        self.chat_llm = ScriptedLLM({"one-on-one": [{"summary": "Asha closed 7 deals this month."},
                                                    {"summary": "Asha is working on price objections; the drill is ready."}],
                                     "focus": {"action": "none"}, "librarian": {"answers": []}})
        self.assertTrue(self.ok("GET", f"/api/team/{self.rep}")["brief"]["model_pending"])
        self.service.wait_idle()
        self.assertIsNone(self.ok("GET", f"/api/team/{self.rep}")["brief"]["summary"])  # "7" is in no point
        self.service._briefs.clear()
        self.ok("GET", f"/api/team/{self.rep}")
        self.service.wait_idle()
        summary = self.ok("GET", f"/api/team/{self.rep}")["brief"]["summary"]
        self.assertEqual(summary["by"], "model:scripted")

    def test_only_a_manager_resolves_an_escalation(self):
        self.assign()
        e = self.ok("GET", "/api/team")["escalations"][0]
        path = f"/api/coach/{self.rep}/escalations/{urllib.parse.quote(e['key'], safe='')}"
        self.assertEqual(self.req("PUT", path, {"status": "resolved", "by": self.rep})[0], 403)
        self.assertEqual(self.req("PUT", path, {"status": "resolved"})[0], 403)
        self.assertEqual(self.req("PUT", path, {"status": "gone", "by": self.manager})[0], 400)
        self.assertEqual(self.ok("PUT", path, {"status": "resolved", "by": self.manager})["resolved_by"], self.manager)
        self.assertEqual(self.ok("GET", "/api/team")["escalations"], [])
        self.assertEqual(self.ok("GET", f"/api/today?person={self.manager}")["escalations"], [])
        self.assertEqual(self.req("PUT", f"/api/coach/{self.rep}/escalations/nope", {"status": "resolved",
                                                                                  "by": self.manager})[0], 404)

    def test_doctor_is_cached(self):
        self.assertTrue(self.ok("GET", "/api/doctor")["ok"])
        self.ok("GET", "/api/doctor")
        self.assertEqual(self.doctor_calls, 1)


class PlaybookTests(TeamApi):
    def test_propose_approve_reject_and_who_may(self):
        self.assign()
        coaching = self.ok("GET", "/api/calls/example-solar-01/coaching")
        m = next(m for m in coaching["moments"] if m["criterion"] == "objection-handling" and m["kind"] == "weak")
        self.ok("POST", f"/api/calls/example-solar-01/moments/{m['id']}/endorse", {"by": self.rep})
        self.chat_llm = ScriptedLLM({"librarian": lambda user: {"answers": [
            {"moment": i, "objection": "A competitor quoted less"} for i in range(user.count("SALESPERSON"))]},
            "focus": {"action": "none"}})
        out = self.ok("POST", "/api/playbook/demo-solar/answers", {"action": "propose", "by": self.rep}, 202)
        self.assertTrue(out["queued"])
        self.service.wait_idle()
        self.assertTrue(any("proposed" in e for e in self.published("playbook")))
        book = self.ok("GET", "/api/playbook/demo-solar")
        endorsed = next(a for a in book["proposed"] if a["criterion"] == "objection-handling")
        self.assertEqual((endorsed["objection"], endorsed["answer_from"]), ("A competitor quoted less", "endorsed line"))
        self.assertEqual(endorsed["answer"], m["better_line"]["text"])  # someone's words, never the model's
        self.assertTrue(endorsed["by"].startswith("librarian:model"))
        # Proposing again finds nothing new.
        self.ok("POST", "/api/playbook/demo-solar/answers", {"action": "propose"}, 202)
        self.service.wait_idle()
        self.assertEqual(len(self.ok("GET", "/api/playbook/demo-solar")["proposed"]), len(book["proposed"]))

        body = {"action": "approve", "id": endorsed["id"], "by": self.rep}
        self.assertEqual(self.req("POST", "/api/playbook/demo-solar/answers", body)[0], 403)
        self.assertEqual(self.req("POST", "/api/playbook/demo-solar/answers", {**body, "by": self.manager, "id": "nope"})[0], 404)
        self.assertEqual(self.req("POST", "/api/playbook/demo-solar/answers", {"action": "publish"})[0], 400)
        self.assertEqual(self.ok("POST", "/api/playbook/demo-solar/answers", {**body, "by": self.manager})["status"], "approved")
        others = [a for a in book["proposed"] if a["id"] != endorsed["id"]]
        if others:
            self.ok("POST", "/api/playbook/demo-solar/answers", {"action": "reject", "id": others[0]["id"], "by": self.manager})
        book = self.ok("GET", "/api/playbook/demo-solar")
        self.assertEqual([a["id"] for a in book["approved"]], [endorsed["id"]])
        self.assertEqual(len(book["rejected"]), 1 if others else 0)
        self.assertEqual(self.req("GET", "/api/playbook/nope")[0], 404)
        self.assertEqual(self.ok("GET", "/api/team")["review"]["proposals"], [])

    def test_a_refused_answer_cannot_be_approved(self):
        self.assign()
        data = self.service.playbook.get("demo-solar")
        data["answers"].append({"id": "a1", "criterion": "accurate-claims", "objection": "x", "answer": "10-year warranty",
                                "status": "refused", "source": {"call_id": "c", "moment_id": "m", "segment_ids": []},
                                "check": {"check": "rejected", "reasons": ["leaves out the Care Plan"]}})
        self.service.playbook.save(data)
        status, out = self.req("POST", "/api/playbook/demo-solar/answers", {"action": "approve", "id": "a1", "by": self.manager})
        self.assertEqual(status, 400)
        self.assertIn("Care Plan", out["error"])
        self.assertEqual(len(self.ok("GET", "/api/playbook/demo-solar")["refused"]), 1)


class DoneCriteriaTests(TeamApi):
    """The two Phase 4 done criteria."""

    def test_an_approved_answer_reaches_the_next_prep_and_the_next_calls_better_line(self):
        self.assign()
        lead = self.ok("POST", "/api/leads", {"name": "Ramesh", "rep_id": self.rep}, 201)
        self.ok("PUT", "/api/calls/example-solar-01/lead", {"lead_id": lead["id"]})
        answer = self.endorse_and_approve()

        # The next prep brief: the team answer for the objection the customer raised.
        o = self.ok("GET", f"/api/leads/{lead['id']}/prep")["likely_objections"][0]
        self.assertEqual((o["answer"]["from"], o["answer"]["answer_id"]), ("team answer", answer["id"]))

        # The next real call with the same objection, coached without a model: the team answer is the better line.
        call_id = self.upload(lead=lead["id"])
        coaching = self.ok("GET", f"/api/calls/{call_id}/coaching")
        m = next(m for m in coaching["moments"] if m["criterion"] == "objection-handling" and m["kind"] == "weak")
        self.assertEqual(m["better_line"]["by"], f"playbook:{answer['id']}")
        self.assertEqual(m["better_line"]["text"], answer["answer"])
        self.assertEqual(m["better_line"]["check"], "ok")
        # Prep for the lead now reads both calls.
        self.assertEqual(len(self.ok("GET", f"/api/leads/{lead['id']}/prep")["calls"]), 2)

    def test_with_a_model_the_answers_go_into_the_line_writers_prompt(self):
        pack = load_pack(ROOT / "packs/demo-solar")
        index = pack_index(pack)
        report = self.service._read("example-solar-01", "report.json")
        answers = [{"id": "a1", "criterion": "objection-handling", "objection": "A competitor quoted less",
                    "answer": "What does the other quote include?", "check": {"sources": []}},
                   {"id": "a2", "criterion": "discovery", "objection": "Wants a price first",
                    "answer": "How big is your monthly bill?", "check": {"sources": []}}]
        found = [{"criterion": "objection-handling", "kind": "weak", "segment": "s05", "why": "Defended the price."}]
        from tests.test_coach import per_skill

        llm = ScriptedLLM({"moments": {"moments": found}, "moments-one": per_skill(found, pack),
                           "line": [{"line": "Our inverter has a 10-year warranty.", "passages": []}] * 2,
                           "verify-lines": {"checks": []}})
        from sel.coach import MomentFinder

        coaching = diagnose(report, pack, index, finder=MomentFinder(llm), writer=LineWriter(llm, index, answers),
                            answers=answers)
        prompt = next(u for n, u in llm.prompts if n == "line")
        self.assertIn("Answers the team approved", prompt)
        self.assertIn("What does the other quote include?", prompt)
        self.assertNotIn("monthly bill", prompt)  # only the moment's own criterion
        m = coaching["moments"][0]
        # Both model lines were refused; the approved answer stands in, checked like any line.
        self.assertEqual((m["better_line"]["by"], len(m["rejected_lines"])), ("playbook:a1", 2))

    def test_escalations_only_for_the_three_cases(self):
        # A contradicted claim on a practice call never escalates; on a real call it does.
        transcript = Transcript.from_dict(json.loads((SOLAR / "transcript.json").read_text(encoding="utf-8")))
        self.service.create_simulated(transcript, None, None, {"pack_id": "demo-solar", "rep_id": self.other,
                                                                "kind": "practice"})
        self.service.wait_idle()
        self.service.run_coach(self.other)
        self.assertEqual(self.ok("GET", "/api/team")["escalations"], [])

        # A Coach that asks for anything else is refused; only the three reasons reach a manager.
        class Pushy:
            name = "coach:model:pushy"

            def decide(self, state):
                return [{"action": "escalate_to_manager", "reason": "low_score", "note": "Scores are low."},
                        {"action": "escalate_to_manager", "reason": "missed_follow_up"},
                        {"action": "escalate_to_manager", "reason": "failed_drill", "drill_id": "nope"}]

        self.service._coach_factory = Pushy
        self.assign()
        self.assertEqual(self.ok("GET", "/api/team")["escalations"], [])
        log = self.ok("GET", f"/api/coach/{self.rep}")["actions"]
        self.assertTrue(log and all(not e["applied"] for e in log if e["by"] == "coach:model:pushy"))

        from sel.coach import RuleCoach

        self.service._coach_factory = RuleCoach
        self.service.run_coach(self.rep)
        reasons = [e["reason"] for e in self.ok("GET", "/api/team")["escalations"]]
        self.assertEqual(reasons, ["contradicted_claim"])
        self.assertTrue(set(reasons) <= set(ESCALATION_REASONS))
        today = self.ok("GET", f"/api/today?person={self.manager}")
        self.assertEqual([e["reason"] for e in today["escalations"]], ["contradicted_claim"])


class LibrarianUnitTests(unittest.TestCase):
    def test_a_rep_answer_quoting_a_deals_price_is_not_proposed(self):
        call = {"id": "c1", "pack_id": "demo-solar"}
        moment = {"id": "m1", "kind": "strong", "criterion": "discovery", "customer_said": {"quote": "How much?"},
                  "evidence": {"quote": "It costs NPR 4,20,000 with installation.", "segment_ids": ["s03"]}}
        self.assertEqual(candidates([(call, {"moments": [moment]})], [], set()), [])
        moment["evidence"]["quote"] = "Before the price: how big is your monthly bill?"
        self.assertEqual(len(candidates([(call, {"moments": [moment]})], [], set())), 1)

    def test_the_same_words_are_proposed_once(self):
        moment = {"id": "m1", "kind": "strong", "criterion": "discovery", "customer_said": {"quote": "How much?"},
                  "evidence": {"quote": "Before the price: how big is your monthly bill?", "segment_ids": ["s03"]}}
        calls = [({"id": f"c{i}", "pack_id": "demo-solar"}, {"moments": [moment]}) for i in range(3)]
        self.assertEqual([c["call_id"] for c in candidates(calls, [], set())], ["c0"])
        on_file = [{"criterion": "discovery", "answer": "Before the price, how big is your monthly bill"}]
        self.assertEqual(candidates(calls, [], set(), on_file), [])


class SlowModelServer:
    """A model whose health check hangs until released: a server that never answers."""

    name = "slow"

    def __init__(self):
        self.gate = threading.Event()
        self.probes = 0

    def health(self):
        self.probes += 1
        self.gate.wait(20)
        return {"ok": True, "models": [], "error": None}


class ProbeNeverBlocksTests(TeamApi):
    """The health probe for the analysis and chat models runs in the background: a page request answers with
    the rules at once, however long the probe takes (no server running: seconds on Windows)."""

    def setUp(self):
        super().setUp()
        self.server = SlowModelServer()
        self.old = (service_mod._ANALYSIS_PROBE, service_mod._CHAT_PROBE)
        service_mod._ANALYSIS_PROBE = service_mod._Probe(lambda: self.server)
        service_mod._CHAT_PROBE = service_mod._Probe(lambda: self.server)
        self.service._analysis_llm_factory = service_mod.default_analysis_llm
        self.service._chat_factory = service_mod.default_chat_factory

    def tearDown(self):
        self.server.gate.set()
        service_mod._ANALYSIS_PROBE, service_mod._CHAT_PROBE = self.old
        super().tearDown()

    def timed(self, path):
        t = time.perf_counter()
        out = self.ok("GET", path)
        return out, time.perf_counter() - t

    def test_prep_and_the_one_to_one_answer_while_the_probe_hangs(self):
        lead = self.ok("POST", "/api/leads", {"name": "Ramesh", "rep_id": self.rep}, 201)
        self.assign()
        self.ok("PUT", "/api/calls/example-solar-01/lead", {"lead_id": lead["id"]})
        for _ in range(3):  # the probe is still hanging on every request
            brief, took = self.timed(f"/api/leads/{lead['id']}/prep")
            self.assertLess(took, 1.0)
            self.assertEqual(brief["by"], "rules (no language model)")
            self.assertNotIn("model_pending", brief)
            view, took = self.timed(f"/api/team/{self.rep}")
            self.assertLess(took, 1.0)
            self.assertFalse(view["brief"]["model_pending"])
        self.assertEqual(self.server.probes, 2)  # one probe per model, not one per request

    def test_a_model_that_answers_is_used_from_the_next_view(self):
        lead = self.ok("POST", "/api/leads", {"name": "Ramesh", "rep_id": self.rep}, 201)
        self.ok("PUT", "/api/calls/example-solar-01/lead", {"lead_id": lead["id"]})
        self.ok("GET", f"/api/leads/{lead['id']}/prep")
        self.server.gate.set()
        deadline = time.monotonic() + 5
        while service_mod._ANALYSIS_PROBE.get() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(self.ok("GET", f"/api/leads/{lead['id']}/prep")["model_pending"])
        self.service.wait_idle()

    def test_an_expired_answer_is_served_while_it_is_refreshed(self):
        probe = service_mod._Probe(lambda: self.server, ttl=0.0)
        self.server.gate.set()
        deadline = time.monotonic() + 5
        while probe.get() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        self.server.gate.clear()
        t = time.perf_counter()
        self.assertIs(probe.get(), self.server)  # expired: last answer now, a new probe behind it
        self.assertLess(time.perf_counter() - t, 0.5)


if __name__ == "__main__":
    unittest.main()
