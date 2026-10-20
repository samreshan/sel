"""How long the AI customer takes to answer in a practice drill, on this machine.

Plays a scripted rep against the AI customer built from the solar-01 price
objection, through the same code a drill uses (persona builder, AICustomer,
the simulator's AI worker), and reports the time from each rep line to the
customer's reply. Uses the chat model configured with SEL_CHAT_LLM_* (which
inherits SEL_LLM_*), or --model to try another one on the same server.

    python scripts/drill_latency.py                      # the configured chat model
    python scripts/drill_latency.py --model qwen2.5:1.5b --runs 3
    python scripts/drill_latency.py --rules              # no model: the floor
    python scripts/drill_latency.py --format json        # the older JSON reply format, to compare with text

Each drill room warms the model up when it opens (as in the app); the script waits for that before
the first rep line, so "first reply" is what a rep sees after reading the customer's opening.

Target from the build plan: 2 to 3 s per reply on the demo laptop.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sel.ai_customer import AICustomer  # noqa: E402
from sel.cli import _report_for_example  # noqa: E402
from sel.coach import diagnose  # noqa: E402
from sel.live import pack_index  # noqa: E402
from sel.llm import chat_client_from_env  # noqa: E402
from sel.pack import load_pack  # noqa: E402
from sel.persona import build_persona  # noqa: E402
from sel.scoring import score_report  # noqa: E402
from sel.service import CallService  # noqa: E402
from sel.simulator import SimulatorHub  # noqa: E402

REP_LINES = [
    "I understand. Can I ask what the other quote includes: which panels and which inverter?",
    "And how many years of warranty did they offer on the inverter?",
    "Ours includes installation and net-metering application support. Would a site visit help you compare?",
    "Great, I'll email you the comparison by tomorrow evening and call you on Sunday morning.",
]


def pct(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(p * (len(ordered) - 1)))]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model", help="model name on the chat server (default: SEL_CHAT_LLM_MODEL / SEL_LLM_MODEL)")
    ap.add_argument("--runs", type=int, default=2, help="drills to play (the first is a warm-up and reported apart)")
    ap.add_argument("--rules", action="store_true", help="no model: the rules customer")
    ap.add_argument("--format", choices=("text", "json"), default=None,
                    help="AI customer reply format (default: SEL_CUSTOMER_FORMAT, else text)")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    args = ap.parse_args()
    if args.model:
        os.environ["SEL_CHAT_LLM_MODEL"] = args.model
    llm = None if args.rules else chat_client_from_env(temperature=0.6)

    pack = load_pack(ROOT / "packs/demo-solar")
    report = _report_for_example(ROOT / "examples/calls/solar-01", ROOT / "packs/demo-solar")
    data = report.to_dict()
    data["score"] = score_report(report, pack)
    fixture = json.loads((ROOT / "examples/calls/solar-01/coaching.json").read_text(encoding="utf-8"))
    moment = next(m for m in diagnose(data, pack, pack_index(pack), fixture=fixture)["moments"]
                  if m["criterion"] == "objection-handling" and m["kind"] == "weak")
    t = time.perf_counter()
    persona = build_persona(moment, pack, ["Ramesh", "Asha"], "latency", llm)
    persona_s = time.perf_counter() - t

    with tempfile.TemporaryDirectory() as tmp:
        service = CallService(tmp, ROOT / "packs")
        hub = SimulatorHub(service, speech_status=lambda: (False, "off"))
        runs = []
        for n in range(args.runs):
            agent = AICustomer({**persona, "difficulty": 2}, llm, fmt=args.format)
            calls_before = llm.stats.calls if llm else 0
            t = time.perf_counter()
            room, client = hub.create_drill_room("demo-solar", agent, f"latency-{n}", "Rep", None, persona["name"], "+05:45")
            while llm and llm.stats.calls == calls_before and time.perf_counter() - t < 180:
                time.sleep(0.02)  # the room's warm-up request (a rep reads the opening meanwhile)
            warm_s = time.perf_counter() - t
            hub.start(room.code, client)
            replies = []
            for line in REP_LINES:
                if room.state != "live":
                    break
                before = len(room.lines)
                hub.add_line(room.code, client, line)
                deadline = time.monotonic() + 180
                while len(room.lines) < before + 2 and room.state == "live" and time.monotonic() < deadline:
                    time.sleep(0.02)
                replies.append(room.lines[-1]["text"] if room.lines[-1]["role"] == "customer" else None)
            runs.append({"seconds": list(room.ai_seconds), "replies": replies, "warm_up_s": round(warm_s, 2),
                         "format": agent.format,
                         "by": sorted({x.get("source") for x in room.lines}), "ended_by_customer": room.state == "ended"})
        hub.close()
        service.wait_idle()

    measured = [s for r in (runs[1:] or runs) for s in r["seconds"]]
    out = {
        "model": llm.name if llm else "rules (no language model)",
        "format": runs[-1]["format"] if llm else None,
        "warm_up_s": [r["warm_up_s"] for r in runs] if llm else None,
        "first_reply_s": [r["seconds"][0] for r in runs if r["seconds"]],
        "machine": f"{platform.processor() or platform.machine()} · {os.cpu_count()} logical CPUs · {platform.system()}",
        "persona_s": round(persona_s, 2),
        "warm_up_replies_s": runs[0]["seconds"] if len(runs) > 1 else None,
        "replies": len(measured),
        "median_s": round(statistics.median(measured), 2) if measured else None,
        "p90_s": round(pct(measured, 0.9), 2) if measured else None,
        "max_s": round(max(measured), 2) if measured else None,
        "target_s": "2 to 3",
        "example_replies": runs[-1]["replies"],
        "llm_usage": llm.stats.to_dict() if llm else None,
    }
    if llm and llm.stats.calls:
        out["tokens_per_request"] = {"prompt": round(llm.stats.prompt_tokens / llm.stats.calls),
                                     "completion": round(llm.stats.completion_tokens / llm.stats.calls)}
    if args.json:
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        print(f"AI customer: {out['model']} ({out['format'] or 'rules'} format)\nMachine: {out['machine']}")
        if out["warm_up_s"]:
            print(f"Warm-up when the room opens: {out['warm_up_s']} s; first reply after it: {out['first_reply_s']} s")
        if out.get("tokens_per_request"):
            print(f"Tokens per request (mean, including the persona build): {out['tokens_per_request']}")
        print(f"Persona built in {out['persona_s']} s")
        print(f"Reply after the rep's line: median {out['median_s']} s, p90 {out['p90_s']} s, max {out['max_s']} s "
              f"over {out['replies']} replies (target {out['target_s']} s)")
        if out["warm_up_replies_s"]:
            print(f"Warm-up drill (not counted): {out['warm_up_replies_s']}")
        for r in out["example_replies"]:
            print(f"  customer: {r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
