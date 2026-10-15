"""Play a recorded stereo test call through the call simulator, with real live speech recognition.

    pip install -e ".[speech]" playwright
    python scripts/simulate_call.py examples/calls/pos-02 --pack demo-pos --model base

Two Chromium processes join one simulated call. Each gets one channel of the
example's stereo recording as its microphone (rep left, customer right), so
the browsers "speak" the call to each other over WebRTC while Sel transcribes
each side live with Whisper, scores it, records it and files it. Prints how
long after each utterance its line appeared, the live score over time, the
filed transcript and drafts, and a score against the example's gold
annotations (matched by time). Needs ffmpeg; downloads the Whisper model on
first use. No language model is needed: without one, Sel's rules analyse the
filed call.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("example", type=Path, help="folder with a stereo audio.ogg (rep left), gold.json and reference.json")
    ap.add_argument("--pack", required=True, help="pack id, e.g. demo-pos")
    ap.add_argument("--model", default="base", help="live Whisper model (SEL_LIVE_WHISPER_MODEL)")
    ap.add_argument("--language", default=None, help="SEL_WHISPER_LANGUAGE, e.g. en or ne")
    args = ap.parse_args()
    os.environ["SEL_LIVE_WHISPER_MODEL"] = args.model
    if args.language:
        os.environ["SEL_WHISPER_LANGUAGE"] = args.language
    os.chdir(ROOT)

    from playwright.sync_api import sync_playwright

    from sel.evaluate import score
    from sel.models import Analysis, Transcript
    from sel.pack import load_pack
    from sel.pipeline import build_report
    from sel.server import serve
    from sel.service import CallService
    from sel.simulator import SimulatorHub

    example = args.example.resolve()
    tmp = Path(tempfile.mkdtemp(prefix="sel-sim-"))
    for i, side in enumerate(("rep", "customer")):
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(example / "audio.ogg"), "-af", f"pan=mono|c0=c{i}",
                        "-ar", "48000", "-c:a", "pcm_s16le", str(tmp / f"{side}.wav")], check=True)
    length = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                   str(tmp / "rep.wav")], capture_output=True, text=True, check=True).stdout)

    service = CallService(tmp / "calls", ROOT / "packs")
    hub = SimulatorHub(service)
    latency: list[float] = []
    plain_add = hub._add

    def add(room, role, start, end, text, source, confidence=None):
        line = plain_add(room, role, start, end, text, source, confidence)
        if source == "speech":
            latency.append(hub._elapsed(room) - end)
        return line

    hub._add = add
    httpd = serve(service, port=0, quiet=True, hub=hub)
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    flags = ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", "--autoplay-policy=no-user-gesture-required"]
    with sync_playwright() as p:
        rb = p.chromium.launch(args=flags + [f"--use-file-for-fake-audio-capture={tmp / 'rep.wav'}"])
        cb = p.chromium.launch(args=flags + [f"--use-file-for-fake-audio-capture={tmp / 'customer.wav'}"])
        rep, cust = rb.new_page(), cb.new_page()
        rep.goto(f"{base}/#sim")
        rep.fill("#sim-name", "Rep")
        rep.select_option("#sim-pack", args.pack)
        rep.click("text=Open a call")
        rep.wait_for_url("**/#sim/*")
        code = rep.url.rsplit("/", 1)[1]
        cust.goto(f"{base}/#sim")
        cust.fill("#sim-name", "Customer")
        cust.fill("#sim-email", "customer@example.com")
        cust.locator(".sim-room-item", has_text=code).locator("button").click()
        cust.wait_for_url(f"**/#sim/{code}")
        rep.click("text=Start the call")
        rep.wait_for_selector(".sim-head :text('Live')")
        room = hub.rooms[code]
        # The fake microphones start playing their file when opened, so open both together.
        cust.click("text=Turn on microphone")
        rep.click("text=Turn on microphone")
        offset = hub._elapsed(room)
        timeline, started = [], time.time()
        while time.time() - started < length + 1:
            rep.wait_for_timeout(10000)
            timeline.append((round(hub._elapsed(room)), (room.live or {}).get("score", {}).get("overall"), len(room.lines)))
        rep.click("text=Hang up")
        rep.wait_for_selector("text=Done by Sel", timeout=300000)
        rb.close()
        cb.close()
    httpd.shutdown()

    d = tmp / "calls" / room.call_id
    call = json.loads((d / "call.json").read_text(encoding="utf-8"))
    report = json.loads((d / "report.json").read_text(encoding="utf-8"))
    print(f"live model {args.model}; microphones opened {offset:.2f} s into the call")
    if latency:
        lat = sorted(latency)
        print(f"line on screen after the speaker stopped: median {statistics.median(lat):.2f} s, "
              f"p90 {lat[max(0, int(len(lat) * 0.9) - 1)]:.2f} s, max {lat[-1]:.2f} s ({len(lat)} lines)")
    print("live score every 10 s (elapsed, score, lines):", timeline)
    print(f"filed {room.call_id}: analyzer {report['meta']['analyzer']}; recording {call['audio_file']}")
    for s in report["transcript"]["segments"]:
        print(f"  {s['start']:6.1f} {s['speaker']} {s['text']}")
    print("drafts:", [(x["kind"], x["status"], x["missing"]) for x in report["drafts"]])
    print("score at hang-up:", call["live_score"]["overall"], "· report scorecard:", report["score"]["overall"])

    gold_file, ref_file = example / "gold.json", example / "reference.json"
    if gold_file.is_file() and ref_file.is_file():
        ref = json.loads(ref_file.read_text(encoding="utf-8"))
        t = Transcript.from_dict(report["transcript"])
        # Same day as the reference call, and times shifted by when the microphones opened.
        t = replace(t, started_at=ref["started_at"],
                    segments=[replace(s, start=s.start - offset, end=s.end - offset) for s in t.segments])
        proposed = Analysis.from_dict(json.loads((d / "analysis.json").read_text(encoding="utf-8")))
        result = score(build_report(t, proposed, load_pack(ROOT / "packs" / args.pack), report["meta"]),
                       json.loads(gold_file.read_text(encoding="utf-8")), reference=ref)
        for key in ("findings.question", "findings.objection", "findings.coaching", "commitments", "claims"):
            v = result[key]
            extra = {k: v[k] for k in v if k not in ("tp", "fp", "fn", "precision", "recall")}
            print(f"  {key:20} found {v['tp']}, extra {v['fp']}, missed {v['fn']} {extra or ''}")
    print(f"files kept in {tmp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
