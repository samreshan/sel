"""Command-line entry point: ``python -m sel <command>``."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .adapters import FixtureAnalyzer, FixtureTranscriber
from .evaluate import score
from .pack import PackError, load_pack
from .pipeline import run


def _report_for_example(example: Path, pack_path: Path, analyzer: str = "fixture"):
    meta = json.loads((example / "transcript.json").read_text(encoding="utf-8"))
    pack = load_pack(pack_path)
    if analyzer == "rules":
        from .live import RuleAnalyzer
        chosen = RuleAnalyzer()
    else:
        chosen = FixtureAnalyzer(example / "analysis.json")
    return run(
        audio_path=meta.get("audio") or "",
        started_at=meta["started_at"],
        pack=pack,
        transcriber=FixtureTranscriber(example / "transcript.json"),
        analyzer=chosen,
    )


def _print_summary(report) -> None:
    t = report.transcript
    print(f"Call {t.call_id} · pack {report.pack_id} · {len(t.segments)} segments")
    analyzer = str(report.meta.get("analyzer", ""))
    note = (" (fixture data, not model output)" if analyzer.startswith("fixture")
            else " (cue words and the pack, no language model)" if analyzer.startswith("rules") else "")
    print(f"  transcriber: {report.meta.get('transcriber')} · analyzer: {report.meta.get('analyzer')}{note}")
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
    p.add_argument("--analyzer", choices=["fixture", "rules"], default="fixture",
                   help="fixture: the example's hand-written analysis; rules: Sel's code-only analyzer")

    p = sub.add_parser("eval", help="score an example's report against its gold annotations")
    p.add_argument("example", type=Path)
    p.add_argument("--pack", type=Path, required=True)
    p.add_argument("--report", type=Path, help="score this saved report.json instead of the fixture analysis")
    p.add_argument("--analyzer", choices=["fixture", "rules"], default="fixture",
                   help="fixture: the example's hand-written analysis; rules: Sel's code-only analyzer")
    p.add_argument("--coaching", choices=["fixture", "rules", "llm"],
                   help="score coaching moments and better lines against coaching_gold.json instead: the example's "
                        "hand-written coaching.json, Sel's rules, or the configured language model (SEL_LLM_*)")
    p.add_argument("--transfer", choices=["rules", "llm"],
                   help="score the Judge's transfer verdicts against transfer_gold.json: Sel's rules, or the "
                        "configured language model (SEL_LLM_*) checked by the rules")

    p = sub.add_parser("analyze", help="run the real pipeline on a recording (local speech + language models)")
    p.add_argument("audio", type=Path)
    p.add_argument("--pack", type=Path, required=True)
    p.add_argument("--started-at", required=True, help="call start, ISO 8601 with offset, e.g. 2026-10-07T14:00:00+05:45")
    p.add_argument("--rep-name")
    p.add_argument("--customer-name")
    p.add_argument("--customer-email")
    p.add_argument("--channels", choices=["auto", "split", "mono"], default="auto",
                   help="split: stereo file with each side on its own channel")
    p.add_argument("--rep-channel", type=int, default=0)
    p.add_argument("--transcript", type=Path, help="skip speech recognition and analyse this transcript.json")
    p.add_argument("--out", type=Path, help="write the report JSON here")

    p = sub.add_parser("doctor", help="show which models Sel uses, where they run and their licences")
    p.add_argument("--json", action="store_true", help="print the report as JSON")
    p.add_argument("--offline", action="store_true", help="don't contact the model server")

    p = sub.add_parser("demo-setup", help="get a running Sel ready for the coaching-loop demo (docs/scope.md)")
    p.add_argument("--url", default="http://127.0.0.1:8765", help="the running Sel (python -m sel serve)")
    p.add_argument("--next", action="store_true",
                   help="also file the follow-up call (made with Piper) so its transfer check runs")
    p.add_argument("--next-audio", type=Path, help="a recording of the follow-up call to use instead of Piper")
    p.add_argument("--minutes", type=float, default=30, help="how long to wait for the first call to be coached")
    p.add_argument("--allow-rules", action="store_true",
                   help="carry on when the models don't answer (a dry run on Sel's rules)")
    p.add_argument("--out-dir", type=Path, default=Path("demo"), help="where the follow-up recording is written")

    p = sub.add_parser("serve", help="start the local review app")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--calls-dir", default=os.environ.get("SEL_CALLS_DIR", "calls"))
    p.add_argument("--packs-dir", default="packs")
    p.add_argument("--no-examples", action="store_true", help="don't load the fixture example calls")
    p.add_argument("--tls-cert", help="serve https with this certificate (needed for microphones on other computers)")
    p.add_argument("--tls-key", help="private key for --tls-cert")

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

    if args.cmd == "doctor":
        from .doctor import check, render
        report = check(probe=not args.offline)
        print(json.dumps(report, indent=2) if args.json else render(report))
        return 0 if report["ok"] else 1
    if args.cmd == "demo-setup":
        from .demo_setup import SetupError, run as demo_setup
        try:
            demo_setup(args.url, next_call=args.next, next_audio=args.next_audio, minutes=args.minutes,
                       allow_rules=args.allow_rules, out_dir=args.out_dir)
        except SetupError as e:
            print(f"demo-setup stopped: {e}", file=sys.stderr)
            return 1
        return 0
    if args.cmd == "serve":
        return _serve(args)
    if args.cmd == "analyze":
        return _analyze(args)

    if args.cmd == "eval" and args.report:
        from .models import Analysis, CallReport, Draft, Rejection, Transcript
        data = json.loads(args.report.read_text(encoding="utf-8"))
        a = Analysis.from_dict(data)
        report = CallReport(data["pack_id"], Transcript.from_dict(data["transcript"]), a.findings, a.claims,
                            a.commitments, [Draft(**d) for d in data["drafts"]],
                            [Rejection(**r) for r in data["rejected"]], data.get("meta", {}))
        ref_file = args.example / "reference.json"  # timings of the example's recording, when it has one
        if not ref_file.is_file():
            ref_file = args.example / "transcript.json"
        reference = json.loads(ref_file.read_text(encoding="utf-8"))
        gold = json.loads((args.example / "gold.json").read_text(encoding="utf-8"))
        print(json.dumps(score(report, gold, reference=reference), indent=2))
        return 0

    report = _report_for_example(args.example, args.pack, args.analyzer)
    if args.cmd == "eval" and args.transfer:
        print(json.dumps(_eval_transfer(args), indent=2, ensure_ascii=False))
        return 0
    if args.cmd == "eval" and args.coaching:
        print(json.dumps(_eval_coaching(args, report), indent=2, ensure_ascii=False))
        return 0
    if args.cmd == "demo":
        if args.json:
            print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        else:
            _print_summary(report)
        return 0

    gold = json.loads((args.example / "gold.json").read_text(encoding="utf-8"))
    print(json.dumps(score(report, gold), indent=2))
    return 0


def _eval_coaching(args, report) -> dict:
    """Coach the example's report (fixture, rules or the configured model) and score it against coaching_gold.json."""
    from .coach import LineWriter, MomentFinder, diagnose
    from .evaluate import score_coaching
    from .live import pack_index
    from .scoring import score_report

    pack = load_pack(args.pack)
    data = report.to_dict()
    data["score"] = score_report(report, pack)
    index = pack_index(pack)
    gold = json.loads((args.example / "coaching_gold.json").read_text(encoding="utf-8"))
    if args.coaching == "fixture":
        fixture = json.loads((args.example / "coaching.json").read_text(encoding="utf-8"))
        coaching = diagnose(data, pack, index, fixture=fixture)
    elif args.coaching == "rules":
        coaching = diagnose(data, pack, index)
    else:
        from .llm import client_from_env

        llm = client_from_env()
        coaching = diagnose(data, pack, index, finder=MomentFinder(llm), writer=LineWriter(llm, index), judge=llm,
                            progress=lambda s, f: print(f"  [{f:4.0%}] {s}", file=sys.stderr))
        coaching["meta"]["llm_usage"] = llm.stats.to_dict()
    out = score_coaching(coaching, gold)
    out["analysis"] = str(report.meta.get("analyzer"))
    if args.coaching == "llm":
        out["llm_usage"] = coaching["meta"]["llm_usage"]
        out["lines_shown"] = [{"moment": f"{m['kind']} {m['criterion']} {m['evidence']['segment_ids']}",
                               "line": (m.get("better_line") or {}).get("text"),
                               "refused": [{"text": x["text"], "reasons": x["reasons"]} for x in m["rejected_lines"]]}
                              for m in coaching["moments"]]
    return out


