import json
import unittest
from pathlib import Path

from sel.analyzer_llm import LLMAnalyzer, chunk_segments, label_speakers, snap_quote
from sel.llm import LLMStats, _parse_json
from sel.models import Transcript
from sel.pack import load_pack
from sel.pipeline import build_report

ROOT = Path(__file__).resolve().parent.parent
SOLAR = ROOT / "examples/calls/solar-01"


class ScriptedLLM:
    """Stands in for a model: returns canned JSON per request name and records prompts."""

    name = "scripted"

    def __init__(self, replies):
        self.replies = replies
        self.prompts = []
        self.stats = LLMStats()

    def complete_json(self, system, user, schema, name, max_tokens=2048):
        self.prompts.append((name, user))
        self.stats.calls += 1
        reply = self.replies[name]
        return reply(user) if callable(reply) else reply


EXTRACT = {
    "questions": [{"segment": "s06", "summary": "Asked about EMI."}],
    # Small models sometimes append the timestamp to the id; Sel keeps the id part.
    "objections": [{"segment": "s04 00:22", "summary": "Competitor is cheaper."}],
    "commitments": [
        # "when" is close to the spoken words (the model wrote "tomorrow evening"); Sel keeps the transcript's words.
        {"segment": "s11", "speaker": "REP", "action": "email the quotation", "channel": "email",
         "when": "by tomorrow evening"},
        {"segment": "s13", "speaker": "REP", "action": "send an engineer", "channel": "visit",
         "when": "sometime next week"},
        # A time that was never said: dropped, so the draft will ask for a date.
        {"segment": "s14", "speaker": "CUSTOMER", "action": "call back", "channel": "call", "when": "Monday 10am"},
    ],
    "claims": [
        {"segment": "s05", "claim": "Inverter warranty is 10 years."},
        {"segment": "s09", "claim": "NEA approval takes two weeks."},
        {"segment": "s42", "claim": "Cites a segment that does not exist."},
        # Cites the customer's question (s08); the rep's answer is s09.
        {"segment": "s08", "claim": "NEA approval normally comes in two weeks."},
    ],
}


def verify_reply(user):
    # Pick the passage id that the prompt lists for the warranty document.
    pid = next(line.split()[0] for line in user.splitlines() if "docs/warranty.md" in line and "Inverter" in line)
    return {"checks": [
        {"claim_index": 0, "verdict": "contradicted", "passage_id": pid, "explanation": "Standard is 5 years."},
        {"claim_index": 1, "verdict": "supported", "passage_id": "P999", "explanation": "made up"},
        {"claim_index": 2, "verdict": "unverified", "passage_id": "", "explanation": "-"},
        {"claim_index": 3, "verdict": "unverified", "passage_id": "", "explanation": "-"},
    ]}


COACH = {"coaching": [
    {"criterion": "clear-next-step", "segment": "[s13]", "summary": "No day for the visit.", "suggestion": "Offer two days."},
    {"criterion": "discovery", "segment": "s99", "summary": "Invented segment.", "suggestion": "x"},
]}


