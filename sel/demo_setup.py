"""``python -m sel demo-setup``: get a running Sel ready for the coaching-loop demo (docs/scope.md).

It talks to the running server through its own API (``sel serve`` must be up),
so everything it files is processed by the same models and shown in the same
browser the demo uses, and it can be run again safely: anything already there
is reused.

1. Checks: open models only (``/api/doctor``), the analysis and chat models
   answer, speech recognition is installed. Stops with what to fix.
2. The team: a rep (Asha Gurung) and a manager (Mina Shrestha).
3. A lead (Ramesh Thapa) for the rep.
4. The first real call: the solar-01 recording, uploaded as an ordinary call
   for that rep and lead, so the real models transcribe, analyse and coach it
   (the example call loaded at start-up carries hand-written stand-ins).
5. Waits until it is coached and the Coach has set the rep's focus.
6. With ``--next``: the "next real call", where the rep asks what the other
   quote includes before defending the price. It is made with Piper from the
   solar-01 transcript (or taken from ``--next-audio``) and uploaded only
   after the focus is set, so its transfer check runs.

Nothing is sent anywhere; the calls are fictional example data.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "examples" / "calls" / "solar-01"
PACK = "demo-solar"
FIRST_FILE = "demo-solar-01.ogg"
NEXT_FILE = "demo-solar-asks.wav"
# The follow-up call: the same conversation, but the rep answers the price objection with a question first
# (the "asks what the other quote includes" case in examples/calls/solar-01/transfer_gold.json).
NEXT_LINE = ("s05", "What does the other quote include: which panel brand and inverter, and how many years of warranty?")
VOICES = ("models/piper/en_US-ryan-medium.onnx", "models/piper/en_US-lessac-medium.onnx")  # rep, customer


class SetupError(RuntimeError):
    """A step that can't go on; the message says what to do."""


