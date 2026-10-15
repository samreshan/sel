"""Measure how fast the review screen is on a long call, in a real browser.

    pip install playwright            # uses Chromium; `playwright install chromium` if none is found
    python scripts/perf_ui.py         # serves this checkout with a generated 2-hour call
    python scripts/perf_ui.py --url http://127.0.0.1:8765 --call example-longcall

Without --url it builds a 2-hour stress call (pos-01 repeated, 1,188 lines,
silent audio made with ffmpeg) and serves it from a temporary folder. Prints
medians as JSON. The numbers in CLAUDE.md come from this script; to compare
two versions, serve each checkout and point --url at both.
"""

from __future__ import annotations

import argparse
import copy
import json
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

READY = "document.querySelectorAll('.transcript .seg').length === {n} && document.querySelector('.panel .card')"
LONGTASKS = ("window.__lt=[];try{new PerformanceObserver(l=>{for(const e of l.getEntries())window.__lt.push(e.duration)})"
             ".observe({type:'longtask',buffered:true})}catch(e){}")
# Click, then wait frame by frame until the condition holds; ms until the frame after it does.
TIMED = """(async ([target, cond]) => {
  const t = performance.now();
  if (target === '__escape') document.activeElement.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
  else if (target.startsWith('__row:')) document.querySelectorAll('.transcript .seg')[Number(target.slice(6))].querySelector('.edit-btn').click();
  else document.querySelector(target).click();
  for (;;) {
    await new Promise(r => requestAnimationFrame(r));
    if (eval(cond)) break;
    if (performance.now() - t > 20000) return -1;
  }
  await new Promise(r => setTimeout(r, 0));
  return performance.now() - t;
})"""


def build_long_call(dest: Path, seconds: int = 7200) -> int:
    """pos-01 repeated for `seconds`, with analysis items on every sixth repeat."""
    src = ROOT / "examples/calls/pos-01"
    t = json.loads((src / "transcript.json").read_text(encoding="utf-8"))
    a = json.loads((src / "analysis.json").read_text(encoding="utf-8"))
    segs, ids = t["segments"], [s["id"] for s in t["segments"]]
    dur = max(s["end"] for s in segs) + 1
    out, reps = [], 0
    while reps * dur < seconds:
        for s in segs:
            out.append({**s, "id": f"s{len(out) + 1:04d}", "start": round(s["start"] + reps * dur, 2),
                        "end": round(s["end"] + reps * dur, 2)})
        reps += 1
    analysis = {"findings": [], "claims": [], "commitments": []}
    for r in range(0, reps, 6):
        for key in analysis:
            for item in a.get(key, []):
                it = copy.deepcopy(item)
                it["id"] = f"{item['id']}r{r}"
                it["evidence"]["segment_ids"] = [f"s{r * len(segs) + ids.index(i) + 1:04d}" for i in item["evidence"]["segment_ids"]]
                analysis[key].append(it)
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "transcript.json").write_text(json.dumps({**t, "segments": out}), encoding="utf-8")
    (dest / "analysis.json").write_text(json.dumps(analysis), encoding="utf-8")
    if shutil.which("ffmpeg"):
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-t",
                        str(int(out[-1]["end"]) + 5), "-c:a", "libopus", "-b:a", "6k", str(dest / "audio.ogg")], check=True)
    return len(out)


def local_server(tmp: Path) -> tuple[str, int]:
    from sel.server import serve
    from sel.service import CallService

    n = build_long_call(tmp / "longcall")
    service = CallService(tmp / "calls", ROOT / "packs")
    service.create_from_example(ROOT / "examples/calls/pos-01", "demo-pos")
    service.create_from_example(tmp / "longcall", "demo-pos")
    httpd = serve(service, port=0, quiet=True, health=lambda: {"llm": {"ok": False}, "asr": {"ok": False}})
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{httpd.server_address[1]}", n


def metrics(cdp) -> dict[str, float]:
    return {m["name"]: m["value"] for m in cdp.send("Performance.getMetrics")["metrics"]}


