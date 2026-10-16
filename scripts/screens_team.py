"""Screenshots of Leads, Prep, Team, a rep, Settings and the Playbook, at desktop and phone width,
plus a keyboard-only pass. No models: Sel's rules and the example calls.

    pip install playwright   # Chromium: `playwright install chromium` (pre-installed in the cloud sandbox)
    python scripts/screens_team.py [--out shots/]

Starts its own server on a temporary calls folder with the two example calls, a manager (Mina), a rep
(Asha) who owns solar-01, a lead (Ramesh Thapa) for that call, and one team answer the Librarian
proposed from an endorsed better line. Writes PNGs to --out and prints what the keyboard pass reached.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sel.adapters import FixtureTranscriber  # noqa: E402
from sel.live import RuleAnalyzer  # noqa: E402
from sel.server import serve  # noqa: E402
from sel.service import CallService  # noqa: E402
from sel.simulator import SimulatorHub  # noqa: E402

SOLAR = ROOT / "examples/calls/solar-01"
SIZES = {"desktop": (1440, 1000), "phone": (390, 844)}


def seed(service: CallService) -> dict[str, str]:
    service.create_from_example(SOLAR, "demo-solar")
    service.create_from_example(ROOT / "examples/calls/pos-01", "demo-pos")
    mgr = service.add_person("Mina Shrestha", "manager")["id"]
    rep = service.add_person("Asha Gurung", "rep")["id"]
    other = service.add_person("Bikash Rai", "rep")["id"]
    service.set_rep("example-solar-01", rep)
    service.set_rep("example-pos-01", other)
    lead = service.add_lead("Ramesh Thapa", "+977 9841-000000", rep)["id"]
    service.set_lead("example-solar-01", lead)
    coaching = service.coaching("example-solar-01")
    m = next(m for m in coaching["moments"] if m["criterion"] == "objection-handling" and m["kind"] == "weak")
    service.moment_action("example-solar-01", m["id"], "endorse", {"by": rep})
    service.wait_idle()
    service.run_librarian("demo-solar")
    return {"manager": mgr, "rep": rep, "lead": lead}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="shots")
    args = ap.parse_args()
    from playwright.sync_api import sync_playwright

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        service = CallService(tmp, ROOT / "packs", analysis_factory=lambda c, s: (
            FixtureTranscriber(SOLAR / "transcript.json"), RuleAnalyzer(), None))
        ids = seed(service)
        hub = SimulatorHub(service, speech_status=lambda: (False, "off"))
        httpd = serve(service, port=0, quiet=True, hub=hub)
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        pages = [("leads", "#leads", ids["rep"]), ("lead", f"#lead/{ids['lead']}", ids["rep"]),
                 ("prep", f"#prep/{ids['lead']}", ids["rep"]), ("team", "#team", ids["manager"]),
                 ("rep", f"#team/{ids['rep']}", ids["manager"]), ("settings", "#settings", ids["manager"]),
                 ("playbook", "#playbook/demo-solar", ids["manager"]), ("call", "#call/example-solar-01", ids["rep"])]
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            errors: list[str] = []
            for size, (w, h) in SIZES.items():
                for name, hash_, viewer in pages:
                    ctx = browser.new_context(viewport={"width": w, "height": h})
                    ctx.add_init_script(f"localStorage.setItem('sel.viewer', JSON.stringify({viewer!r}))")
                    page = ctx.new_page()
                    page.on("pageerror", lambda e: errors.append(str(e)))
                    page.goto(base + "/" + hash_)
                    page.wait_for_selector("main .page, main .call-head", timeout=15000)
                    page.wait_for_timeout(600)
                    overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
                    page.screenshot(path=str(out / f"{name}-{size}.png"), full_page=True)
                    print(f"{name:9} {size:8} horizontal overflow {overflow}px")
                    ctx.close()

            # Keyboard only: from the skip link, Tab to Leads, open it, then to the lead and Prep, and rehearse.
            ctx = browser.new_context(viewport={"width": SIZES["desktop"][0], "height": SIZES["desktop"][1]})
            ctx.add_init_script(f"localStorage.setItem('sel.viewer', JSON.stringify({ids['rep']!r}))")
            page = ctx.new_page()
            page.goto(base + "/#today")
            page.wait_for_selector("main h1")
            reached = []

            def tab_to(selector: str, limit: int = 80) -> bool:
                for _ in range(limit):
                    page.keyboard.press("Tab")
                    if page.evaluate("(s) => document.activeElement && document.activeElement.matches(s)", selector):
                        reached.append(selector)
                        return True
                return False

            ok = tab_to("a[data-nav=leads]")
            page.keyboard.press("Enter")
            page.wait_for_selector("text=Your leads")
            ok = ok and tab_to(f"a[href='#prep/{ids['lead']}']")
            page.keyboard.press("Enter")
            page.wait_for_selector("text=Likely objections")
            ok = ok and tab_to("button.primary")
            focus_ring = page.evaluate("getComputedStyle(document.activeElement).outlineStyle")
            page.keyboard.press("Enter")
            page.wait_for_url("**/#practice/*", timeout=10000)
            reached.append(page.url.split("#", 1)[1])
            print("keyboard pass:", "ok" if ok else "FAILED", "→", " → ".join(reached), f"(focus outline: {focus_ring})")
            ctx.close()
            browser.close()
            print("page errors:", errors or "none")
        service.wait_idle()
        httpd.shutdown()
        hub.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
