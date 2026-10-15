"""Grade a call against the pack's scorecard from signals code can measure.

Two producers feed the same grader:

* **live** (``sel.live``): rules over the transcript as it is being spoken,
  used by the call simulator while the call is running;
* **report** (``signals_from_report``): the reviewed report after the call,
  where questions, objections, claim verdicts and due dates come from the
  analysis that passed the evidence gate.

A criterion is graded only when it maps to a measurable signal: named with
``signal = "..."`` in ``pack.toml``, or inferred from its wording ("Discovery
before price" → discovery). Everything else is left to the coaching review
and shown as ``not_scored``; the engine never contains business-specific
rules, only these five generic signals.
"""

from __future__ import annotations

import re
from typing import Any

from .models import CallReport, Transcript
from .pack import SIGNALS, Criterion, Pack

# Checked against the title first, then the description; first match wins.
_INFER: list[tuple[str, tuple[str, ...]]] = [
    ("objections", ("objection", "pushback", "push back")),
    ("next_step", ("next step", "follow-up", "follow up", "book", "demo", "callback", "call back", "schedule")),
    ("claims", ("claim", "accurate", "approved product", "product sheet")),
    ("discovery", ("discovery", "qualify", "learn", "find out", "needs")),
    ("talk_ratio", ("listen", "talk time", "talk less")),
]

STATUS_ORDER = {"missed": 0, "partial": 1, "open": 2, "met": 3, "na": 4, "not_scored": 5}


def criterion_signal(c: Criterion) -> str | None:
    if c.signal:
        return c.signal if c.signal in SIGNALS else None
    for text in (c.title.lower(), c.description.lower()):
        for signal, words in _INFER:
            if any(re.search(r"(?<!\w)" + re.escape(w), text) for w in words):
                return signal
    return None