def _eval_transfer(args) -> dict:
    """The Judge on labelled variants of an example call (transfer_gold.json): accuracy and false passes."""
    import copy

    from .drills import judge
    from .live import RuleAnalyzer
    from .models import Transcript
    from .pipeline import build_report
    from .scoring import score_report

    pack = load_pack(args.pack)
    gold = json.loads((args.example / "transfer_gold.json").read_text(encoding="utf-8"))
    base = json.loads((args.example / "transcript.json").read_text(encoding="utf-8"))
    llm = None
    if args.transfer == "llm":
        from .llm import client_from_env
        llm = client_from_env()
    cases = []
    for case in gold["cases"]:
        data = copy.deepcopy(base)
        for seg in data["segments"]:
            seg["text"] = case["edits"].get(seg["id"], seg["text"])
        transcript = Transcript.from_dict(data)
        report = build_report(transcript, RuleAnalyzer().analyze(transcript, pack), pack)
        v = judge(transcript, score_report(report, pack), pack, gold["criterion"], llm)
        cases.append({"case": case["name"], "expected": case["verdict"], "verdict": v["verdict"],
                      "right": v["verdict"] == case["verdict"], "segment_ids": v["segment_ids"], "why": v["why"],
                      "by": v["by"], "rules_said": (v.get("code") or {}).get("verdict")})
    n = len(cases)
    return {"criterion": gold["criterion"], "judge": args.transfer, "cases": n,
            "accuracy": round(sum(c["right"] for c in cases) / n, 3) if n else None,
            "unsure": sum(c["verdict"] == "unsure" for c in cases),
            # The costliest error: a pass that closes a focus the rep has not earned.
            "false_pass": sum(c["verdict"] == "pass" and c["expected"] != "pass" for c in cases),
            "llm_usage": llm.stats.to_dict() if llm else None, "results": cases}


