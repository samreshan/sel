"""Practice and measure: personas, the AI customer, drills and their debriefs, transfer checks, the Coach's actions."""

import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from sel.adapters import FixtureAnalyzer, FixtureTranscriber
from sel.ai_customer import AICustomer, rules_reply
from sel.coach import RuleCoach, apply_actions
from sel.drills import code_verdict, judge
from sel.llm import LLMError, LLMStats
from sel.models import Transcript
from sel.pack import load_pack
from sel.persona import anonymise, build_persona, rules_persona
from sel.server import serve
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
        self.prompts.append((name, system, user))
        reply = self.replies[name]
        if isinstance(reply, list):
            reply = reply.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply(user) if callable(reply) else reply


MOMENT = {"criterion": "objection-handling", "why": "Defended the price instead of asking.",
          "customer_said": {"segment_id": "s04", "quote": "That is a little expensive, Ramesh here. Another company quoted NPR 3,60,000."},
          "evidence": {"segment_ids": ["s05"], "quote": "Our panels have a 25-year warranty, Asha says."}}


class PersonaTests(unittest.TestCase):
    def setUp(self):
        self.pack = load_pack(ROOT / "packs/demo-solar")

    def test_anonymise_removes_people_and_deals(self):
        text = "Namaste Ramesh ji, call 9841234567 or ramesh@example.com. Arko company le 3 lakh 60 ma dinchu bhaneko."
        out = anonymise(text, ["Ramesh"])
        for leaked in ("Ramesh", "9841234567", "ramesh@example.com", "3 lakh", "60"):
            self.assertNotIn(leaked, out)
        self.assertIn("a different price", out)

    def test_rules_persona_repeats_the_real_objection_anonymised(self):
        p = rules_persona(MOMENT, self.pack, ["Ramesh", "Asha"], "d1")
        self.assertEqual(p["opening"], "That is a little expensive, here. Another company quoted a different price.")
        self.assertNotIn("Ramesh", json.dumps(p))

    def test_model_persona_sees_only_anonymised_words_and_its_output_is_cleaned(self):
        llm = ScriptedLLM({"persona": {"about": "Ramesh owns a house in Lalitpur.", "wants": "A fair price.",
                                       "opening": "Another company quoted NPR 3,60,000, call me on 9841234567.",
                                       "if_good": "Opens up.", "if_poor": "Stays doubtful."}})
        p = build_persona(MOMENT, self.pack, ["Ramesh", "Asha"], "d1", llm)
        _, _, prompt = llm.prompts[0]
        for leaked in ("Ramesh", "Asha", "3,60,000"):
            self.assertNotIn(leaked, prompt)
        self.assertNotIn("Ramesh", p["about"])
        self.assertNotIn("9841234567", p["opening"])
        self.assertNotIn("3,60,000", p["opening"])
        self.assertTrue(p["by"].startswith("model:"))
        self.assertIn(p["name"], ("Sita", "Hari", "Kamala", "Bishnu", "Gita", "Nabin", "Anita", "Suman", "Pooja", "Dipak"))


