"""Local web server: the review screen and its JSON API.

Binds to 127.0.0.1 by default. Recordings and reports never leave the
machine unless the configured LLM endpoint is a hosted service.
Standard library only.
"""

from __future__ import annotations

import json
import mimetypes
import re
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .service import CallService

WEB_DIR = Path(__file__).parent / "web"
MAX_UPLOAD = 500 * 1024 * 1024


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def make_handler(service: CallService, health=None, quiet: bool = False):
    class Handler(BaseHTTPRequestHandler):
        server_version = "Sel/0.1"

        def log_message(self, fmt: str, *args: Any) -> None:
            if not quiet and "/audio" not in self.path:
                super().log_message(fmt, *args)

        # -- plumbing -------------------------------------------------------

        def _send(self, status: int, body: bytes, ctype: str, extra: dict[str, str] | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, value: Any, status: int = 200) -> None:
            self._send(status, json.dumps(value, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def _body(self) -> bytes:
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_UPLOAD:
                raise ApiError(413, "recording is larger than 500 MB")
            return self.rfile.read(n) if n else b""

        def _json_body(self) -> dict[str, Any]:
            try:
                value = json.loads(self._body() or b"{}")
            except json.JSONDecodeError as e:
                raise ApiError(400, f"invalid JSON: {e}") from e
            if not isinstance(value, dict):
                raise ApiError(400, "expected a JSON object")
            return value

        def _dispatch(self) -> None:
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
            except KeyError as e:
                self._json({"error": f"not found: {e.args[0] if e.args else path}"}, 404)
            except ValueError as e:
                self._json({"error": str(e)}, 400)

        do_GET = do_POST = do_PUT = do_DELETE = do_HEAD = _dispatch

        # -- static ---------------------------------------------------------

        def index(self, q):
            self.static(q, "index.html")

        def static(self, q, name):
            path = (WEB_DIR / name).resolve()
            if WEB_DIR.resolve() not in path.parents or not path.is_file():
                raise ApiError(404, "not found")
            ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript",):
                ctype += "; charset=utf-8"
            self._send(200, path.read_bytes(), ctype)

        # -- API ------------------------------------------------------------

        def health(self, q):
            self._json(health() if health else {"llm": None, "asr": None})

        def packs(self, q):
            self._json([{"id": p.id, "name": p.name, "description": p.description, "languages": p.languages,
                         "scorecard": [c.__dict__ for c in p.scorecard]} for p in service.packs()])

        def pack_source(self, q, pack_id):
            ref = q.get("ref", "")
            text = service.pack(pack_id).source_text(ref)
            if text is None:
                raise ApiError(404, f"{ref!r} is not in pack {pack_id!r}")
            self._json({"pack_id": pack_id, "source": ref, "text": text})

        def calls(self, q):
            self._json(service.list())

        def create_call(self, q):
            if "pack" not in q or "started_at" not in q:
                raise ApiError(400, "pack and started_at are required query parameters")
            audio = self._body()
            if not audio:
                raise ApiError(400, "send the recording as the request body")
            call_id = service.create(audio, q.get("filename", "recording.wav"), {
                "pack_id": q["pack"], "started_at": q["started_at"], "rep_name": q.get("rep_name"),
                "customer_name": q.get("customer_name"), "customer_email": q.get("customer_email"),
                "channels": q.get("channels", "auto"), "rep_channel": q.get("rep_channel", "0"),
            })
            self._json({"id": call_id}, 201)

        def call(self, q, call_id):
            self._json(service.view(call_id))

        def delete_call(self, q, call_id):
            service.delete(call_id)
            self._json({"deleted": call_id})

        def retry(self, q, call_id):
            service.retry(call_id)
            self._json(service.view(call_id))

        def audio(self, q, call_id):
            path = service.audio_path(call_id)
            if not path.is_file():
                raise ApiError(404, "this call has no recording")
            size = path.stat().st_size
            ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            rng = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
            with path.open("rb") as f:
                if rng and (rng.group(1) or rng.group(2)):
                    if rng.group(1):
                        start = int(rng.group(1))
                        end = min(int(rng.group(2)) if rng.group(2) else size - 1, size - 1)
                    else:
                        start, end = max(0, size - int(rng.group(2))), size - 1
                    if start > end:
                        self._send(416, b"", ctype, {"Content-Range": f"bytes */{size}"})
                        return
                    f.seek(start)
                    body = f.read(end - start + 1)
                    self._send(206, body, ctype, {"Accept-Ranges": "bytes", "Content-Range": f"bytes {start}-{end}/{size}"})
                else:
                    self._send(200, f.read(), ctype, {"Accept-Ranges": "bytes"})

        def edit_segment(self, q, call_id, seg_id):
            text = self._json_body().get("text")
            if not isinstance(text, str) or not text.strip():
                raise ApiError(400, "text is required")
            self._json(service.edit_segment(call_id, seg_id, text.strip()))

        def edit_draft(self, q, call_id, draft_id):
            fields = self._json_body().get("fields")
            if not isinstance(fields, dict):
                raise ApiError(400, "fields must be an object")
            self._json(service.edit_draft(call_id, draft_id, {k: str(v) for k, v in fields.items()}))

        def approve_draft(self, q, call_id, draft_id):
            try:
                self._json(service.approve_draft(call_id, draft_id))
            except ValueError as e:
                raise ApiError(409, str(e)) from e

        def ics(self, q, call_id, draft_id):
            draft = service.draft(call_id, draft_id)
            if not draft.get("ics"):
                raise ApiError(409, f"fill in {', '.join(draft['missing']) or 'the date and time'} first")
            self._send(200, draft["ics"].encode(), "text/calendar; charset=utf-8",
                       {"Content-Disposition": f'attachment; filename="{draft_id}.ics"'})

        def dismiss(self, q, call_id, item_id):
            self._json(service.set_dismissed(call_id, item_id, True))

        def restore(self, q, call_id, item_id):
            self._json(service.set_dismissed(call_id, item_id, False))

    C, D, I = r"([a-z0-9-]+)", r"([A-Za-z0-9_-]+)", r"([A-Za-z0-9_.-]+)"
    ROUTES = [
        ("GET", r"/", Handler.index),
        ("GET", r"/static/([A-Za-z0-9_.-]+)", Handler.static),
        ("GET", r"/api/health", Handler.health),
        ("GET", r"/api/packs", Handler.packs),
        ("GET", rf"/api/packs/{D}/source", Handler.pack_source),
        ("GET", r"/api/calls", Handler.calls),
        ("POST", r"/api/calls", Handler.create_call),
        ("GET", rf"/api/calls/{C}", Handler.call),
        ("DELETE", rf"/api/calls/{C}", Handler.delete_call),
        ("POST", rf"/api/calls/{C}/retry", Handler.retry),
        ("GET", rf"/api/calls/{C}/audio", Handler.audio),
        ("PUT", rf"/api/calls/{C}/segments/{D}", Handler.edit_segment),
        ("PUT", rf"/api/calls/{C}/drafts/{D}", Handler.edit_draft),
        ("POST", rf"/api/calls/{C}/drafts/{D}/approve", Handler.approve_draft),
        ("GET", rf"/api/calls/{C}/drafts/{D}/ics", Handler.ics),
        ("POST", rf"/api/calls/{C}/items/{I}/dismiss", Handler.dismiss),
        ("POST", rf"/api/calls/{C}/items/{I}/restore", Handler.restore),
    ]
    return Handler


def serve(service: CallService, host: str = "127.0.0.1", port: int = 8765, health=None,
          quiet: bool = False) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), make_handler(service, health, quiet))
