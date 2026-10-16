"""Practice drills: one weak moment, an AI customer built from it, attempts and their debriefs.

A drill lives in ``drills/<id>.json`` next to the call folders::

    {id, person_id, pack_id, criterion, title, source {call_id, moment_id},
     moment {customer_said, quote, why, better_line}, persona, difficulty,
     status: preparing | ready, created_by, attempts: [...]}

An attempt is one practice call in a simulator room with the AI customer. When
it ends, the Judge grades the drill's criterion on the practice transcript and
the debrief puts three answers side by side: what the rep said on the real
call, what they said in practice, and the better line.

**Judge.** Code grades the criterion from Sel's rules (``sel.scoring``): met is
a pass, partly or missed is a fail, a moment that never came up is not
applicable. A model (when given) judges meaning and must cite a segment; code
checks the segment exists and is the rep's. Where code measured the skill and
the two disagree, the verdict is ``unsure`` and counts for nothing: a pass
needs both, and "never came up" is decided by code alone.
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .files import read_json, write_json
from .llm import LLMClient, LLMError
from .models import Transcript
from .pack import Pack
from .scoring import criterion_signal

VERDICTS = ("pass", "fail", "not_applicable", "unsure")
MAX_DIFFICULTY = 3
FAILS_TO_ESCALATE = 3


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DrillStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.lock = threading.RLock()
        self._cache: dict[str, tuple[int, dict[str, Any]]] = {}

    def _path(self, drill_id: str) -> Path:
        if not re.fullmatch(r"[a-z0-9-]{1,64}", drill_id or ""):
            raise KeyError(drill_id)
        return self.root / f"{drill_id}.json"

    def get(self, drill_id: str) -> dict[str, Any]:
        path = self._path(drill_id)
        try:
            mtime = path.stat().st_mtime_ns
        except OSError as e:
            raise KeyError(f"no drill {drill_id!r}") from e
        hit = self._cache.get(drill_id)
        if hit is None or hit[0] != mtime:
            hit = (mtime, read_json(path))
            self._cache[drill_id] = hit
        return json.loads(json.dumps(hit[1]))

    def save(self, drill: dict[str, Any]) -> None:
        with self.lock:
            self.root.mkdir(parents=True, exist_ok=True)
            path = self._path(drill["id"])
            write_json(path, drill)
            # Two writes can share an mtime; this process's own write must never be served stale.
            self._cache[drill["id"]] = (path.stat().st_mtime_ns, json.loads(json.dumps(drill)))

    def list(self, person_id: str | None = None) -> list[dict[str, Any]]:
        if not self.root.is_dir():
            return []
        out = []
        for p in sorted(self.root.glob("*.json"), reverse=True):
            try:
                d = self.get(p.stem)
            except (KeyError, json.JSONDecodeError):
                continue
            if person_id is None or d.get("person_id") == person_id:
                out.append(d)
        return out

    @staticmethod
    def new_id() -> str:
        return "d" + datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]


def summary(drill: dict[str, Any]) -> dict[str, Any]:
    """What a list needs: no persona prompt details, the last verdict and counts."""
    attempts = drill.get("attempts", [])
    verdicts = [a.get("verdict") for a in attempts if a.get("verdict")]
    return {k: drill.get(k) for k in ("id", "person_id", "pack_id", "criterion", "title", "status", "difficulty",
                                      "created_at", "created_by", "source")} | {
        "attempts": len(attempts), "passes": verdicts.count("pass"), "fails": verdicts.count("fail"),
        "last_verdict": verdicts[-1] if verdicts else None,
        "customer": (drill.get("persona") or {}).get("name")}


# -- the Judge ------------------------------------------------------------------

def _obj(props: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


JUDGE_SYSTEM = """You judge one skill on a sales call transcript (a practice call or a real call).
Lines look like: [s07] REP: words spoken. The REP is the salesperson.
Skill: {title}. {description}
verdict: pass if the REP did it where it mattered; fail if the moment came up and the REP did not;
not_applicable if the moment never came up. segment: the id of the line that shows it ("" if not_applicable).
why: one short sentence about that line. Return only JSON."""

JUDGE_SCHEMA = _obj({"verdict": {"type": "string", "enum": ["pass", "fail", "not_applicable"]},
                     "segment": {"type": "string", "maxLength": 8}, "why": {"type": "string", "maxLength": 200}})


def code_verdict(score: dict[str, Any], criterion: str) -> dict[str, Any] | None:
    """The rules' verdict from a graded scorecard, or None when code does not measure this criterion."""
    c = next((x for x in score.get("criteria", []) if x["id"] == criterion), None)
    if c is None or c.get("status") == "not_scored":
        return None
    status = c.get("status")
    verdict = "pass" if status == "met" else "fail" if status in ("missed", "partial") else "not_applicable"
    return {"verdict": verdict, "segment": c.get("segment"), "why": c.get("detail") or ""}