class AICustomerTests(unittest.TestCase):
    PERSONA = {"name": "Sita", "about": "A homeowner.", "wants": "Value.", "opening": "It is a little expensive.",
               "if_good": "Warms up.", "if_poor": "Stays doubtful.", "difficulty": 1}

    def test_rules_customer_softens_after_a_question_and_ends_on_a_dated_promise(self):
        lines = [{"role": "customer", "text": "It is a little expensive."}, {"role": "rep", "text": "What are you comparing it with?"}]
        self.assertEqual(rules_reply(lines, 1)["mood"], "warming")
        lines.append({"role": "rep", "text": "I'll email you the comparison by tomorrow evening."})
        self.assertTrue(rules_reply(lines, 1)["end_call"])

    def test_defending_first_needs_one_more_question(self):
        lines = [{"role": "customer", "text": "It is a little expensive."}, {"role": "rep", "text": "It has a great warranty."}]
        self.assertEqual(rules_reply(lines, 1)["mood"], "doubtful")
        lines.append({"role": "rep", "text": "What does the other quote include?"})
        self.assertEqual(rules_reply(lines, 1)["mood"], "calm")  # asked once, but defended first
        lines.append({"role": "rep", "text": "I'll email you the comparison by tomorrow evening."})
        refused = rules_reply(lines, 1)
        self.assertFalse(refused["end_call"])  # not convinced yet: no day is fixed
        self.assertIn("not ready", refused["say"])
        lines.append({"role": "rep", "text": "Which matters more to you, the price or how long it lasts?"})
        self.assertEqual(rules_reply(lines, 1)["mood"], "warming")

    def test_the_model_cannot_hang_up_early(self):
        llm = ScriptedLLM({"customer": {"say": "Okay, bye.", "end_call": True, "mood": "calm"}})
        ai = AICustomer(self.PERSONA, llm)
        lines = [{"role": "customer", "text": "It is a little expensive."}, {"role": "rep", "text": "What matters most to you?"}]
        self.assertFalse(ai.reply(lines)["end_call"])
        lines.append({"role": "rep", "text": "I'll email you the quote by tomorrow evening."})
        self.assertTrue(ai.reply(lines)["end_call"])  # a dated next step was agreed

    def test_model_reply_is_checked_and_falls_back_to_rules(self):
        llm = ScriptedLLM({"customer": [
            {"say": "Fine, what is in the price?", "end_call": False, "mood": "calm"},
            {"say": "[s02] CUSTOMER: ok", "end_call": False, "mood": "calm"},
            LLMError("timeout")]})
        ai = AICustomer(self.PERSONA, llm)
        lines = [{"role": "customer", "text": "It is a little expensive."}, {"role": "rep", "text": "Hello."}]
        self.assertEqual(ai.reply(lines)["say"], "Fine, what is in the price?")
        self.assertEqual(ai.reply(lines)["by"], "rules (no language model)")  # ids in the line: refused
        self.assertEqual(ai.reply(lines)["by"], "rules (no language model)")  # model error
        self.assertEqual(len(ai.timings), 3)
        # The persona is a stable system prefix; the transcript is the user message.
        names = {p[1] for p in llm.prompts}
        self.assertEqual(len(names), 1)
        self.assertIn("It is a little expensive.", llm.prompts[0][1])


