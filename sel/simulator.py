"""The call simulator: two open Sel sessions call each other, and Sel reviews the call as it happens.

One tab starts a call as the rep (or the customer) and another tab, on the
same computer or another one on the network, joins it. The browsers talk
directly over WebRTC; Sel's server only relays their connection setup
(offer, answer, ICE candidates) through the event stream. Then:

* **Recorded.** The rep's browser records a stereo file, rep on the left and
  the customer on the right, so speaker labels are exact (the same layout the
  upload path calls ``channels=split``).
* **Transcribed live.** Each browser streams its own microphone as 16 kHz PCM.
  ``PcmSegmenter`` cuts it into utterances at pauses and a single worker
  thread runs Whisper on them (``WhisperTranscriber.transcribe_pcm``). Without
  faster-whisper, participants type their lines; both feed the same
  transcript. (Browser captions were removed: Chrome sends that audio to its
  vendor's speech service, and Sel only uses open models.)
* **Scored live.** Every new line re-runs the rules in ``sel.live`` and the
  scorecard grade, and both tabs get the score and the next best actions.
* **Filed with next actions.** Hanging up uploads the recording; Sel files a
  call (``source: simulator``) that runs through the normal pipeline: analysis
  (the LLM if reachable, else rules), evidence gate, due dates and follow-up
  drafts waiting for approval. Nothing is ever sent.

A **drill room** (``create_drill_room``) has an AI customer instead of a
second person (``sel.ai_customer``): it opens with the persona's line when the
call starts, and every line the rep adds (typed or spoken) queues one reply on
the AI worker thread, so a slow model never blocks a request. When the AI
customer ends the call, the room ends; the rep's tab then files it as a
practice call, and ``on_drill_finished`` hands the transcript to the debrief.

Rooms live in memory only; a server restart forgets them (filed calls stay).
"""

from __future__ import annotations

import json
import math
import os
import queue
import secrets
import threading
import time
from array import array
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .models import Segment, Speaker, Transcript
from .live import LiveScorer

ROLES = ("rep", "customer")
_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
ROOM_IDLE_S = 6 * 3600
SAVED_KEEP_S = 3600
MAX_LINE_CHARS = 2000
MAX_LINES = 5000
FINALIZE_WAIT_S = 90.0
MAX_ROOMS = 200
MAX_PENDING_SPEECH = 40  # utterances queued for Whisper per room; beyond this new audio is dropped
RATE = 16000


class RoomStateError(Exception):
    """The request doesn't fit the room's state (HTTP 409)."""


@dataclass
class Participant:
    role: str
    client: str
    name: str | None
    email: str | None
    joined_at: str
    person_id: str | None = None  # who this is on the team (people.json), when the browser said
    streams: int = 0  # open event streams (tabs); online while > 0
    mic: bool = False
    ai: bool = False  # the AI customer of a drill room


@dataclass
class Room:
    code: str
    pack_id: str
    pack_name: str
    offset: str  # UTC offset of the browser that opened the room, e.g. "+05:45"
    scorer: LiveScorer
    created: float = field(default_factory=time.time)
    touched: float = field(default_factory=time.time)
    state: str = "waiting"  # waiting → ready → live → ended → saving → saved | failed
    participants: dict[str, Participant] = field(default_factory=dict)
    lines: list[dict[str, Any]] = field(default_factory=list)
    t0: float | None = None  # monotonic clock at start
    started_at: str | None = None
    duration: float | None = None
    live: dict[str, Any] | None = None
    call_id: str | None = None
    error: str | None = None
    pending_speech: int = 0
    segmenters: dict[str, PcmSegmenter] = field(default_factory=dict)
    drill_id: str | None = None
    agent: Any = None  # AICustomer in a drill room
    ai_busy: bool = False  # a reply is being written
    ai_again: bool = False  # the rep spoke again while it was
    ai_seconds: list[float] = field(default_factory=list)  # time from the rep's line to the AI's reply
    lock: threading.RLock = field(default_factory=threading.RLock)
    idle: threading.Condition = field(init=False)
    next_id: int = 1

    def __post_init__(self) -> None:
        self.idle = threading.Condition(self.lock)


