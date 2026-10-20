import json
import shutil
import tempfile
import unittest
from pathlib import Path

from sel.adapters import FixtureAnalyzer, FixtureTranscriber
from sel.evaluate import score
from sel.grounding import ground
from sel.models import Analysis, Transcript
from sel.pack import PackError, load_pack
from sel.pipeline import run

ROOT = Path(__file__).resolve().parent.parent
SOLAR = ROOT / "examples/calls/solar-01"
POS = ROOT / "examples/calls/pos-01"


def report_for(example, pack):
    return run(
        "",
        json.loads((example / "transcript.json").read_text())["started_at"],
        load_pack(ROOT / "packs" / pack),
        FixtureTranscriber(example / "transcript.json"),
        FixtureAnalyzer(example / "analysis.json"),
    )


class EvidenceGate(unittest.TestCase):
    def setUp(self):
        self.pack = load_pack(ROOT / "packs/demo-solar")
        self.transcript = Transcript.from_dict(json.loads((SOLAR / "transcript.json").read_text()))

    def analysis(self, **items):
        return Analysis.from_dict(items)

    def test_rejects_quote_not_in_cited_segment(self):
        a = self.analysis(findings=[{"id": "x", "kind": "objection", "summary": "s",
                                     "evidence": {"segment_ids": ["s02"], "quote": "made up words"}}])
        kept, rejected = ground(a, self.transcript, self.pack)
        self.assertEqual(kept.findings, [])
        self.assertEqual(rejected[0].item_id, "x")

    def test_rejects_unknown_segment_and_empty_evidence(self):
        a = self.analysis(findings=[
            {"id": "a", "kind": "question", "summary": "s", "evidence": {"segment_ids": ["s99"]}},
            {"id": "b", "kind": "question", "summary": "s", "evidence": {"segment_ids": []}},
        ])
        kept, rejected = ground(a, self.transcript, self.pack)
        self.assertEqual(kept.findings, [])
        self.assertEqual({r.item_id for r in rejected}, {"a", "b"})

    def test_quote_matching_ignores_case_and_spacing(self):
        a = self.analysis(findings=[{"id": "q", "kind": "question", "summary": "s",
                                     "evidence": {"segment_ids": ["s06"], "quote": "an  EMI option"}}])
        kept, _ = ground(a, self.transcript, self.pack)
        self.assertEqual(len(kept.findings), 1)

    def test_rejects_unknown_scorecard_criterion(self):
        a = self.analysis(findings=[{"id": "c", "kind": "coaching", "criterion": "rapport", "summary": "s",
                                     "evidence": {"segment_ids": ["s01"]}}])
        _, rejected = ground(a, self.transcript, self.pack)
        self.assertIn("scorecard", rejected[0].reasons[0])

    def test_commitment_owner_must_speak_in_evidence(self):
        a = self.analysis(commitments=[{"id": "k", "owner": "S2", "action": "email", "channel": "email",
                                        "evidence": {"segment_ids": ["s11"]}}])
        kept, rejected = ground(a, self.transcript, self.pack)
        self.assertEqual(kept.commitments, [])
        self.assertIn("does not speak", rejected[0].reasons[0])

    def test_verdict_without_valid_source_is_downgraded(self):
        a = self.analysis(claims=[
            {"id": "nosrc", "claim": "c", "verdict": "supported", "evidence": {"segment_ids": ["s05"]}},
            {"id": "baddoc", "claim": "c", "verdict": "contradicted", "evidence": {"segment_ids": ["s05"]},
             "sources": [{"source": "docs/missing.md", "quote": "x"}]},
            {"id": "badfact", "claim": "c", "verdict": "supported", "evidence": {"segment_ids": ["s05"]},
             "sources": [{"source": "fact:panel-warranty", "quote": "30-year"}]},
        ])
        kept, rejected = ground(a, self.transcript, self.pack)
        self.assertEqual([c.verdict for c in kept.claims], ["unverified"] * 3)
        self.assertTrue(all(c.sources == [] for c in kept.claims))
        self.assertEqual(len(rejected), 3)