class ToughCustomerTests(unittest.TestCase):
    """Code decides where the customer stands; the model only words it, and may not give in early."""

    PERSONA = {**AICustomerTests.PERSONA, "difficulty": 2}

    def lines(self, *rep):
        return TextCustomerTests.lines(self, *rep)

    def test_stance_counts_real_questions_only(self):
        from sel.ai_customer import stance

        self.assertEqual(stance(self.lines("Hello."), 2)["stance"], "holding")
        for offer in ("Can I send you a brochure?", "Does that make sense?", "It lasts long, right?",
                      "Would you like me to email it?"):
            with self.subTest(offer=offer):
                self.assertEqual(stance(self.lines(offer), 1)["asked"], 0)
        st = stance(self.lines("What are you comparing it with?", "And what is your monthly bill?"), 2)
        self.assertEqual((st["stance"], st["asked"], st["needed"]), ("softening", 2, 2))
        defended = stance(self.lines("Ours has a 25-year warranty.", "What are you comparing?", "How big is your roof?"), 2)
        self.assertEqual((defended["defended"], defended["needed"], defended["stance"]), (True, 3, "holding"))
        self.assertEqual(stance(self.lines("Hello.", "I'll call you on Sunday morning."), 2)["stance"], "refuse_step")

    def test_each_rep_turn_carries_its_note_and_the_prompts_still_extend(self):
        from sel.ai_customer import NOTES

        chat = TextCustomerTests.Chat(["Why is it so much?", "Hmm, my bill is about 3,000 a month."])
        ai = AICustomer(self.PERSONA, chat)
        ai.reply(self.lines("Ours has a 25-year warranty."))
        ai.reply(self.lines("Ours has a 25-year warranty.", "What is your monthly bill?"))
        a, b = chat.calls[0]["messages"], chat.calls[1]["messages"]
        self.assertEqual(b[:len(a)], a)  # the note on a turn never changes later
        self.assertTrue(a[-1]["content"].endswith(NOTES["holding"]))
        self.assertTrue(b[-1]["content"].endswith(NOTES["holding"]))  # defended first: one question is not enough
        self.assertIn("[note]", a[0]["content"])

    def test_a_model_that_gives_in_early_is_overruled(self):
        chat = TextCustomerTests.Chat(["Absolutely, that's very helpful!", "Sounds good, let's book it."])
        ai = AICustomer(self.PERSONA, chat)
        held = ai.reply(self.lines("Ours has a 25-year warranty."))
        self.assertEqual(held["by"], "rules (no language model)")
        self.assertEqual(ai.overruled, ["Absolutely, that's very helpful!"])
        asked = self.lines("What are you comparing?", "What is your monthly bill?")
        self.assertEqual(ai.reply(asked)["say"], "Sounds good, let's book it.")  # softened: the model may warm up
        self.assertEqual(len(ai.overruled), 1)

    def test_a_dated_step_before_softening_does_not_end_the_call(self):
        chat = TextCustomerTests.Chat(["Okay, Sunday works.", "I'm not ready for that yet."])
        ai = AICustomer(self.PERSONA, chat)
        early = ai.reply(self.lines("Hello.", "I'll call you on Sunday morning."))
        self.assertFalse(early["end_call"])
        self.assertEqual(early["by"], "rules (no language model)")  # "works" while holding: overruled
        self.assertIn("not ready", early["say"])
        self.assertTrue(chat.calls[0]["messages"][-1]["content"].endswith("Say you are not ready to commit, and why.]"))

    def test_json_format_gets_the_note_and_the_same_guard(self):
        llm = ScriptedLLM({"customer": {"say": "Absolutely, that's very helpful.", "end_call": True, "mood": "warming"}})
        ai = AICustomer(self.PERSONA, llm, fmt="json")
        out = ai.reply(self.lines("Ours has a 25-year warranty."))
        self.assertEqual(out["by"], "rules (no language model)")
        self.assertFalse(out["end_call"])
        self.assertIn("[note: you are not convinced yet", llm.prompts[0][2])

    def test_the_rules_customer_says_goodbye_unconvinced(self):
        lines = self.lines(*["It is the best on the market."] * 8)
        out = rules_reply(lines, 2)
        self.assertTrue(out["end_call"])
        self.assertEqual(out["mood"], "doubtful")
        self.assertIn("think about it", out["say"])


class JudgeTests(unittest.TestCase):
    def setUp(self):
        self.pack = load_pack(ROOT / "packs/demo-solar")
        self.t = Transcript.from_dict(json.loads((SOLAR / "transcript.json").read_text(encoding="utf-8")))

    def score(self, status, segment="s05"):
        return {"criteria": [{"id": "objection-handling", "status": status, "segment": segment, "detail": "d"}]}

    def test_code_verdicts(self):
        self.assertEqual(code_verdict(self.score("met"), "objection-handling")["verdict"], "pass")
        self.assertEqual(code_verdict(self.score("missed"), "objection-handling")["verdict"], "fail")
        self.assertEqual(code_verdict(self.score("na"), "objection-handling")["verdict"], "not_applicable")
        self.assertIsNone(code_verdict(self.score("not_scored"), "objection-handling"))

    def test_model_and_rules_must_agree_for_a_pass(self):
        llm = ScriptedLLM({"judge": {"verdict": "pass", "segment": "s05", "why": "Handled well."}})
        v = judge(self.t, self.score("missed"), self.pack, "objection-handling", llm)
        self.assertEqual(v["verdict"], "unsure")
        v = judge(self.t, self.score("met"), self.pack, "objection-handling", llm)
        self.assertEqual((v["verdict"], v["segment_ids"]), ("pass", ["s05"]))

    def test_never_came_up_is_decided_by_code(self):
        llm = ScriptedLLM({"judge": {"verdict": "fail", "segment": "s05", "why": "x"}})
        v = judge(self.t, self.score("na", None), self.pack, "objection-handling", llm)
        self.assertEqual(v["verdict"], "not_applicable")
        self.assertEqual(llm.prompts, [])

    def test_a_customer_line_is_moved_to_the_reps_reply_and_unknown_segments_refused(self):
        llm = ScriptedLLM({"judge": [{"verdict": "fail", "segment": "s04", "why": "x"},
                                     {"verdict": "fail", "segment": "s99", "why": "x"}]})
        self.assertEqual(judge(self.t, self.score("missed"), self.pack, "objection-handling", llm)["segment_ids"], ["s05"])
        v = judge(self.t, self.score("missed"), self.pack, "objection-handling", llm)
        self.assertEqual(v["by"], "rules (no language model)")
        self.assertIn("s99", v["refused"][0])


