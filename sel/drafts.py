"""Editable follow-up drafts built from grounded commitments.

Drafts are proposals. Sel never sends an email or creates an event; a person
reviews each draft and approves it. A draft is ``needs_input`` when something
it needs (recipient, date, time) was not clearly stated in the call; the
missing fields are listed rather than filled with a guess.
"""

from __future__ import annotations

import re
import string
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone

from .models import Commitment, Draft, Transcript
from .pack import Pack

DEFAULT_EMAIL = """Subject: Following up on our call

Hello {customer_name},

Thank you for your time today. On our call I promised to {action}.

Best regards,
{rep_name}
{business_name}
"""

DEFAULT_CALENDAR = """Title: {action} ({customer_name})

Follow-up promised on the call of {call_date}: "{quote}"
"""

EVENT_MINUTES = 30
CALENDAR_CHANNELS = {"call", "meeting", "visit"}


class _Recorder(dict):
    """format_map helper: leaves a visible placeholder for unknown values and remembers them."""

    def __init__(self, values: dict[str, str | None]):
        super().__init__({k: v for k, v in values.items() if v})
        self.missing: set[str] = set()

    def __missing__(self, key: str) -> str:
        self.missing.add(key)
        return f"[{key.replace('_', ' ')}]"


def _split_template(text: str) -> tuple[dict[str, str], str]:
    headers: dict[str, str] = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines) and lines[i].strip() and ":" in lines[i]:
        key, value = lines[i].split(":", 1)
        headers[key.strip().lower()] = value.strip()
        i += 1
    return headers, "\n".join(lines[i:]).strip() + "\n"


def _render(template: str, values: dict[str, str | None]) -> tuple[dict[str, str], set[str]]:
    rec = _Recorder(values)
    headers, body = _split_template(template)
    out = {k: string.Formatter().vformat(v, (), rec) for k, v in headers.items()}
    out["body"] = string.Formatter().vformat(body, (), rec)
    return out, rec.missing


def _ics(uid: str, title: str, description: str, start: datetime) -> str:
    def fmt(dt: datetime) -> str:
        return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")

    end = start + timedelta(minutes=EVENT_MINUTES)
    return "\r\n".join([
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Sel//Follow-up draft//EN",
        "BEGIN:VEVENT",
        f"UID:{uid}@sel.local",
        f"DTSTAMP:{fmt(datetime.now(timezone.utc))}",
        f"DTSTART:{fmt(start)}",
        f"DTEND:{fmt(end)}",
        f"SUMMARY:{esc(title)}",
        f"DESCRIPTION:{esc(description)}",
        "END:VEVENT",
        "END:VCALENDAR",
        "",
    ])


def build_drafts(commitments: list[Commitment], transcript: Transcript, pack: Pack) -> list[Draft]:
    drafts: list[Draft] = []
    call_start = datetime.fromisoformat(transcript.started_at)
    customer = next((s for s in transcript.speakers if s.role == "customer"), None)

    for c in commitments:
        owner = transcript.speaker(c.owner)
        if owner is None or owner.role != "rep":
            continue  # customer promises are tracked, not drafted for
        if c.channel != "email" and c.channel not in CALENDAR_CHANNELS:
            continue

        due = c.due
        exact_day = due is not None and due.status == "exact"
        values: dict[str, str | None] = {
            "business_name": pack.name,
            "rep_name": owner.name,
            "customer_name": customer.name if customer else None,
            "action": c.action,
            "quote": c.evidence.quote,
            "due_phrase": c.due_text,
            "due_date": due.date if exact_day else None,
            "call_date": call_start.date().isoformat(),
        }

        if c.channel == "email":
            fields, missing = _render(pack.templates.get("email", DEFAULT_EMAIL), values)
            fields["to"] = (customer.email if customer else None) or ""
            if not fields["to"]:
                missing.add("to")
            if exact_day:
                fields["send_by"] = due.date  # type: ignore[union-attr,assignment]
            else:
                missing.add("send_by")
            drafts.append(_draft(c, "email", fields, missing))
            continue

        fields, missing = _render(pack.templates.get("calendar", DEFAULT_CALENDAR), values)
        fields.setdefault("title", c.action)
        ics = None
        if not exact_day:
            missing.add("date")
        else:
            fields["date"] = due.date  # type: ignore[union-attr,assignment]
        if due is None or not due.time:
            missing.add("time")
        else:
            fields["time"] = due.time
        if not missing:
            start = datetime.fromisoformat(f"{due.date}T{due.time}").replace(tzinfo=call_start.tzinfo)  # type: ignore[union-attr]
            ics = _ics(f"{transcript.call_id}-{c.id}", fields["title"], fields["body"], start)
        drafts.append(_draft(c, "calendar", fields, missing, ics))

    return drafts


def _draft(c: Commitment, kind: str, fields: dict[str, str], missing: set[str], ics: str | None = None) -> Draft:
    if c.due and c.due.reason:
        said = f'The call said "{c.due_text}": ' if c.due_text and c.due.status != "exact" else ""
        fields["review_note"] = f"{said}{c.due.reason}."
    return Draft(
        id=f"draft-{c.id}",
        kind=kind,  # type: ignore[arg-type]
        commitment_id=c.id,
        status="needs_input" if missing else "ready_for_approval",
        fields=fields,
        missing=sorted(missing),
        ics=ics,
    )


_PLACEHOLDER = re.compile(r"\[(business name|rep name|customer name|action|quote|due phrase|due date|call date)\]")
EDITABLE = {"email": ("to", "subject", "body", "send_by"), "calendar": ("title", "body", "date", "time")}


def _valid_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


def _valid_time(value: str) -> bool:
    try:
        time.fromisoformat(value)
        return len(value) == 5
    except ValueError:
        return False


def apply_edits(draft: Draft, edits: dict[str, str], transcript: Transcript) -> Draft:
    """Apply a person's edits and recompute what is still missing.

    Editing always clears an earlier approval: the person approves the final text.
    """
    fields = dict(draft.fields)
    for key in EDITABLE[draft.kind]:
        if key in edits:
            fields[key] = str(edits[key]).strip() if key != "body" else str(edits[key])
    missing: set[str] = set()
    for key in EDITABLE[draft.kind]:
        if _PLACEHOLDER.search(fields.get(key, "")):
            missing.add(key)
    ics = None
    if draft.kind == "email":
        if "@" not in fields.get("to", ""):
            missing.add("to")
        if not _valid_date(fields.get("send_by", "")):
            missing.add("send_by")
    else:
        if not _valid_date(fields.get("date", "")):
            missing.add("date")
        if not _valid_time(fields.get("time", "")):
            missing.add("time")
        if not missing:
            tz = datetime.fromisoformat(transcript.started_at).tzinfo
            start = datetime.fromisoformat(f"{fields['date']}T{fields['time']}").replace(tzinfo=tz)
            ics = _ics(f"{transcript.call_id}-{draft.commitment_id}", fields.get("title", ""), fields.get("body", ""), start)
    return replace(draft, fields=fields, missing=sorted(missing), ics=ics,
                   status="needs_input" if missing else "ready_for_approval")


def approve(draft: Draft) -> Draft:
    if draft.status == "needs_input":
        raise ValueError(f"draft {draft.id} still needs: {', '.join(draft.missing)}")
    return replace(draft, status="approved")
