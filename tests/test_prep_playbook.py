"""Leads, the playbook overlay (Librarian) and Prep, with scripted stand-ins for the models."""

import json
import tempfile
import unittest
from pathlib import Path

from sel.cli import _report_for_example
from sel.coach import diagnose
from sel.leads import LeadStore
from sel.live import pack_index
from sel.llm import LLMStats
from sel.pack import load_pack
from sel.playbook import PlaybookStore, candidates, match, propose
from sel.prep import build_brief
from sel.scoring import score_report

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
        return reply(user) if callable(reply) else reply


def solar():
    pack = load_pack(ROOT / "packs/demo-solar")
    report = _report_for_example(SOLAR, ROOT / "packs/demo-solar")
    data = report.to_dict()
    data["score"] = score_report(report, pack)
    fixture = json.loads((SOLAR / "coaching.json").read_text(encoding="utf-8"))
    return pack, data, diagnose(data, pack, pack_index(pack), fixture=fixture)


class LeadStoreTests(unittest.TestCase):
    def test_add_update_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LeadStore(Path(tmp) / "leads")
            a = store.add("  Ramesh  Thapa ", "+977 9841-234567 (mobile)", "asha")
            self.assertEqual((a["id"], a["name"], a["phone"]), ("ramesh-thapa", "Ramesh Thapa", "+977 9841-234567"))
            self.assertEqual(store.add("Ramesh Thapa")["id"], "ramesh-thapa-2")
            store.link(a["id"], "c1")
            store.link(a["id"], "c1")
            self.assertEqual(store.get(a["id"])["call_ids"], ["c1"])
            store.update(a["id"], notes="Wants a 5 kW system")
            self.assertEqual(store.get(a["id"])["notes"], "Wants a 5 kW system")
            self.assertEqual(len(store.list("asha")), 1)
            with self.assertRaises(ValueError):
                store.add("")
            with self.assertRaises(KeyError):
                store.get("../x")


class LibrarianTests(unittest.TestCase):
    def setUp(self):
        self.pack, self.report, self.coaching = solar()
        self.index = pack_index(self.pack)
        # A rep who handled the price objection well, and an endorsed line.
        for m in self.coaching["moments"]:
            if m["criterion"] == "objection-handling":
                m["state"] = "endorsed"
        self.call = {"id": "c1", "pack_id": "demo-solar"}

    def test_candidates_are_real_words_anonymised(self):
        cands = candidates([(self.call, self.coaching)], ["Ramesh", "Asha"], set())
        endorsed = next(c for c in cands if c["criterion"] == "objection-handling")
        self.assertEqual(endorsed["answer_from"], "endorsed line")
        self.assertIn("other quote include", endorsed["answer"])
        self.assertNotIn("3,60,000", endorsed["objection"])
        self.assertEqual(candidates([(self.call, self.coaching)], [], {(c["call_id"], c["moment_id"]) for c in cands}), [])

    def test_proposals_are_gated_and_the_model_only_picks_and_labels(self):
        cands = candidates([(self.call, self.coaching)], ["Ramesh"], set())
        cands.append({**cands[0], "moment_id": "mx", "answer": "Our inverter has a 10-year warranty, no extra cost."})
        llm = ScriptedLLM({"librarian": {"answers": [{"moment": i, "objection": "A competitor quoted less"} for i in range(len(cands))]
                                         + [{"moment": 99, "objection": "made up"}]}})
        proposals, refused = propose(cands, self.pack, self.index, llm)
        self.assertTrue(all(p["answer"] in [c["answer"] for c in cands] for p in proposals))  # the words are never the model's
        self.assertEqual(proposals[0]["objection"], "A competitor quoted less")
        self.assertEqual(len(refused), 1)
        self.assertIn("Care Plan", refused[0]["check"]["reasons"][0])

    def test_match_and_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = PlaybookStore(Path(tmp))
            proposals, _ = propose(candidates([(self.call, self.coaching)], [], set()), self.pack, self.index)
            data = store.get("demo-solar")
            data["answers"] = [{**p, "status": "approved"} for p in proposals]
            store.save(data)
            approved = store.approved("demo-solar", "objection-handling")
            self.assertEqual(len(approved), 1)
            self.assertEqual(match(approved, "That is too expensive, another company quoted less")[0]["id"], approved[0]["id"])
            self.assertEqual(match(approved, "When will the engineer come?"), [])


class PrepTests(unittest.TestCase):
    def setUp(self):
        self.pack, self.report, self.coaching = solar()
        self.lead = {"id": "ramesh", "name": "Ramesh", "phone": None, "rep_id": "asha", "notes": ""}
        self.calls = [({"id": "c1", "started_at": "2026-10-07T14:00:00+05:45"}, self.report, self.coaching)]
        self.answer = {"id": "a1", "criterion": "objection-handling", "objection": "A competitor quoted less",
                       "answer": "What does the other quote include?", "status": "approved"}

    def test_rules_brief_uses_the_team_answer(self):
        brief = build_brief(self.lead, self.calls, [self.answer], {"title": "Price objections"})
        o = brief["likely_objections"][0]
        self.assertEqual((o["answer"]["from"], o["answer"]["text"]), ("team answer", "What does the other quote include?"))
        self.assertEqual(brief["summary"]["by"], "rules (no language model)")
        self.assertTrue(any(p["owner"] == "customer" for p in brief["promises"]))
        self.assertTrue(any(g.get("criterion") == "discovery" for g in brief["gaps"]))
        # Without a team answer, the coaching's better line for that moment.
        o = build_brief(self.lead, self.calls, [], None)["likely_objections"][0]
        self.assertEqual(o["answer"]["from"], "better line")

    def test_model_points_by_id_and_a_summary_with_made_up_numbers_is_dropped(self):
        llm = ScriptedLLM({"prep": {"likely_objections": ["O1", "Q9"], "gaps": ["G1", "O1"],
                                    "summary": "Ramesh wants a 7 kW system for 9 lakh."}})
        brief = build_brief(self.lead, self.calls, [self.answer], None, llm)
        self.assertEqual([o["id"] for o in brief["likely_objections"]], ["O1"])
        self.assertEqual([g["id"] for g in brief["gaps"]], ["G1"])
        self.assertEqual(brief["summary"]["by"], "rules (no language model)")
        llm.replies["prep"] = {"likely_objections": ["O1"], "gaps": [], "summary": "Price is the concern; confirm the site visit day."}
        self.assertTrue(build_brief(self.lead, self.calls, [], None, llm)["summary"]["by"].startswith("model:"))
        _, prompt = llm.prompts[0]
        self.assertNotIn('"segments"', prompt)  # reports, not raw transcripts


if __name__ == "__main__":
    unittest.main()