class CoachActionTests(unittest.TestCase):
    def state(self, **over):
        skill = {"key": "demo-solar/objection-handling", "pack_id": "demo-solar", "criterion": "objection-handling",
                 "title": "Price objections", "signal": "objections", "weight": 2, "weak": 1, "strong": 0,
                 "calls": ["c1"], "weak_calls": ["c1"], "moments": [{"call_id": "c1", "moment_id": "m2", "why": "w",
                                                                    "quote": "q", "at": "00:22"}]}
        base = {"skills": [skill], "calls_seen": 1, "transfer": [], "escalations": [], "drills": [], "contradicted": [],
                "closed": [], "focus": None}
        base.update(over)
        return base

    def test_a_turn_sets_a_focus_and_a_drill(self):
        state = self.state()
        acts = RuleCoach().decide(state)
        self.assertEqual([a["action"] for a in acts], ["set_focus", "create_drill"])
        coach_file = {}
        log = apply_actions(acts, state, coach_file, "coach:rules")
        self.assertTrue(all(e["applied"] for e in log))

    def test_transfer_pass_closes_the_focus_but_escalates_a_managers(self):
        focus = {"pack_id": "demo-solar", "criterion": "objection-handling", "title": "Price objections",
                 "since": "2026-10-16T00:00:00+00:00", "status": "active", "set_by": "coach:rules"}
        passed = {"call_id": "c9", "criterion": "objection-handling", "pack_id": "demo-solar", "verdict": "pass",
                  "at": "2026-10-17T00:00:00+00:00", "why": "Asked what the quote includes."}
        state = self.state(focus=focus, transfer=[passed])
        acts = RuleCoach().decide(state)
        self.assertEqual(acts[0]["action"], "close_focus")
        coach_file = {"focus": dict(focus)}
        apply_actions(acts, state, coach_file, "coach:rules")
        self.assertEqual(coach_file["history"][-1]["status"], "passed")
        self.assertEqual(coach_file["history"][-1]["evidence"]["call_id"], "c9")
        # A manager's focus: escalated, never closed by the Coach.
        mfocus = {**focus, "set_by_role": "manager"}
        state = self.state(focus=mfocus, transfer=[passed])
        acts = RuleCoach().decide(state)
        self.assertEqual(acts[0]["reason"], "change_manager_focus")
        coach_file = {"focus": dict(mfocus)}
        log = apply_actions([{"action": "close_focus", "call_id": "c9"}] + acts, state, coach_file, "coach:model")
        self.assertIn("manager", log[0]["refused"])
        self.assertEqual(coach_file["focus"]["status"], "active")
        self.assertEqual(coach_file["escalations"][0]["reason"], "change_manager_focus")

    def test_close_needs_evidence_and_escalations_need_a_reason_on_the_list(self):
        focus = {"pack_id": "demo-solar", "criterion": "objection-handling", "title": "t", "since": "2026-10-16",
                 "status": "active"}
        coach_file = {"focus": dict(focus)}
        log = apply_actions([{"action": "close_focus", "call_id": "c9"},
                             {"action": "escalate_to_manager", "reason": "low_score"},
                             {"action": "escalate_to_manager", "reason": "contradicted_claim", "call_id": "c1", "claim_id": "c3"},
                             {"action": "raise_difficulty", "drill_id": "dx"}], self.state(focus=focus), coach_file, "x")
        self.assertEqual([e["applied"] for e in log], [False, False, False, False])
        state = self.state(focus=focus, contradicted=[{"call_id": "c1", "claim_id": "c3", "claim": "10-year warranty"}],
                           drills=[{"id": "d1", "pack_id": "demo-solar", "criterion": "objection-handling", "fails": 3,
                                    "last_verdict": "fail", "difficulty": 1, "last_difficulty": 1, "source": {}}])
        acts = RuleCoach().decide(state)
        reasons = sorted(a.get("reason", "") for a in acts if a["action"] == "escalate_to_manager")
        self.assertEqual(reasons, ["contradicted_claim", "failed_drill"])
        apply_actions(acts, state, coach_file, "coach:rules")
        log = apply_actions(acts, state, coach_file, "coach:rules")  # the same again: already escalated
        self.assertTrue(all(not e["applied"] for e in log if e["action"] == "escalate_to_manager"))


