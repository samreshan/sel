"""Prep: a brief before the next call with a lead, from that lead's earlier calls.

The Prep writer reads the lead's earlier **reports** (findings, claims,
promises, coaching moments), never raw transcripts. Everything in the brief is
an item from those reports, picked by id:

* **likely objections**: objections the customer raised before, each with the
  best answer Sel has for it: an approved team answer from the playbook, else
  the better line written for that moment, else nothing;
* **open promises**: what the rep and the customer said they would do;
* **discovery gaps**: scorecard skills that fell short with this customer and
  questions the customer asked;
* the rep's current **focus**, as a reminder.

With a model (the analysis client), the writer picks which objections are
likely to come back and which gaps matter, as ids, and writes a two-sentence
summary. Code checks the summary like any generated text: every number in it
must appear in the items it was given, or the summary is dropped. Without a
model, every earlier objection and gap is listed and the summary is built
from the items.
"""

from __future__ import annotations

from typing import Any

from .grounding import numbers
from .llm import LLMClient
from .playbook import match

PREP_SYSTEM = """You prepare a salesperson for their next call with a customer, from notes on earlier calls.
Items have ids like O1 (an objection the customer raised), Q2 (a question they asked), P3 (a promise), G4 (a skill
that fell short). likely_objections: ids of the objections most likely to come up again. gaps: ids of the
questions and skills to cover this time. summary: two short sentences about where things stand, using only the
items. Return only JSON."""


def _items(lead: dict[str, Any], calls: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]]):
    """Numbered items from the lead's reports and coaching, oldest call first."""
    objections, questions, promises, gaps = [], [], [], []
    for call, report, coaching in calls:
        roles = {s["id"]: s["role"] for s in report["transcript"]["speakers"]}
        segs = {s["id"]: s for s in report["transcript"]["segments"]}
        moments = (coaching or {}).get("moments", [])
        for f in report.get("findings", []):
            seg = (f["evidence"].get("segment_ids") or [None])[0]
            quote = f["evidence"].get("quote") or (segs.get(seg) or {}).get("text")
            item = {"call_id": call["id"], "started_at": call.get("started_at"), "finding_id": f["id"],
                    "text": f["summary"], "quote": quote, "segment_id": seg}
            if f["kind"] == "objection":
                # The better line for the rep's answer to this objection, if coaching wrote one.
                m = next((m for m in moments if m["kind"] == "weak" and m.get("customer_said")
                          and m["customer_said"]["segment_id"] in f["evidence"].get("segment_ids", [])
                          and (m.get("better_line") or {}).get("check") == "ok"), None)
                item["better_line"] = m["better_line"]["text"] if m else None
                item["criterion"] = m["criterion"] if m else None
                objections.append(item)
            elif f["kind"] == "question":
                questions.append(item)
        for k in report.get("commitments", []):
            promises.append({"call_id": call["id"], "owner": roles.get(k["owner"], "other"), "action": k["action"],
                             "due_text": k.get("due_text"), "due": k.get("due")})
        for c in (report.get("score") or {}).get("criteria", []):
            if c.get("status") in ("missed", "partial"):
                gaps.append({"call_id": call["id"], "criterion": c["id"], "title": c["title"], "detail": c.get("detail"),
                             "suggestion": c.get("suggestion")})
    for prefix, items in (("O", objections), ("Q", questions), ("P", promises), ("G", gaps)):
        for i, x in enumerate(items):
            x["id"] = f"{prefix}{i + 1}"
    return objections, questions, promises, gaps


def build_brief(lead: dict[str, Any], calls: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]],
                answers: list[dict[str, Any]], focus: dict[str, Any] | None, llm: LLMClient | None = None
                ) -> dict[str, Any]:
    objections, questions, promises, gaps = _items(lead, calls)
    for o in objections:
        best = match([a for a in answers if not o.get("criterion") or a.get("criterion") == o["criterion"]] or answers,
                     f"{o['text']} {o.get('quote') or ''}", k=1)
        if best:
            o["answer"] = {"from": "team answer", "text": best[0]["answer"], "objection": best[0]["objection"],
                           "answer_id": best[0]["id"]}
        elif o.get("better_line"):
            o["answer"] = {"from": "better line", "text": o["better_line"]}
        else:
            o["answer"] = None
    picked_o, picked_g, summary, by = objections, gaps + questions, None, "rules (no language model)"
    if llm is not None and (objections or gaps or questions):
        listed = "\n".join(
            [f"{o['id']}: objection: {o['text']} (said: \"{o.get('quote') or ''}\")" for o in objections]
            + [f"{q['id']}: question: {q['text']}" for q in questions]
            + [f"{p['id']}: {p['owner']} promised to {p['action']} (when: {p.get('due_text') or 'not said'})" for p in promises]
            + [f"{g['id']}: fell short on {g['title']} ({g.get('detail') or ''})" for g in gaps])
        schema = {"type": "object", "additionalProperties": False, "required": ["likely_objections", "gaps", "summary"],
                  "properties": {"likely_objections": {"type": "array", "items": {"type": "string", "maxLength": 8}, "maxItems": 5},
                                 "gaps": {"type": "array", "items": {"type": "string", "maxLength": 8}, "maxItems": 5},
                                 "summary": {"type": "string", "maxLength": 300}}}
        out = llm.complete_json(PREP_SYSTEM, f"Customer: {lead['name']}\n\n{listed}", schema, "prep", max_tokens=300)
        ids = {x["id"]: x for x in objections + questions + gaps}
        po = [ids[i] for i in out.get("likely_objections", []) if i in ids and i.startswith("O")]
        pg = [ids[i] for i in out.get("gaps", []) if i in ids and i[0] in "GQ"]
        picked_o, picked_g = po or objections, pg or (gaps + questions)
        text = " ".join(str(out.get("summary") or "").split())
        # Generated text is checked: a number the items don't contain means the summary made something up.
        if text and numbers(text) <= numbers(listed):
            summary = {"text": text, "by": f"model:{llm.name}"}
        by = f"model:{llm.name}"
    if summary is None:
        last = calls[-1][0] if calls else None
        parts = [f"{len(calls)} earlier call{'s' if len(calls) != 1 else ''} with {lead['name']}"]
        if objections:
            parts.append(f"they raised {len(objections)} objection{'s' if len(objections) != 1 else ''}")
        open_rep = [p for p in promises if p["owner"] == "rep"]
        if open_rep:
            parts.append(f"you promised {len(open_rep)} thing{'s' if len(open_rep) != 1 else ''}")
        summary = {"text": "; ".join(parts) + "." if last else f"No earlier calls with {lead['name']} yet.",
                   "by": "rules (no language model)"}
    return {"lead": {k: lead.get(k) for k in ("id", "name", "phone", "rep_id", "notes")},
            "calls": [{"call_id": c["id"], "started_at": c.get("started_at"), "overall": (r.get("score") or {}).get("overall")}
                      for c, r, _ in calls],
            "summary": summary, "likely_objections": picked_o, "gaps": picked_g,
            "promises": promises, "focus": focus, "by": by}
