"""The team: who reps and managers are, so Sel can say whose calls and coaching are whose.

There is no login (docs/decisions.md, open question 12). A browser picks who it is viewing as, and
calls carry the ``rep_id`` of the person who made them. People live in one small JSON file next
to the calls (``<calls dir>/people.json``).
"""

from __future__ import annotations

import json
import os
import re
import threading
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROLES = ("rep", "manager")
MAX_PEOPLE = 200
MAX_NAME = 80


def _slug(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")[:32] or "person"


class PeopleStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._cache: tuple[int, list[dict[str, Any]]] | None = None

    def list(self) -> list[dict[str, Any]]:
        try:
            mtime = self.path.stat().st_mtime_ns
        except OSError:
            return []
        if self._cache is None or self._cache[0] != mtime:
            people = json.loads(self.path.read_text(encoding="utf-8"))
            self._cache = (mtime, people)
        return [dict(p) for p in self._cache[1]]

    def get(self, person_id: str | None) -> dict[str, Any] | None:
        return next((p for p in self.list() if p["id"] == person_id), None) if person_id else None

    def _save(self, people: list[dict[str, Any]]) -> None:
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(people, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    @staticmethod
    def _clean(name: Any, role: Any) -> tuple[str, str]:
        name = " ".join(str(name or "").split())[:MAX_NAME]
        if not name:
            raise ValueError("name is required")
        if role not in ROLES:
            raise ValueError("role must be 'rep' or 'manager'")
        return name, role

    def add(self, name: Any, role: Any = "rep") -> dict[str, Any]:
        name, role = self._clean(name, role)
        with self._lock:
            people = self.list()
            if len(people) >= MAX_PEOPLE:
                raise ValueError(f"at most {MAX_PEOPLE} people")
            if any(p["name"].casefold() == name.casefold() for p in people):
                raise ValueError(f"{name} is already on the team")
            base = _slug(name)
            ids = {p["id"] for p in people}
            pid, n = base, 2
            while pid in ids:
                pid, n = f"{base}-{n}", n + 1
            person = {"id": pid, "name": name, "role": role,
                      "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            self._save(people + [person])
            return person

    def update(self, person_id: str, name: Any = None, role: Any = None) -> dict[str, Any]:
        with self._lock:
            people = self.list()
            person = next((p for p in people if p["id"] == person_id), None)
            if person is None:
                raise KeyError(person_id)
            new_name, new_role = self._clean(person["name"] if name is None else name,
                                             person["role"] if role is None else role)
            if any(p["id"] != person_id and p["name"].casefold() == new_name.casefold() for p in people):
                raise ValueError(f"{new_name} is already on the team")
            person.update(name=new_name, role=new_role)
            self._save(people)
            return person
