"""Leads: the thin context Prep needs. A name, a phone number, whose lead it is, notes, and its calls.

``leads/<id>/lead.json`` next to the call folders::

    {id, name, phone, rep_id, notes, call_ids, created_at, updated_at}

Leads are not a CRM: they exist so the Prep writer can read a lead's earlier
calls (their reports, never raw transcripts) before the next call. A call
points at its lead with ``lead_id`` in call.json; ``call_ids`` here mirrors it.
"""

from __future__ import annotations

import re
import threading
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .files import read_json, write_json

MAX_LEADS = 5000
MAX_NOTES = 2000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _slug(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")[:40] or "lead"


class LeadStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.lock = threading.RLock()

    def _path(self, lead_id: str) -> Path:
        if not re.fullmatch(r"[a-z0-9-]{1,64}", lead_id or ""):
            raise KeyError(lead_id)
        return self.root / lead_id / "lead.json"

    def get(self, lead_id: str) -> dict[str, Any]:
        path = self._path(lead_id)
        if not path.is_file():
            raise KeyError(f"no lead {lead_id!r}")
        return read_json(path)

    def list(self, rep_id: str | None = None) -> list[dict[str, Any]]:
        if not self.root.is_dir():
            return []
        out = []
        for d in sorted(self.root.iterdir()):
            f = d / "lead.json"
            if f.is_file():
                lead = read_json(f)
                if rep_id is None or lead.get("rep_id") == rep_id:
                    out.append(lead)
        return sorted(out, key=lambda x: x.get("updated_at") or "", reverse=True)

    def save(self, lead: dict[str, Any]) -> None:
        with self.lock:
            path = self._path(lead["id"])
            path.parent.mkdir(parents=True, exist_ok=True)
            write_json(path, lead)

    def add(self, name: Any, phone: Any = None, rep_id: str | None = None, notes: Any = None) -> dict[str, Any]:
        name = " ".join(str(name or "").split())[:80]
        if not name:
            raise ValueError("name is required")
        phone = re.sub(r"[^\d+ -]", "", str(phone or ""))[:30].strip() or None
        with self.lock:
            if len(self.list()) >= MAX_LEADS:
                raise ValueError(f"at most {MAX_LEADS} leads")
            base, n = _slug(name), 2
            lead_id = base
            while (self.root / lead_id).exists():
                lead_id, n = f"{base}-{n}", n + 1
            lead = {"id": lead_id, "name": name, "phone": phone, "rep_id": rep_id,
                    "notes": str(notes or "").strip()[:MAX_NOTES], "call_ids": [], "created_at": _now(),
                    "updated_at": _now()}
            self.save(lead)
            return lead

    def update(self, lead_id: str, **changes: Any) -> dict[str, Any]:
        with self.lock:
            lead = self.get(lead_id)
            if "notes" in changes:
                lead["notes"] = str(changes["notes"] or "").strip()[:MAX_NOTES]
            if "name" in changes and str(changes["name"] or "").strip():
                lead["name"] = " ".join(str(changes["name"]).split())[:80]
            if "phone" in changes:
                lead["phone"] = re.sub(r"[^\d+ -]", "", str(changes["phone"] or ""))[:30].strip() or None
            if "rep_id" in changes:
                lead["rep_id"] = changes["rep_id"] or None
            lead["updated_at"] = _now()
            self.save(lead)
            return lead

    def link(self, lead_id: str, call_id: str, linked: bool = True) -> dict[str, Any]:
        with self.lock:
            lead = self.get(lead_id)
            ids = [c for c in lead.get("call_ids", []) if c != call_id]
            if linked:
                ids.append(call_id)
            lead.update(call_ids=ids, updated_at=_now())
            self.save(lead)
            return lead