class PcmSegmenter:
    """Cuts a live stream of 16-bit mono 16 kHz PCM into utterances at pauses.

    A frame (20 ms) counts as speech when its RMS clears an adaptive noise
    floor. An utterance is emitted after ``pause`` seconds of silence that
    follow at least ``min_speech`` seconds of speech, or when it reaches
    ``max_len`` seconds. Leading silence is trimmed to ``keep`` seconds so
    windows stay short. Pure Python (``array``), about 16k multiply-adds per
    second of audio.
    """

    FRAME = 320  # samples per 20 ms frame

    def __init__(self, pause: float = 0.7, min_speech: float = 0.35, max_len: float = 15.0, keep: float = 0.3,
                 min_rms: float = 250.0):
        self.pause, self.min_speech, self.max_len, self.keep, self.min_rms = pause, min_speech, max_len, keep, min_rms
        self.noise = 100.0
        self.cursor: float | None = None  # call time of the next sample
        self.rem = b""
        self._reset(0.0)

    def _reset(self, start: float) -> None:
        self.buf = bytearray()
        self.start = start
        self.speech = 0.0
        self.silence = 0.0

    def feed(self, pcm: bytes, t: float | None = None) -> list[tuple[float, bytes]]:
        out: list[tuple[float, bytes]] = []
        if t is not None and self.cursor is not None and abs(t - self.cursor) > 1.0:
            out.extend(self.flush())  # a gap (muted, dropped chunks): close the utterance
            self.rem = b""
        if self.cursor is None or (t is not None and abs(t - self.cursor) > 1.0):
            self.cursor = t or 0.0
            self._reset(self.cursor)
        data = self.rem + pcm
        usable = len(data) - len(data) % (self.FRAME * 2)
        self.rem = data[usable:]
        frame_s = self.FRAME / RATE
        for i in range(0, usable, self.FRAME * 2):
            frame = data[i:i + self.FRAME * 2]
            samples = array("h")
            samples.frombytes(frame)
            rms = math.sqrt(sum(x * x for x in samples) / len(samples))
            speaking = rms > max(self.min_rms, self.noise * 3.0)
            if not speaking:
                self.noise = 0.95 * self.noise + 0.05 * rms
            if not self.buf:
                self.start = self.cursor
            self.buf += frame
            self.cursor += frame_s
            if self.speech == 0.0 and not speaking:
                excess = len(self.buf) - int(self.keep * RATE) * 2
                if excess > 0:
                    del self.buf[:excess]
                    self.start += excess / 2 / RATE
                continue
            if speaking:
                self.speech += frame_s
                self.silence = 0.0
            else:
                self.silence += frame_s
            if self.silence >= self.pause:
                if self.speech >= self.min_speech:
                    out.append(self._emit(trim_tail=True))
                else:
                    self._reset(self.cursor)  # a click or a cough
            elif len(self.buf) / 2 / RATE >= self.max_len:
                out.append(self._emit(trim_tail=False))
        return out

    def _emit(self, trim_tail: bool) -> tuple[float, bytes]:
        end = len(self.buf)
        if trim_tail:
            end -= max(0, int((self.silence - self.keep) * RATE)) * 2
        window = (self.start, bytes(self.buf[:end]))
        self._reset(self.cursor or 0.0)
        return window

    def flush(self) -> list[tuple[float, bytes]]:
        if self.buf and self.speech >= self.min_speech:
            return [self._emit(trim_tail=True)]
        self._reset(self.cursor or 0.0)
        return []


def _offset(raw: str | None) -> str:
    s = (raw or "").strip()
    if len(s) == 6 and s[0] in "+-" and s[1:3].isdigit() and s[3] == ":" and s[4:].isdigit():
        return s
    return "+00:00"


def _tz(offset: str) -> timezone:
    sign = 1 if offset[0] == "+" else -1
    return timezone(sign * timedelta(hours=int(offset[1:3]), minutes=int(offset[4:6])))


def _clean(text: Any, limit: int) -> str | None:
    if text is None:
        return None
    s = " ".join(str(text).split())[:limit]
    return s or None