class AnalyzerTests(unittest.TestCase):
    def setUp(self):
        self.pack = load_pack(ROOT / "packs/demo-solar")
        self.transcript = Transcript.from_dict(json.loads((SOLAR / "transcript.json").read_text()))
        self.llm = ScriptedLLM({
            "customer": {k: EXTRACT[k] for k in ("questions", "objections")},
            "commitments": {"commitments": EXTRACT["commitments"]},
            "claims": {"claims": EXTRACT["claims"]},
            "verify": verify_reply, "coach": COACH})
        analyzer = LLMAnalyzer(self.llm)
        self.analysis = analyzer.analyze(self.transcript, self.pack)
        self.analysis_stats = analyzer.stats
        self.report = build_report(self.transcript, self.analysis, self.pack)

    def test_quotes_are_the_transcript_words(self):
        k1 = self.analysis.commitments[0]
        self.assertEqual(k1.evidence.quote, self.transcript.segment("s11").text)
        self.assertEqual(k1.owner, "S1")
        self.assertEqual(k1.due_text, "by tomorrow evening")
        objection = next(f for f in self.report.findings if f.kind == "objection")
        self.assertEqual(objection.evidence.segment_ids, ["s04"])

    def test_due_phrase_not_in_the_call_is_dropped(self):
        k3 = next(c for c in self.report.commitments if c.action == "call back")
        self.assertIsNone(k3.due_text)
        self.assertEqual(k3.due.status, "missing")
        self.assertEqual(self.analysis_stats["due_phrases_not_in_call"], 1)

    def test_claim_on_customer_line_moves_to_rep_answer(self):
        c4 = next(c for c in self.report.claims if c.id == "c4")
        self.assertEqual(c4.evidence.segment_ids, ["s09"])

    def test_unknown_segment_is_refused_visibly(self):
        self.assertIn("c3", {r.item_id for r in self.report.rejected})

    def test_claim_sources_come_from_retrieved_passages_not_the_model(self):
        c1 = next(c for c in self.report.claims if c.id == "c1")
        self.assertEqual(c1.verdict, "contradicted")
        self.assertEqual(c1.sources[0].source, "docs/warranty.md")
        self.assertIn("5-year", c1.sources[0].quote)

    def test_verdict_citing_unknown_passage_is_downgraded(self):
        c2 = next(c for c in self.report.claims if c.id == "c2")
        self.assertEqual(c2.verdict, "unverified")
        self.assertIn("c2", {r.item_id for r in self.report.rejected})

    def test_coaching_with_invented_segment_is_refused(self):
        coaching = [f for f in self.report.findings if f.kind == "coaching"]
        self.assertEqual([f.criterion for f in coaching], ["clear-next-step"])

    def test_dates_resolved_by_code(self):
        due = {c.action: c.due for c in self.report.commitments}
        self.assertEqual(due["email the quotation"].date, "2026-10-08")
        self.assertEqual(due["send an engineer"].status, "ambiguous")

    def test_prompts_carry_segment_ids_and_roles(self):
        name, user = self.llm.prompts[0]
        self.assertEqual([p[0] for p in self.llm.prompts[:3]], ["customer", "commitments", "claims"])
        self.assertIn("[s01] REP: Hello", user)
        self.assertIn("[s02] CUSTOMER: Yes,", user)


class Helpers(unittest.TestCase):
    def setUp(self):
        self.transcript = Transcript.from_dict(json.loads((SOLAR / "transcript.json").read_text()))

    def test_snap_moves_quote_to_neighbouring_segment(self):
        seg, quote, adjusted = snap_quote("Is there an EMI option?", "s05", self.transcript)
        self.assertEqual((seg, adjusted), ("s06", True))

    def test_snap_leaves_unrelated_quote_for_the_gate(self):
        seg, quote, adjusted = snap_quote("barsat ma kam generate huncha", "s02", self.transcript)
        self.assertEqual((seg, quote, adjusted), ("s02", "barsat ma kam generate huncha", False))

    def test_chunks_cover_every_segment_with_overlap(self):
        chunks = chunk_segments(self.transcript.segments, max_chars=300, overlap=1)
        self.assertGreater(len(chunks), 1)
        covered = {s.id for c in chunks for s in c}
        self.assertEqual(covered, {s.id for s in self.transcript.segments})
        self.assertEqual(chunks[0][-1].id, chunks[1][0].id)

    def test_label_speakers(self):
        data = json.loads((SOLAR / "transcript.json").read_text())
        for s in data["segments"]:
            s["speaker"] = "S0"
        data["speakers"].append({"id": "S0", "role": "other", "name": None})
        unlabeled = Transcript.from_dict(data)
        truth = {s.id: s.speaker for s in self.transcript.segments}
        llm = ScriptedLLM({"speakers": {"labels": [
            {"segment": sid, "role": "REP" if spk == "S1" else "CUSTOMER"} for sid, spk in truth.items()]}})
        labeled = label_speakers(unlabeled, load_pack(ROOT / "packs/demo-solar"), llm)
        self.assertEqual({s.id: s.speaker for s in labeled.segments}, truth)
        self.assertEqual({s.id for s in labeled.speakers}, {"S1", "S2"})

    def test_parse_json_tolerates_fences_and_prose(self):
        self.assertEqual(_parse_json('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(_parse_json('Here you go: {"a": [1]} done'), {"a": [1]})



class DuePhrases(unittest.TestCase):
    def test_widen_over_adjacent_time_words_only(self):
        from sel.analyzer_llm import widen_due_phrase as widen
        line = "Huncha, ma bholi beluka samma quotation ra EMI details email garchu."
        self.assertEqual(widen("beluka samma", line), "bholi beluka samma")
        self.assertEqual(widen("Monday", "I will call you on Monday at 11 am, okay?"), "Monday at 11 am")
        self.assertEqual(widen("sukrabar", "Ok, ma pani ghar ma kura garera sukrabar call garchu."), "sukrabar")


if __name__ == "__main__":
    unittest.main()
