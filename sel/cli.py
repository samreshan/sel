"""Command-line entry point: ``python -m sel <command>``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .adapters import FixtureAnalyzer, FixtureTranscriber
from .evaluate import score
from .pack import PackError, load_pack
from .pipeline import run


def _report_for_example(example: Path, pack_path: Path):
    meta = json.loads((example / "transcript.json").read_text(encoding="utf-8"))
    pack = load_pack(pack_path)
    return run(
        audio_path=meta.get("audio") or "",
        started_at=meta["started_at"],
        pack=pack,
        transcriber=FixtureTranscriber(example / "transcript.json"),
        analyzer=FixtureAnalyzer(example / "analysis.json"),
    )


def _print_summary(report) -> None:
    t = report.transcript
    print(f"Call {t.call_id} · pack {report.pack_id} · {len(t.segments)} segments")
    print(f"  analyzer: {report.meta['analyzer']} (fixture data, not model output)")
    by_seg = {s.id: s for s in t.segments}

    def at(ids: list[str]) -> str:
        s = by_seg[ids[0]]
        return f"{int(s.start // 60):02d}:{s.start % 60:04.1f}"

    print("\nFindings")
    for f in report.findings:
        print(f"  [{at(f.evidence.segment_ids)}] {f.kind:<9} {f.summary}")
    print("\nClaims")
    for c in report.claims:
        src = ", ".join(s.source for s in c.sources) or "no source"
        print(f"  [{at(c.evidence.segment_ids)}] {c.verdict:<12} {c.claim}  ({src})")
    print("\nCommitments")
    for c in report.commitments:
        owner = t.speaker(c.owner)
        due = c.due
        when = due.date if due and due.status == "exact" else f"{due.status if due else 'missing'}: {due.reason if due else ''}"
        print(f"  [{at(c.evidence.segment_ids)}] {owner.role if owner else c.owner:<8} {c.action} → {when}")
    print("\nDrafts (nothing is sent without approval)")
    for d in report.drafts:
        extra = f" missing: {', '.join(d.missing)}" if d.missing else ""
        print(f"  {d.kind:<8} {d.status}{extra} — {d.fields.get('subject') or d.fields.get('title')}")
    if report.rejected:
        print("\nRefused by the evidence gate")
        for r in report.rejected:
            print(f"  {r.item_type} {r.item_id}: {'; '.join(r.reasons)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sel", description="Sel: on-device sales-call review (reference pipeline)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("validate-pack", help="check a business pack")
    p.add_argument("pack", type=Path)

    p = sub.add_parser("demo", help="run an example call (fixture transcript + fixture analysis)")
    p.add_argument("example", type=Path, help="e.g. examples/calls/solar-01")
    p.add_argument("--pack", type=Path, required=True)
    p.add_argument("--json", action="store_true", help="print the full report as JSON")

    p = sub.add_parser("eval", help="score an example's report against its gold annotations")
    p.add_argument("example", type=Path)
    p.add_argument("--pack", type=Path, required=True)

    args = parser.parse_args(argv)

    if args.cmd == "validate-pack":
        try:
            pack = load_pack(args.pack)
        except PackError as e:
            print(f"invalid: {e}", file=sys.stderr)
            return 1
        print(f"ok: {pack.id} ({pack.name}) — {len(pack.docs)} docs, {len(pack.facts)} facts, "
              f"{len(pack.scorecard)} scorecard criteria, templates: {', '.join(pack.templates) or 'defaults'}")
        return 0

    report = _report_for_example(args.example, args.pack)
    if args.cmd == "demo":
        if args.json:
            print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        else:
            _print_summary(report)
        return 0

    gold = json.loads((args.example / "gold.json").read_text(encoding="utf-8"))
    print(json.dumps(score(report, gold), indent=2))
    return 0
