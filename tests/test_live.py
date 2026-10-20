import json
import unittest
from dataclasses import replace
from pathlib import Path

from sel.adapters import FixtureTranscriber
from sel.dates import due_phrase_in, resolve_due
from sel.evaluate import score
from sel.live import LiveScorer, RuleAnalyzer, read_line, transcript_signals
from sel.models import Segment, Speaker, Transcript
from sel.pack import Criterion, load_pack
from sel.pipeline import run
from sel.scoring import criterion_signal, grade, score_report

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = [("solar-01", "demo-solar"), ("pos-01", "demo-pos"), ("pos-02", "demo-pos")]


def transcript(name):
    return Transcript.from_dict(json.loads((ROOT / "examples/calls" / name / "transcript.json").read_text(encoding="utf-8")))


class DuePhraseTests(unittest.TestCase):
    def test_finds_the_spoken_words(self):
        cases = {
            "Huncha, ma bholi beluka samma quotation ra EMI details email garchu.": "bholi beluka samma",
            "Sometime next week hamro engineer pathaula.": "Sometime next week",
            "I'll call you on Monday at 11 a.m. to check on your decision.": "Monday at 11 a.m",
            "Then I'll email you the trial link today, so your team can start this week.": "today",
            "Ma tapailai pricing PDF aaja nai email gardinchu.": "aaja",
            "We will install it in 3 days.": "in 3 days",
            "I am going to send it": None,  # "am" is a time word only after a number
            "Ma pachhi call garera confirm garchu.": "pachhi",
        }
        for text, expected in cases.items():
            self.assertEqual(due_phrase_in(text), expected, text)

    def test_clock_spellings(self):
        for phrase in ("Monday at 11 a.m.", "Monday at 11 a.m", "Monday at 11am"):
            due = resolve_due(phrase, "2026-10-08T15:00:00+05:45")
            self.assertEqual((due.status, due.date, due.time), ("exact", "2026-10-12", "11:00"), phrase)
        self.assertIsNone(resolve_due("Monday 11 amazing deals", "2026-10-08T15:00:00+05:45").time)


class LineRuleTests(unittest.TestCase):
    def test_questions_skip_tag_questions(self):
        self.assertTrue(read_line("EMI ko option cha?", "customer").question)
        self.assertTrue(read_line("Net metering ko process kati time lagcha", "customer").question)
        self.assertFalse(read_line("You mentioned a restaurant in Lalitpur, is that right?", "rep").question)
        self.assertFalse(read_line("Tapaile website ma inquiry garnu bhayeko thiyo, hoina?", "rep").question)

    def test_commitments(self):
        info = read_line("Sometime next week hamro engineer pathaula. Ma pachhi call garera confirm garchu.", "rep")
        self.assertEqual([(c["channel"], c["due_text"]) for c in info.commitments],
                         [("visit", "Sometime next week"), ("call", "pachhi")])
        # Reported speech is not a promise, and neither is "I'll understand".
        self.assertEqual(read_line("Arko company le 3 lakh 60 ma dinchu bhaneko thiyo.", "customer").commitments, [])
        self.assertEqual(read_line("I'll understand. Setup takes about one day per outlet.", "rep").commitments, [])
        self.assertEqual(read_line("Then I'll email you the trial link today.", "rep").commitments[0]["action"],
                         "email you the trial link")

    def test_claims_one_per_number(self):
        self.assertEqual(len(read_line("Hamro panel ma 25 years performance warranty cha, ani inverter ma pani "
                                       "10 years warranty cha.", "rep").claims), 2)
        self.assertEqual(len(read_line("Our pricing: the Standard plan is NPR 2,000 per month per outlet, billed yearly.",
                                       "rep").claims), 1)
        self.assertEqual(read_line("That decides which plan fits.", "rep").claims, [])


class SignalTests(unittest.TestCase):
    def test_solar_signals(self):
        pack = load_pack(ROOT / "packs/demo-solar")
        sig = transcript_signals(transcript("solar-01"), pack)
        self.assertEqual(sig["price"]["segment"], "s03")
        self.assertEqual(sig["rep_questions_before_price"], 0)
        self.assertEqual([q["segment"] for q in sig["customer_questions"]], ["s06", "s08", "s12"])
        self.assertEqual([(o["segment"], o["handled"]) for o in sig["objections"]], [("s04", False)])
        # "details pathaidinchu" (s07), "bholi beluka samma ... email garchu" (s11) and "Bholi email pathauchu"
        # (s15) are one promise, dated by s11.
        email = next(c for c in sig["commitments"] if c["channel"] == "email")
        self.assertEqual((email["segments"], email["due"]["date"]), (["s11", "s07", "s15"], "2026-10-08"))
        risky = [c["segment"] for c in sig["claims"] if c["status"] == "risk"]
        self.assertEqual(risky, ["s09"])  # nothing in the pack covers the NEA timeline

    def test_numbers_that_disagree_with_the_pack_are_flagged(self):
        pack = load_pack(ROOT / "packs/demo-pos")
        sig = transcript_signals(transcript("pos-02"), pack)
        price = next(c for c in sig["claims"] if c["segment"] == "s10")
        self.assertEqual(price["status"], "risk")
        self.assertIn("2,500", price["quote"])

    def test_objection_split_over_two_lines_is_one(self):
        pack = load_pack(ROOT / "packs/demo-pos")
        t = Transcript("x", None, "2026-10-08T15:00:00+05:45", [Speaker("S1", "rep"), Speaker("S2", "customer")], [
            Segment("s1", 0, 3, "S1", "The Standard plan is NPR 2,500 per month."),
            Segment("s2", 3, 5, "S2", "Hmm, that's still a lot for one small cafe."),
            Segment("s3", 5, 7, "S2", "We're not sure we need that much."),
            Segment("s4", 7, 9, "S1", "How many outlets do you have?"),
        ])
        sig = transcript_signals(t, pack)
        self.assertEqual([(o["segments"], o["handled"]) for o in sig["objections"]], [(["s2", "s3"], True)])


