"""Local web server: the review screen, the call simulator and their JSON API.

Binds to 127.0.0.1 by default. Recordings and reports never leave the
machine unless the configured LLM endpoint is a hosted service.
Standard library only.

Performance notes (measured numbers are in CLAUDE.md):

* HTTP/1.1 keep-alive, so a page load reuses a few connections. A request
  whose body was not read gets ``Connection: close``, so stray bytes can
  never be parsed as the next request.
* Static files are served from memory with their gzip version precomputed.
  ``index.html`` refers to them by content hash (``app.js?v=…``) and those
  URLs are cacheable forever, so a reload fetches only ``index.html`` (a 304).
* Call views, the call list and packs carry ETags computed from file
  metadata; an unchanged call answers 304 without reading or serialising it.
* Recordings stream from disk in chunks and uploads stream to disk, so a
  500 MB file never sits in memory.
* ``GET /api/events`` is one Server-Sent Events stream per tab that carries
  call updates, simulator rooms and WebRTC signalling, replacing polling.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import mimetypes
import queue
import re
import ssl
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .service import CallService
from .playground import Playground
from .simulator import RoomStateError, SimulatorHub

WEB_DIR = Path(__file__).parent / "web"
MAX_UPLOAD = 500 * 1024 * 1024
MAX_JSON = 1024 * 1024
MAX_AUDIO_CHUNK = 2 * 1024 * 1024
GZIP_MIN = 1024
COMPRESSIBLE = ("text/", "application/javascript", "application/json", "image/svg+xml")
SSE_PING_S = 15.0
HEALTH_TTL_S = 30.0
DOCTOR_TTL_S = 30.0


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class StaticFiles:
    """Web assets in memory, keyed by name, reloaded when a file changes on disk."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self._cache: dict[str, tuple[int, int, dict[str, Any]]] = {}
        self._index: tuple[tuple, dict[str, Any]] | None = None
        self._lock = threading.Lock()

    def get(self, name: str) -> dict[str, Any] | None:
        path = (self.root / name).resolve()
        if self.root not in path.parents or not path.is_file():
            return None
        st = path.stat()
        with self._lock:
            hit = self._cache.get(name)
            if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
                return hit[2]
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype == "application/javascript":
            ctype += "; charset=utf-8"
        entry = _entry(path.read_bytes(), ctype)
        with self._lock:
            self._cache[name] = (st.st_mtime_ns, st.st_size, entry)
        return entry

    def index(self) -> dict[str, Any]:
        """index.html with every ``/static/<name>`` reference pinned to that file's content hash."""
        raw = self.get("index.html")
        if raw is None:
            raise ApiError(404, "index.html is missing")
        text = raw["body"].decode("utf-8")
        names = sorted(set(re.findall(r"/static/([A-Za-z0-9_.-]+)", text)))
        versions = tuple((n, (self.get(n) or {}).get("hash", "")) for n in names)
        key = (raw["etag"], versions)
        with self._lock:
            if self._index and self._index[0] == key:
                return self._index[1]
        for name, h in versions:
            if h:
                text = text.replace(f"/static/{name}\"", f"/static/{name}?v={h}\"")
        entry = _entry(text.encode("utf-8"), raw["ctype"])
        with self._lock:
            self._index = (key, entry)
        return entry


def _entry(body: bytes, ctype: str) -> dict[str, Any]:
    digest = hashlib.sha256(body).hexdigest()
    gz = gzip.compress(body, 9, mtime=0) if ctype.startswith(COMPRESSIBLE) and len(body) >= GZIP_MIN else None
    return {"body": body, "gz": gz if gz and len(gz) < len(body) else None, "ctype": ctype,
            "hash": digest[:12], "etag": f'"{digest[:20]}"'}