class SimulatorHub:
    def __init__(self, service, speech_factory: Callable[[], Any] | None = None,
                 speech_status: Callable[[], tuple[bool, str | None]] | None = None,
                 ice_servers: list[dict[str, Any]] | None = None, clock: Callable[[], float] = time.monotonic):
        from .asr import live_model_name, live_speech_available

        self.service = service
        self.bus = service.bus
        self.rooms: dict[str, Room] = {}
        self._lock = threading.RLock()
        self._clock = clock
        self._speech_factory = speech_factory or _default_speech_factory
        ok, reason = (speech_status or live_speech_available)()
        self.speech = {"live": ok, "model": live_model_name() if ok and speech_factory is None else None,
                       "reason": reason, "loaded": False}
        self._engine = None
        self._asr: queue.Queue[tuple[Room, str, float, bytes] | None] = queue.Queue()
        self._asr_thread: threading.Thread | None = None
        self._ai: queue.Queue[tuple[Room, float] | None] = queue.Queue()
        self._ai_thread: threading.Thread | None = None
        # Called with (room, transcript, final score, call id) after a drill room is filed.
        self.on_drill_finished: Callable[[Room, Transcript, dict[str, Any], str], None] | None = \
            getattr(service, "drill_finished", None)
        if ice_servers is None:
            try:
                ice_servers = json.loads(os.environ.get("SEL_ICE_SERVERS", "[]"))
            except json.JSONDecodeError:
                ice_servers = []
        self.ice_servers = ice_servers

    # -- lookups ----------------------------------------------------------------

    def _room(self, code: str) -> Room:
        room = self.rooms.get(str(code).lower())
        if room is None:
            raise KeyError(f"no call {code!r}; it may have ended or the server restarted")
        room.touched = time.time()
        return room

    @staticmethod
    def _who(room: Room, client: str | None) -> Participant:
        for p in room.participants.values():
            if client and secrets.compare_digest(p.client, str(client)):
                return p
        raise PermissionError("you are not part of this call")

    def role_of(self, code: str, client: str | None) -> str | None:
        try:
            return self._who(self._room(code), client).role
        except (KeyError, PermissionError):
            return None

    def _elapsed(self, room: Room) -> float:
        if room.t0 is None:
            return 0.0
        if room.duration is not None:
            return room.duration
        return max(0.0, self._clock() - room.t0)

    # -- views and events ----------------------------------------------------------

    def capabilities(self) -> dict[str, Any]:
        return {"speech": dict(self.speech), "ice_servers": self.ice_servers}

    def view(self, room: Room, you: str | None = None, lines: bool = True) -> dict[str, Any]:
        with room.lock:
            out = {
                "code": room.code, "pack_id": room.pack_id, "pack_name": room.pack_name, "state": room.state,
                "created_at": datetime.fromtimestamp(room.created, timezone.utc).isoformat(timespec="seconds"),
                "started_at": room.started_at, "elapsed": round(self._elapsed(room), 2),
                "participants": [{"role": p.role, "name": p.name, "email": p.email, "online": p.streams > 0,
                                  "mic": p.mic, "ai": p.ai, "person_id": p.person_id}
                                 for p in sorted(room.participants.values(), key=lambda p: p.role != "rep")],
                "you": you, "live": room.live, "call_id": room.call_id, "error": room.error,
                "drill_id": room.drill_id, "ai_busy": room.ai_busy,
                "pending_speech": room.pending_speech, "speech": dict(self.speech), "ice_servers": self.ice_servers,
            }
            if lines:
                out["lines"] = list(room.lines)
            return out

    def _broadcast(self, room: Room) -> None:
        self.bus.publish(f"room:{room.code}", "room", self.view(room, lines=False))

    def list_open(self) -> list[dict[str, Any]]:
        self._reap()
        out = []
        with self._lock:
            rooms = list(self.rooms.values())
        for room in rooms:
            with room.lock:
                if room.state != "waiting" or len(room.participants) != 1:
                    continue
                host = next(iter(room.participants.values()))
                out.append({"code": room.code, "pack_id": room.pack_id, "pack_name": room.pack_name,
                            "host": {"role": host.role, "name": host.name, "online": host.streams > 0},
                            "free_role": "customer" if host.role == "rep" else "rep",
                            "created_at": datetime.fromtimestamp(room.created, timezone.utc).isoformat(timespec="seconds")})
        return sorted(out, key=lambda r: r["created_at"], reverse=True)

    def _lobby_changed(self) -> None:
        self.bus.publish("rooms", "rooms", self.list_open())

    def _reap(self) -> None:
        now = time.time()
        with self._lock:
            for code, room in list(self.rooms.items()):
                idle = now - room.touched
                if idle > ROOM_IDLE_S or (room.state in ("saved", "failed") and idle > SAVED_KEEP_S):
                    del self.rooms[code]

    # -- joining -----------------------------------------------------------------

    def _person(self, person_id: Any) -> str | None:
        if not person_id:
            return None
        if self.service.people.get(str(person_id)) is None:
            raise KeyError(f"no person {person_id!r}")
        return str(person_id)

    def create(self, pack_id: str, role: str, name: str | None, email: str | None, offset: str | None,
               person_id: str | None = None) -> tuple[Room, str]:
        if role not in ROLES:
            raise ValueError("role must be 'rep' or 'customer'")
        pack = self.service.pack(pack_id)
        person_id = self._person(person_id)
        self._reap()
        with self._lock:
            if len(self.rooms) >= MAX_ROOMS:
                raise RoomStateError("too many open calls on this server; try again later")
            code = "".join(secrets.choice(_ALPHABET) for _ in range(6))
            while code in self.rooms:
                code = "".join(secrets.choice(_ALPHABET) for _ in range(6))
            room = Room(code=code, pack_id=pack.id, pack_name=pack.name, offset=_offset(offset), scorer=LiveScorer(pack))
            client = secrets.token_hex(16)
            room.participants[role] = Participant(role, client, _clean(name, 80), _clean(email, 200), _iso_now(),
                                                  person_id=person_id)
            self.rooms[code] = room
        self._lobby_changed()
        return room, client

    def create_drill_room(self, pack_id: str, agent: Any, drill_id: str, rep_name: str | None, person_id: str | None,
                          customer_name: str | None, offset: str | None, opening_objection: bool = False) -> tuple[Room, str]:
        """A practice room: the rep (this browser) and an AI customer, ready to start."""
        pack = self.service.pack(pack_id)
        person_id = self._person(person_id)
        self._reap()
        with self._lock:
            if len(self.rooms) >= MAX_ROOMS:
                raise RoomStateError("too many open calls on this server; try again later")
            code = "".join(secrets.choice(_ALPHABET) for _ in range(6))
            while code in self.rooms:
                code = "".join(secrets.choice(_ALPHABET) for _ in range(6))
            room = Room(code=code, pack_id=pack.id, pack_name=pack.name, offset=_offset(offset),
                        scorer=LiveScorer(pack, opening_objection=opening_objection),
                        state="ready", drill_id=drill_id, agent=agent)
            client = secrets.token_hex(16)
            room.participants["rep"] = Participant("rep", client, _clean(rep_name, 80), None, _iso_now(), person_id=person_id)
            room.participants["customer"] = Participant("customer", secrets.token_hex(16), _clean(customer_name, 80), None,
                                                        _iso_now(), streams=1, ai=True)
            self.rooms[code] = room
        if hasattr(agent, "warm"):
            # Load the model and read the persona now, on the AI worker, so the rep's first line
            # doesn't wait for it (the worker answers in order, so a real reply never overtakes it).
            self._ensure_ai()
            self._ai.put((room, None))
        return room, client

    def join(self, code: str, role: str | None, name: str | None, email: str | None,
             person_id: str | None = None) -> tuple[Room, str]:
        room = self._room(code)
        person_id = self._person(person_id)
        with room.lock:
            free = [r for r in ROLES if r not in room.participants]
            if not free:
                raise RoomStateError("this call already has a rep and a customer")
            if role and role not in free:
                raise RoomStateError(f"the {role} has already joined this call")
            role = role or free[0]
            client = secrets.token_hex(16)
            room.participants[role] = Participant(role, client, _clean(name, 80), _clean(email, 200), _iso_now(),
                                                  person_id=person_id)
            if room.state == "waiting" and len(room.participants) == 2:
                room.state = "ready"
            self._broadcast(room)
        self._lobby_changed()
        return room, client

    def presence(self, code: str, client: str, delta: int) -> None:
        try:
            room = self._room(code)
            with room.lock:
                p = self._who(room, client)
                p.streams = max(0, p.streams + delta)
                if p.streams == 0:
                    p.mic = False
                self._broadcast(room)
        except (KeyError, PermissionError):
            return
        self._lobby_changed()

    def set_mic(self, code: str, client: str, mic: bool) -> dict[str, Any]:
        room = self._room(code)
        with room.lock:
            self._who(room, client).mic = bool(mic)
            self._broadcast(room)
        return self.view(room, lines=False)

    def leave(self, code: str, client: str) -> None:
        room = self._room(code)
        with room.lock:
            p = self._who(room, client)
            if room.state == "live":
                self._end(room)
            if room.state in ("waiting", "ready"):
                del room.participants[p.role]
                room.state = "waiting"
                if not room.participants:
                    with self._lock:
                        self.rooms.pop(room.code, None)
            self._broadcast(room)
        self._lobby_changed()

    # -- the call ------------------------------------------------------------------

    def signal(self, code: str, client: str, data: Any) -> None:
        """Relay WebRTC setup (offer, answer, ICE candidate) to the other participant."""
        room = self._room(code)
        with room.lock:
            me = self._who(room, client)
            other = next((p for p in room.participants.values() if p.role != me.role), None)
        if other is None:
            raise RoomStateError("nobody else has joined yet")
        self.bus.publish(f"room:{room.code}", "signal", {"from": me.role, "data": data}, to=other.client)

    def start(self, code: str, client: str) -> dict[str, Any]:
        room = self._room(code)
        with room.lock:
            self._who(room, client)
            if room.state != "ready":
                raise RoomStateError("both the rep and the customer must join before the call starts"
                                     if room.state == "waiting" else f"the call is already {room.state}")
            room.t0 = self._clock()
            room.started_at = datetime.now(_tz(room.offset)).isoformat(timespec="seconds")
            room.state = "live"
            room.live = room.scorer.snapshot(self._transcript(room), now=0.0)
            self._broadcast(room)
        self._lobby_changed()
        if room.agent is not None:
            opening = room.agent.opening()
            self._add(room, "customer", 0.0, max(1.0, len(opening["say"].split()) / 2.5), opening["say"], "ai",
                      mood=opening.get("mood"))
        if self.speech["live"]:
            self._ensure_asr()
            self._asr.put((room, "", 0.0, b""))  # warm-up: load the model before the first utterance
        return self.view(room)

    def add_line(self, code: str, client: str, text: Any, start: Any = None, end: Any = None,
                 source: str = "typed") -> dict[str, Any]:
        room = self._room(code)
        text = _clean(text, MAX_LINE_CHARS)
        if not text:
            raise ValueError("text is required")
        if source != "typed":
            raise ValueError("source must be 'typed'")
        with room.lock:
            me = self._who(room, client)
            if room.state != "live":
                raise RoomStateError("lines can be added only while the call is live")
            now = self._elapsed(room)
            words = max(1, len(text.split()))
            try:
                s = float(start) if start is not None else None
                e = float(end) if end is not None else None
            except (TypeError, ValueError) as err:
                raise ValueError("start and end are seconds since the call started") from err
            if s is None or not math.isfinite(s):
                s = now
            s = min(max(0.0, s), now + 1.0)
            e = e if e is not None and math.isfinite(e) and e > s else s + max(1.0, words / 2.5)
            return self._add(room, me.role, s, e, text, source)

    def _add(self, room: Room, role: str, start: float, end: float, text: str, source: str,
             confidence: float | None = None, mood: str | None = None) -> dict[str, Any]:
        with room.lock:
            if len(room.lines) >= MAX_LINES:
                raise RoomStateError("this call has reached the line limit")
            if source in ("typed", "ai") and room.lines and start <= room.lines[-1]["start"]:
                # Typed and AI lines are said now: never before the latest line (speech can arrive late; these can't).
                end += room.lines[-1]["start"] + 0.01 - start
                start = room.lines[-1]["start"] + 0.01
            line = {"id": f"l{room.next_id}", "role": role, "speaker": "S1" if role == "rep" else "S2",
                    "start": round(start, 2), "end": round(end, 2), "text": text, "source": source,
                    "confidence": None if confidence is None else round(confidence, 3), "room": room.code}
            if mood:
                line["mood"] = mood
            room.next_id += 1
            room.lines.append(line)
            room.lines.sort(key=_order)
            room.live = room.scorer.snapshot(self._transcript(room), now=self._elapsed(room))
            topic = f"room:{room.code}"
            self.bus.publish(topic, "line", line)
            self.bus.publish(topic, "live", room.live)
            if room.agent is not None and role == "rep" and room.state == "live":
                if room.ai_busy:
                    room.ai_again = True
                else:
                    room.ai_busy = True
                    self._broadcast(room)
                    self._ensure_ai()
                    self._ai.put((room, time.monotonic()))
            return line

    def add_audio(self, code: str, client: str, pcm: bytes, t: float | None) -> int:
        """A chunk of the sender's own microphone; returns how many utterances were queued for Whisper."""
        if not self.speech["live"]:
            raise RoomStateError(self.speech["reason"] or "live speech recognition is not available")
        room = self._room(code)
        with room.lock:
            me = self._who(room, client)
            if room.state in ("saving", "saved", "failed"):
                # The microphone's last chunk (flushed at hang-up) can arrive after the call is being
                # saved. That is normal, not an error: drop it, as audio is dropped when Whisper is behind.
                return 0
            if room.state not in ("live", "ended"):
                raise RoomStateError("audio is accepted only during the call")
            seg = room.segmenters.setdefault(me.role, PcmSegmenter())
            windows = seg.feed(pcm, t)
            if room.pending_speech >= MAX_PENDING_SPEECH:
                windows = []  # Whisper can't keep up; better a gap than an ever-growing backlog
            room.pending_speech += len(windows)
        self._ensure_asr()
        for start, window in windows:
            self._asr.put((room, me.role, start, window))
        return len(windows)

    def end(self, code: str, client: str) -> dict[str, Any]:
        room = self._room(code)
        with room.lock:
            self._who(room, client)
            if room.state == "live":
                self._end(room)
                self._broadcast(room)
        return self.view(room, lines=False)

    def _end(self, room: Room) -> None:
        room.duration = self._elapsed(room)
        room.state = "ended"

    def finish(self, code: str, client: str, audio: bytes | Path | None, filename: str | None) -> dict[str, Any]:
        """File the call: wait for live speech to catch up, then hand transcript and recording to the service."""
        room = self._room(code)
        with room.lock:
            me = self._who(room, client)
            if room.state == "live":
                self._end(room)
            if room.state not in ("ended", "failed"):
                raise RoomStateError(f"the call is {room.state}" if room.state != "saved" else "this call is already saved")
            if audio is not None and me.role != "rep":
                raise PermissionError("only the rep's browser records the call")
            room.state, room.error = "saving", None
            flushed = [(role, w) for role, s in room.segmenters.items() for w in s.flush()]
            room.pending_speech += len(flushed)
            self._broadcast(room)
        for role, (start, window) in flushed:
            self._ensure_asr()
            self._asr.put((room, role, start, window))
        deadline = time.monotonic() + FINALIZE_WAIT_S
        with room.idle:
            while room.pending_speech > 0 and time.monotonic() < deadline:
                room.idle.wait(timeout=0.5)
        try:
            with room.lock:
                transcript = self._transcript(room, final=True)
                live = room.scorer.snapshot(transcript, now=room.duration or 0.0, final=True)
                room.live = {**(room.live or {}), "final_score": live["score"]}
                rep = room.participants.get("rep")
            meta = {"pack_id": room.pack_id, "room": room.code, "live_score": live["score"], "analyzer": "auto",
                    "rep_id": rep.person_id if rep else None, "kind": "practice"}
            if room.drill_id:
                # A drill is filed for its debrief; the rules analyse it so the machine stays free for the next one.
                meta.update(analyzer="rules", drill_id=room.drill_id)
            call_id = self.service.create_simulated(transcript, audio, filename, meta)
        except Exception as e:  # noqa: BLE001 - shown in both tabs
            with room.lock:
                room.state, room.error = "failed", f"{type(e).__name__}: {e}"
                self._broadcast(room)
            raise
        with room.lock:
            room.state, room.call_id = "saved", call_id
            self._broadcast(room)
        self._lobby_changed()
        if room.drill_id and self.on_drill_finished is not None:
            self.on_drill_finished(room, transcript, live["score"], call_id)
        return self.view(room, lines=False)

    def _transcript(self, room: Room, final: bool = False) -> Transcript:
        rep = room.participants.get("rep")
        cust = room.participants.get("customer")
        speakers = [Speaker("S1", "rep", rep.name if rep else None),
                    Speaker("S2", "customer", cust.name if cust else None, cust.email if cust else None)]
        lines = sorted(room.lines, key=_order)
        width = max(2, len(str(len(lines))))
        segments = [Segment(id=f"s{i + 1:0{width}d}" if final else x["id"], start=x["start"], end=x["end"],
                            speaker=x["speaker"], text=x["text"], confidence=x.get("confidence"))
                    for i, x in enumerate(lines)]
        started = room.started_at or datetime.now(_tz(room.offset)).isoformat(timespec="seconds")
        return Transcript(call_id="", audio=None, started_at=started, speakers=speakers, segments=segments)

    # -- live speech --------------------------------------------------------------

    def _ensure_asr(self) -> None:
        with self._lock:
            if self._asr_thread is None or not self._asr_thread.is_alive():
                self._asr_thread = threading.Thread(target=self._asr_loop, name="sel-live-asr", daemon=True)
                self._asr_thread.start()

    def _asr_loop(self) -> None:
        while True:
            item = self._asr.get()
            if item is None:
                return
            room, role, start, pcm = item
            try:
                if self._engine is None:
                    self._engine = self._speech_factory()
                    self.speech["loaded"] = True
                if pcm:
                    for a, b, text, conf in self._engine.transcribe_pcm(pcm):
                        self._add(room, role, start + a, start + b, text, "speech", conf)
            except RoomStateError:
                pass
            except Exception as e:  # noqa: BLE001 - reported in the room; typed lines keep working
                self.speech.update(live=False, reason=f"live speech recognition failed: {type(e).__name__}: {e}")
                self._broadcast(room)
            finally:
                if pcm:
                    with room.idle:
                        room.pending_speech = max(0, room.pending_speech - 1)
                        room.idle.notify_all()

    # -- the AI customer -------------------------------------------------------------

    def _ensure_ai(self) -> None:
        with self._lock:
            if self._ai_thread is None or not self._ai_thread.is_alive():
                self._ai_thread = threading.Thread(target=self._ai_loop, name="sel-ai-customer", daemon=True)
                self._ai_thread.start()

    def _ai_loop(self) -> None:
        while True:
            item = self._ai.get()
            if item is None:
                return
            room, asked = item
            if asked is None:
                try:
                    room.agent.warm()
                except Exception:  # noqa: BLE001 - a warm-up only saves time; the drill works without it
                    pass
                continue
            try:
                while True:
                    with room.lock:
                        room.ai_again = False
                        lines = [{"role": x["role"], "text": x["text"], "mood": x.get("mood")} for x in room.lines]
                    reply = room.agent.reply(lines)  # outside the lock: a model may take seconds
                    with room.lock:
                        if room.state != "live":
                            break
                        if room.ai_again:
                            continue  # the rep said more meanwhile: answer all of it
                        room.ai_seconds.append(round(time.monotonic() - asked, 2))
                        room.ai_busy = False  # under the same lock as the reply, so no rep line falls in between
                        now = self._elapsed(room)
                        self._add(room, "customer", now, now + max(1.0, len(reply["say"].split()) / 2.5), reply["say"],
                                  "ai", mood=reply.get("mood"))
                        if reply.get("end_call"):
                            self._end(room)
                        break
            except Exception as e:  # noqa: BLE001 - the drill goes on; the rep sees why the customer went quiet
                with room.lock:
                    room.error = f"The AI customer failed: {type(e).__name__}: {e}"
            finally:
                with room.lock:
                    room.ai_busy = False
                    self._broadcast(room)

    def close(self) -> None:
        self._asr.put(None)
        self._ai.put(None)


def _default_speech_factory():
    from .asr import WhisperTranscriber, live_model_name

    return WhisperTranscriber(speakers=[], model=live_model_name())


def _order(line: dict[str, Any]) -> tuple[float, int]:
    """Lines in call order; lines that start together keep the order they arrived in (a reply after its question)."""
    return line["start"], int(line["id"][1:])


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
