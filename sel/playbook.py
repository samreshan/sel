"""The playbook overlay: answers the team approved, learned from its own calls.

A pack (``packs/<id>``) is the business's documents and is never written to.
What the team learns lives next to the call folders in
``playbook/<pack_id>.json``::

    {pack_id, answers: [{id, criterion, objection, answer, source {call_id, moment_id, segment_ids},
                         by, status: proposed | approved | rejected, check, proposed_at,
                         approved_by, approved_at}]}

**Librarian.** Reads strong moments (a rep handled it well) and better lines a
person endorsed, across the team's real calls, and proposes answers. The
answer is always someone's own words: the rep's line from the transcript or
the endorsed line, never text the Librarian writes. It must pass the same
claim gate as any suggested line (``coach.check_line``). With a model, the
Librarian picks which candidates are worth proposing and writes a short label
for the objection (by candidate number); without one, every candidate is
proposed with the anonymised customer line as its label. A manager approves or
rejects each proposal; only approved answers are used.

**Use.** Approved answers are matched to a customer line by shared content words
(``match``) and feed the Line writer (as examples in its prompt, and as the
better line itself when no model writes one), the Prep brief and the AI
customer's rehearsal.
"""

from __future__ import annotations

import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .files import read_json, write_json
from .llm import LLMClient
from .pack import Pack
from .persona import anonymise


STATUSES = ("proposed", "approved", "rejected")
_PLACEHOLDERS = re.compile(r"\b(?:a different price|a phone number|an email address|a number)\b")
MIN_SHARED = 1  # content words an answer must share with a customer line to count as an answer to it


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class PlaybookStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.lock = threading.RLock()

    def _path(self, pack_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", pack_id or ""):
            raise KeyError(pack_id)
        return self.root / f"{pack_id}.json"

    def get(self, pack_id: str) -> dict[str, Any]:
        path = self._path(pack_id)
        return read_json(path) if path.is_file() else {"pack_id": pack_id, "answers": []}

    def save(self, data: dict[str, Any]) -> None:
        with self.lock:
            self.root.mkdir(parents=True, exist_ok=True)
            write_json(self._path(data["pack_id"]), data)

    def approved(self, pack_id: str, criterion: str | None = None) -> list[dict[str, Any]]:
        return [a for a in self.get(pack_id)["answers"]
                if a["status"] == "approved" and (criterion is None or a.get("criterion") == criterion)]


def match(answers: list[dict[str, Any]], text: str, k: int = 3) -> list[dict[str, Any]]:
    """Approved answers closest to a customer line, best first, by shared content words.

    BM25 is no use on a handful of answers (every word is rare), so this counts the content words an
    answer's objection and words share with the line; at least ``MIN_SHARED`` are needed.
    """
    from .coach import _content, _tokens

    want = _content(_tokens(text))
    scored = []
    for i, a in enumerate(answers):
        shared = len(want & _content(_tokens(f"{a['objection']} {a['answer']}")))
        if shared >= MIN_SHARED:
            scored.append((-shared, i, a))
    return [a for _, _, a in sorted(scored)[:k]]


def _same(criterion: str, text: str) -> tuple[str, str]:
    return criterion, " ".join(re.findall(r"\w+", text.casefold()))


def candidates(coachings: list[tuple[dict[str, Any], dict[str, Any]]], names: list[str],
               known: set[tuple[str, str]], answers: list[dict[str, Any]] = ()) -> list[dict[str, Any]]:
    """Strong moments with a customer line, and endorsed better lines, not proposed before.

    The same words for the same criterion are proposed once (``answers``: those already on file).
    """
    out = []
    said = {_same(a["criterion"], a["answer"]) for a in answers}
    for call, coaching in coachings:
        for m in coaching.get("moments", []):
            key = (call["id"], m["id"])
            if key in known or m.get("state") == "dismissed" or not m.get("customer_said"):
                continue
            if m["kind"] == "strong":
                answer, by = m["evidence"]["quote"], "rep"
                if _PLACEHOLDERS.search(anonymise(answer, names)) and not _PLACEHOLDERS.search(answer):
                    continue  # it quotes a deal's price or a phone number: not words a whole team can reuse
            elif m.get("state") in ("endorsed", "edited") and (m.get("better_line") or {}).get("check") == "ok":
                answer, by = m["better_line"]["text"], "endorsed line"
            else:
                continue
            if _same(m["criterion"], answer) in said:
                continue
            said.add(_same(m["criterion"], answer))
            out.append({"call_id": call["id"], "moment_id": m["id"], "criterion": m["criterion"],
                        "pack_id": coaching.get("pack_id") or call.get("pack_id"),
                        "segment_ids": m["evidence"]["segment_ids"], "answer_from": by,
                        "objection": anonymise(m["customer_said"]["quote"], names),
                        "answer": anonymise(answer, names) if by == "rep" else answer})
    return out


LIBRARIAN_SYSTEM = """You keep a sales team's playbook. Below are numbered moments from the team's calls: what a customer
said, and what a salesperson answered that worked. Choose the ones worth teaching to the whole team (a clear answer to
a concern customers often raise); skip weak or unclear ones. For each, write objection: a short label for the
customer's concern (under 12 words, no names, no prices). Return only JSON."""


def propose(cands: list[dict[str, Any]], pack: Pack, index, llm: LLMClient | None = None
            ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(proposals, refused): candidates turned into proposed answers, each checked against the pack."""
    from .coach import check_line

    labels: dict[int, str] = {}
    chosen = list(range(len(cands)))
    by = "librarian:rules"
    if llm is not None and cands:
        schema = {"type": "object", "additionalProperties": False, "required": ["answers"], "properties": {
            "answers": {"type": "array", "maxItems": len(cands), "items": {
                "type": "object", "additionalProperties": False, "required": ["moment", "objection"],
                "properties": {"moment": {"type": "integer"}, "objection": {"type": "string", "maxLength": 100}}}}}}
        listed = "\n".join(f"{i}. CUSTOMER: {c['objection']}\n   SALESPERSON: {c['answer']}" for i, c in enumerate(cands))
        out = llm.complete_json(LIBRARIAN_SYSTEM, f"Business: {pack.name}\n\n{listed}", schema, "librarian", max_tokens=400)
        chosen = [a["moment"] for a in out.get("answers", []) if isinstance(a.get("moment"), int) and 0 <= a["moment"] < len(cands)]
        labels = {a["moment"]: " ".join(str(a.get("objection") or "").split())[:100] for a in out.get("answers", [])
                  if isinstance(a.get("moment"), int)}
        by = f"librarian:model:{llm.name}"
    proposals, refused = [], []
    for i in dict.fromkeys(chosen):
        c = cands[i]
        checked = check_line(c["answer"], [], pack, index)
        if checked["facts"]:
            # An answer with product facts must cite the pack: give it the passages BM25 finds for it.
            sources = [{"source": p.source, "quote": p.text} for p, _ in index.search(c["answer"], k=3)]
            checked = check_line(c["answer"], sources, pack, index)
        entry = {"id": "a" + uuid.uuid4().hex[:8], "criterion": c["criterion"],
                 "objection": anonymise(labels.get(i) or c["objection"], []), "answer": c["answer"],
                 "answer_from": c["answer_from"],
                 "source": {k: c[k] for k in ("call_id", "moment_id", "segment_ids")}, "by": by,
                 "check": {k: checked[k] for k in ("check", "reasons", "sources")}, "proposed_at": _now()}
        if checked["check"] != "ok":
            refused.append({**entry, "status": "refused"})
        else:
            proposals.append({**entry, "status": "proposed"})
    return proposals, refused