def make_handler(service: CallService, health=None, quiet: bool = False, hub: SimulatorHub | None = None,
                 doctor=None):
    hub = hub or SimulatorHub(service)
    playground = Playground(service)
    if doctor is None:
        from .doctor import check

        def doctor():
            return check(probe=True)
    doctor_cache: dict[str, Any] = {"at": 0.0, "value": None}
    static_files = StaticFiles(WEB_DIR)
    health_cache: dict[str, Any] = {"at": 0.0, "value": None}
    health_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        server_version = "Sel/0.2"
        protocol_version = "HTTP/1.1"
        timeout = 300  # idle keep-alive connections are closed after five minutes

        def log_message(self, fmt: str, *args: Any) -> None:
            if not quiet and not any(p in self.path for p in ("/audio", "/api/events", "/signal")):
                super().log_message(fmt, *args)

        # -- plumbing -------------------------------------------------------

        def _accepts_gzip(self) -> bool:
            return "gzip" in (self.headers.get("Accept-Encoding") or "")

        def _send(self, status: int, body: bytes, ctype: str, extra: dict[str, str] | None = None,
                  gz: bytes | None = None, compress: bool = False) -> None:
            headers = dict(extra or {})
            headers.setdefault("Cache-Control", "no-store")
            if gz is None and compress and len(body) >= GZIP_MIN and self._accepts_gzip():
                gz = gzip.compress(body, 5, mtime=0)
            if gz is not None and self._accepts_gzip() and status == 200:
                body = gz
                headers["Content-Encoding"] = "gzip"
                headers["Vary"] = "Accept-Encoding"
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in headers.items():
                self.send_header(k, v)
            if self._unread():
                self.send_header("Connection", "close")
                self.close_connection = True
            self.end_headers()
            if self.command != "HEAD" and status != 304:
                self.wfile.write(body)

        def _not_modified(self, etag: str, cache: str) -> bool:
            match = self.headers.get("If-None-Match")
            if match and etag in [t.strip() for t in match.split(",")]:
                self._send(304, b"", "text/plain", {"ETag": etag, "Cache-Control": cache})
                return True
            return False

        def _json(self, value: Any, status: int = 200, etag: str | None = None) -> None:
            body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
            extra = {"ETag": etag, "Cache-Control": "no-cache"} if etag else None
            self._send(status, body, "application/json; charset=utf-8", extra, compress=True)

        def _length(self) -> int:
            try:
                return max(0, int(self.headers.get("Content-Length") or 0))
            except ValueError as e:
                raise ApiError(400, "bad Content-Length") from e

        def _unread(self) -> bool:
            return self._length_or_zero() > 0 and not getattr(self, "_body_read", False)

        def _length_or_zero(self) -> int:
            try:
                return max(0, int(self.headers.get("Content-Length") or 0))
            except (ValueError, AttributeError):
                return 0

        def _body(self, limit: int = MAX_JSON) -> bytes:
            n = self._length()
            if n > limit:
                raise ApiError(413, f"request body is larger than {limit // (1024 * 1024)} MB")
            self._body_read = True
            return self.rfile.read(n) if n else b""

        def _body_to_file(self, limit: int = MAX_UPLOAD) -> Path | None:
            """Stream the request body to a temporary file in the calls folder; None when empty."""
            n = self._length()
            if n > limit:
                raise ApiError(413, "recording is larger than 500 MB")
            if n == 0:
                self._body_read = True
                return None
            path = service.incoming_path()
            left = n
            try:
                with path.open("wb") as f:
                    while left:
                        chunk = self.rfile.read(min(left, 1024 * 1024))
                        if not chunk:
                            raise ApiError(400, "upload ended early")
                        f.write(chunk)
                        left -= len(chunk)
            except BaseException:
                path.unlink(missing_ok=True)
                self.close_connection = True
                raise
            self._body_read = True
            return path

        def _json_body(self) -> dict[str, Any]:
            try:
                value = json.loads(self._body() or b"{}")
            except json.JSONDecodeError as e:
                raise ApiError(400, f"invalid JSON: {e}") from e
            if not isinstance(value, dict):
                raise ApiError(400, "expected a JSON object")
            return value

        def _tab(self) -> str | None:
            return self.headers.get("X-Sel-Tab") or None

        def _dispatch(self) -> None:
            self._body_read = False
            url = urllib.parse.urlsplit(self.path)
            query = {k: v[-1] for k, v in urllib.parse.parse_qs(url.query).items()}
            path = url.path
            try:
                for method, pattern, fn in ROUTES:
                    if method != self.command and not (method == "GET" and self.command == "HEAD"):
                        continue
                    m = re.fullmatch(pattern, path)
                    if m:
                        return fn(self, query, *m.groups())
                raise ApiError(404, f"no route for {self.command} {path}")
            except ApiError as e:
                self._json({"error": str(e)}, e.status)
            except PermissionError as e:
                self._json({"error": str(e)}, 403)
            except RoomStateError as e:
                self._json({"error": str(e)}, 409)
            except KeyError as e:
                self._json({"error": f"not found: {e.args[0] if e.args else path}"}, 404)
            except ValueError as e:
                self._json({"error": str(e)}, 400)

        do_GET = do_POST = do_PUT = do_DELETE = do_HEAD = _dispatch

        # -- static ---------------------------------------------------------

        def _asset(self, entry: dict[str, Any], immutable: bool) -> None:
            cache = "public, max-age=31536000, immutable" if immutable else "no-cache"
            if self._not_modified(entry["etag"], cache):
                return
            self._send(200, entry["body"], entry["ctype"], {"ETag": entry["etag"], "Cache-Control": cache},
                       gz=entry["gz"])

        def index(self, q):
            self._asset(static_files.index(), immutable=False)

        def static(self, q, name):
            entry = static_files.get(name)
            if entry is None:
                raise ApiError(404, "not found")
            self._asset(entry, immutable=q.get("v") == entry["hash"])

        # -- events -----------------------------------------------------------

        def _sse(self, event: str, data: Any) -> None:
            payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
            self.wfile.write(f"event: {event}\ndata: {payload}\n\n".encode())

        def events(self, q):
            code, client, tab = q.get("room"), q.get("client"), q.get("tab")
            role = hub.role_of(code, client) if code and client else None
            topics = {"calls", "rooms"} | ({f"room:{code.lower()}"} if role else set())
            sub = service.bus.subscribe(topics, client=client if role else None)
            present = False
            try:
                if role:
                    hub.presence(code, client, +1)
                    present = True
                hello = {"calls": service.list(), "people": service.people.list(),
                         "rooms": hub.list_open(), "sim": hub.capabilities(),
                         "room": hub.view(hub._room(code), you=role) if role else None}
                self.close_connection = True
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()
                self.wfile.write(b"retry: 1500\n\n")
                self._sse("hello", hello)
                while True:
                    try:
                        ev = sub.get(timeout=SSE_PING_S)
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                        continue
                    if ev is None:
                        break
                    if ev.origin and tab and ev.origin == tab:
                        continue  # this tab caused it and already has the result
                    self._sse(ev.type, ev.data)
            except OSError:
                pass  # the tab went away
            finally:
                sub.close()
                if present:
                    hub.presence(code, client, -1)

        # -- API ------------------------------------------------------------

        def health(self, q):
            with health_lock:
                if health_cache["value"] is None or time.monotonic() - health_cache["at"] > HEALTH_TTL_S:
                    health_cache["value"] = health() if health else {"llm": None, "asr": None}
                    health_cache["at"] = time.monotonic()
                value = health_cache["value"]
            self._json(value)

        def packs(self, q):
            etag = service.packs_etag()
            if self._not_modified(etag, "no-cache"):
                return
            self._json([{"id": p.id, "name": p.name, "description": p.description, "languages": p.languages,
                         "scorecard": [c.__dict__ for c in p.scorecard]} for p in service.packs()], etag=etag)

        def pack_detail(self, q, pack_id):
            pack = service.pack(pack_id)
            etag = f'{service.packs_etag()[:-1]}-{pack_id}"'
            if self._not_modified(etag, "no-cache"):
                return
            self._json({"id": pack.id, "name": pack.name, "description": pack.description,
                        "languages": pack.languages, "week_starts_on": pack.week_starts_on,
                        "scorecard": [c.__dict__ for c in pack.scorecard],
                        "facts": [f.__dict__ for f in pack.facts.values()],
                        "docs": sorted(pack.docs), "templates": sorted(pack.templates)}, etag=etag)

        def pack_source(self, q, pack_id):
            ref = q.get("ref", "")
            text = service.pack(pack_id).source_text(ref)
            if text is None:
                raise ApiError(404, f"{ref!r} is not in pack {pack_id!r}")
            etag = f'"src-{hashlib.sha256((pack_id + ref + text).encode()).hexdigest()[:20]}"'
            if self._not_modified(etag, "no-cache"):
                return
            self._json({"pack_id": pack_id, "source": ref, "text": text}, etag=etag)

        def calls(self, q):
            listed = service.list()
            etag = f'W/"list-{hashlib.sha256(json.dumps(listed).encode()).hexdigest()[:20]}"'
            if self._not_modified(etag, "no-cache"):
                return
            self._json(listed, etag=etag)

        def create_call(self, q):
            if "pack" not in q or "started_at" not in q:
                raise ApiError(400, "pack and started_at are required query parameters")
            path = self._body_to_file()
            if path is None:
                raise ApiError(400, "send the recording as the request body")
            try:
                call_id = service.create(path, q.get("filename", "recording.wav"), {
                    "pack_id": q["pack"], "started_at": q["started_at"], "rep_name": q.get("rep_name"),
                    "rep_id": q.get("rep_id"),
                    "customer_name": q.get("customer_name"), "customer_email": q.get("customer_email"),
                    "lead_id": q.get("lead_id"),
                    "channels": q.get("channels", "auto"), "rep_channel": q.get("rep_channel", "0"),
                })
            finally:
                path.unlink(missing_ok=True)
            self._json({"id": call_id}, 201)

        def set_rep(self, q, call_id):
            b = self._json_body()
            self._json(service.set_rep(call_id, b.get("rep_id") or None, self._tab()))

        def set_lead(self, q, call_id):
            b = self._json_body()
            self._json(service.set_lead(call_id, b.get("lead_id") or None, self._tab()))

        # -- leads and Prep ----------------------------------------------------------

        def leads(self, q):
            self._json(service.list_leads(q.get("rep") or None))

        def add_lead(self, q):
            b = self._json_body()
            self._json(service.add_lead(b.get("name"), b.get("phone"), b.get("rep_id") or None, b.get("notes"),
                                        self._tab()), 201)

        def lead(self, q, lead_id):
            self._json(service.lead_view(lead_id))

        def update_lead(self, q, lead_id):
            self._json(service.update_lead(lead_id, self._json_body(), self._tab()))

        def prep(self, q, lead_id):
            self._json(service.prep(lead_id))

        def rehearse(self, q, lead_id):
            b = self._json_body()
            self._json(service.rehearse(lead_id, b.get("by") or None, self._tab()), 201)

        # -- team, playbook, settings -------------------------------------------------

        def team(self, q):
            self._json(service.team())

        def team_patterns(self, q):
            self._json(service.team_patterns())

        def team_member(self, q, person_id):
            self._json(service.team_member(person_id))

        def escalation(self, q, person_id, key):
            b = self._json_body()
            key = urllib.parse.unquote(key)  # keys hold ":" and "+" (e.g. a time), sent percent-encoded
            self._json(service.resolve_escalation(person_id, key, b.get("status"), b.get("by") or None, self._tab()))

        def playbook(self, q, pack_id):
            self._json(service.playbook_view(pack_id))

        def playbook_answers(self, q, pack_id):
            b = self._json_body()
            action = b.get("action")
            out = service.playbook_action(pack_id, action, b.get("id"), b.get("by") or None, self._tab())
            self._json(out, 202 if action == "propose" else 200)

        def doctor(self, q):
            with health_lock:
                if doctor_cache["value"] is None or time.monotonic() - doctor_cache["at"] > DOCTOR_TTL_S:
                    doctor_cache["value"] = doctor()
                    doctor_cache["at"] = time.monotonic()
                value = doctor_cache["value"]
            self._json(value)

        # -- people and Today ------------------------------------------------------

        def people(self, q):
            self._json(service.people.list())

        def add_person(self, q):
            b = self._json_body()
            self._json(service.add_person(b.get("name"), b.get("role") or "rep", self._tab()), 201)

        def update_person(self, q, person_id):
            b = self._json_body()
            self._json(service.update_person(person_id, b.get("name"), b.get("role"), self._tab()))

        def today(self, q):
            self._json(service.today(q.get("person") or None))

        def call(self, q, call_id):
            etag = service.etag(call_id)
            if self._not_modified(etag, "no-cache"):
                return
            self._json(service.view(call_id), etag=etag)

        def delete_call(self, q, call_id):
            service.delete(call_id)
            self._json({"deleted": call_id})

        def retry(self, q, call_id):
            service.retry(call_id, q.get("transcribe"))
            self._json(service.view(call_id))

        def audio(self, q, call_id):
            path = service.audio_path(call_id)
            if not path.is_file():
                raise ApiError(404, "this call has no recording")
            st = path.stat()
            size = st.st_size
            etag = f'"a-{st.st_mtime_ns:x}-{size:x}"'
            ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            if path.suffix == ".webm":
                ctype = "audio/webm"
            headers = {"Accept-Ranges": "bytes", "ETag": etag, "Cache-Control": "no-cache"}
            rng = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
            start, end, status = 0, size - 1, 200
            if rng and (rng.group(1) or rng.group(2)):
                if rng.group(1):
                    start = int(rng.group(1))
                    end = min(int(rng.group(2)) if rng.group(2) else size - 1, size - 1)
                else:
                    start, end = max(0, size - int(rng.group(2))), size - 1
                if start > end:
                    self._send(416, b"", ctype, {"Content-Range": f"bytes */{size}"})
                    return
                status = 206
                headers["Content-Range"] = f"bytes {start}-{end}/{size}"
            elif self._not_modified(etag, "no-cache"):
                return
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(end - start + 1))
            for k, v in headers.items():
                self.send_header(k, v)
            self.end_headers()
            if self.command == "HEAD":
                return
            with path.open("rb") as f:
                f.seek(start)
                left = end - start + 1
                while left:
                    chunk = f.read(min(left, 256 * 1024))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)

        def edit_segment(self, q, call_id, seg_id):
            text = self._json_body().get("text")
            if not isinstance(text, str) or not text.strip():
                raise ApiError(400, "text is required")
            result = service.edit_segment(call_id, seg_id, text.strip())
            service.changed(call_id, self._tab())
            self._json(result)

        def edit_draft(self, q, call_id, draft_id):
            fields = self._json_body().get("fields")
            if not isinstance(fields, dict):
                raise ApiError(400, "fields must be an object")
            result = service.edit_draft(call_id, draft_id, {k: str(v) for k, v in fields.items()})
            service.changed(call_id, self._tab())
            self._json(result)

        def approve_draft(self, q, call_id, draft_id):
            try:
                result = service.approve_draft(call_id, draft_id)
            except ValueError as e:
                raise ApiError(409, str(e)) from e
            service.changed(call_id, self._tab())
            self._json(result)

        def ics(self, q, call_id, draft_id):
            draft = service.draft(call_id, draft_id)
            if not draft.get("ics"):
                raise ApiError(409, f"fill in {', '.join(draft['missing']) or 'the date and time'} first")
            self._send(200, draft["ics"].encode(), "text/calendar; charset=utf-8",
                       {"Content-Disposition": f'attachment; filename="{draft_id}.ics"'})

        # -- coaching -----------------------------------------------------------

        def coaching(self, q, call_id):
            data = service.coaching(call_id)
            if data is None:
                raise ApiError(404, "this call has not been coached yet")
            self._json(data)

        def moment(self, q, call_id, moment_id, action):
            self._json(service.moment_action(call_id, moment_id, action, self._json_body(), self._tab()))

        def moment_drill(self, q, call_id, moment_id):
            b = self._json_body()
            self._json(service.create_drill(call_id, moment_id, b.get("by") or None, by=f"person:{b['by']}" if b.get("by") else None,
                                            origin=self._tab()), 201)

        # -- drills --------------------------------------------------------------

        def playground_list(self, q):
            self._json(playground.list(q.get("person"), q.get("pack")))

        def playground_submit(self, q):
            self._json(playground.submit(self._json_body()), 201)

        def drills(self, q):
            self._json(service.list_drills(q.get("person") or None))

        def drill(self, q, drill_id):
            self._json(service.drill(drill_id))

        def drill_start(self, q, drill_id):
            b = self._json_body()
            room, client = service.start_drill(drill_id, hub, b.get("by") or None, b.get("offset"))
            self._json({"room": hub.view(room, you="rep"), "client": client}, 201)

        def drill_debrief(self, q, drill_id):
            self._json(service.request_debrief(drill_id), 202)

        def coach_view(self, q, person_id):
            self._json(service.coach_view(person_id))

        def set_focus(self, q, person_id):
            b = self._json_body()
            self._json(service.set_focus(person_id, b.get("pack_id"), b.get("criterion"), b.get("by") or None,
                                         self._tab()))

        def dismiss(self, q, call_id, item_id):
            review = service.set_dismissed(call_id, item_id, True)
            service.changed(call_id, self._tab())
            self._json(review)

        def restore(self, q, call_id, item_id):
            review = service.set_dismissed(call_id, item_id, False)
            service.changed(call_id, self._tab())
            self._json(review)

        # -- call simulator ------------------------------------------------------

        def sim_info(self, q):
            self._json({**hub.capabilities(), "rooms": hub.list_open()})

        def _joined(self, room, client, role, status=200):
            self._json({"room": hub.view(room, you=role), "client": client}, status)

        def sim_create(self, q):
            b = self._json_body()
            role = b.get("role") or "rep"
            room, client = hub.create(str(b.get("pack_id") or ""), role, b.get("name"), b.get("email"), b.get("offset"),
                                      person_id=b.get("person_id") or None)
            self._joined(room, client, role, 201)

        def sim_room(self, q, code):
            role = hub.role_of(code, q.get("client"))
            self._json(hub.view(hub._room(code), you=role))

        def sim_join(self, q, code):
            b = self._json_body()
            room, client = hub.join(code, b.get("role") or None, b.get("name"), b.get("email"),
                                    person_id=b.get("person_id") or None)
            self._joined(room, client, hub.role_of(code, client))

        def sim_signal(self, q, code):
            b = self._json_body()
            hub.signal(code, b.get("client"), b.get("data"))
            self._json({"ok": True})

        def sim_mic(self, q, code):
            b = self._json_body()
            self._json(hub.set_mic(code, b.get("client"), bool(b.get("mic"))))

        def sim_start(self, q, code):
            b = self._json_body()
            self._json(hub.start(code, b.get("client")))

        def sim_line(self, q, code):
            b = self._json_body()
            self._json(hub.add_line(code, b.get("client"), b.get("text"), b.get("start"), b.get("end"),
                                    b.get("source") or "typed"), 201)

        def sim_audio(self, q, code):
            pcm = self._body(MAX_AUDIO_CHUNK)
            try:
                t = float(q["t"]) if "t" in q else None
            except ValueError as e:
                raise ApiError(400, "t is seconds since the call started") from e
            self._json({"queued": hub.add_audio(code, q.get("client"), pcm, t)})

        def sim_end(self, q, code):
            b = self._json_body()
            self._json(hub.end(code, b.get("client")))

        def sim_finish(self, q, code):
            path = self._body_to_file()
            try:
                self._json(hub.finish(code, q.get("client"), path, q.get("filename")))
            finally:
                if path is not None:
                    path.unlink(missing_ok=True)

        def sim_leave(self, q, code):
            b = self._json_body()
            hub.leave(code, b.get("client"))
            self._json({"ok": True})

    C, D, I, R = r"([a-z0-9-]+)", r"([A-Za-z0-9_-]+)", r"([A-Za-z0-9_.-]+)", r"([a-z0-9]{6})"
    ROUTES = [
        ("GET", r"/", Handler.index),
        ("GET", r"/static/([A-Za-z0-9_.-]+)", Handler.static),
        ("GET", r"/api/events", Handler.events),
        ("GET", r"/api/health", Handler.health),
        ("GET", r"/api/packs", Handler.packs),
        ("GET", rf"/api/packs/{D}", Handler.pack_detail),
        ("GET", rf"/api/packs/{D}/source", Handler.pack_source),
        ("GET", r"/api/people", Handler.people),
        ("POST", r"/api/people", Handler.add_person),
        ("PUT", rf"/api/people/{D}", Handler.update_person),
        ("GET", r"/api/today", Handler.today),
        ("GET", r"/api/leads", Handler.leads),
        ("POST", r"/api/leads", Handler.add_lead),
        ("GET", rf"/api/leads/{C}", Handler.lead),
        ("PUT", rf"/api/leads/{C}", Handler.update_lead),
        ("GET", rf"/api/leads/{C}/prep", Handler.prep),
        ("POST", rf"/api/leads/{C}/rehearse", Handler.rehearse),
        ("GET", r"/api/team", Handler.team),
        ("GET", r"/api/team/patterns", Handler.team_patterns),
        ("GET", rf"/api/team/{D}", Handler.team_member),
        ("PUT", rf"/api/coach/{D}/escalations/([^/]{{1,300}})", Handler.escalation),
        ("GET", rf"/api/playbook/{D}", Handler.playbook),
        ("POST", rf"/api/playbook/{D}/answers", Handler.playbook_answers),
        ("GET", r"/api/doctor", Handler.doctor),
        ("GET", r"/api/calls", Handler.calls),
        ("POST", r"/api/calls", Handler.create_call),
        ("GET", rf"/api/calls/{C}", Handler.call),
        ("DELETE", rf"/api/calls/{C}", Handler.delete_call),
        ("POST", rf"/api/calls/{C}/retry", Handler.retry),
        ("PUT", rf"/api/calls/{C}/rep", Handler.set_rep),
        ("PUT", rf"/api/calls/{C}/lead", Handler.set_lead),
        ("GET", rf"/api/calls/{C}/audio", Handler.audio),
        ("PUT", rf"/api/calls/{C}/segments/{D}", Handler.edit_segment),
        ("PUT", rf"/api/calls/{C}/drafts/{D}", Handler.edit_draft),
        ("POST", rf"/api/calls/{C}/drafts/{D}/approve", Handler.approve_draft),
        ("GET", rf"/api/calls/{C}/drafts/{D}/ics", Handler.ics),
        ("POST", rf"/api/calls/{C}/items/{I}/dismiss", Handler.dismiss),
        ("POST", rf"/api/calls/{C}/items/{I}/restore", Handler.restore),
        ("GET", rf"/api/calls/{C}/coaching", Handler.coaching),
        ("POST", rf"/api/calls/{C}/moments/{D}/(endorse|dismiss|edit|note)", Handler.moment),
        ("POST", rf"/api/calls/{C}/moments/{D}/drill", Handler.moment_drill),
        ("GET", r"/api/playground", Handler.playground_list),
        ("POST", r"/api/playground", Handler.playground_submit),
        ("GET", r"/api/drills", Handler.drills),
        ("GET", rf"/api/drills/{C}", Handler.drill),
        ("POST", rf"/api/drills/{C}/start", Handler.drill_start),
        ("POST", rf"/api/drills/{C}/debrief", Handler.drill_debrief),
        ("GET", rf"/api/coach/{D}", Handler.coach_view),
        ("PUT", rf"/api/coach/{D}/focus", Handler.set_focus),
        ("GET", r"/api/sim", Handler.sim_info),
        ("POST", r"/api/sim/rooms", Handler.sim_create),
        ("GET", rf"/api/sim/rooms/{R}", Handler.sim_room),
        ("POST", rf"/api/sim/rooms/{R}/join", Handler.sim_join),
        ("POST", rf"/api/sim/rooms/{R}/signal", Handler.sim_signal),
        ("POST", rf"/api/sim/rooms/{R}/mic", Handler.sim_mic),
        ("POST", rf"/api/sim/rooms/{R}/start", Handler.sim_start),
        ("POST", rf"/api/sim/rooms/{R}/lines", Handler.sim_line),
        ("POST", rf"/api/sim/rooms/{R}/audio", Handler.sim_audio),
        ("POST", rf"/api/sim/rooms/{R}/end", Handler.sim_end),
        ("POST", rf"/api/sim/rooms/{R}/finish", Handler.sim_finish),
        ("POST", rf"/api/sim/rooms/{R}/leave", Handler.sim_leave),
    ]
    return Handler


def serve(service: CallService, host: str = "127.0.0.1", port: int = 8765, health=None,
          quiet: bool = False, hub: SimulatorHub | None = None, tls: tuple[str, str] | None = None,
          doctor=None) -> ThreadingHTTPServer:
    """The app server. ``tls=(certfile, keyfile)`` serves https, which browsers on other computers
    need before they allow the microphone (only localhost counts as secure over plain http)."""
    httpd = ThreadingHTTPServer((host, port), make_handler(service, health, quiet, hub, doctor))
    if tls:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(*tls)
        # Handshake on first read, in the request's own thread, not in the accept loop.
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True, do_handshake_on_connect=False)
    return httpd