def mmss(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s // 60:02d}:{s % 60:02d}"


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def empty_signals() -> dict[str, Any]:
    return {
        "duration": 0.0,
        "talk": {"rep": 0.0, "customer": 0.0, "other": 0.0},
        "longest_rep": {"seconds": 0.0, "segment": None},
        "rep_questions": [],
        "price": None,
        "rep_questions_before_price": 0,
        "customer_questions": [],
        "objections": [],
        "claims": [],
        "commitments": [],
    }


def talk_signals(transcript: Transcript, sig: dict[str, Any]) -> None:
    """Talk time per role and the rep's longest unbroken stretch, in place."""
    roles = {s.id: s.role for s in transcript.speakers}
    run, run_start_seg, prev_role = 0.0, None, None
    for seg in transcript.segments:
        role = roles.get(seg.speaker, "other")
        length = max(0.0, seg.end - seg.start)
        sig["talk"][role] = sig["talk"].get(role, 0.0) + length
        sig["duration"] = max(sig["duration"], seg.end)
        if role == "rep":
            if prev_role != "rep":
                run, run_start_seg = 0.0, seg.id
            run += length
            if run > sig["longest_rep"]["seconds"]:
                sig["longest_rep"] = {"seconds": round(run, 1), "segment": run_start_seg}
        prev_role = role
    sig["talk"] = {k: round(v, 1) for k, v in sig["talk"].items()}


# -- grading -----------------------------------------------------------------


def _grade_signal(signal: str, sig: dict[str, Any], final: bool) -> tuple[float | None, str, str | None, str]:
    """(points 0..1 or None, detail, segment id, status override or "")."""
    if signal == "discovery":
        price, before, total = sig["price"], sig["rep_questions_before_price"], len(sig["rep_questions"])
        if price:
            pts = 1.0 if before >= 2 else 0.5 if before == 1 else 0.0
            return pts, f"{_plural(before, 'discovery question')} before the price at {mmss(price['start'])}", price["segment"], ""
        if total >= 2 or final:
            pts = 1.0 if total >= 2 else 0.5 if total == 1 else 0.0
            first = sig["rep_questions"][0] if sig["rep_questions"] else None
            return pts, f"{_plural(total, 'discovery question')} asked", first, ""
        return None, f"{_plural(total, 'discovery question')} so far", None, ""

    if signal == "objections":
        objections = sig["objections"]
        if not objections:
            return None, "No objections raised", None, "na"
        judged = [o for o in objections if o.get("handled") is not None]
        if not judged:
            return None, "Objection raised; waiting for the rep's reply", objections[-1]["segment"], ""
        handled = sum(1 for o in judged if o["handled"])
        miss = next((o for o in judged if not o["handled"]), None)
        detail = f"{handled} of {_plural(len(judged), 'objection')} answered with a question first"
        return handled / len(judged), detail, (miss.get("reply") or miss["segment"]) if miss else judged[-1]["segment"], ""

    if signal == "claims":
        claims = sig["claims"]
        if not claims:
            return None, "No product claims yet" if not final else "No product claims made", None, "na"
        value = {"ok": 1.0, "check": 1.0 if not final else 0.5, "risk": 0.0, "bad": 0.0}
        pts = sum(value.get(c["status"], 0.5) for c in claims) / len(claims)
        bad = [c for c in claims if c["status"] in ("risk", "bad")]
        unchecked = sum(1 for c in claims if c["status"] == "check")
        parts = []
        if bad:
            parts.append(f"{_plural(len(bad), 'claim')} at odds with the pack")
        if unchecked:
            parts.append(f"{unchecked} not confirmed" if final else f"{unchecked} with no number conflict")
        ok = sum(1 for c in claims if c["status"] == "ok")
        if ok:
            parts.append(f"{ok} supported")
        return pts, ", ".join(parts) or f"{_plural(len(claims), 'claim')}", (bad[0] if bad else claims[0])["segment"], ""

    if signal == "next_step":
        mine = [c for c in sig["commitments"] if c["owner_role"] == "rep"]
        if not mine:
            if final:
                return 0.0, "No follow-up was agreed", None, ""
            return None, "No follow-up agreed yet", None, ""
        exact = [c for c in mine if (c.get("due") or {}).get("status") == "exact"]
        loose = next((c for c in mine if (c.get("due") or {}).get("status") != "exact"), None)
        detail = f"{len(exact)} of {_plural(len(mine), 'follow-up')} with a fixed day"
        return len(exact) / len(mine), detail, (loose or mine[0])["segment"], ""

    if signal == "talk_ratio":
        rep, cust = sig["talk"].get("rep", 0.0), sig["talk"].get("customer", 0.0)
        if rep + cust < 30 and not final:
            return None, "Not enough talk yet", None, ""
        if rep + cust == 0:
            return None, "Nobody spoke", None, "na"
        share = rep / (rep + cust)
        pts = 1.0 if 0.3 <= share <= 0.65 else 0.5 if 0.2 <= share <= 0.75 else 0.0
        return pts, f"Rep spoke {round(share * 100)}% of the time", sig["longest_rep"]["segment"], ""

    return None, "", None, "not_scored"


def grade(sig: dict[str, Any], pack: Pack, final: bool) -> dict[str, Any]:
    criteria = []
    total_w = got = 0.0
    for c in pack.scorecard:
        signal = criterion_signal(c)
        if signal is None:
            criteria.append({"id": c.id, "title": c.title, "signal": None, "weight": c.weight, "status": "not_scored",
                             "points": None, "detail": "Judged in the coaching review, not scored by code",
                             "segment": None, "suggestion": c.description})
            continue
        pts, detail, seg, override = _grade_signal(signal, sig, final)
        if override:
            status = override
        elif pts is None:
            status = "open"
        else:
            status = "met" if pts >= 0.999 else "partial" if pts >= 0.5 else "missed"
        if pts is not None:
            total_w += c.weight
            got += c.weight * pts
        criteria.append({"id": c.id, "title": c.title, "signal": signal, "weight": c.weight, "status": status,
                         "points": None if pts is None else round(pts, 2), "detail": detail, "segment": seg,
                         "suggestion": c.description})
    rep, cust = sig["talk"].get("rep", 0.0), sig["talk"].get("customer", 0.0)
    return {
        "final": final,
        "overall": round(100 * got / total_w) if total_w else None,
        "criteria": criteria,
        "talk": {**sig["talk"], "rep_share": round(rep / (rep + cust), 3) if rep + cust else None,
                 "longest_rep": sig["longest_rep"]},
        "duration": round(sig["duration"], 1),
        "counts": {
            "customer_questions": len(sig["customer_questions"]),
            "rep_questions": len(sig["rep_questions"]),
            "objections": len(sig["objections"]),
            "claims": len(sig["claims"]),
            "claims_at_risk": sum(1 for x in sig["claims"] if x["status"] in ("risk", "bad")),
            "commitments": len(sig["commitments"]),
        },
    }


def signals_from_report(report: CallReport, pack: Pack) -> dict[str, Any]:
    """Signals from a reviewed report: what passed the evidence gate, plus rules for what no analyzer reports."""
    from .live import transcript_signals  # rules for rep questions, price timing and replies to objections

    rules = transcript_signals(report.transcript, pack)
    sig = empty_signals()
    sig.update({k: rules[k] for k in ("duration", "talk", "longest_rep", "rep_questions", "price",
                                       "rep_questions_before_price")})
    by_seg = {s.id: s for s in report.transcript.segments}
    roles = {s.id: s.role for s in report.transcript.speakers}
    replies = {o["segment"]: o for o in rules["objections"]}
    for f in report.findings:
        seg = f.evidence.segment_ids[0] if f.evidence.segment_ids else None
        if f.kind == "question" and seg:
            sig["customer_questions"].append({"segment": seg, "text": f.summary, "answered": True})
        elif f.kind == "objection" and seg:
            ruled = replies.get(seg) or _reply_to(report.transcript, seg, roles)
            sig["objections"].append({"segment": seg, "text": f.summary, "cue": None,
                                      "reply": ruled.get("reply"), "handled": bool(ruled.get("handled"))})
    status = {"supported": "ok", "contradicted": "bad", "unverified": "check"}
    for c in report.claims:
        seg = c.evidence.segment_ids[0] if c.evidence.segment_ids else None
        if seg in by_seg:
            sig["claims"].append({"id": c.id, "segment": seg, "claim": c.claim, "status": status.get(c.verdict, "check")})
    for c in report.commitments:
        seg = c.evidence.segment_ids[0] if c.evidence.segment_ids else None
        sig["commitments"].append({
            "id": c.id, "segment": seg, "owner_role": roles.get(c.owner, "other"), "action": c.action,
            "channel": c.channel, "due_text": c.due_text,
            "due": {"status": c.due.status, "date": c.due.date, "reason": c.due.reason} if c.due else None,
        })
    return sig


def _reply_to(transcript: Transcript, seg_id: str, roles: dict[str, str]) -> dict[str, Any]:
    from .live import objection_reply

    return objection_reply(transcript, seg_id, roles)


def score_report(report: CallReport, pack: Pack) -> dict[str, Any]:
    return grade(signals_from_report(report, pack), pack, final=True)