def _rep_line(transcript: Transcript, seg: str) -> str:
    """A verdict about the rep cites the rep's line: a customer line becomes the rep's next reply."""
    found = transcript.segment(seg)
    sp = transcript.speaker(found.speaker) if found else None
    if found is None or sp is None or sp.role == "rep":
        return seg
    ids = [s.id for s in transcript.segments]
    return next((s.id for s in transcript.segments[ids.index(seg) + 1:]
                 if transcript.speaker(s.speaker) and transcript.speaker(s.speaker).role == "rep"), seg)


def judge(transcript: Transcript, score: dict[str, Any], pack: Pack, criterion: str,
          llm: LLMClient | None = None) -> dict[str, Any]:
    """{verdict, segment_ids, why, by, code}: the criterion on this transcript, checked as described above."""
    from .analyzer_llm import render, seg_id

    crit = pack.criterion(criterion)
    if crit is None:
        raise KeyError(f"{criterion!r} is not in {pack.name}")
    code = code_verdict(score, criterion)
    if code and code.get("segment"):
        code["segment"] = _rep_line(transcript, code["segment"])
    out: dict[str, Any] = {"criterion": criterion, "code": code}
    rule_based = {"verdict": code["verdict"] if code else "unsure", "segment_ids": [code["segment"]] if code and code.get("segment") else [],
                  "why": code["why"] if code else "Sel's rules do not measure this skill; no model judged it.",
                  "by": "rules (no language model)"}
    if llm is None or (code and code["verdict"] == "not_applicable"):
        return {**out, **rule_based}
    try:
        reply = llm.complete_json(JUDGE_SYSTEM.format(title=crit.title, description=crit.description),
                                  render(transcript.segments, transcript, max_chars=300), JUDGE_SCHEMA, "judge",
                                  max_tokens=160)
    except LLMError as e:
        return {**out, **rule_based, "model_error": str(e)}
    verdict = reply.get("verdict") if reply.get("verdict") in VERDICTS else "unsure"
    seg = seg_id(reply.get("segment", "")) if reply.get("segment") else ""
    reasons = []
    found = transcript.segment(seg) if seg else None
    if verdict in ("pass", "fail"):
        if found is None:
            reasons.append(f"cited segment {seg or '(none)'} is not in the transcript")
        else:
            seg = _rep_line(transcript, seg)
    if reasons:
        return {**out, **rule_based, "refused": reasons}
    judged = {"verdict": verdict, "segment_ids": [seg] if seg else [],
              "why": " ".join(str(reply.get("why") or "").split())[:240], "by": f"model:{llm.name}"}
    if code and code["verdict"] != verdict and criterion_signal(crit) is not None:
        judged.update(verdict="unsure", why=f"The model said {verdict} but Sel's rules say {code['verdict']}: "
                                            f"{judged['why'] or code['why']}")
    return {**out, **judged}


def practice_answer(transcript: Transcript, verdict: dict[str, Any]) -> dict[str, Any] | None:
    """The rep's line the verdict cites, else the rep's first answer to the customer's opening."""
    seg = transcript.segment(verdict["segment_ids"][0]) if verdict.get("segment_ids") else None
    if seg is None:
        reps = [s for s in transcript.segments if transcript.speaker(s.speaker) and transcript.speaker(s.speaker).role == "rep"]
        seg = reps[0] if reps else None
    return {"segment_id": seg.id, "text": seg.text, "start": seg.start} if seg else None


def debrief(drill: dict[str, Any], transcript: Transcript, score: dict[str, Any], pack: Pack,
            llm: LLMClient | None = None) -> dict[str, Any]:
    """The instant debrief of one attempt."""
    v = judge(transcript, score, pack, drill["criterion"], llm)
    crit = next((c for c in score.get("criteria", []) if c["id"] == drill["criterion"]), {})
    moment = drill.get("moment") or {}
    return {
        "verdict": v["verdict"], "why": v["why"], "segment_ids": v["segment_ids"], "by": v["by"], "judge": v,
        "overall": score.get("overall"), "criterion_status": crit.get("status"), "criterion_detail": crit.get("detail"),
        "real_answer": moment.get("quote"), "practice_answer": practice_answer(transcript, v),
        "better_line": (moment.get("better_line") or {}).get("text"),
        "lines": len(transcript.segments),
    }