class EndToEnd(unittest.TestCase):
    def test_solar_refuses_invented_items(self):
        r = report_for(SOLAR, "demo-solar")
        self.assertEqual({x.item_id for x in r.rejected}, {"f8", "c5"})
        self.assertEqual(next(c for c in r.claims if c.id == "c5").verdict, "unverified")

        visit = next(c for c in r.commitments if c.id == "k2")
        # The fixture "model" supplied 2026-10-12; Sel recomputes from the words and refuses.
        self.assertEqual(visit.due.status, "ambiguous")
        self.assertIsNone(visit.due.date)

    def test_solar_drafts(self):
        r = report_for(SOLAR, "demo-solar")
        drafts = {d.commitment_id: d for d in r.drafts}
        self.assertNotIn("k4", drafts)  # customer's own promise: tracked, not drafted
        self.assertEqual(drafts["k1"].status, "ready_for_approval")
        self.assertEqual(drafts["k1"].fields["send_by"], "2026-10-08")
        self.assertEqual(drafts["k1"].fields["to"], "ramesh@example.com")
        self.assertIn("Ramesh", drafts["k1"].fields["body"])
        for k in ("k2", "k3"):
            self.assertEqual(drafts[k].status, "needs_input")
            self.assertEqual(drafts[k].missing, ["date", "time"])
            self.assertIsNone(drafts[k].ics)
            self.assertIn("review_note", drafts[k].fields)

    def test_pos_agreed_time_produces_calendar_invite(self):
        r = report_for(POS, "demo-pos")
        demo = next(d for d in r.drafts if d.kind == "calendar")
        self.assertEqual(demo.status, "ready_for_approval")
        self.assertEqual((demo.fields["date"], demo.fields["time"]), ("2026-10-11", "15:00"))
        # 15:00 Nepal time (+05:45) is 09:15 UTC.
        self.assertIn("DTSTART:20261011T091500Z", demo.ics)
        self.assertEqual(r.rejected, [])

    def test_missing_recipient_is_flagged_not_guessed(self):
        data = json.loads((POS / "transcript.json").read_text())
        data["speakers"][1].pop("email")
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp) / "transcript.json"
            t.write_text(json.dumps(data))
            r = run("", data["started_at"], load_pack(ROOT / "packs/demo-pos"),
                    FixtureTranscriber(t), FixtureAnalyzer(POS / "analysis.json"))
        email = next(d for d in r.drafts if d.kind == "email")
        self.assertEqual(email.status, "needs_input")
        self.assertEqual(email.missing, ["to"])

    def test_eval_scores_fixture(self):
        r = report_for(SOLAR, "demo-solar")
        s = score(r, json.loads((SOLAR / "gold.json").read_text()))
        self.assertEqual(s["commitments"]["recall"], 1.0)
        self.assertEqual(s["commitments"]["invented_dates"], 0)
        self.assertEqual(s["claims"]["false_supported"], 0)
        self.assertEqual(s["findings.coaching"]["fn"], 1)
        self.assertEqual(s["rejected_by_evidence_gate"], 2)


class Packs(unittest.TestCase):
    def test_example_packs_load(self):
        for name in ("demo-solar", "demo-pos"):
            with self.subTest(pack=name):
                self.assertTrue(load_pack(ROOT / "packs" / name).id)

    def test_invalid_pack_reports_problems(self):
        with tempfile.TemporaryDirectory() as tmp:
            dst = Path(tmp) / "p"
            shutil.copytree(ROOT / "packs/demo-pos", dst)
            (dst / "pack.toml").write_text('[pack]\nid = "x"\nname = "X"\nweek_starts_on = "funday"\n')
            with self.assertRaises(PackError) as ctx:
                load_pack(dst)
        msg = str(ctx.exception)
        self.assertIn("week_starts_on", msg)
        self.assertIn("scorecard", msg)


    def test_objections_and_the_skill_they_are_judged_on(self):
        solar, pos = load_pack(ROOT / "packs/demo-solar"), load_pack(ROOT / "packs/demo-pos")
        self.assertTrue(solar.objections and pos.objections)
        price = solar.objection("competitor-cheaper")
        self.assertEqual(solar.objection_criterion(price).id, "objection-handling")  # the pack's objection skill
        self.assertEqual(solar.objection_criterion(solar.objection("wait-for-subsidy")).id, "discovery")  # its own
        self.assertEqual(pos.objection_criterion(pos.objection("too-expensive")).id, "qualify")  # no objection skill: discovery
        self.assertIsNone(solar.objection("nope"))

    def test_invalid_objections_are_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            dst = Path(tmp) / "p"
            shutil.copytree(ROOT / "packs/demo-pos", dst)
            with (dst / "pack.toml").open("a", encoding="utf-8") as f:
                f.write('\n[[objections]]\nid = "Bad Id"\ntopic = ""\nsays = "x"\ncriterion = "missing"\n'
                        '\n[[objections]]\nid = "too-expensive"\ntopic = "Price"\nsays = "Again"\n')
            with self.assertRaises(PackError) as ctx:
                load_pack(dst)
        msg = str(ctx.exception)
        for words in ("objection ids must be unique", "lower-case", "topic and says are required", "not in the scorecard"):
            self.assertIn(words, msg)


if __name__ == "__main__":
    unittest.main()
