"""Calls on disk, the background job that analyses them, and review actions.

Each call is a folder under ``SEL_CALLS_DIR`` (default ``calls/``)::

    call.json            metadata and job status
    audio.<ext>          the recording, as uploaded (or recorded by the call simulator)
    live_transcript.json what the call simulator transcribed while the call was running
    transcript.json      current transcript (including a person's corrections)
    analysis.json        what the model proposed, before the evidence gate
    review.json          dismissed items, draft edits and approvals, transcript edit log
    report.json          the rendered report (rebuilt from the files above), with its scorecard

Everything stays on this machine. Jobs run one at a time, because speech and
language models compete for the same CPU.

Changes are published on an ``EventBus`` (topic ``calls``) so open tabs update
without polling. Packs and the call list are cached and re-read only when
their files change.
"""

from __future__ import annotations

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

from .drafts import apply_edits, approve
from .events import EventBus
from .models import Analysis, Speaker, Transcript
from .pack import Pack, load_pack
from .pipeline import analyze_call, build_report
from .scoring import score_report

AUDIO_TYPES = {".wav", ".mp3", ".m4a", ".ogg", ".opus", ".flac", ".webm", ".aac", ".mp4"}
SUMMARY_FIELDS = ("id", "pack_id", "source", "status", "stage", "progress", "started_at", "customer_name",
                  "original_filename", "created_at", "updated_at")
EMPTY_REVIEW = {"dismissed": [], "drafts": {}, "transcript_edits": []}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]


class CallService:
    def __init__(self, calls_dir: str | Path, packs_dir: str | Path, analysis_factory=None,
                 bus: EventBus | None = None):
        self.root = Path(calls_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.packs_dir = Path(packs_dir)
        self.bus = bus or EventBus()
        self._lock = threading.RLock()
        self._jobs: queue.Queue[str] = queue.Queue()
        self._pack_cache: tuple[tuple, list[Pack]] | None = None
        self._summaries: dict[str, tuple[int, dict[str, Any]]] = {}
        # Builds (transcriber, analyzer, speaker_labeler) for a call; injectable for tests.
        self._factory = analysis_factory or default_factory
        self._worker = threading.Thread(target=self._work, daemon=True)
        self._worker.start()

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
        return json.loads(path.read_text(encoding="utf-8"))

    def _write(self, call_id: str, name: str, value: Any) -> None:
        path = self.root / call_id / name
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    def _set_status(self, call_id: str, **changes: Any) -> None:
        with self._lock:
            call = self._read(call_id, "call.json")
            call.update(changes, updated_at=_now())
            self._write(call_id, "call.json", call)
        self._publish(call)

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
        for name in ("call.json", "report.json", "review.json"):
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
        call_id = _new_id()
        d = self.root / call_id
        d.mkdir(parents=True)
        self._store_audio(audio, d / f"audio{suffix}")
        call = {
            "id": call_id, "pack_id": pack.id, "source": "audio", "original_filename": filename,
            "audio_file": f"audio{suffix}", "started_at": meta["started_at"],
            "rep_name": meta.get("rep_name") or None, "customer_name": meta.get("customer_name") or None,
            "customer_email": meta.get("customer_email") or None,
            "channels": meta.get("channels", "auto"), "rep_channel": int(meta.get("rep_channel", 0)),
            "status": "queued", "stage": "waiting", "progress": 0.0, "error": None,
            "created_at": _now(), "updated_at": _now(),
        }
        self._write(call_id, "call.json", call)
        self._write(call_id, "review.json", EMPTY_REVIEW)
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
        call = {
            "id": call_id, "pack_id": pack.id, "source": "simulator", "original_filename": None,
            "audio_file": audio_file, "started_at": transcript.started_at,
            "rep_name": rep.name if rep else None, "customer_name": cust.name if cust else None,
            "customer_email": cust.email if cust else None,
            # The simulator records the rep on the left channel and the customer on the right.
            "channels": "split", "rep_channel": 0,
            "transcribe": "live" if transcript.segments or audio_file is None else "recording",
            "analyzer": meta.get("analyzer", "auto"), "room": meta.get("room"), "live_score": meta.get("live_score"),
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
        call = {
            "id": call_id, "pack_id": pack_id, "source": "fixture", "original_filename": None,
            "audio_file": audio_file, "started_at": transcript["started_at"],
            "status": "done", "stage": "done", "progress": 1.0, "error": None,
            "created_at": _now(), "updated_at": _now(),
            "report_meta": {"transcriber": "fixture-transcript", "analyzer": "fixture-analysis (hand-written, not model output)"},
        }
        self._write(call_id, "call.json", call)
        self._write(call_id, "transcript.json", transcript)
        self._write(call_id, "analysis.json", json.loads((example / "analysis.json").read_text(encoding="utf-8")))
        self._write(call_id, "review.json", EMPTY_REVIEW)
        self._rebuild(call_id)
        self._publish(call)
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
        shutil.rmtree(self._dir(call_id))
        self._summaries.pop(call_id, None)
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
        self._set_status(call_id, status="done", stage="done", progress=1.0)

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
                c = json.loads((d / "call.json").read_text(encoding="utf-8"))
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

    def changed(self, call_id: str, origin: str | None) -> None:
        """Tell other tabs that a review action changed this call."""
        self.bus.publish("calls", "call-changed", {"id": call_id}, origin=origin)


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