def _health():
    from .llm import client_from_env
    out = {"asr": {"model": os.environ.get("SEL_WHISPER_MODEL", "small")}}
    try:
        llm = client_from_env()
        out["llm"] = {"name": llm.name, **llm.health()}
    except Exception as e:  # noqa: BLE001
        out["llm"] = {"name": None, "ok": False, "error": str(e)}
    try:
        import faster_whisper  # noqa: F401
        out["asr"]["ok"] = True
    except ImportError:
        out["asr"].update(ok=False, error="pip install faster-whisper")
    return out


def _serve(args) -> int:
    from .server import serve
    from .service import CallService, default_analysis_llm, default_chat_factory

    service = CallService(args.calls_dir, args.packs_dir, chat_factory=default_chat_factory,
                          analysis_llm_factory=default_analysis_llm)
    if not args.no_examples:
        root = Path(__file__).resolve().parent.parent / "examples" / "calls"
        for example, pack_id in (("solar-01", "demo-solar"), ("pos-01", "demo-pos")):
            if (root / example).is_dir():
                service.create_from_example(root / example, pack_id)
    if bool(args.tls_cert) != bool(args.tls_key):
        print("give both --tls-cert and --tls-key", file=sys.stderr)
        return 2
    tls = (args.tls_cert, args.tls_key) if args.tls_cert else None
    httpd = serve(service, args.host, args.port, health=_health, tls=tls)
    scheme = "https" if tls else "http"
    print(f"Sel is running at {scheme}://{args.host}:{args.port}  (Ctrl+C to stop)")
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print("  Reachable from other computers, with no login: use only on a network you trust.")
        if not tls:
            print("  Browsers on other computers allow the microphone only over https (--tls-cert/--tls-key).")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        service.bus.close()
    return 0


def _analyze(args) -> int:
    from .adapters import FixtureTranscriber
    from .analyzer_llm import LLMAnalyzer, label_speakers
    from .asr import WhisperTranscriber
    from .llm import client_from_env
    from .models import Speaker
    from .pipeline import analyze_call

    pack = load_pack(args.pack)
    llm = client_from_env()
    speakers = [Speaker("S1", "rep", args.rep_name), Speaker("S2", "customer", args.customer_name, args.customer_email)]
    if args.transcript:
        transcriber = FixtureTranscriber(args.transcript)
    else:
        transcriber = WhisperTranscriber(speakers=speakers, rep_channel=args.rep_channel,
                                         split_channels={"split": True, "mono": False}.get(args.channels))
    analyzer = LLMAnalyzer(llm)

    def progress(stage: str, frac: float) -> None:
        print(f"  [{frac:4.0%}] {stage}", file=sys.stderr)

    analyzer.progress = lambda s, f: progress(s, 0.45 + 0.5 * f)
    report, _ = analyze_call(str(args.audio), args.started_at, pack, transcriber, analyzer,
                             call_id=args.audio.stem, speaker_labeler=lambda t, p: label_speakers(t, p, llm),
                             progress=progress)
    if args.out:
        args.out.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"report written to {args.out}", file=sys.stderr)
    _print_summary(report)
    return 0