def fixture_factory(call, speakers):
    return FixtureTranscriber(SOLAR / "transcript.json"), FixtureAnalyzer(SOLAR / "analysis.json"), None


class PracticeLoopTests(unittest.TestCase):
    """The loop over the API, without models: weak moment → focus and drill → practice → debrief → transfer."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.service = CallService(cls.tmp.name, ROOT / "packs", analysis_factory=fixture_factory)
        cls.service.create_from_example(SOLAR, "demo-solar")
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

    def req(self, method, path, body=None, raw=None):
        data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
        r = urllib.request.Request(self.base + path, data=data, method=method)
        try:
            with urllib.request.urlopen(r, timeout=20) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def until(self, fn, timeout=10.0, why=lambda: ""):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            value = fn()
            if value:
                return value
            time.sleep(0.03)
        self.fail(f"timed out {why()}")

    def coach(self, rep):
        status, c = self.req("GET", f"/api/coach/{rep}")
        self.assertEqual(status, 200, c)
        return c

    def drill_room(self, drill_id, rep):
        status, out = self.req("POST", f"/api/drills/{drill_id}/start", {"by": rep})
        self.assertEqual(status, 201, out)
        code, client = out["room"]["code"], out["client"]
        self.assertTrue(any(p["ai"] for p in out["room"]["participants"]))
        status, room = self.req("POST", f"/api/sim/rooms/{code}/start", {"client": client})
        self.assertEqual(room["lines"][0]["source"], "ai")  # the AI customer opens with the real objection
        return code, client, room

    def say(self, code, client, text):
        n = len(self.hub.rooms[code].lines)
        self.req("POST", f"/api/sim/rooms/{code}/lines", {"client": client, "text": text})
        self.until(lambda: len(self.hub.rooms[code].lines) >= n + 2 or self.hub.rooms[code].state != "live")

    def finish(self, code, client, drill_id, attempts):
        self.req("POST", f"/api/sim/rooms/{code}/finish?client={client}", raw=b"")
        return self.until(lambda: (d := self.service.drills.get(drill_id))["attempts"] and len(d["attempts"]) == attempts
                          and d["attempts"][-1].get("status") == "done" and d)

    def test_the_whole_loop(self):
        rep = self.service.add_person("Asha Loop", "rep")["id"]
        self.req("PUT", "/api/calls/example-solar-01/rep", {"rep_id": rep})
        # The Coach sets a focus and a drill for it from the real weak moment; the persona is built (rules).
        coach = self.until(lambda: (c := self.coach(rep))["drills"] and c)
        self.assertEqual(coach["focus"]["criterion"], "objection-handling")
        escalations = coach["escalations"]
        self.assertEqual([e["reason"] for e in escalations], ["contradicted_claim"])  # the 10-year inverter claim
        drill_id = coach["drills"][0]["id"]
        drill = self.until(lambda: (d := self.req("GET", f"/api/drills/{drill_id}")[1])["status"] == "ready" and d)
        self.assertIn("a different price", drill["persona"]["opening"])
        self.assertNotIn("3,60,000", json.dumps(drill["persona"]))

        # Attempt 1: defend the price; the customer pushes back. Fail.
        code, client, _ = self.drill_room(drill_id, rep)
        self.say(code, client, "Our panels have a 25-year performance warranty.")
        self.assertIn(self.hub.rooms[code].lines[-1]["mood"], ("doubtful", "annoyed"))
        d = self.finish(code, client, drill_id, 1)
        self.assertEqual(d["attempts"][0]["verdict"], "fail")
        self.assertEqual(d["attempts"][0]["debrief"]["real_answer"], drill["moment"]["quote"])
        self.assertEqual(d["attempts"][0]["practice_answer"] if False else d["attempts"][0]["debrief"]["practice_answer"]["text"],
                         "Our panels have a 25-year performance warranty.")
        call = self.req("GET", f"/api/calls/{d['attempts'][0]['sim_call_id']}")[1]
        self.until(lambda: self.req("GET", f"/api/calls/{call['id']}")[1]["status"] == "done")
        call = self.req("GET", f"/api/calls/{call['id']}")[1]
        self.assertEqual((call["kind"], call["drill_id"], call.get("coaching")), ("practice", drill_id, None))

        # Attempt 2: ask first. Pass, and the Coach makes the drill harder.
        code, client, _ = self.drill_room(drill_id, rep)
        self.say(code, client, "What does the other quote include: which panels and inverter?")
        d = self.finish(code, client, drill_id, 2)
        self.assertEqual(d["attempts"][1]["verdict"], "pass")
        self.until(lambda: self.service.drills.get(drill_id)["difficulty"] == 2)

        # A later real call where the rep handles the objection: transfer passes; the Coach closes the focus.
        moved = json.loads((SOLAR / "transcript.json").read_text(encoding="utf-8"))
        moved["segments"][4]["text"] = "What does the other quote include: which panel brand and inverter?"
        path = Path(self.tmp.name) / "handled.json"
        path.write_text(json.dumps(moved), encoding="utf-8")
        self.service._factory = lambda call, speakers: (FixtureTranscriber(path), FixtureAnalyzer(SOLAR / "analysis.json"), None)
        status, out = self.req("POST", f"/api/calls?pack=demo-solar&started_at=2026-10-20T10:00:00%2B05:45"
                                       f"&filename=c.wav&rep_id={rep}", raw=b"RIFF")
        self.assertEqual(status, 201)
        coach = self.until(lambda: (c := self.req("GET", f"/api/coach/{rep}")[1])["history"] and c, timeout=20,
                           why=lambda: json.dumps([self.service.coach_store.get(rep).get("transfer"),
                                                   self.req("GET", f"/api/calls/{out['id']}")[1].get("status")]))
        passed = coach["history"][-1]
        self.assertEqual((passed["status"], passed["criterion"]), ("passed", "objection-handling"))
        self.assertEqual(passed["evidence"]["call_id"], out["id"])
        self.assertEqual(coach["transfer"][-1]["segment_ids"], ["s05"])
        self.assertNotEqual((coach["focus"] or {}).get("criterion"), "objection-handling")  # the next focus
        today = self.req("GET", f"/api/today?person={rep}")[1]
        self.assertEqual(today["transfer"][-1]["verdict"], "pass")

    def test_drill_needs_a_rep_and_a_weak_moment(self):
        status, out = self.req("POST", "/api/calls/example-solar-01/moments/m1/drill", {})
        self.assertIn(status, (201, 400))
        c = self.req("GET", "/api/calls/example-solar-01/coaching")[1]
        strong = next(m for m in c["moments"] if m["kind"] == "strong")
        rep = self.service.add_person("Bikash Loop", "rep")["id"]
        status, out = self.req("POST", f"/api/calls/example-solar-01/moments/{strong['id']}/drill", {"by": rep})
        self.assertEqual(status, 400)
        self.assertEqual(self.req("POST", "/api/drills/nope/start", {})[0], 404)


if __name__ == "__main__":
    unittest.main()


class TextCustomerTests(unittest.TestCase):
    """The default plain-text format: chat turns, a spoken line, code-decided ending and mood."""

    PERSONA = AICustomerTests.PERSONA

    class Chat:
        name = "chat"

        def __init__(self, replies):
            self.replies = list(replies)
            self.calls = []
            self.stats = LLMStats()

        def complete_chat(self, messages, max_tokens=256, stop=None):
            self.calls.append({"messages": [dict(m) for m in messages], "max_tokens": max_tokens, "stop": stop})
            reply = self.replies.pop(0) if self.replies else "Okay."
            if isinstance(reply, Exception):
                raise reply
            return reply

    def lines(self, *rep):
        out = [{"role": "customer", "text": "It is a little expensive."}]
        for i, text in enumerate(rep):
            out.append({"role": "rep", "text": text})
            if i < len(rep) - 1:
                out.append({"role": "customer", "text": f"Reply {i}."})
        return out

    def test_text_is_the_default_and_each_prompt_extends_the_last(self):
        chat = self.Chat(["Customer: \"What does it include?\" (pauses)", "Hmm, and the warranty? (pauses)"])
        ai = AICustomer(self.PERSONA, chat)
        self.assertEqual(ai.format, "text")
        first = ai.reply(self.lines("Hello."))
        self.assertEqual(first["say"], "What does it include?")  # label and quotes removed
        self.assertFalse(first["end_call"])
        second = ai.reply(self.lines("Hello.", "Ours is a 5-year warranty."))
        self.assertEqual(second["say"], "Hmm, and the warranty?")  # the stage direction is dropped
        a, b = chat.calls[0]["messages"], chat.calls[1]["messages"]
        self.assertEqual(b[:len(a)], a)  # the second prompt starts with the whole first one
        self.assertEqual([m["role"] for m in a], ["system", "user", "assistant", "user"])
        self.assertIn("It is a little expensive.", a[0]["content"])
        self.assertEqual(chat.calls[0]["stop"], ["\n"])
        self.assertLessEqual(chat.calls[0]["max_tokens"], 60)

    def test_code_decides_the_mood_and_the_ending(self):
        chat = self.Chat(["Fine, thanks. End call.", "Great, talk then."])
        ai = AICustomer(self.PERSONA, chat)
        plain = ai.reply(self.lines("What are you comparing?"))
        self.assertFalse(plain["end_call"])  # the model's "End call." does not hang up
        self.assertEqual(plain["say"], "Fine, thanks.")
        self.assertEqual(plain["mood"], rules_reply(self.lines("What are you comparing?"), 1)["mood"])
        done = ai.reply(self.lines("What are you comparing?", "I'll email you the quote by tomorrow evening."))
        self.assertTrue(done["end_call"])  # a dated next step: code ends the call, in the model's words
        self.assertEqual(done["say"], "Great, talk then.")

    def test_unusable_lines_and_errors_fall_back_to_rules(self):
        chat = self.Chat(["[s02] ok", "", LLMError("timeout")])
        ai = AICustomer(self.PERSONA, chat)
        for _ in range(3):
            self.assertEqual(ai.reply(self.lines("Hello."))["by"], "rules (no language model)")
        self.assertEqual(len(ai.timings), 3)

    def test_json_format_can_be_chosen(self):
        from unittest import mock
        with mock.patch.dict("os.environ", {"SEL_CUSTOMER_FORMAT": "json"}):
            self.assertEqual(AICustomer(self.PERSONA, self.Chat([])).format, "json")
        self.assertEqual(AICustomer(self.PERSONA, self.Chat([]), fmt="json").format, "json")

    def test_warm_up_reads_the_prompt_the_first_reply_starts_with(self):
        chat = self.Chat(["", "Fine, go on."])
        ai = AICustomer(self.PERSONA, chat)
        ai.warm()
        ai.reply(self.lines("Hello."))
        warm, first = chat.calls[0], chat.calls[1]
        self.assertEqual(warm["max_tokens"], 1)
        self.assertEqual(first["messages"][:len(warm["messages"])], warm["messages"])

    def test_drill_room_warms_up_before_the_first_reply(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = CallService(tmp, ROOT / "packs")
            hub = SimulatorHub(service, speech_status=lambda: (False, "off"))
            chat = self.Chat(["", "What does the other quote include?"])
            room, client = hub.create_drill_room("demo-solar", AICustomer(self.PERSONA, chat), "d1", "Asha", None,
                                                 "Sita", "+05:45")
            deadline = time.monotonic() + 5
            while not chat.calls and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(chat.calls[0]["max_tokens"], 1)  # warmed before anyone spoke
            hub.start(room.code, client)
            hub.add_line(room.code, client, "Hello, what does the other quote include?")
            while len(room.lines) < 3 and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(room.lines[-1]["text"], "What does the other quote include?")
            hub.close()
            service.wait_idle()