def measure(base: str, call: str, n: int, short: str, n_short: int) -> dict:
    from playwright.sync_api import sync_playwright

    ready = READY.format(n=n)
    out: dict = {}
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        cold = []
        for _ in range(6):
            ctx = b.new_context()
            page = ctx.new_page()
            page.add_init_script(LONGTASKS)
            sizes = []
            page.on("requestfinished", lambda r: sizes.append(max(0, r.sizes()["responseBodySize"]) + r.sizes()["responseHeadersSize"]))
            page.goto(f"{base}/#call/{call}")
            page.wait_for_function(ready)
            cold.append((page.evaluate("performance.now()"), sum(page.evaluate("window.__lt")), sum(sizes)))
            ctx.close()
        cold = cold[1:]  # the first run warms up the browser
        out["cold_ready_ms"] = round(statistics.median(c[0] for c in cold))
        out["cold_longtasks_ms"] = round(statistics.median(c[1] for c in cold))
        out["cold_kb"] = round(statistics.median(c[2] for c in cold) / 1024, 1)

        ctx = b.new_context()
        page = ctx.new_page()
        page.goto(f"{base}/#call/{call}")
        page.wait_for_function(ready)
        reloads, kb = [], []
        for _ in range(6):
            sizes = []
            # -1 means the body came from the browser's cache.
            page.on("requestfinished", lambda r, s=sizes: s.append(max(0, r.sizes()["responseBodySize"])))
            page.reload()
            page.wait_for_function(ready)
            reloads.append(page.evaluate("performance.now()"))
            page.wait_for_timeout(300)
            kb.append(sum(sizes) / 1024)
        out["reload_ready_ms"] = round(statistics.median(reloads[1:]))
        out["reload_kb"] = round(statistics.median(kb[1:]), 1)

        timed = {"tab": [], "evidence": [], "edit_open": [], "edit_close": [], "switch_short": [], "switch_long": []}
        row = min(900, n - 1)
        for _ in range(3):
            t = lambda target, cond: page.evaluate(TIMED, [target, cond])  # noqa: E731
            timed["tab"].append(t("#tab-claims", "document.querySelector('.panel .claim')"))
            timed["tab"].append(t("#tab-coaching", "document.querySelector('.panel .finding')"))
            timed["evidence"].append(t(".panel .ref-btn", "document.querySelector('.transcript .seg.is-evidence')"))
            page.evaluate(f"document.querySelectorAll('.transcript .seg')[{row}].scrollIntoView()")
            timed["edit_open"].append(t(f"__row:{row}", "document.querySelector('.edit-text')"))
            timed["edit_close"].append(t("__escape", "!document.querySelector('.edit-text')"))
            timed["switch_short"].append(t(f'a[href="#call/{short}"]', f"document.querySelectorAll('.transcript .seg').length === {n_short}"))
            timed["switch_long"].append(t(f'a[href="#call/{call}"]', ready))
        out["interaction_ms"] = {k: round(statistics.median(v), 1) for k, v in timed.items()}

        cdp = ctx.new_cdp_session(page)
        cdp.send("Performance.enable")
        page.evaluate("(() => { const a = document.querySelector('audio'); if (!a) return; a.muted = true;"
                      " a.currentTime = a.duration ? a.duration / 2 : 0; a.playbackRate = 2; return a.play(); })()")
        page.wait_for_timeout(3000)
        m0 = metrics(cdp)
        page.wait_for_timeout(8000)
        m1 = metrics(cdp)
        out["playback_8s_ms"] = {k: round((m1[k] - m0[k]) * 1000, 1) for k in ("ScriptDuration", "LayoutDuration", "TaskDuration")}

        issued = []
        page.on("request", lambda r: issued.append(r.url))
        page.evaluate("document.querySelector('audio') && document.querySelector('audio').pause()")
        page.wait_for_timeout(25000)
        out["idle_requests_per_25s"] = sum(1 for u in issued if "/audio" not in u)
        b.close()
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", help="a running Sel server (default: serve this checkout with a generated long call)")
    ap.add_argument("--call", default="example-longcall")
    ap.add_argument("--lines", type=int, default=1188, help="lines in --call, when --url is given")
    args = ap.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        if args.url:
            base, n = args.url.rstrip("/"), args.lines
        else:
            base, n = local_server(Path(tmp))
        print(json.dumps(measure(base, args.call, n, "example-pos-01", 11), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