class Api:
    def __init__(self, base: str, timeout: float = 60.0):
        self.base = base.rstrip("/")
        self.timeout = timeout

    def call(self, method: str, path: str, body: Any = None, data: bytes | None = None,
             content_type: str = "application/json") -> Any:
        payload = data if data is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(self.base + path, data=payload, method=method,
                                     headers={"Content-Type": content_type, "X-Sel-Tab": "demo-setup"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read()).get("error")
            except Exception:  # noqa: BLE001
                detail = None
            raise SetupError(f"{method} {path} returned HTTP {e.code}: {detail or e.reason}") from e
        except urllib.error.URLError as e:
            raise SetupError(f"Sel is not running at {self.base} ({e.reason}). Start it first: python -m sel serve") from e


def check(api: Api, say: Callable[[str], None], allow_rules: bool) -> dict[str, Any]:
    report = api.call("GET", "/api/doctor")
    if not report.get("ok"):
        raise SetupError("sel doctor: " + "; ".join(report.get("problems") or ["a proprietary model service is configured"]))
    say("Open models only: OK")
    missing = []
    for key, what in (("llm", "analysis model"), ("chat", "chat model (Coach and AI customer)")):
        info = report.get(key) or {}
        health = info.get("health") or {}
        if info.get("error") or not health.get("ok"):
            missing.append(f"{what} {info.get('model') or ''}: {info.get('error') or health.get('error') or 'not reachable'}")
        else:
            say(f"{what[0].upper() + what[1:]}: {info['model']} ({info.get('licence')}), {info.get('runs_on')}")
    if not (report.get("speech") or {}).get("installed"):
        raise SetupError('speech recognition is not installed: pip install -e ".[speech]" (and ffmpeg)')
    say("Speech recognition: installed")
    if missing:
        hint = "Start Ollama and pull the models (ollama pull qwen2.5:7b; ollama pull qwen2.5:1.5b)."
        if not allow_rules:
            raise SetupError("; ".join(missing) + ". " + hint + " Or pass --allow-rules for a dry run on Sel's rules.")
        say("Warning: " + "; ".join(missing) + ". Continuing on Sel's rules (--allow-rules): no better lines.")
    return report


def person(api: Api, name: str, role: str, say: Callable[[str], None]) -> dict[str, Any]:
    for p in api.call("GET", "/api/people"):
        if p["name"].casefold() == name.casefold():
            if p["role"] != role:
                say(f"Note: {name} is already on the team as a {p['role']}, not a {role}; using them as they are.")
            else:
                say(f"{name} ({role}): already on the team")
            return p
    p = api.call("POST", "/api/people", {"name": name, "role": role})
    say(f"{name} ({role}): added")
    return p


def lead(api: Api, name: str, rep_id: str, say: Callable[[str], None]) -> dict[str, Any]:
    for x in api.call("GET", "/api/leads"):
        if x["name"].casefold() == name.casefold():
            say(f"Lead {name}: already there")
            return x
    x = api.call("POST", "/api/leads", {"name": name, "rep_id": rep_id,
                                        "notes": "Fictional demo lead: a homeowner comparing solar quotes."})
    say(f"Lead {name}: added for the rep")
    return x


def existing(api: Api, filename: str) -> dict[str, Any] | None:
    calls = [c for c in api.call("GET", "/api/calls") if c.get("original_filename") == filename]
    return calls[0] if calls else None


def upload(api: Api, audio: Path, filename: str, started_at: str, rep: dict[str, Any], lead_: dict[str, Any],
           customer: str) -> str:
    q = urllib.parse.urlencode({"pack": PACK, "started_at": started_at, "filename": filename, "rep_id": rep["id"],
                                "rep_name": rep["name"], "lead_id": lead_["id"], "customer_name": customer,
                                "channels": "auto"})
    created = api.call("POST", f"/api/calls?{q}", data=audio.read_bytes(), content_type="application/octet-stream")
    return created["id"]


def wait_coached(api: Api, call_id: str, minutes: float, say: Callable[[str], None],
                 sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """Until the call is analysed and coached; prints each stage once."""
    deadline = time.monotonic() + minutes * 60
    seen = None
    while True:
        call = api.call("GET", f"/api/calls/{urllib.parse.quote(call_id)}")
        stage = (call.get("status"), call.get("stage"), call.get("coaching_status"))
        if stage != seen:
            say(f"  {call_id}: {call.get('status')} · {call.get('stage') or ''}"
                + (f" · coaching {call['coaching_status']}" if call.get("coaching_status") else ""))
            seen = stage
        if call.get("status") == "error":
            raise SetupError(f"analysing {call_id} failed: {call.get('error')}")
        if call.get("status") == "done" and call.get("coaching_status") in ("done", "error"):
            if call.get("coaching_status") == "error":
                say(f"  Coaching fell back to Sel's rules: {call.get('coaching_error') or 'see the Coaching tab'}")
            return call
        if time.monotonic() > deadline:
            raise SetupError(f"{call_id} is still {call.get('status')} after {minutes:g} minutes; it keeps running in "
                             "Sel. Run demo-setup again later to carry on.")
        sleep(2)


def wait_focus(api: Api, person_id: str, minutes: float, sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    deadline = time.monotonic() + minutes * 60
    while True:
        view = api.call("GET", f"/api/coach/{urllib.parse.quote(person_id)}")
        if view.get("focus"):
            return view
        if time.monotonic() > deadline:
            raise SetupError("the Coach has not set a focus yet; open Today as the rep, or run demo-setup again")
        sleep(1)


def next_transcript() -> dict[str, Any]:
    t = json.loads((EXAMPLE / "transcript.json").read_text(encoding="utf-8"))
    for s in t["segments"]:
        if s["id"] == NEXT_LINE[0]:
            s["text"] = NEXT_LINE[1]
    return t


def make_next_audio(out: Path, say: Callable[[str], None]) -> Path:
    """Read the follow-up call aloud with Piper (scripts/synth_call.py), rep left and customer right."""
    voices = [ROOT / v for v in VOICES]
    if not all(v.is_file() for v in voices):
        raise SetupError("the Piper voices are missing (models/piper/); run scripts/setup_local.sh once with the "
                         "network on, or record the follow-up call yourself and pass --next-audio")
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import synth_call  # noqa: PLC0415 - optional, needs piper-tts
    except ImportError as e:
        raise SetupError(f'Piper is not installed ({e}): pip install -e ".[tts]", or pass --next-audio') from e
    from piper import PiperVoice  # noqa: PLC0415

    say("Making the follow-up call with Piper (a minute or so)…")
    rep, customer = (PiperVoice.load(str(v)) for v in voices)
    signal, _ = synth_call.build(next_transcript(), {"rep": rep, "customer": customer}, 0.4, stereo=True)
    synth_call.write_wav(out, signal)
    return out


def run(url: str, *, rep_name: str = "Asha Gurung", manager_name: str = "Mina Shrestha", lead_name: str = "Ramesh Thapa",
        next_call: bool = False, next_audio: Path | None = None, minutes: float = 30, allow_rules: bool = False,
        out_dir: Path = Path("demo"), say: Callable[[str], None] = print,
        sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    api = Api(url)
    say(f"Sel at {api.base}")
    check(api, say, allow_rules)

    rep = person(api, rep_name, "rep", say)
    manager = person(api, manager_name, "manager", say)
    the_lead = lead(api, lead_name, rep["id"], say)

    audio = next((EXAMPLE / f"audio{ext}" for ext in (".ogg", ".wav", ".mp3") if (EXAMPLE / f"audio{ext}").is_file()), None)
    if audio is None:
        raise SetupError(f"no recording in {EXAMPLE}")
    started = json.loads((EXAMPLE / "transcript.json").read_text(encoding="utf-8"))["started_at"]
    first = existing(api, FIRST_FILE)
    if first:
        say(f"First call: already filed ({first['id']})")
        first_id = first["id"]
    else:
        first_id = upload(api, audio, FIRST_FILE, started, rep, the_lead, lead_name)
        say(f"First call: uploaded as {first_id}; the models transcribe, analyse and coach it now")
    t = time.monotonic()
    wait_coached(api, first_id, minutes, say, sleep)
    say(f"First call: ready ({round(time.monotonic() - t)} s waited)")
    coach = wait_focus(api, rep["id"], 5, sleep)
    focus = coach["focus"]
    say(f"{rep_name}'s focus: {focus.get('title') or focus.get('criterion')}; drills: {len(coach.get('drills') or [])}")

    result = {"rep": rep, "manager": manager, "lead": the_lead, "first_call": first_id, "focus": focus, "next_call": None}
    if next_call or next_audio:
        follow = existing(api, NEXT_FILE)
        if follow:
            say(f"Follow-up call: already filed ({follow['id']})")
            result["next_call"] = follow["id"]
        else:
            path = Path(next_audio) if next_audio else out_dir / NEXT_FILE
            if not path.is_file():
                if next_audio:
                    raise SetupError(f"{path} does not exist")
                out_dir.mkdir(parents=True, exist_ok=True)
                make_next_audio(path, say)
            when = (datetime.fromisoformat(started) + timedelta(days=2)).isoformat()
            result["next_call"] = upload(api, path, NEXT_FILE, when, rep, the_lead, lead_name)
            say(f"Follow-up call: uploaded as {result['next_call']}; it is coached and its transfer check runs "
                "after the first call (watch it on Today as the rep)")

    say("")
    say("Ready. In the browser:")
    say(f"  1. Viewing as {rep_name} → Calls → {lead_name} ({first_id}) → Coaching tab")
    say(f"  2. Practise this (90 s) on the price objection")
    say(f"  3. Today: this week's focus")
    if result["next_call"]:
        say(f"  4. The follow-up call ({result['next_call']}): the transfer check on Today; then Viewing as "
            f"{manager_name} → Team")
    else:
        say(f"  4. For the transfer check, run again with --next about 10 minutes before recording")
    say("  5. Settings: every model, where it runs, its licence")
    return result
