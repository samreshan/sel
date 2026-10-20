"""Calls on disk, the background job that analyses them, and review actions.

Each call is a folder under ``SEL_CALLS_DIR`` (default ``calls/``)::

    call.json            metadata and job status
    audio.<ext>          the recording, as uploaded (or recorded by the call simulator)
    live_transcript.json what the call simulator transcribed while the call was running
    transcript.json      current transcript (including a person's corrections)
    analysis.json        what the model proposed, before the evidence gate
    review.json          dismissed items, draft edits and approvals, transcript edit log
    report.json          the rendered report (rebuilt from the files above), with its scorecard
    coaching.json        weak and strong moments, better lines and what the line gate refused (sel/coach.py),
                         with each person's endorsements, edits, dismissals and notes

and next to the call folders: ``people.json`` holds the team (``sel/people.py``),
``coach/<person>.json`` each rep's focus, transfer checks, escalations and the
Coach's actions log, ``drills/<id>.json`` the practice drills
(``sel/drills.py``), ``leads/<id>/lead.json`` the leads Prep reads
(``sel/leads.py``) and ``playbook/<pack>.json`` the answers the team approved
(``sel/playbook.py``, the overlay on a pack, which is never written to). A call is ``kind`` real (uploaded, examples) or practice
(the simulator); only real calls count towards skills and transfer checks.
A call's ``rep_id`` says whose call it is; Today (``today()``) gathers what
needs each person's attention across their calls.

After a call's report is ready the call is marked done, so it can be reviewed
at once, and the same job then coaches it (``coaching_status`` in call.json).
The Coach runs on its own small queue, so picking a focus never waits behind
a long analysis.

Everything stays on this machine. Jobs run one at a time, because speech and
language models compete for the same CPU.

Changes are published on an ``EventBus`` (topic ``calls``) so open tabs update
without polling. Packs and the call list are cached and re-read only when
their files change.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import shutil
import threading
import traceback
import uuid
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import coach as coaching_mod
from . import drills as drills_mod
from . import persona as persona_mod
from . import playbook as playbook_mod
from . import prep as prep_mod
from . import transfer as transfer_mod
from .files import read_json, write_json
from .coach import CoachStore, LineWriter, ModelCoach, MomentFinder, RuleCoach
from .drills import DrillStore
from .drafts import apply_edits, approve
from .events import EventBus
from .grounding import numbers
from .leads import LeadStore
from .live import objection_cue, pack_index
from .models import Analysis, Speaker, Transcript
from .pack import Pack, load_pack
from .people import PeopleStore
from .pipeline import analyze_call, build_report
from .playbook import PlaybookStore
from .scoring import criterion_signal, score_report

AUDIO_TYPES = {".wav", ".mp3", ".m4a", ".ogg", ".opus", ".flac", ".webm", ".aac", ".mp4"}
SUMMARY_FIELDS = ("id", "pack_id", "source", "status", "stage", "progress", "started_at", "customer_name",
                  "original_filename", "created_at", "updated_at", "rep_id", "rep_name", "coaching_status", "kind",
                  "drill_id", "lead_id")
MOMENT_ACTIONS = ("endorse", "dismiss", "edit", "note")
TODAY_RECENT = 8  # calls listed under "Recent calls"
SKILL_WINDOW = 10  # a person's latest finished calls that "Skills to work on" counts over
EMPTY_REVIEW = {"dismissed": [], "drafts": {}, "transcript_edits": []}
ESCALATION_STATUSES = ("open", "resolved")
SEVERAL_REPS = 2  # a team pattern: a skill at least this many reps fell short on


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def call_kind(call: dict[str, Any]) -> str:
    """real (a customer call) or practice (the simulator). Calls filed before kinds existed: by source."""
    return call.get("kind") or ("practice" if call.get("source") == "simulator" else "real")


def _new_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]


class CallService:
    def __init__(self, calls_dir: str | Path, packs_dir: str | Path, analysis_factory=None,
                 bus: EventBus | None = None, coaching_factory=None, coach_factory=None, chat_factory=None,
                 analysis_llm_factory=None):
        self.root = Path(calls_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.packs_dir = Path(packs_dir)
        self.bus = bus or EventBus()
        self._lock = threading.RLock()
        self._jobs: queue.Queue[str] = queue.Queue()
        self._agents: queue.Queue[str] = queue.Queue()
        self._pack_cache: tuple[tuple, list[Pack]] | None = None
        self._summaries: dict[str, tuple[int, dict[str, Any]]] = {}
        self._reports: dict[str, tuple[int, dict[str, Any]]] = {}
        self._coachings: dict[str, tuple[int, dict[str, Any]]] = {}
        self.people = PeopleStore(self.root / "people.json")
        self.coach_store = CoachStore(self.root / "coach")
        self.drills = DrillStore(self.root / "drills")
        self.leads = LeadStore(self.root / "leads")
        self.playbook = PlaybookStore(self.root / "playbook")
        # Model-written briefs (Prep, 1:1) by (kind, id), kept with a signature of what they were built from.
        self._briefs: dict[tuple[str, str], tuple[str, dict[str, Any]]] = {}
        self._pending: set[tuple[str, str]] = set()  # agent work queued and not finished, so it is queued once
        # Builds (transcriber, analyzer, speaker_labeler) for a call; injectable for tests.
        self._factory = analysis_factory or default_factory
        # The language model that coaches a call (moments, better lines), or None for rules.
        # Default: the analysis model the call was analysed with, so rules-analysed calls get rules coaching.
        self._coaching_factory = coaching_factory or (lambda call, analyzer: getattr(analyzer, "llm", None))
        # The fast chat model (``SEL_CHAT_LLM_*``) for personas, the AI customer and debriefs, or None for
        # rules. None unless given: ``sel serve`` passes ``default_chat_factory``; tests stay deterministic.
        self._chat_factory = chat_factory or (lambda: None)
        # The analysis model when its server answers, for Prep; None for rules (``sel serve`` passes
        # ``default_analysis_llm``; tests stay deterministic).
        self._analysis_llm_factory = analysis_llm_factory or (lambda: None)
        # The Coach: on the chat model when there is one, else rules.
        self._coach_factory = coach_factory or (lambda: ModelCoach(llm) if (llm := self._chat_factory()) else RuleCoach())
        self._worker = threading.Thread(target=self._work, daemon=True)
        self._worker.start()
        self._agent_worker = threading.Thread(target=self._agent_work, daemon=True)
        self._agent_worker.start()

    # -- packs ------------------------------------------------------------

    def _packs_signature(self) -> tuple:
        """Every file under the packs directory with its size and mtime; cheap next to parsing the packs."""
        sig = []
        for dirpath, dirnames, filenames in os.walk(self.packs_dir):
            dirnames.sort()
            for name in sorted(filenames):
                try:
                    st = os.stat(os.path.join(dirpath, name))
                except OSError:
                    continue
                sig.append((dirpath, name, st.st_size, st.st_mtime_ns))
        return tuple(sig)

    def packs(self) -> list[Pack]:
        signature = self._packs_signature()
        cached = self._pack_cache
        if cached and cached[0] == signature:
            return cached[1]
        out = []
        for d in sorted(self.packs_dir.iterdir()):
            if (d / "pack.toml").is_file():
                try:
                    out.append(load_pack(d))
                except ValueError:
                    continue
        self._pack_cache = (signature, out)
        return out

    def packs_etag(self) -> str:
        return f'W/"packs-{hash(self._packs_signature()) & 0xFFFFFFFF:08x}"'

    def pack(self, pack_id: str) -> Pack:
        for p in self.packs():
            if p.id == pack_id:
                return p
        raise KeyError(f"no business pack {pack_id!r}")

    # -- files --------------------------------------------------------------

    def _dir(self, call_id: str) -> Path:
        if not re.fullmatch(r"[a-z0-9-]{1,64}", call_id):
            raise KeyError(call_id)
        d = self.root / call_id
        if not (d / "call.json").is_file():
            raise KeyError(call_id)
        return d

    def _read(self, call_id: str, name: str, default: Any = None) -> Any:
        path = self.root / call_id / name
        if not path.is_file():
            return default
        return read_json(path)

    def _write(self, call_id: str, name: str, value: Any) -> None:
        path = self.root / call_id / name
        write_json(path, value)
        # Caches are keyed by mtime, and two writes can share one: drop this call's entry on every write.
        cache = {"call.json": self._summaries, "report.json": self._reports, "coaching.json": self._coachings}.get(name)
        if cache is not None:
            cache.pop(call_id, None)

    def _set_status(self, call_id: str, **changes: Any) -> None:
        with self._lock:
            call = self._read(call_id, "call.json")
            if call is None:
                return  # deleted while its job was running
            call.update(changes, updated_at=_now())
            self._write(call_id, "call.json", call)
        self._publish(call)

    def wait_idle(self, timeout: float = 30.0) -> bool:
        """Wait until no analysis, coaching or Coach work is queued or running (tests, shutdown)."""
        import time

        deadline = time.monotonic() + timeout
        while self._jobs.unfinished_tasks or self._agents.unfinished_tasks:
            if time.monotonic() > deadline:
                return False
            time.sleep(0.02)
        return True

    def _publish(self, call: dict[str, Any], origin: str | None = None) -> None:
        self.bus.publish("calls", "call", {k: call.get(k) for k in SUMMARY_FIELDS}, origin=origin)

    def audio_path(self, call_id: str) -> Path:
        call = self._read(self._dir(call_id).name, "call.json")
        if not call.get("audio_file"):
            raise KeyError("this call has no recording")
        return self.root / call_id / call["audio_file"]

    def etag(self, call_id: str) -> str:
        """Changes whenever anything ``view`` returns changes, without reading the files."""
        d = self._dir(call_id)
        parts = []
        for name in ("call.json", "report.json", "review.json", "coaching.json"):
            try:
                st = (d / name).stat()
                parts.append(f"{st.st_mtime_ns:x}.{st.st_size:x}")
            except OSError:
                parts.append("-")
        return f'W/"{call_id}-{"-".join(parts)}"'

    def incoming_path(self) -> Path:
        """A fresh path for streaming an upload to disk before it becomes a call."""
        d = self.root / ".incoming"
        d.mkdir(exist_ok=True)
        return d / uuid.uuid4().hex

    @staticmethod
    def _store_audio(audio: bytes | Path, dest: Path) -> None:
        if isinstance(audio, (bytes, bytearray)):
            dest.write_bytes(audio)
        else:
            os.replace(audio, dest)

    # -- creating calls -------------------------------------------------------

    def create(self, audio: bytes | Path, filename: str, meta: dict[str, Any]) -> str:
        """A call from an uploaded recording; ``audio`` is the bytes or a file to move into place."""
        pack = self.pack(meta["pack_id"])
        suffix = Path(filename).suffix.lower()
        if suffix not in AUDIO_TYPES:
            raise ValueError(f"unsupported audio type {suffix or '(none)'}; use one of {', '.join(sorted(AUDIO_TYPES))}")
        datetime.fromisoformat(meta["started_at"])  # validates, raises ValueError
        rep_id, rep_name = self._rep(meta.get("rep_id"), meta.get("rep_name"))
        lead_id = meta.get("lead_id") or None
        if lead_id:
            self.leads.get(lead_id)  # KeyError for an unknown lead
        call_id = _new_id()
        d = self.root / call_id
        d.mkdir(parents=True)
        self._store_audio(audio, d / f"audio{suffix}")
        call = {
            "id": call_id, "pack_id": pack.id, "source": "audio", "original_filename": filename,
            "audio_file": f"audio{suffix}", "started_at": meta["started_at"],
            "rep_id": rep_id, "rep_name": rep_name, "customer_name": meta.get("customer_name") or None,
            "customer_email": meta.get("customer_email") or None,
            "channels": meta.get("channels", "auto"), "rep_channel": int(meta.get("rep_channel", 0)),
            "kind": "real", "lead_id": lead_id,
            "status": "queued", "stage": "waiting", "progress": 0.0, "error": None,
            "created_at": _now(), "updated_at": _now(),
        }
        self._write(call_id, "call.json", call)
        self._write(call_id, "review.json", EMPTY_REVIEW)
        if lead_id:
            self.leads.link(lead_id, call_id)
        self._jobs.put(call_id)
        self._publish(call)
        return call_id

    def create_simulated(self, transcript: Transcript, audio: bytes | Path | None, filename: str | None,
                         meta: dict[str, Any]) -> str:
        """A call recorded by the call simulator: the live transcript, and the recording when there is one.

        The analysis job reads the live transcript (``transcribe: live``) so the
        report is ready seconds after hanging up; "Re-transcribe" on the review
        page runs speech recognition over the stereo recording instead.
        """
        pack = self.pack(meta["pack_id"])
        call_id = _new_id()
        d = self.root / call_id
        d.mkdir(parents=True)
        audio_file = None
        if audio is not None:
            suffix = Path(filename or "recording.webm").suffix.lower() or ".webm"
            if suffix not in AUDIO_TYPES:
                raise ValueError(f"unsupported audio type {suffix}")
            audio_file = f"audio{suffix}"
            self._store_audio(audio, d / audio_file)
        transcript = replace(transcript, call_id=call_id, audio=audio_file)
        self._write(call_id, "live_transcript.json", asdict(transcript))
        rep = next((s for s in transcript.speakers if s.role == "rep"), None)
        cust = next((s for s in transcript.speakers if s.role == "customer"), None)
        rep_id, rep_name = self._rep(meta.get("rep_id"), rep.name if rep else None)
        call = {
            "id": call_id, "pack_id": pack.id, "source": "simulator", "original_filename": None,
            "audio_file": audio_file, "started_at": transcript.started_at,
            "rep_id": rep_id, "rep_name": rep_name, "customer_name": cust.name if cust else None,
            "customer_email": cust.email if cust else None,
            # The simulator records the rep on the left channel and the customer on the right.
            "channels": "split", "rep_channel": 0,
            "transcribe": "live" if transcript.segments or audio_file is None else "recording",
            "analyzer": meta.get("analyzer", "auto"), "room": meta.get("room"), "live_score": meta.get("live_score"),
            "kind": meta.get("kind", "practice"), "drill_id": meta.get("drill_id"),
            "status": "queued", "stage": "waiting", "progress": 0.0, "error": None,
            "created_at": _now(), "updated_at": _now(),
        }
        self._write(call_id, "call.json", call)
        self._write(call_id, "review.json", EMPTY_REVIEW)
        self._jobs.put(call_id)
        self._publish(call)
        return call_id

    def create_from_example(self, example_dir: str | Path, pack_id: str) -> str:
        """Load a fixture call (transcript + hand-written analysis) without running models."""
        example = Path(example_dir)
        # reference.json, when present, is the same transcript with the timings of the example's recording.
        source = example / "reference.json" if (example / "reference.json").is_file() else example / "transcript.json"
        transcript = json.loads(source.read_text(encoding="utf-8"))
        call_id = f"example-{example.name}"
        d = self.root / call_id
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
        audio_file = None
        for suffix in sorted(AUDIO_TYPES):
            src = example / f"audio{suffix}"
            if src.is_file():
                shutil.copy(src, d / src.name)
                audio_file = src.name
                break
        transcript["call_id"] = call_id
        rep = next((sp for sp in transcript.get("speakers", []) if sp.get("role") == "rep"), {})
        cust = next((sp for sp in transcript.get("speakers", []) if sp.get("role") == "customer"), {})
        call = {
            "id": call_id, "pack_id": pack_id, "source": "fixture", "original_filename": None,
            "audio_file": audio_file, "started_at": transcript["started_at"],
            "rep_id": None, "rep_name": rep.get("name"), "customer_name": cust.get("name"), "kind": "real",
            "status": "done", "stage": "done", "progress": 1.0, "error": None,
            "created_at": _now(), "updated_at": _now(),
            "report_meta": {"transcriber": "fixture-transcript", "analyzer": "fixture-analysis (hand-written, not model output)"},
        }
        self._write(call_id, "call.json", call)
        self._write(call_id, "transcript.json", transcript)
        self._write(call_id, "analysis.json", json.loads((example / "analysis.json").read_text(encoding="utf-8")))
        self._write(call_id, "review.json", EMPTY_REVIEW)
        self._rebuild(call_id)
        # coaching.json in an example is a hand-written stand-in for the coaching models, like analysis.json.
        fixture = example / "coaching.json"
        self._coach_call(call_id, fixture=json.loads(fixture.read_text(encoding="utf-8")) if fixture.is_file() else None)
        self._publish(self._read(call_id, "call.json"))
        return call_id

    def retry(self, call_id: str, transcribe: str | None = None) -> None:
        """Run the analysis again; for a simulated call, ``transcribe="recording"`` re-transcribes the audio."""
        self._dir(call_id)
        call = self._read(call_id, "call.json")
        if call.get("source") not in ("audio", "simulator"):
            raise ValueError("only uploaded or simulated calls can be re-analysed")
        changes: dict[str, Any] = {}
        if transcribe is not None:
            if call.get("source") != "simulator" or transcribe not in ("live", "recording"):
                raise ValueError("transcribe must be 'live' or 'recording', for simulated calls only")
            if transcribe == "recording" and not call.get("audio_file"):
                raise ValueError("this call has no recording to transcribe")
            changes["transcribe"] = transcribe
        self._set_status(call_id, status="queued", stage="waiting", progress=0.0, error=None, **changes)
        self._jobs.put(call_id)

    def delete(self, call_id: str) -> None:
        lead_id = (self._read(self._dir(call_id).name, "call.json") or {}).get("lead_id")
        shutil.rmtree(self._dir(call_id))
        if lead_id:
            try:
                self.leads.link(lead_id, call_id, linked=False)
            except KeyError:
                pass
        self._summaries.pop(call_id, None)
        self._reports.pop(call_id, None)
        self.bus.publish("calls", "call-deleted", {"id": call_id})

    # -- the job --------------------------------------------------------------

    def _work(self) -> None:
        while True:
            call_id = self._jobs.get()
            try:
                self._process(call_id)
            except Exception as e:  # noqa: BLE001 - shown to the user on the call page
                self._set_status(call_id, status="error", stage="failed", error=f"{type(e).__name__}: {e}",
                                 traceback=traceback.format_exc(limit=5))
            finally:
                self._jobs.task_done()

    def _process(self, call_id: str) -> None:
        call = self._read(call_id, "call.json")
        pack = self.pack(call["pack_id"])
        speakers = [Speaker("S1", "rep", call.get("rep_name")),
                    Speaker("S2", "customer", call.get("customer_name"), call.get("customer_email"))]
        transcriber, analyzer, labeler = self._factory(call, speakers)
        if call.get("transcribe") == "live":
            from .adapters import StoredTranscriber
            transcriber = StoredTranscriber(self.root / call_id / "live_transcript.json",
                                            "live transcript (call simulator)")
        self._set_status(call_id, status="running", stage="starting", progress=0.01)

        def progress(stage: str, frac: float) -> None:
            self._set_status(call_id, stage=stage, progress=round(frac, 3))

        if hasattr(analyzer, "progress"):
            analyzer.progress = lambda s, f: progress(s, 0.45 + 0.5 * f)
        audio = str(self.root / call_id / call["audio_file"]) if call.get("audio_file") else ""
        report, proposed = analyze_call(audio, call["started_at"], pack, transcriber, analyzer,
                                        call_id=call_id, speaker_labeler=labeler, progress=progress)
        self._write(call_id, "transcript.json", asdict(report.transcript))
        self._write(call_id, "analysis.json", asdict(proposed))
        self._set_status(call_id, report_meta=report.meta)
        self._rebuild(call_id)
        if call_kind(call) == "practice":
            # Practice is debriefed (drills) or reviewed as it is; it does not count towards coaching.
            self._set_status(call_id, status="done", stage="done", progress=1.0)
            return
        # The review is ready now; coaching follows in the same job and shows on the Coaching tab when done.
        self._set_status(call_id, status="done", stage="done", progress=1.0, coaching_status="queued")
        try:
            self._coach_call(call_id, analyzer=analyzer)
        except Exception as e:  # noqa: BLE001 - the call itself is reviewed and done; only coaching failed
            self._set_status(call_id, coaching_status="error", coaching_stage=None,
                             coaching_error=f"{type(e).__name__}: {e}")

    # -- coaching -----------------------------------------------------------------

    def _coach_call(self, call_id: str, analyzer: Any = None, fixture: dict[str, Any] | None = None) -> None:
        """Moments and better lines for one call (coaching.json), then the Coach for its rep.

        A model error falls back to rules, so the Coaching tab always has moments; the error is kept.
        """
        call = self._read(call_id, "call.json")
        pack = self.pack(call["pack_id"])
        report = self._read(call_id, "report.json")
        old = self._read(call_id, "coaching.json")
        llm = None if fixture is not None else self._coaching_factory(call, analyzer)
        index = pack_index(pack)
        answers = self.playbook.approved(pack.id)  # the flywheel: what the team approved coaches the next call
        before = llm.stats.to_dict() if llm is not None else None
        self._set_status(call_id, coaching_status="running", coaching_stage="finding moments", coaching_error=None)

        def progress(stage: str, frac: float) -> None:
            self._set_status(call_id, coaching_stage=stage.removeprefix("coaching: "))

        error = None
        try:
            coaching = coaching_mod.diagnose(
                report, pack, index, finder=MomentFinder(llm) if llm else None,
                writer=LineWriter(llm, index, answers) if llm else None, judge=llm, fixture=fixture, progress=progress,
                answers=answers)
            if llm is not None:
                after = llm.stats.to_dict()
                coaching["meta"]["llm_usage"] = {k: round(after[k] - before[k], 2) for k in after}
        except Exception as e:  # noqa: BLE001 - the model failed; rules still give the rep their moments
            error = f"{type(e).__name__}: {e}"
            coaching = coaching_mod.diagnose(report, pack, index, answers=answers)
            coaching["error"] = f"The coaching model failed, so Sel's rules found these moments: {error}"
        self._write(call_id, "coaching.json", coaching_mod.carry_over(coaching, old))
        self._set_status(call_id, coaching_status="error" if error else "done", coaching_stage=None,
                         coaching_error=error)
        self.bus.publish("calls", "coaching", {"id": call_id})
        if call.get("rep_id"):
            self.check_transfer(call_id, llm)
            self.request_coach(call["rep_id"])

    def check_transfer(self, call_id: str, llm: Any = None) -> dict[str, Any] | None:
        """If this real call's rep has a focus set before the call, judge that skill on it (sel/transfer.py)."""
        call = self._read(call_id, "call.json")
        rep_id = call.get("rep_id") if call else None
        if not rep_id or call.get("status") != "done":
            return None
        data = self.coach_store.get(rep_id)
        if not transfer_mod.due({**call, "kind": call_kind(call)}, data.get("focus"), data.get("transfer", [])):
            return None
        report = self._read(call_id, "report.json")
        result = transfer_mod.check(call, report, self.pack(call["pack_id"]), data["focus"], llm)
        with self.coach_store.lock:
            data = self.coach_store.get(rep_id)
            data.setdefault("transfer", []).append(result)
            data["transfer"] = data["transfer"][-100:]
            self.coach_store.save(rep_id, data)
        self.bus.publish("calls", "coach", {"person_id": rep_id, "transfer": result})
        return result

    def coaching(self, call_id: str) -> dict[str, Any] | None:
        self._dir(call_id)
        return self._read(call_id, "coaching.json")

    def _coaching(self, call_id: str) -> dict[str, Any] | None:
        """coaching.json, cached by mtime (Today and the Coach read many)."""
        path = self.root / call_id / "coaching.json"
        try:
            mtime = path.stat().st_mtime_ns
        except OSError:
            return None
        cached = self._coachings.get(call_id)
        if cached is None or cached[0] != mtime:
            cached = (mtime, read_json(path))
            self._coachings[call_id] = cached
        return cached[1]

    def moment_action(self, call_id: str, moment_id: str, action: str, body: dict[str, Any],
                      origin: str | None = None) -> dict[str, Any]:
        """endorse / dismiss / edit / note one moment. ``body["by"]`` is the person acting (no login)."""
        if action not in MOMENT_ACTIONS:
            raise KeyError(action)
        by = body.get("by") or None
        if by and self.people.get(str(by)) is None:
            raise KeyError(f"no person {by!r}")
        with self._lock:
            self._dir(call_id)
            coaching = self._read(call_id, "coaching.json")
            if coaching is None:
                raise KeyError(f"{call_id} has no coaching yet")
            pack = self.pack(coaching.get("pack_id") or self._read(call_id, "call.json")["pack_id"])
            moment = coaching_mod.act_on_moment(coaching, moment_id, action, body, pack, pack_index(pack), by)
            self._write(call_id, "coaching.json", coaching)
        self.bus.publish("calls", "coaching", {"id": call_id}, origin=origin)
        return moment

    # -- the Coach ----------------------------------------------------------------

    def request_coach(self, person_id: str) -> None:
        """Ask the Coach to look at this rep (runs on the agent queue)."""
        self._agents.put(("coach", person_id))

    def _agent_work(self) -> None:
        """Agent work that must not wait behind a long analysis: Coach turns, personas, debriefs, transfer checks."""
        while True:
            kind, arg = self._agents.get()
            try:
                if kind == "coach":
                    self.run_coach(arg)
                elif kind == "persona":
                    self.prepare_drill(arg)
                elif kind == "debrief":
                    self.run_debrief(arg)
                elif kind == "prep":
                    self.build_prep(arg)
                elif kind == "one_on_one":
                    self.build_one_on_one(arg)
                elif kind == "librarian":
                    self.run_librarian(arg)
                elif kind == "transfer":
                    if self.check_transfer(arg, self._chat_factory()):
                        rep_id = (self._read(arg, "call.json") or {}).get("rep_id")
                        if rep_id:
                            self.run_coach(rep_id)
            except Exception:  # noqa: BLE001 - agents are best effort; the next event asks again
                traceback.print_exc()
            finally:
                with self._lock:
                    self._pending.discard((kind, arg))
                self._agents.task_done()

    def coach_state(self, person_id: str) -> dict[str, Any]:
        """What the Coach reads: the rep's focus and their moments across recent coached calls."""
        person = self.people.get(person_id)
        if person is None:
            raise KeyError(f"no person {person_id!r}")
        calls = [c for c in self.list() if c.get("rep_id") == person_id and c.get("status") == "done"
                 and call_kind(c) == "real"]
        calls.sort(key=lambda c: c.get("started_at") or "", reverse=True)
        coached = [(c, x) for c in calls[:SKILL_WINDOW] if (x := self._coaching(c["id"]))]
        packs = {p.id: p for p in self.packs()}
        data = self.coach_store.get(person_id)
        contradicted = []
        for c in calls[:SKILL_WINDOW]:
            for claim in (self._report(c["id"]) or {}).get("claims", []):
                if claim.get("verdict") == "contradicted":
                    contradicted.append({"call_id": c["id"], "claim_id": claim["id"], "claim": claim["claim"]})
        closed = [f"{h['pack_id']}/{h['criterion']}" for h in data.get("history", [])[-3:] if h.get("status") == "passed"]
        return {"person_id": person_id, "name": person["name"], "focus": data.get("focus"),
                "skills": coaching_mod.skill_candidates(coached, packs.get), "calls_seen": len(coached),
                "transfer": data.get("transfer", []), "escalations": data.get("escalations", []),
                # Playground drills are practice only: they never escalate, change difficulty or a focus.
                "drills": [self._drill_state(d) for d in self.drills.list(person_id) if d.get("origin") != "playground"],
                "contradicted": contradicted, "closed": closed}

    @staticmethod
    def _drill_state(d: dict[str, Any]) -> dict[str, Any]:
        s = drills_mod.summary(d)
        last = (d.get("attempts") or [{}])[-1] if d.get("attempts") else {}
        return {**s, "last_difficulty": last.get("difficulty")}

    def run_coach(self, person_id: str) -> list[dict[str, Any]]:
        """One Coach turn for a rep: it proposes actions, code validates and applies them."""
        person = self.people.get(person_id)
        if person is None or person["role"] != "rep":
            return []
        state = self.coach_state(person_id)
        agent = self._coach_factory()
        try:
            actions = agent.decide(state)
        except Exception as e:  # noqa: BLE001 - a model that fails or answers nonsense: the rules decide
            agent = RuleCoach()
            actions = agent.decide(state)
            state["model_error"] = f"{type(e).__name__}: {e}"
        if not actions:
            return []
        with self.coach_store.lock:
            data = self.coach_store.get(person_id)
            log = coaching_mod.apply_actions(actions, state, data, agent.name)
            self.coach_store.save(person_id, data)
        for entry in log:
            if not entry["applied"]:
                continue
            args = entry["args"]
            if entry["action"] == "create_drill":
                self.create_drill(args["call_id"], args["moment_id"], person_id, by=agent.name)
            elif entry["action"] == "raise_difficulty":
                self.raise_difficulty(args["drill_id"])
        if any(e["applied"] for e in log):
            self.bus.publish("calls", "coach", {"person_id": person_id, "focus": data.get("focus")})
        return log

    # -- drills -------------------------------------------------------------------

    def create_drill(self, call_id: str, moment_id: str, person_id: str | None = None, by: str | None = None,
                     origin: str | None = None) -> dict[str, Any]:
        """A drill on a weak moment of a real call, for the call's rep (or ``person_id``). One per rep and moment."""
        call = self._read(self._dir(call_id).name, "call.json")
        person_id = call.get("rep_id") or person_id
        person = self.people.get(person_id) if person_id else None
        if person is None or person["role"] != "rep":
            raise ValueError("say whose call this is first: a drill belongs to a rep")
        coaching = self._read(call_id, "coaching.json") or {}
        moment = next((m for m in coaching.get("moments", []) if m["id"] == moment_id), None)
        if moment is None:
            raise KeyError(moment_id)
        if moment["kind"] != "weak":
            raise ValueError("drills practise moments where the rep fell short")
        for d in self.drills.list(person_id):
            if d.get("source") == {"call_id": call_id, "moment_id": moment_id}:
                return d
        line = moment.get("better_line") if (moment.get("better_line") or {}).get("check") == "ok" else None
        drill = {
            "id": self.drills.new_id(), "person_id": person_id, "pack_id": coaching.get("pack_id") or call["pack_id"],
            "criterion": moment["criterion"], "title": moment["title"],
            "source": {"call_id": call_id, "moment_id": moment_id},
            "moment": {"customer_said": (moment.get("customer_said") or {}).get("quote"), "quote": moment["evidence"]["quote"],
                       "why": moment.get("why"), "better_line": {"text": line["text"], "by": line.get("by")} if line else None,
                       "customer_name": call.get("customer_name"), "started_at": call.get("started_at")},
            "persona": None, "difficulty": 1, "status": "preparing", "created_at": _now(),
            "created_by": by or "person", "attempts": [],
        }
        self.drills.save(drill)
        self._agents.put(("persona", drill["id"]))
        self.bus.publish("calls", "drill", drills_mod.summary(drill), origin=origin)
        return drill

    def _real_names(self, call: dict[str, Any]) -> list[str]:
        names = [call.get("customer_name"), call.get("rep_name")] + [p["name"] for p in self.people.list()]
        transcript = self._read(call["id"], "transcript.json") or {}
        names += [s.get("name") for s in transcript.get("speakers", [])]
        return [n for n in names if n]

    def prepare_drill(self, drill_id: str) -> dict[str, Any]:
        """Build the drill's persona (chat model, else rules) from the anonymised moment."""
        from .persona import build_persona

        drill = self.drills.get(drill_id)
        if drill.get("origin") == "playground":
            return self._prepare_objection_drill(drill)
        call = self._read(drill["source"]["call_id"], "call.json") or {"id": drill["source"]["call_id"]}
        pack = self.pack(drill["pack_id"])
        moment = {"criterion": drill["criterion"], "why": drill["moment"].get("why"),
                  "customer_said": {"quote": drill["moment"].get("customer_said")} if drill["moment"].get("customer_said") else None,
                  "evidence": {"quote": drill["moment"]["quote"]}}
        names = self._real_names(call) if call.get("pack_id") else []
        llm = self._chat_factory()
        try:
            persona = build_persona(moment, pack, names, drill_id, llm, drill["difficulty"])
        except Exception as e:  # noqa: BLE001 - a model error leaves the rules persona; the drill still works
            persona = build_persona(moment, pack, names, drill_id, None, drill["difficulty"])
            persona["model_error"] = f"{type(e).__name__}: {e}"
        with self.drills.lock:
            drill = self.drills.get(drill_id)
            drill.update(persona=persona, status="ready")
            self.drills.save(drill)
        self.bus.publish("calls", "drill", drills_mod.summary(drill))
        return drill

    def raise_difficulty(self, drill_id: str) -> dict[str, Any]:
        with self.drills.lock:
            drill = self.drills.get(drill_id)
            drill["difficulty"] = min(drills_mod.MAX_DIFFICULTY, int(drill.get("difficulty") or 1) + 1)
            if drill.get("persona"):
                drill["persona"]["difficulty"] = drill["difficulty"]
            self.drills.save(drill)
        self.bus.publish("calls", "drill", drills_mod.summary(drill))
        return drill

    def drill(self, drill_id: str) -> dict[str, Any]:
        drill = self.drills.get(drill_id)
        person = self.people.get(drill["person_id"])
        return {**drill, "person_name": person["name"] if person else None}

    def list_drills(self, person_id: str | None) -> list[dict[str, Any]]:
        return [drills_mod.summary(d) for d in self.drills.list(person_id)]

    def start_drill(self, drill_id: str, hub, by: str | None = None, offset: str | None = None) -> tuple[Any, str]:
        """A practice room with the AI customer for this drill; returns (room, the rep's client token)."""
        from .ai_customer import AICustomer

        drill = self.drills.get(drill_id)
        if drill.get("status") != "ready" or not drill.get("persona"):
            raise ValueError("the customer for this drill is still being prepared; try again in a moment")
        person = self.people.get(drill["person_id"])
        persona = {**drill["persona"], "difficulty": drill.get("difficulty") or 1}
        agent = AICustomer(persona, self._chat_factory())
        # The AI customer opens with the objection being practised, whatever words it uses: a Playground
        # objection drill always, a call-moment drill when its skill is objection handling.
        crit = self.pack(drill["pack_id"]).criterion(drill["criterion"])
        opening_objection = drill.get("origin") == "playground" or bool(crit and criterion_signal(crit) == "objections")
        return hub.create_drill_room(drill["pack_id"], agent, drill_id, person["name"] if person else None,
                                     drill["person_id"], persona.get("name"), offset, opening_objection)

    def drill_finished(self, room, transcript: Transcript, score: dict[str, Any], call_id: str) -> None:
        """A drill room was filed (the hub calls this): record the attempt and queue its debrief."""
        with self.drills.lock:
            drill = self.drills.get(room.drill_id)
            drill["attempts"].append({"at": _now(), "room": room.code, "sim_call_id": call_id, "status": "judging",
                                      "difficulty": drill.get("difficulty") or 1,
                                      "ai_seconds": list(room.ai_seconds), "customer_by": room.agent.name if room.agent else None})
            self.drills.save(drill)
        self._agents.put(("debrief", room.drill_id))
        self.bus.publish("calls", "drill", drills_mod.summary(drill))

    def run_debrief(self, drill_id: str) -> dict[str, Any]:
        """Judge the latest attempt (chat model, else rules) and write its debrief; then a Coach turn."""
        drill = self.drills.get(drill_id)
        if not drill["attempts"]:
            raise ValueError("no attempt to debrief yet")
        attempt = drill["attempts"][-1]
        call_id = attempt["sim_call_id"]
        transcript = Transcript.from_dict(self._read(call_id, "live_transcript.json"))
        pack = self.pack(drill["pack_id"])
        score = self._read(call_id, "call.json").get("live_score") or {}
        llm = self._chat_factory()
        try:
            result = drills_mod.debrief(drill, transcript, score, pack, llm)
        except Exception as e:  # noqa: BLE001 - the rules still debrief
            result = drills_mod.debrief(drill, transcript, score, pack, None)
            result["model_error"] = f"{type(e).__name__}: {e}"
        with self.drills.lock:
            drill = self.drills.get(drill_id)
            drill["attempts"][-1].update(status="done", verdict=result["verdict"], debrief=result)
            self.drills.save(drill)
        self.bus.publish("calls", "drill", drills_mod.summary(drill))
        if drill.get("origin") != "playground":
            self.run_coach(drill["person_id"])
        return drill

    def request_debrief(self, drill_id: str) -> dict[str, Any]:
        drill = self.drills.get(drill_id)
        if not drill["attempts"]:
            raise ValueError("no attempt to debrief yet")
        self._agents.put(("debrief", drill_id))
        return drill

    # -- Playground objection drills -------------------------------------------------

    def heard_objections(self, pack_id: str, limit: int = 6) -> list[dict[str, Any]]:
        """Objections customers raised on the team's reviewed real calls of this pack, newest first.

        The customer's words are anonymised (the call's and the team's names, numbers, amounts); the same
        words are listed once. Each has a stable id for its drill.
        """
        out, seen = [], set()
        for c in self._real_done():
            if c.get("pack_id") != pack_id:
                continue
            report = self._report(c["id"]) or {}
            dismissed = set(((self._read(c["id"], "review.json") or {}).get("dismissed")) or [])
            segs = {x["id"]: x["text"] for x in (report.get("transcript") or {}).get("segments", [])}
            names = None
            for f in report.get("findings", []):
                if f.get("kind") != "objection" or f["id"] in dismissed:
                    continue
                if names is None:
                    names = self._real_names(c)
                quote = f["evidence"].get("quote") or " ".join(segs.get(x, "") for x in f["evidence"].get("segment_ids", []))
                says = persona_mod.anonymise(" ".join(quote.split()), names)[:240]
                key = " ".join(re.findall(r"\w+", says.casefold()))
                if not says or key in seen:
                    continue
                seen.add(key)
                out.append({"id": "h" + hashlib.sha256(f"{c['id']}|{f['id']}".encode()).hexdigest()[:12],
                            # Only the anonymised words: the finding's summary may name a price or a person.
                            "topic": (objection_cue(says) or "Concern").capitalize(), "says": says,
                            "call_id": c["id"], "finding_id": f["id"],
                            "started_at": c.get("started_at")})
                if len(out) >= limit:
                    return out
        return out

    def playground_objections(self, pack_id: str, person_id: str | None = None) -> dict[str, Any]:
        """The pack's objections and the ones heard on the team's calls, each with this rep's drill if any."""
        pack = self.pack(pack_id)
        drills = {}
        if person_id:
            for d in self.drills.list(person_id):
                if d.get("origin") == "playground" and d.get("pack_id") == pack.id:
                    drills[(d["source"].get("objection_id") or d["source"].get("heard_id"))] = drills_mod.summary(d)

        def item(base: dict[str, Any], criterion) -> dict[str, Any]:
            return {**base, "criterion": criterion.id, "criterion_title": criterion.title, "drill": drills.get(base["id"])}

        return {
            "pack": [item({"id": o.id, "topic": o.topic, "says": o.says, "context": o.context, "hint": o.hint},
                          pack.objection_criterion(o)) for o in pack.objections],
            "heard": [item(h, pack.objection_criterion()) for h in self.heard_objections(pack.id)],
        }

    def create_playground_drill(self, person_id: str, pack_id: str, objection_id: str | None = None,
                                heard_id: str | None = None, origin: str | None = None) -> dict[str, Any]:
        """A Playground drill on one objection (the pack's, or one heard on a real call), for a rep.

        One per rep and objection: practising again adds attempts to it. It never counts towards skills,
        focus or transfer, and the Coach does not see it.
        """
        person = self.people.get(person_id)
        if person is None or person["role"] != "rep":
            raise ValueError("Choose a rep to practise: a drill belongs to a rep")
        pack = self.pack(pack_id)
        if bool(objection_id) == bool(heard_id):
            raise ValueError("give one of objection_id or heard_id")
        if objection_id:
            obj = pack.objection(objection_id)
            if obj is None:
                raise KeyError(f"no objection {objection_id!r} in {pack.name}")
            source = {"pack_id": pack.id, "objection_id": obj.id}
            says, context, hint, topic = obj.says, obj.context, obj.hint, obj.topic
            criterion = pack.objection_criterion(obj)
        else:
            heard = next((h for h in self.heard_objections(pack.id, limit=50) if h["id"] == heard_id), None)
            if heard is None:
                raise KeyError(f"no objection {heard_id!r} heard on {pack.name} calls")
            source = {"pack_id": pack.id, "heard_id": heard["id"], "call_id": heard["call_id"],
                      "finding_id": heard["finding_id"]}
            says, context, hint, topic = heard["says"], None, None, heard["topic"]
            criterion = pack.objection_criterion()
        for d in self.drills.list(person_id):
            if d.get("origin") == "playground" and d.get("source") == source:
                return d
        team = self.playbook.approved(pack.id, criterion.id)
        best = (playbook_mod.match(team, says, k=1) or [None])[0]
        drill = {
            "id": self.drills.new_id(), "person_id": person_id, "pack_id": pack.id, "origin": "playground",
            "criterion": criterion.id, "title": f"{topic}: {says}"[:120], "source": source,
            "moment": {"customer_said": says, "quote": None, "why": None, "context": context, "hint": hint,
                       "better_line": {"text": best["answer"], "by": f"playbook:{best['id']}"} if best else None},
            "persona": None, "difficulty": 1, "status": "preparing", "created_at": _now(),
            "created_by": "person", "attempts": [],
        }
        self.drills.save(drill)
        self._agents.put(("persona", drill["id"]))
        self.bus.publish("calls", "drill", drills_mod.summary(drill), origin=origin)
        return drill

    def _prepare_objection_drill(self, drill: dict[str, Any]) -> dict[str, Any]:
        pack = self.pack(drill["pack_id"])
        heard = "heard_id" in drill["source"]
        call = (self._read(drill["source"]["call_id"], "call.json") or {}) if heard else {}
        names = self._real_names(call) if call.get("id") else [p["name"] for p in self.people.list()]
        m = drill["moment"]
        llm = self._chat_factory()
        args = (m["customer_said"], m.get("context"), pack, drill["criterion"], names, drill["id"])
        try:
            persona = persona_mod.objection_persona(*args, llm=llm, difficulty=drill["difficulty"], anonymised=heard)
        except Exception as e:  # noqa: BLE001 - a model error leaves the rules persona; the drill still works
            persona = persona_mod.objection_persona(*args, llm=None, difficulty=drill["difficulty"], anonymised=heard)
            persona["model_error"] = f"{type(e).__name__}: {e}"
        with self.drills.lock:
            drill = self.drills.get(drill["id"])
            drill.update(persona=persona, status="ready")
            self.drills.save(drill)
        self.bus.publish("calls", "drill", drills_mod.summary(drill))
        return drill

    def set_focus(self, person_id: str, pack_id: str | None, criterion: str | None, by: str | None,
                  origin: str | None = None) -> dict[str, Any]:
        """A person sets (or clears) a rep's focus. A manager's focus is theirs: the Coach won't replace it."""
        person = self.people.get(person_id)
        if person is None:
            raise KeyError(f"no person {person_id!r}")
        setter = self.people.get(by) if by else None
        if by and setter is None:
            raise KeyError(f"no person {by!r}")
        focus = None
        if criterion:
            pack = self.pack(str(pack_id or ""))
            crit = pack.criterion(criterion)
            if crit is None:
                raise ValueError(f"{criterion!r} is not a skill in {pack.name}")
            state = self.coach_state(person_id)
            skill = next((s for s in state["skills"] if s["key"] == f"{pack.id}/{crit.id}"), None)
            focus = {"pack_id": pack.id, "criterion": crit.id, "title": crit.title, "since": _now(),
                     "set_by": f"person:{setter['id']}" if setter else "person", "set_by_role": setter["role"] if setter else None,
                     "status": "active", "reason": "Chosen by " + (setter["name"] if setter else "a person") + ".",
                     "moments": skill["moments"][:3] if skill else []}
        with self.coach_store.lock:
            data = self.coach_store.get(person_id)
            if data.get("focus"):
                data.setdefault("history", []).append({**data["focus"], "status": "replaced" if focus else "cleared",
                                                       "ended": _now()})
            data["focus"] = focus
            data.setdefault("actions", []).append({"at": _now(), "by": f"person:{by}" if by else "person",
                                                   "action": "set_focus" if focus else "clear_focus",
                                                   "args": {"pack_id": pack_id, "criterion": criterion}, "applied": True})
            self.coach_store.save(person_id, data)
        self.bus.publish("calls", "coach", {"person_id": person_id, "focus": focus}, origin=origin)
        return self.coach_view(person_id)

    def _focus_detail(self, person_id: str, focus: dict[str, Any] | None) -> dict[str, Any] | None:
        """The focus with its latest weak moment and better line, read from the rep's calls."""
        if not focus:
            return None
        names = {c["id"]: c for c in self.list()}
        out = dict(focus)
        latest = None
        for ref in focus.get("moments") or []:
            coaching = self._coaching(ref["call_id"]) if ref.get("call_id") in names else None
            m = next((x for x in (coaching or {}).get("moments", []) if x["id"] == ref["moment_id"]), None)
            if m:
                c = names[ref["call_id"]]
                latest = {"call_id": c["id"], "customer_name": c.get("customer_name"), "started_at": c.get("started_at"),
                          "moment_id": m["id"], "quote": m["evidence"]["quote"], "why": m.get("why"),
                          "customer_said": m.get("customer_said"), "segment_ids": m["evidence"]["segment_ids"],
                          "better_line": m.get("better_line")}
                break
        out["latest"] = latest
        setter = (focus.get("set_by") or "").removeprefix("person:")
        person = self.people.get(setter) if setter and not setter.startswith("coach") else None
        out["set_by_name"] = person["name"] if person else ("Sel's coach" if str(focus.get("set_by", "")).startswith("coach")
                                                           else None)
        return out

    def coach_view(self, person_id: str) -> dict[str, Any]:
        state = self.coach_state(person_id)
        data = self.coach_store.get(person_id)
        return {"person": self.people.get(person_id), "focus": self._focus_detail(person_id, data.get("focus")),
                "skills": state["skills"], "calls_seen": state["calls_seen"],
                "history": data.get("history", [])[-10:], "actions": data.get("actions", [])[-20:],
                "transfer": data.get("transfer", [])[-10:], "escalations": data.get("escalations", []),
                "drills": self.list_drills(person_id)}

    # -- review ---------------------------------------------------------------

    def _rebuild(self, call_id: str) -> dict[str, Any]:
        """Re-run everything after the model: gate, dates, drafts, then the person's draft edits."""
        call = self._read(call_id, "call.json")
        pack = self.pack(call["pack_id"])
        transcript = Transcript.from_dict(self._read(call_id, "transcript.json"))
        proposed = Analysis.from_dict(self._read(call_id, "analysis.json", {}))
        review = self._read(call_id, "review.json", EMPTY_REVIEW)
        report = build_report(transcript, proposed, pack, call.get("report_meta"))
        drafts = []
        for d in report.drafts:
            override = review["drafts"].get(d.id)
            if override:
                d = apply_edits(d, override.get("fields", {}), transcript)
                if override.get("approved") and d.status == "ready_for_approval":
                    d = approve(d)
            drafts.append(d)
        report.drafts = drafts
        data = report.to_dict()
        data["score"] = score_report(report, pack)
        self._write(call_id, "report.json", data)
        return data

    def view(self, call_id: str) -> dict[str, Any]:
        self._dir(call_id)
        call = self._read(call_id, "call.json")
        return {
            **{k: v for k, v in call.items() if k != "traceback"},
            "audio_url": f"/api/calls/{call_id}/audio" if call.get("audio_file") else None,
            "report": self._read(call_id, "report.json"),
            "review": self._read(call_id, "review.json"),
            "coaching": self._read(call_id, "coaching.json"),
        }

    def list(self) -> list[dict[str, Any]]:
        calls, seen = [], set()
        for d in sorted(self.root.iterdir(), reverse=True):
            try:
                mtime = (d / "call.json").stat().st_mtime_ns
            except OSError:
                continue
            seen.add(d.name)
            cached = self._summaries.get(d.name)
            if cached is None or cached[0] != mtime:
                c = read_json(d / "call.json")
                cached = (mtime, {k: c.get(k) for k in SUMMARY_FIELDS})
                self._summaries[d.name] = cached
            calls.append(cached[1])
        for gone in set(self._summaries) - seen:
            self._summaries.pop(gone, None)
        return calls

    def edit_segment(self, call_id: str, segment_id: str, text: str) -> dict[str, Any]:
        with self._lock:
            self._dir(call_id)
            transcript = Transcript.from_dict(self._read(call_id, "transcript.json"))
            seg = transcript.segment(segment_id)
            if seg is None:
                raise KeyError(segment_id)
            review = self._read(call_id, "review.json")
            review["transcript_edits"].append({"segment_id": segment_id, "old": seg.text, "new": text, "at": _now()})
            segments = [replace(s, text=text) if s.id == segment_id else s for s in transcript.segments]
            self._write(call_id, "transcript.json", asdict(replace(transcript, segments=segments)))
            self._write(call_id, "review.json", review)
            before = {r["item_id"] for r in (self._read(call_id, "report.json") or {}).get("rejected", [])}
            data = self._rebuild(call_id)
            coaching = self._read(call_id, "coaching.json")
            if coaching:  # quotes are the transcript's current words
                self._write(call_id, "coaching.json",
                            coaching_mod.refresh_quotes(coaching, replace(transcript, segments=segments)))
            after = {r["item_id"] for r in data["rejected"]}
            return {"report": data, "newly_rejected": sorted(after - before), "restored": sorted(before - after)}

    def edit_draft(self, call_id: str, draft_id: str, fields: dict[str, str]) -> dict[str, Any]:
        with self._lock:
            self._dir(call_id)
            review = self._read(call_id, "review.json")
            prior = review["drafts"].get(draft_id, {}).get("fields", {})
            review["drafts"][draft_id] = {"fields": {**prior, **fields}, "approved": False, "edited_at": _now()}
            self._write(call_id, "review.json", review)
            return self._draft(self._rebuild(call_id), draft_id)

    def approve_draft(self, call_id: str, draft_id: str) -> dict[str, Any]:
        with self._lock:
            self._dir(call_id)
            data = self._read(call_id, "report.json")
            draft = self._draft(data, draft_id)
            if draft["status"] == "needs_input":
                raise ValueError(f"fill in {', '.join(draft['missing'])} before approving")
            review = self._read(call_id, "review.json")
            entry = review["drafts"].setdefault(draft_id, {"fields": {}})
            entry.update(approved=True, approved_at=_now())
            self._write(call_id, "review.json", review)
            return self._draft(self._rebuild(call_id), draft_id)

    def draft(self, call_id: str, draft_id: str) -> dict[str, Any]:
        self._dir(call_id)
        return self._draft(self._read(call_id, "report.json"), draft_id)

    @staticmethod
    def _draft(report: dict[str, Any], draft_id: str) -> dict[str, Any]:
        for d in report.get("drafts", []):
            if d["id"] == draft_id:
                return d
        raise KeyError(draft_id)

    def set_dismissed(self, call_id: str, item_id: str, dismissed: bool) -> dict[str, Any]:
        with self._lock:
            self._dir(call_id)
            review = self._read(call_id, "review.json")
            items = set(review["dismissed"])
            (items.add if dismissed else items.discard)(item_id)
            review["dismissed"] = sorted(items)
            self._write(call_id, "review.json", review)
            return review

    # -- people and Today -------------------------------------------------------

    def _rep(self, rep_id: Any, rep_name: Any) -> tuple[str | None, str | None]:
        """Validate a rep id; the person's name stands in when no rep name was typed."""
        name = " ".join(str(rep_name or "").split())[:80] or None
        if not rep_id:
            return None, name
        person = self.people.get(str(rep_id))
        if person is None:
            raise KeyError(f"no person {rep_id!r}")
        return person["id"], name or person["name"]

    def add_person(self, name: Any, role: Any = "rep", origin: str | None = None) -> dict[str, Any]:
        person = self.people.add(name, role)
        self.bus.publish("calls", "people", self.people.list(), origin=origin)
        return person

    def update_person(self, person_id: str, name: Any = None, role: Any = None,
                      origin: str | None = None) -> dict[str, Any]:
        person = self.people.update(person_id, name, role)
        self.bus.publish("calls", "people", self.people.list(), origin=origin)
        return person

    def set_rep(self, call_id: str, rep_id: str | None, origin: str | None = None) -> dict[str, Any]:
        """Say whose call this is (or nobody's). The transcript's speaker names are left as they are."""
        with self._lock:
            self._dir(call_id)
            person = self.people.get(rep_id) if rep_id else None
            if rep_id and person is None:
                raise KeyError(f"no person {rep_id!r}")
            call = self._read(call_id, "call.json")
            call.update(rep_id=person["id"] if person else None, updated_at=_now())
            if person and not call.get("rep_name"):
                call["rep_name"] = person["name"]
            self._write(call_id, "call.json", call)
        self._publish(call, origin)
        if person and call.get("status") == "done":
            self._agents.put(("transfer", call_id))  # a focus set before this call: did the skill show up?
            self.request_coach(person["id"])  # this call's moments now count towards their coaching
        return {k: call.get(k) for k in SUMMARY_FIELDS}

    def _report(self, call_id: str) -> dict[str, Any] | None:
        path = self.root / call_id / "report.json"
        try:
            mtime = path.stat().st_mtime_ns
        except OSError:
            return None
        cached = self._reports.get(call_id)
        if cached is None or cached[0] != mtime:
            cached = (mtime, read_json(path))
            self._reports[call_id] = cached
        return cached[1]

    def today(self, person_id: str | None) -> dict[str, Any]:
        """What needs this person's attention: their calls for a rep, the whole team's for a manager.

        Everything is read from the calls' own files (status, drafts, scorecard); nothing is guessed.
        """
        person = self.people.get(person_id)
        if person_id and person is None:
            raise KeyError(f"no person {person_id!r}")
        manager = bool(person and person["role"] == "manager")
        names = {p["id"]: p["name"] for p in self.people.list()}
        calls = self.list()
        mine = calls if manager else (
            [c for c in calls if c.get("rep_id") == person_id] if person else calls)

        def ref(c: dict[str, Any]) -> dict[str, Any]:
            return {"id": c["id"], "pack_id": c.get("pack_id"), "customer_name": c.get("customer_name"),
                    "rep_id": c.get("rep_id"), "rep_name": names.get(c.get("rep_id")) or c.get("rep_name"),
                    "started_at": c.get("started_at"), "source": c.get("source")}

        in_progress = [{**ref(c), "status": c["status"], "stage": c.get("stage"), "progress": c.get("progress")}
                       for c in mine if c.get("status") in ("queued", "running")]
        failed = [{**ref(c), "status": "error"} for c in mine if c.get("status") == "error"]
        follow_ups, recent, skills = [], [], {}
        done = [c for c in mine if c.get("status") == "done"]
        done.sort(key=lambda c: c.get("started_at") or "", reverse=True)
        real_seen = 0
        for i, c in enumerate(done):
            report = self._report(c["id"])
            if not report:
                continue
            for d in report.get("drafts", []):
                if d["status"] in ("needs_input", "ready_for_approval"):
                    follow_ups.append({"call": ref(c), "draft_id": d["id"], "kind": d["kind"], "status": d["status"],
                                       "subject": d["fields"].get("subject") or d["fields"].get("title") or "",
                                       "missing": d.get("missing", [])})
            score = report.get("score") or {}
            if i < TODAY_RECENT:
                recent.append({**ref(c), "overall": score.get("overall")})
            # Skills count real calls only: practice is where a rep tries things out.
            real = call_kind(c) == "real"
            real_seen += real
            if real and real_seen <= SKILL_WINDOW:
                for cr in score.get("criteria", []):
                    if cr.get("status") not in ("met", "partial", "missed"):
                        continue
                    k = (c["pack_id"], cr["id"])
                    s = skills.setdefault(k, {"pack_id": c["pack_id"], "criterion_id": cr["id"], "title": cr["title"],
                                              "suggestion": cr.get("suggestion"), "scored": 0, "short": 0, "calls": []})
                    s["scored"] += 1
                    if cr["status"] != "met":
                        s["short"] += 1
                        s["calls"].append({**ref(c), "status": cr["status"], "segment": cr.get("segment"),
                                           "detail": cr.get("detail")})
        follow_ups.sort(key=lambda f: (f["status"] != "needs_input", f["call"]["started_at"] or ""))
        to_work_on = sorted((s for s in skills.values() if s["short"]), key=lambda s: (-s["short"] / s["scored"], -s["short"]))
        out: dict[str, Any] = {
            "person": person, "scope": "team" if manager else "mine" if person else "all",
            "follow_ups": follow_ups, "in_progress": in_progress, "failed": failed,
            "recent": recent, "skills": to_work_on, "counts": {
                "calls": len(mine), "needs_input": sum(f["status"] == "needs_input" for f in follow_ups),
                "ready_for_approval": sum(f["status"] == "ready_for_approval" for f in follow_ups)},
            "focus": self._focus_detail(person["id"], self.coach_store.get(person["id"]).get("focus"))
            if person and not manager else None,
            "drills": self.list_drills(person["id"]) if person and not manager else [],
            "transfer": self.coach_store.get(person["id"]).get("transfer", [])[-3:] if person and not manager else [],
        }
        if manager:
            out["team"] = self._team_roster()
            out["unassigned"] = [ref(c) for c in mine if not c.get("rep_id")][:TODAY_RECENT]
            out["escalations"] = [{**e, "person_id": p["id"], "person_name": p["name"]}
                                  for p in self.people.list() if p["role"] == "rep"
                                  for e in self.coach_store.get(p["id"]).get("escalations", []) if e.get("status") == "open"]
        return out

    # -- leads and Prep ----------------------------------------------------------

    def _queue_agent(self, kind: str, arg: str) -> bool:
        """Queue agent work once: a second request while it is waiting or running is the same work."""
        with self._lock:
            if (kind, arg) in self._pending:
                return False
            self._pending.add((kind, arg))
        self._agents.put((kind, arg))
        return True

    def _lead_out(self, lead: dict[str, Any]) -> dict[str, Any]:
        rep = self.people.get(lead["rep_id"]) if lead.get("rep_id") else None
        return {**lead, "rep_name": rep["name"] if rep else None}

    def list_leads(self, rep_id: str | None = None) -> list[dict[str, Any]]:
        return [self._lead_out(lead) for lead in self.leads.list(rep_id)]

    def add_lead(self, name: Any, phone: Any = None, rep_id: Any = None, notes: Any = None,
                 origin: str | None = None) -> dict[str, Any]:
        rep_id, _ = self._rep(rep_id, None)
        lead = self.leads.add(name, phone, rep_id, notes)
        self.bus.publish("calls", "lead", {"id": lead["id"]}, origin=origin)
        return self._lead_out(lead)

    def update_lead(self, lead_id: str, changes: dict[str, Any], origin: str | None = None) -> dict[str, Any]:
        allowed = {k: v for k, v in changes.items() if k in ("name", "phone", "notes", "rep_id")}
        if allowed.get("rep_id"):
            allowed["rep_id"], _ = self._rep(allowed["rep_id"], None)
        self.leads.update(lead_id, **allowed)
        self.bus.publish("calls", "lead", {"id": lead_id}, origin=origin)
        return self.lead_view(lead_id)

    def lead_view(self, lead_id: str) -> dict[str, Any]:
        """The lead with a summary of each of its calls, newest first."""
        lead = self.leads.get(lead_id)
        ids = set(lead.get("call_ids", []))
        calls = sorted((c for c in self.list() if c["id"] in ids), key=lambda c: c.get("started_at") or "", reverse=True)
        names = {p["id"]: p["name"] for p in self.people.list()}
        out = []
        for c in calls:
            score = (self._report(c["id"]) or {}).get("score") or {}
            out.append({**c, "rep_name": names.get(c.get("rep_id")) or c.get("rep_name"), "overall": score.get("overall")})
        return {**self._lead_out(lead), "calls": out}

    def set_lead(self, call_id: str, lead_id: str | None, origin: str | None = None) -> dict[str, Any]:
        """Say which lead a call was with (or none): call.json ``lead_id`` and the lead's ``call_ids``."""
        with self._lock:
            self._dir(call_id)
            if lead_id:
                self.leads.get(lead_id)
            call = self._read(call_id, "call.json")
            old = call.get("lead_id")
            call.update(lead_id=lead_id or None, updated_at=_now())
            self._write(call_id, "call.json", call)
            if old and old != lead_id:
                try:
                    self.leads.link(old, call_id, linked=False)
                except KeyError:
                    pass  # the old lead is gone
            if lead_id:
                self.leads.link(lead_id, call_id)
        self._publish(call, origin)
        for changed in {old, lead_id} - {None}:
            self.bus.publish("calls", "lead", {"id": changed}, origin=origin)
        return {k: call.get(k) for k in SUMMARY_FIELDS}

    def _prep_inputs(self, lead_id: str):
        """(lead, [(call, report, coaching)] oldest first, approved answers, the rep's focus, signature)."""
        lead = self.leads.get(lead_id)
        ids = set(lead.get("call_ids", []))
        calls = [c for c in self.list() if c["id"] in ids and c.get("status") == "done" and call_kind(c) == "real"]
        calls.sort(key=lambda c: c.get("started_at") or "")
        triples = [(c, r, self._coaching(c["id"])) for c in calls if (r := self._report(c["id"]))]
        answers = [a for pack_id in dict.fromkeys(c["pack_id"] for c, _, _ in triples)
                   for a in self.playbook.approved(pack_id)]
        rep_id = lead.get("rep_id") or next((c.get("rep_id") for c, _, _ in reversed(triples) if c.get("rep_id")), None)
        focus = self.coach_store.get(rep_id).get("focus") if rep_id else None
        focus = focus if (focus or {}).get("status") == "active" else None
        sig = json.dumps([lead.get("name"), [self.etag(c["id"]) for c, _, _ in triples], [a["id"] for a in answers],
                          (focus or {}).get("since")])
        return lead, triples, answers, focus, sig

    def prep(self, lead_id: str) -> dict[str, Any]:
        """The brief before the next call with this lead. Sel's rules answer at once; with the analysis
        model, a model brief (it picks by id and writes a checked summary) is built on the agent queue,
        announced with a ``lead`` event, and returned from then on until the lead's calls change."""
        lead, calls, answers, focus, sig = self._prep_inputs(lead_id)
        cached = self._briefs.get(("prep", lead_id))
        if cached and cached[0] == sig:
            return cached[1]
        brief = prep_mod.build_brief(lead, calls, answers, focus)
        if calls and self._analysis_llm_factory() is not None:
            self._queue_agent("prep", lead_id)
            brief["model_pending"] = True
        return brief

    def build_prep(self, lead_id: str) -> dict[str, Any]:
        lead, calls, answers, focus, sig = self._prep_inputs(lead_id)
        llm = self._analysis_llm_factory()
        try:
            brief = prep_mod.build_brief(lead, calls, answers, focus, llm)
        except Exception as e:  # noqa: BLE001 - the rules brief stands
            brief = prep_mod.build_brief(lead, calls, answers, focus)
            brief["model_error"] = f"{type(e).__name__}: {e}"
        self._briefs[("prep", lead_id)] = (sig, brief)
        self.bus.publish("calls", "lead", {"id": lead_id, "prep": True})
        return brief

    def rehearse(self, lead_id: str, by: str | None = None, origin: str | None = None) -> dict[str, Any]:
        """A drill from the lead's latest weak objection moment (newest real call first)."""
        lead = self.leads.get(lead_id)
        _, calls, _, _, _ = self._prep_inputs(lead_id)
        for call, _, coaching in reversed(calls):
            pack = self.pack(call["pack_id"])
            for m in sorted((coaching or {}).get("moments", []), key=lambda m: -(m.get("start") or 0)):
                crit = pack.criterion(m["criterion"])
                if m["kind"] == "weak" and m.get("state") != "dismissed" and crit and criterion_signal(crit) == "objections":
                    person = call.get("rep_id") or lead.get("rep_id") or by
                    return self.create_drill(call["id"], m["id"], person, by=f"person:{by}" if by else "prep",
                                             origin=origin)
        raise ValueError(f"no objection to rehearse: none of {lead['name']}'s reviewed calls has one the rep fell short on")

    # -- the team (managers) --------------------------------------------------------

    def _team_roster(self) -> list[dict[str, Any]]:
        """Each rep: real calls, average score over the last 10 reviewed, focus, open follow-ups."""
        open_by_rep: dict[str, int] = {}
        for c in self.list():
            if c.get("status") != "done" or not c.get("rep_id"):
                continue
            report = self._report(c["id"])
            if not report:
                continue
            n = sum(1 for d in report.get("drafts", []) if d["status"] in ("needs_input", "ready_for_approval"))
            if n:
                open_by_rep[c["rep_id"]] = open_by_rep.get(c["rep_id"], 0) + n
        rows = []
        for p in self.people.list():
            if p["role"] != "rep":
                continue
            theirs = self._real_done(p["id"])
            scores = [(self._report(c["id"]) or {}).get("score", {}).get("overall") for c in theirs[:SKILL_WINDOW]]
            scores = [x for x in scores if x is not None]
            focus = self.coach_store.get(p["id"]).get("focus")
            rows.append({"id": p["id"], "name": p["name"], "calls": len(theirs),
                         "average": round(sum(scores) / len(scores)) if scores else None,
                         "open_follow_ups": open_by_rep.get(p["id"], 0),
                         "focus": {"title": focus["title"], "pack_id": focus["pack_id"],
                                   "criterion": focus["criterion"], "set_by": focus.get("set_by")}
                         if focus and focus.get("status") == "active" else None})
        return rows

    def _real_done(self, rep_id: str | None = None) -> list[dict[str, Any]]:
        """Reviewed real calls, newest first; one rep's when ``rep_id`` is given."""
        calls = [c for c in self.list() if c.get("status") == "done" and call_kind(c) == "real"
                 and (rep_id is None or c.get("rep_id") == rep_id)]
        return sorted(calls, key=lambda c: c.get("started_at") or "", reverse=True)

    def _criteria_order(self) -> dict[str, tuple[int, int, dict[str, Any]]]:
        out = {}
        for i, p in enumerate(self.packs()):
            for j, c in enumerate(p.scorecard):
                out[f"{p.id}/{c.id}"] = (i, j, {"key": f"{p.id}/{c.id}", "pack_id": p.id, "criterion": c.id, "title": c.title})
        return out

    def _matrix(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """(reps, criteria): per rep and criterion, scored and fell-short counts over their last 10 real calls."""
        order = self._criteria_order()
        reps, used = [], set()
        for p in self.people.list():
            if p["role"] != "rep":
                continue
            skills: dict[str, dict[str, Any]] = {}
            calls = self._real_done(p["id"])[:SKILL_WINDOW]
            for c in calls:
                for cr in ((self._report(c["id"]) or {}).get("score") or {}).get("criteria", []):
                    if cr.get("status") not in ("met", "partial", "missed"):
                        continue
                    key = f"{c['pack_id']}/{cr['id']}"
                    s = skills.setdefault(key, {"scored": 0, "short": 0, "calls": []})
                    s["scored"] += 1
                    if cr["status"] != "met":
                        s["short"] += 1
                        s["calls"].append(c["id"])
                    used.add(key)
            focus = self.coach_store.get(p["id"]).get("focus")
            reps.append({"id": p["id"], "name": p["name"], "calls": len(calls), "skills": skills,
                         "focus": {k: focus.get(k) for k in ("title", "pack_id", "criterion", "set_by")}
                         if focus and focus.get("status") == "active" else None})
        criteria = [order[k][2] for k in sorted(used, key=lambda k: order.get(k, (99, 99))[:2]) if k in order]
        return reps, criteria

    def team_patterns(self, reps: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """What repeats across reps: skills several fell short on, and the objections customers raise.

        Objections are the reports' own findings, grouped by Sel's cue words for the customer's line.
        """
        if reps is None:
            reps, _ = self._matrix()
        order = self._criteria_order()
        skills = []
        for key in {k for r in reps for k in r["skills"]}:
            scored = [r for r in reps if r["skills"].get(key, {}).get("scored")]
            short = [r for r in scored if r["skills"][key]["short"]]
            if len(short) >= SEVERAL_REPS and key in order:
                skills.append({**order[key][2], "reps_scored": len(scored), "reps_short": len(short),
                               "reps": [{"id": r["id"], "name": r["name"]} for r in short]})
        skills.sort(key=lambda s: (-s["reps_short"], -s["reps_short"] / s["reps_scored"], s["title"]))
        names = {p["id"]: p["name"] for p in self.people.list()}
        cues: dict[str, dict[str, Any]] = {}
        for c in self._real_done():
            if not c.get("rep_id"):
                continue
            report = self._report(c["id"]) or {}
            dismissed = set(((self._read(c["id"], "review.json") or {}).get("dismissed")) or [])
            segs = {s["id"]: s["text"] for s in (report.get("transcript") or {}).get("segments", [])}
            for f in report.get("findings", []):
                if f.get("kind") != "objection" or f["id"] in dismissed:
                    continue
                quote = f["evidence"].get("quote") or " ".join(segs.get(s, "") for s in f["evidence"].get("segment_ids", []))
                cue = objection_cue(quote) or "other concerns"
                g = cues.setdefault(cue, {"cue": cue, "count": 0, "reps": set(), "examples": []})
                g["count"] += 1
                g["reps"].add(c["rep_id"])
                if len(g["examples"]) < 3:
                    g["examples"].append({"call_id": c["id"], "rep_id": c["rep_id"], "rep_name": names.get(c["rep_id"]),
                                          "customer_name": c.get("customer_name"), "quote": quote,
                                          "segment_ids": f["evidence"].get("segment_ids", [])})
        objections = sorted(({**g, "reps": len(g["reps"])} for g in cues.values()),
                            key=lambda g: (-g["count"], g["cue"]))
        return {"skills": skills, "objections": objections}

    def _open_escalations(self) -> list[dict[str, Any]]:
        return [{**e, "person_id": p["id"], "person_name": p["name"]}
                for p in self.people.list() if p["role"] == "rep"
                for e in self.coach_store.get(p["id"]).get("escalations", []) if e.get("status") == "open"]

    def team(self) -> dict[str, Any]:
        """The manager's view: the reps × skills matrix, patterns, open escalations and the review queue."""
        reps, criteria = self._matrix()
        proposals = [{**a, "pack_id": p.id} for p in self.packs()
                     for a in self.playbook.get(p.id)["answers"] if a["status"] == "proposed"]
        names = {p["id"]: p["name"] for p in self.people.list()}
        unassigned = [{"id": c["id"], "pack_id": c.get("pack_id"), "customer_name": c.get("customer_name"),
                       "rep_name": names.get(c.get("rep_id")) or c.get("rep_name"), "started_at": c.get("started_at")}
                      for c in self._real_done() if not c.get("rep_id")][:TODAY_RECENT]
        return {"reps": reps, "criteria": criteria, "patterns": self.team_patterns(reps),
                "roster": self._team_roster(),
                "escalations": self._open_escalations(),
                "review": {"proposals": proposals, "unassigned": unassigned}}

    def _one_on_one_points(self, person: dict[str, Any], data: dict[str, Any], timelines: list[dict[str, Any]],
                           drills: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Talking points for a 1:1, every one read from the rep's files."""
        points = []
        focus = data.get("focus") if (data.get("focus") or {}).get("status") == "active" else None
        if focus:
            points.append({"kind": "focus", "icon": "target", "title": f"Focus: {focus['title']}",
                           "detail": focus.get("reason")})
            practice = [d for d in drills if d["criterion"] == focus["criterion"] and d["pack_id"] == focus["pack_id"]]
            tries = sum(d["attempts"] for d in practice)
            if practice:
                passes = sum(d["passes"] for d in practice)
                points.append({"kind": "practice", "icon": "target", "drill_id": practice[0]["id"],
                               "title": f"Practised it {tries} time{'s' if tries != 1 else ''}, passed {passes}"
                               if tries else "A drill is ready but not tried yet",
                               "detail": practice[0].get("title")})
            checks = [t for t in data.get("transfer", []) if t.get("criterion") == focus["criterion"]
                      and (t.get("at") or "") >= (focus.get("since") or "")]
            if checks:
                t = checks[-1]
                verdict = {"pass": "showed up", "fail": "did not show up yet", "not_applicable": "did not come up",
                           "unsure": "was unclear"}.get(t["verdict"], t["verdict"])
                points.append({"kind": "transfer", "icon": "phone", "call_id": t["call_id"],
                               "title": f"On the latest real call, the skill {verdict}", "detail": t.get("why")})
        for h in data.get("history", [])[-3:]:
            if h.get("status") == "passed":
                points.append({"kind": "closed", "icon": "check", "title": f"Done: {h['title']}",
                               "detail": (h.get("evidence") or {}).get("quote"),
                               "call_id": (h.get("evidence") or {}).get("call_id")})
        weakest = max(timelines, key=lambda t: (t["short"], -t["met"]), default=None)
        if weakest and weakest["short"] and (not focus or weakest["criterion"] != focus["criterion"]):
            last = next((p for p in reversed(weakest["points"]) if p["status"] != "met"), None)
            points.append({"kind": "short", "icon": "alert", "call_id": last["call_id"] if last else None,
                           "title": f"Fell short on {weakest['title']} in {weakest['short']} of {len(weakest['points'])} calls",
                           "detail": (last.get("moment") or {}).get("quote") if last else None})
        strong = [t for t in timelines if t["met"] and not t["short"] and len(t["points"]) >= 2]
        if strong:
            points.append({"kind": "strong", "icon": "check", "title": f"Doing well: {strong[0]['title']}",
                           "detail": f"Met on all {len(strong[0]['points'])} reviewed calls."})
        for e in data.get("escalations", []):
            if e.get("status") == "open":
                points.append({"kind": "escalation", "icon": "alert", "title": "Open escalation", "detail": e.get("note"),
                               "call_id": e.get("call_id"), "drill_id": e.get("drill_id")})
        return points

    def team_member(self, person_id: str) -> dict[str, Any]:
        """One rep for a manager: focus, skill timelines (oldest call first), transfer checks, drills, 1:1 brief."""
        person = self.people.get(person_id)
        if person is None:
            raise KeyError(f"no person {person_id!r}")
        data = self.coach_store.get(person_id)
        calls = list(reversed(self._real_done(person_id)[:SKILL_WINDOW]))
        order = self._criteria_order()
        lines: dict[str, dict[str, Any]] = {}
        for c in calls:
            coaching = self._coaching(c["id"]) or {}
            for cr in ((self._report(c["id"]) or {}).get("score") or {}).get("criteria", []):
                if cr.get("status") not in ("met", "partial", "missed"):
                    continue
                key = f"{c['pack_id']}/{cr['id']}"
                want = "strong" if cr["status"] == "met" else "weak"
                m = next((m for m in coaching.get("moments", []) if m["criterion"] == cr["id"] and m["kind"] == want
                          and m.get("state") != "dismissed"), None)
                tl = lines.setdefault(key, {**(order.get(key, (0, 0, {}))[2] or {"key": key, "pack_id": c["pack_id"],
                                                                                   "criterion": cr["id"]}),
                                            "title": cr["title"], "met": 0, "short": 0, "points": []})
                tl["met" if cr["status"] == "met" else "short"] += 1
                tl["points"].append({"call_id": c["id"], "customer_name": c.get("customer_name"),
                                     "started_at": c.get("started_at"), "status": cr["status"],
                                     "moment": {"id": m["id"], "kind": m["kind"], "quote": m["evidence"]["quote"],
                                                "why": m.get("why")} if m else None})
        timelines = sorted(lines.values(), key=lambda t: order.get(t["key"], (99, 99))[:2])
        drills = self.list_drills(person_id)
        points = self._one_on_one_points(person, data, timelines, drills)
        sig = json.dumps(points, sort_keys=True)
        cached = self._briefs.get(("one_on_one", person_id))
        fresh = bool(cached and cached[0] == sig)
        summary = cached[1].get("summary") if fresh else None
        if not fresh and points and self._chat_factory() is not None:
            self._queue_agent("one_on_one", person_id)
        pending = ("one_on_one", person_id) in self._pending
        return {"person": person, "focus": self._focus_detail(person_id, data.get("focus")), "timelines": timelines,
                "transfer": data.get("transfer", [])[-10:], "drills": drills, "history": data.get("history", [])[-10:],
                "escalations": data.get("escalations", []),
                "brief": {"points": points, "summary": summary, "model_pending": pending}}

    def build_one_on_one(self, person_id: str) -> dict[str, Any] | None:
        """A two-sentence summary of the 1:1 points from the chat model; numbers must come from the points."""
        view = self.team_member(person_id)
        points = view["brief"]["points"]
        sig = json.dumps(points, sort_keys=True)
        llm = self._chat_factory()
        summary = None
        if llm is not None and points:
            listed = "\n".join(f"- {p['title']}" + (f": {p['detail']}" if p.get("detail") else "") for p in points)
            schema = {"type": "object", "additionalProperties": False, "required": ["summary"],
                      "properties": {"summary": {"type": "string", "maxLength": 300}}}
            try:
                out = llm.complete_json(ONE_ON_ONE_SYSTEM, f"Rep: {view['person']['name']}\n\n{listed}", schema,
                                        "one-on-one", max_tokens=160)
                text = " ".join(str(out.get("summary") or "").split())
                # Generated text is checked: a number the points don't contain is made up.
                if text and numbers(text) <= numbers(listed):
                    summary = {"text": text, "by": f"model:{llm.name}"}
            except Exception:  # noqa: BLE001 - the points stand on their own
                traceback.print_exc()
        self._briefs[("one_on_one", person_id)] = (sig, {"summary": summary})
        self.bus.publish("calls", "coach", {"person_id": person_id, "brief": True})
        return summary

    def resolve_escalation(self, person_id: str, key: str, status: str, by: str | None,
                           origin: str | None = None) -> dict[str, Any]:
        """A manager marks an escalation handled. Only a manager may (no login: ``by`` is who is viewing)."""
        if status not in ESCALATION_STATUSES:
            raise ValueError(f"status must be one of {', '.join(ESCALATION_STATUSES)}")
        setter = self.people.get(by) if by else None
        if setter is None or setter["role"] != "manager":
            raise PermissionError("only a manager can mark an escalation handled")
        with self.coach_store.lock:
            data = self.coach_store.get(person_id)
            e = next((e for e in data.get("escalations", []) if e["key"] == key), None)
            if e is None:
                raise KeyError(f"no escalation {key!r}")
            e.update(status=status, resolved_by=setter["id"] if status == "resolved" else None,
                     resolved_at=_now() if status == "resolved" else None)
            self.coach_store.save(person_id, data)
        self.bus.publish("calls", "coach", {"person_id": person_id, "escalation": key}, origin=origin)
        return e

    # -- the playbook overlay (team answers) ------------------------------------------

    def playbook_view(self, pack_id: str) -> dict[str, Any]:
        pack = self.pack(pack_id)
        answers = [{**a, "pack_id": pack.id} for a in self.playbook.get(pack.id)["answers"]]
        return {"pack_id": pack.id, "name": pack.name, **{
            status: [a for a in answers if a["status"] == status] for status in ("approved", "proposed", "refused", "rejected")}}

    def playbook_action(self, pack_id: str, action: str, answer_id: str | None, by: str | None,
                        origin: str | None = None) -> dict[str, Any]:
        """propose (the Librarian, on the agent queue), approve or reject (managers only)."""
        pack = self.pack(pack_id)
        person = self.people.get(by) if by else None
        if by and person is None:
            raise KeyError(f"no person {by!r}")
        if action == "propose":
            self._queue_agent("librarian", pack.id)
            return {"queued": True, "pack_id": pack.id}
        if action not in ("approve", "reject"):
            raise ValueError("action must be propose, approve or reject")
        if person is None or person["role"] != "manager":
            raise PermissionError("only a manager can approve or reject a team answer")
        with self.playbook.lock:
            data = self.playbook.get(pack.id)
            a = next((a for a in data["answers"] if a["id"] == answer_id), None)
            if a is None:
                raise KeyError(f"no answer {answer_id!r}")
            if a["status"] == "refused":
                raise ValueError("Sel refused this answer: " + "; ".join((a.get("check") or {}).get("reasons", [])))
            a.update(status="approved" if action == "approve" else "rejected", decided_by=person["id"], decided_at=_now())
            self.playbook.save(data)
        self.bus.publish("calls", "playbook", {"pack_id": pack.id, "id": a["id"], "status": a["status"]}, origin=origin)
        return {**a, "pack_id": pack.id}

    def run_librarian(self, pack_id: str) -> dict[str, Any]:
        """Propose team answers from strong moments and endorsed lines on the team's real calls."""
        pack = self.pack(pack_id)
        calls = [c for c in self._real_done() if c.get("pack_id") == pack.id and c.get("rep_id")]
        coachings = [(c, x) for c in calls if (x := self._coaching(c["id"]))]
        names = sorted({n for c, _ in coachings for n in self._real_names(c)})
        on_file = self.playbook.get(pack.id)["answers"]
        known = {(a["source"]["call_id"], a["source"]["moment_id"]) for a in on_file}
        cands = [c for c in playbook_mod.candidates(coachings, names, known, on_file) if c.get("pack_id") in (None, pack.id)]
        index = pack_index(pack)
        llm = self._chat_factory()
        error = None
        try:
            proposals, refused = playbook_mod.propose(cands, pack, index, llm)
        except Exception as e:  # noqa: BLE001 - the rules propose every candidate instead
            error = f"{type(e).__name__}: {e}"
            proposals, refused = playbook_mod.propose(cands, pack, index, None)
        with self.playbook.lock:
            data = self.playbook.get(pack.id)
            data["answers"].extend(proposals + refused)
            self.playbook.save(data)
        result = {"pack_id": pack.id, "proposed": len(proposals), "refused": len(refused), "model_error": error}
        self.bus.publish("calls", "playbook", result)
        return result

    def changed(self, call_id: str, origin: str | None) -> None:
        """Tell other tabs that a review action changed this call."""
        self.bus.publish("calls", "call-changed", {"id": call_id}, origin=origin)


ONE_ON_ONE_SYSTEM = """You help a sales manager prepare a one-to-one with a salesperson. Below are talking points from
the rep's calls, practice and checks. Write summary: two short sentences on where the rep stands and what to discuss,
using only the points. Return only JSON."""

_CHAT_HEALTH: dict[str, Any] = {"at": 0.0, "llm": None}
_ANALYSIS_HEALTH: dict[str, Any] = {"at": 0.0, "llm": None}


def default_chat_factory():
    """The fast chat model (``SEL_CHAT_LLM_*``) when its server answers (checked at most once a minute), else None."""
    import time

    from .llm import LLMError, chat_client_from_env

    if time.monotonic() - _CHAT_HEALTH["at"] > 60:
        try:
            llm = chat_client_from_env(temperature=0.3)
            _CHAT_HEALTH["llm"] = llm if llm.health().get("ok") else None
        except LLMError:
            _CHAT_HEALTH["llm"] = None
        _CHAT_HEALTH["at"] = time.monotonic()
    return _CHAT_HEALTH["llm"]


def default_analysis_llm():
    """The analysis model (``SEL_LLM_*``) when its server answers (checked at most once a minute), else None."""
    import time

    from .llm import LLMError, client_from_env

    if time.monotonic() - _ANALYSIS_HEALTH["at"] > 60:
        try:
            llm = client_from_env()
            _ANALYSIS_HEALTH["llm"] = llm if llm.health().get("ok") else None
        except LLMError:
            _ANALYSIS_HEALTH["llm"] = None
        _ANALYSIS_HEALTH["at"] = time.monotonic()
    return _ANALYSIS_HEALTH["llm"]


def default_factory(call: dict[str, Any], speakers: list[Speaker]):
    """Real models: faster-whisper for speech, and the configured LLM (or rules) for analysis.

    ``call["analyzer"]``: ``llm`` (default for uploads), ``rules`` (no model), or
    ``auto`` (default for simulated calls): the LLM when its server answers,
    otherwise the rules analyzer, named as such in the report.
    """
    from .asr import WhisperTranscriber
    from .live import RuleAnalyzer

    channels = call.get("channels", "auto")
    transcriber = WhisperTranscriber(
        speakers=speakers, rep_channel=call.get("rep_channel", 0),
        split_channels={"split": True, "mono": False}.get(channels))
    choice = call.get("analyzer", "llm")
    if choice in ("auto", "llm"):
        from .analyzer_llm import LLMAnalyzer, label_speakers
        from .llm import LLMError, client_from_env

        try:
            llm = client_from_env()
            usable = choice == "llm" or llm.health().get("ok")
        except LLMError:
            if choice == "llm":
                raise
            llm, usable = None, False
        if usable and llm is not None:
            return transcriber, LLMAnalyzer(llm), lambda t, p: label_speakers(t, p, llm)
    return transcriber, RuleAnalyzer(), None