class RuleAnalyzerTests(unittest.TestCase):
    def test_against_gold(self):
        """On the hand-typed example calls the rules find every question, objection, claim and promise."""
        for name, pack_id in EXAMPLES:
            example = ROOT / "examples/calls" / name
            report = run("", transcript(name).started_at, load_pack(ROOT / "packs" / pack_id),
                         FixtureTranscriber(example / "transcript.json"), RuleAnalyzer())
            result = score(report, json.loads((example / "gold.json").read_text(encoding="utf-8")))
            for key in ("findings.question", "findings.objection", "commitments", "claims"):
                self.assertEqual((result[key]["fp"], result[key]["fn"]), (0, 0), f"{name} {key}")
            self.assertEqual(result["commitments"]["due_date_agreement"], 1.0, name)
            self.assertEqual(result["commitments"]["invented_dates"], 0, name)
            self.assertEqual(result["claims"]["false_supported"], 0, name)
            self.assertEqual({c.verdict for c in report.claims}, {"unverified"}, name)
            self.assertEqual(report.rejected, [], name)


class ScoringTests(unittest.TestCase):
    def test_criteria_map_to_signals(self):
        expected = {
            "demo-solar": {"discovery": "discovery", "objection-handling": "objections",
                           "accurate-claims": "claims", "clear-next-step": "next_step"},
            "demo-pos": {"qualify": "discovery", "accurate-claims": "claims", "book-demo": "next_step"},
        }
        for pack_id, want in expected.items():
            scorecard = load_pack(ROOT / "packs" / pack_id).scorecard
            self.assertEqual({c.id: criterion_signal(c) for c in scorecard}, want)
            # Packs may leave `signal` out; the wording gives the same answer.
            self.assertEqual({c.id: criterion_signal(replace(c, signal=None)) for c in scorecard}, want)
        self.assertIsNone(criterion_signal(Criterion("x", "Friendly tone", "Greets the customer warmly.")))

    def test_live_grade_and_actions(self):
        pack = load_pack(ROOT / "packs/demo-solar")
        snap = LiveScorer(pack).snapshot(transcript("solar-01"))
        status = {c["id"]: c["status"] for c in snap["score"]["criteria"]}
        self.assertEqual(status["discovery"], "missed")
        self.assertEqual(status["objection-handling"], "missed")
        self.assertEqual(status["clear-next-step"], "missed")  # 1 of 3 follow-ups has a day
        self.assertIsInstance(snap["score"]["overall"], int)
        kinds = [a["kind"] for a in snap["actions"]]
        self.assertIn("objection", kinds)
        self.assertIn("date", kinds)
        # Scorecard wording comes from the pack, not from code.
        objection = next(a for a in snap["actions"] if a["kind"] == "objection")
        self.assertIn("ask what the other quote includes", objection["text"])

    def test_open_criteria_do_not_count_early(self):
        pack = load_pack(ROOT / "packs/demo-solar")
        t = transcript("solar-01")
        early = Transcript(t.call_id, t.audio, t.started_at, t.speakers, t.segments[:2])
        result = grade(transcript_signals(early, pack), pack, final=False)
        self.assertEqual({c["status"] for c in result["criteria"]}, {"open", "na"})
        self.assertIsNone(result["overall"])

    def test_report_score_uses_verdicts(self):
        pack = load_pack(ROOT / "packs/demo-solar")
        example = ROOT / "examples/calls/solar-01"
        from sel.adapters import FixtureAnalyzer
        report = run("", "2026-10-07T14:00:00+05:45", pack, FixtureTranscriber(example / "transcript.json"),
                     FixtureAnalyzer(example / "analysis.json"))
        claims = next(c for c in score_report(report, pack)["criteria"] if c["id"] == "accurate-claims")
        self.assertIn("at odds with the pack", claims["detail"])  # the contradicted inverter warranty


class OpeningObjectionTests(unittest.TestCase):
    def test_a_drill_opening_counts_as_the_objection_without_cue_words(self):
        pack = load_pack(Path(__file__).resolve().parent.parent / "packs/demo-solar")
        t = Transcript(call_id="d", audio=None, started_at="2026-10-20T10:00:00+05:45",
                       speakers=[Speaker("s1", "rep", "Rep"), Speaker("s2", "customer", "Kamala")],
                       segments=[Segment("s01", 0.0, 3.0, "s2", "Inverters break all the time. What happens after the warranty?"),
                                 Segment("s02", 3.5, 6.0, "s1", "What worries you most about it?")])
        plain = LiveScorer(pack).snapshot(t, final=True)["score"]
        drill = LiveScorer(pack, opening_objection=True).snapshot(t, final=True)["score"]
        status = lambda score: next(c["status"] for c in score["criteria"] if c["id"] == "objection-handling")
        self.assertEqual(status(plain), "na")  # no cue word: the rules see no objection
        self.assertEqual(status(drill), "met")  # in a drill the opening is the objection, answered with a question


if __name__ == "__main__":
    unittest.main()
