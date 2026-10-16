"""Transfer checks: did the skill a rep is focusing on show up on their next real calls?

After a real call is reviewed and coached, if its rep has an active focus set
before the call, the Judge (``sel.drills.judge``) grades that one criterion on
the whole call: pass, fail, not applicable (the moment never came up), or
unsure (the model and Sel's rules disagree). The result, with the segment it
cites, goes into the rep's coach file, and the Coach acts on it: a pass closes
the focus, which is the loop's measure of whether practice carried over.

Practice calls never count: only calls of kind ``real``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .drills import judge
from .llm import LLMClient
from .models import Transcript
from .pack import Pack


def due(call: dict[str, Any], focus: dict[str, Any] | None, done: list[dict[str, Any]]) -> bool:
    """Whether this call should be checked against the focus: real, newer than the focus, not checked yet."""
    if not focus or focus.get("status") != "active" or call.get("pack_id") != focus.get("pack_id"):
        return False
    if (call.get("kind") or "real") != "real":
        return False
    if any(t.get("call_id") == call["id"] and t.get("criterion") == focus["criterion"] for t in done):
        return False
    return (call.get("created_at") or "") >= (focus.get("since") or "")


def check(call: dict[str, Any], report: dict[str, Any], pack: Pack, focus: dict[str, Any],
          llm: LLMClient | None = None) -> dict[str, Any]:
    transcript = Transcript.from_dict(report["transcript"])
    v = judge(transcript, report.get("score") or {}, pack, focus["criterion"], llm)
    quotes = [s.text for sid in v["segment_ids"] if (s := transcript.segment(sid))]
    return {"call_id": call["id"], "criterion": focus["criterion"], "pack_id": pack.id, "verdict": v["verdict"],
            "segment_ids": v["segment_ids"], "quote": quotes[0] if quotes else None, "why": v["why"], "by": v["by"],
            "customer_name": call.get("customer_name"), "started_at": call.get("started_at"),
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
