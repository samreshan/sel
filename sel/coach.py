"""Coaching: find the moments that matter in a call, write better lines, and pick a focus.

The coaching loop (README, "How the AI works")::

    real call filed → DIAGNOSE (this module) → FOCUS (the Coach) → PRACTISE
    → PREPARE → PERFORM (the next real call) → MEASURE → next focus

Each agent is one narrow prompt with schema-constrained JSON, and code checks
everything it returns before anyone sees it:

* **Moment finder** reads the reviewed report and the scorecard grade and
  points at weak and strong moments per scorecard criterion, as segment ids.
  Code attaches the transcript's own words, moves a moment cited on a customer
  line to the rep's reply, and refuses what it can't tie back: an unknown
  segment or criterion, or praise for a line whose claim the pack contradicts.
* **Line writer** (retrieval-augmented) gets a weak moment and the pack
  passages BM25 finds for it, and returns a better line plus the ids of the
  passages it used. Code swaps the ids for the passages' own text and runs the
  line through ``check_line``. A refused line is retried once with the reasons,
  and kept with them, so the reviewer sees what Sel refused and why.
* **Coach** reads a rep's coach file (their moments across calls) and returns
  actions from a whitelist; ``apply_actions`` validates each one against the
  current state before it is written.

``check_line`` is the claim gate for words Sel suggests. A suggested line that
states a product fact must cite a pack passage, every number in it must be in
what it cites, it must be about what the passage is about, it must not flip a
"does not" in the pack, and a number the pack only gives with a condition
("extendable to 10 years with the paid Care Plan") must come with that
condition. A model judge may refuse more lines; it can never pass a line the
code refused.

Without a language model, moments come from the scorecard grade and the
reviewed findings, and no line is written: Sel shows the pack's own words for
what good looks like instead of inventing a line.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .files import read_json, write_json
from .grounding import normalize, numbers
from .llm import LLMClient
from .models import Transcript
from .pack import Pack
from .retrieval import Passage, tokenize
from .scoring import criterion_signal, mmss

KINDS = ("weak", "strong")
STATES = ("sel", "endorsed", "edited", "dismissed")
MAX_PER_KEY = 2  # moments per (criterion, kind) in one call
MAX_LINES = 4  # weak moments per call that get a better line written
PASSAGES_PER_LINE = 4
MAX_NOTE = 500
MAX_LINE_CHARS = 400
Progress = Callable[[str, float], None]

# Coach actions the code knows how to validate and apply. Anything else a model returns is refused.
COACH_ACTIONS = ("set_focus", "close_focus", "create_drill", "raise_difficulty", "escalate_to_manager")
# When skills tie, the Coach focuses on conversation skills a practice drill can train before claim accuracy,
# which is fixed by the playbook and escalated to a manager when a wrong claim reaches a customer.
SIGNAL_PRIORITY = ("objections", "discovery", "next_step", "talk_ratio", "claims", None)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# -- the transcript ---------------------------------------------------------------


def _role(transcript: Transcript, seg_id: str) -> str | None:
    seg = transcript.segment(seg_id)
    sp = transcript.speaker(seg.speaker) if seg else None
    return sp.role if sp else None


def _rep_reply(transcript: Transcript, seg_id: str) -> str | None:
    """The rep's next line after ``seg_id`` (within two lines), for a moment cited on what the customer said."""
    ids = [s.id for s in transcript.segments]
    if seg_id not in ids:
        return None
    i = ids.index(seg_id)
    for seg in transcript.segments[i + 1:i + 3]:
        if _role(transcript, seg.id) == "rep":
            return seg.id
    return None


def _customer_before(transcript: Transcript, seg_id: str) -> dict[str, Any] | None:
    ids = [s.id for s in transcript.segments]
    if seg_id not in ids:
        return None
    i = ids.index(seg_id)
    if i > 0 and _role(transcript, ids[i - 1]) == "customer":
        prev = transcript.segments[i - 1]
        return {"segment_id": prev.id, "quote": prev.text}
    return None


# -- the line gate ----------------------------------------------------------------

_NEGATIONS = frozenset({
    "not", "no", "never", "cannot", "can't", "don't", "doesn't", "isn't", "aren't", "won't", "without", "nor",
    "chaina", "chhaina", "hudaina", "hundaina", "mildaina", "gardaina", "pardaina", "dina", "sakdaina",
    "छैन", "हुँदैन", "मिल्दैन", "गर्दैन",
})
# Words that say a number or a feature holds only under some condition; the condition words follow.
_CONDITION = re.compile(
    r"\b(?:paid|optional|extra|additional|premium)\s+((?:[^\W\d][\w-]*\s*){1,3})"
    r"|\b(?:only\s+(?:with|on|for|if|when|in)|if|unless|provided(?:\s+that)?|subject\s+to|depending\s+on|depends\s+on|"
    r"requires?)\s+((?:[^\W\d][\w-]*[\s,]*){1,4})",
    re.I)
# Too common in product talk to say what a sentence is about.
_GENERIC = frozenset({
    "year", "warranty", "price", "pricing", "cost", "month", "include", "included", "includes", "including",
    "system", "plan", "per", "npr", "rs", "rupee", "available", "we", "our", "you", "your", "have", "has", "will",
    "can", "get", "hamro", "hamile", "tapai", "tapailai", "ho", "ani", "le", "lai", "chai", "pani", "sabai", "samma",
    "extend", "extends", "extended", "extendable", "cover", "covers", "is", "are", "be", "also", "just", "any",
    "time", "all", "only", "there", "it", "they", "this", "that", "yes", "no", "not",
    "parcha", "lagcha", "milcha", "huncha", "garchau", "dinchau", "barsa", "barsha", "mahina",
})


def _stem(token: str) -> str:
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _tokens(text: str) -> set[str]:
    return {_stem(t) for t in tokenize(text)}


def _content(tokens: set[str]) -> set[str]:
    return {t for t in tokens if t not in _GENERIC and not t.replace(".", "").isdigit() and len(t) > 2}


def _negated(text: str) -> bool:
    words = re.findall(r"[\wऀ-ॿ']+", text.casefold())
    return any(w in _NEGATIONS or w.endswith("n't") for w in words)


_TRAILING = re.compile(r"^(?:depending|depends|if|unless|provided|subject\s+to|only|as\s+long\s+as)\b", re.I)


def _sentences(text: str) -> list[list[str]]:
    """Sentences, each split into clauses."""
    out = []
    for sentence in re.split(r"(?<=[.!?])\s+|;\s*", text):
        clauses = [c.strip() for c in re.split(r",\s+|:\s+", sentence) if c.strip(" .,;:")]
        if clauses:
            out.append(clauses)
    return out


def _clauses(text: str) -> list[str]:
    return [c for s in _sentences(text) for c in s]


def _condition_words(clause: str) -> set[str]:
    words: set[str] = set()
    for m in _CONDITION.finditer(clause):
        words |= _content(_tokens(m.group(1) or m.group(2) or ""))
    return words


def _conditions_on(n: str, passages: list[str]) -> list[tuple[str, set[str]]]:
    """For each clause stating number ``n``: (the clause with its qualifiers, the words of its condition).

    A condition inside the clause counts ("extendable to 10 years with the paid Care Plan"), and so does a
    qualifier that follows it in the same sentence ("330 to 390 units per month, depending on season").
    """
    out = []
    for passage in passages:
        for clauses in _sentences(passage):
            for i, clause in enumerate(clauses):
                if n not in numbers(clause):
                    continue
                words, shown = _condition_words(clause), [clause]
                for later in clauses[i + 1:]:
                    if _TRAILING.match(later):
                        words |= _condition_words(later)
                        shown.append(later)
                out.append((", ".join(shown), words))
    return out


FACT_MATCH_SCORE = 2.0  # BM25 score from which a clause counts as talking about something the pack covers
MAX_REASONS = 4
_IDS = re.compile(r"\[\s*(?:[sSpP]\d{1,4}\b[^\]]*)\]|\bP\d{1,3}\b|\bs\d{2,4}\b")


def _about_the_pack(clause: str, index) -> bool:
    hits = index.search(clause, k=1) if index is not None else []
    return bool(hits) and hits[0][1] >= FACT_MATCH_SCORE and \
        len(_content(_tokens(clause)) & _content(_tokens(hits[0][0].text))) >= 2


def _has_claim_word(clause: str) -> bool:
    from .live import CLAIM_WORDS, _has

    return _has(clause, CLAIM_WORDS) is not None


def line_facts(text: str, index=None) -> list[str]:
    """The product facts a suggested line states, one per stated number, like the rep claims Sel's rules find.

    A clause is a fact when it has a number or a claim word ("warranty", "included"), or when it closely
    matches a pack passage ("it connects to your accounting software"). Questions and promises are not facts.
    """
    from .live import _PUNCT, clauses, is_claimish, is_commitment, is_question, sentences

    facts: list[str] = []
    for sentence in sentences(text):
        groups: list[list[str]] = []
        asking = is_question(sentence)
        for clause in clauses(sentence):
            if is_commitment(clause, sentence):
                continue
            if asking or is_question(clause):
                # A question asks, unless it slips in a product figure: "Before I quote a price: how big is
                # your bill?" asks; "Did you know the inverter has a 10-year warranty?" claims.
                if not (numbers(clause) and (_has_claim_word(clause) or _about_the_pack(clause, index))):
                    continue
            elif not (is_claimish(clause) or _about_the_pack(clause, index)):
                continue
            if not groups or (numbers(clause) and numbers(" ".join(groups[-1]))):
                groups.append([clause])
            else:
                groups[-1].append(clause)
        facts.extend(", ".join(g).strip(_PUNCT + " ") for g in groups)
    return facts


_UNITS = {"year", "years", "yr", "yrs", "barsa", "barsha", "kw", "units", "unit", "month", "months", "mahina", "day",
          "days", "din", "week", "weeks", "hapta", "%", "percent", "lakh", "hajar", "outlets", "counters"}


def _number_phrase(fact: str, n: str) -> str:
    """How the line said number ``n``: "3 lakh 60 hajar", "2,000", "10 years"."""
    from .retrieval import _AMOUNT

    folded = fact.casefold()
    for m in _AMOUNT.finditer(folded):
        if m.group(1):
            value = float(m.group(1)) * 100_000 + (float(m.group(2)) * 1_000 if m.group(2) else 0)
        else:
            value = float(m.group(3)) * 1_000
        if str(round(value)) == n:
            return fact[m.start():m.end()]
    for m in re.finditer(r"\d[\d,]*(?:\.\d+)?", fact):
        if m.group(0).replace(",", "") == n:
            after = re.match(r"\s*-?\s*([^\W\d]+|%)", fact[m.end():])
            unit = after.group(0) if after and after.group(1).casefold() in _UNITS else ""
            return (m.group(0) + unit).strip()
    return n


def _pack_says(fact: str, index) -> str | None:
    hits = index.search(fact, k=1) if index is not None else []
    return hits[0][0].text if hits else None


def check_line(text: str, sources: list[dict[str, str]], pack: Pack, index=None) -> dict[str, Any]:
    """The claim gate for a suggested line. Returns ``{check: ok|rejected, reasons, sources, facts}``.

    ``sources`` are ``{source, quote}`` pack passages the line says it used; only
    ones whose quote really is in that pack source count.
    """
    reasons: list[str] = []
    valid: list[dict[str, str]] = []
    dropped: list[str] = []
    # Small models loop and paste their citations into the line ("… please? [s02] [P2] [P4] [s03] …").
    ids = _IDS.findall(text)
    if ids:
        shown = ", ".join(dict.fromkeys(ids[:4]))
        return {"check": "rejected", "sources": [], "facts": [],
                "reasons": [f"contains segment or passage ids ({shown}) instead of only the words to say"]}
    for s in sources or []:
        full = pack.source_text(str(s.get("source", "")))
        if full is None or not s.get("quote") or normalize(s["quote"]) not in normalize(full):
            dropped.append(str(s.get("source") or "?"))
        else:
            valid.append({"source": s["source"], "quote": s["quote"]})
    facts = line_facts(text, index)
    if not facts:
        return {"check": "ok", "reasons": [], "sources": valid, "facts": [],
                "note": "States no product fact, so there is nothing to check against the pack."}

    # A fact's statement is one sentence, so the whole statement counts when a fact is cited.
    cited = [pack.source_text(s["source"]) if s["source"].startswith("fact:") else s["quote"] for s in valid]
    cited = [c for c in cited if c]
    cited_clauses = [c for passage in cited for c in _clauses(passage)]
    cited_numbers = numbers(" ".join(cited))
    cited_tokens = _tokens(" ".join(cited))
    line_tokens = _tokens(text)
    if dropped and not valid:
        reasons.append(f"cites {', '.join(dropped)}, which Sel could not find in the pack")

    for fact in facts:
        if not valid:
            reasons.append(f"“{fact}” states a product fact but cites no passage from the pack")
            continue
        fact_tokens = _tokens(fact)
        missing = sorted(numbers(fact) - cited_numbers, key=lambda x: float(x))
        if missing:
            said = ", ".join(_number_phrase(fact, n) for n in missing)
            hint = _pack_says(fact, index)
            reasons.append(f"says {said}, which no cited passage states" + (f"; the pack says “{hint}”" if hint else ""))
            continue
        subject = _content(fact_tokens)
        if subject and not subject & cited_tokens:
            reasons.append(f"“{fact}”: the cited passages are about something else")
            continue
        best = max(cited_clauses, key=lambda c: len(_tokens(c) & fact_tokens), default="")
        # One direction only: the pack says "does not" and the line says it does. A line that under-promises
        # in other words ("hamro haat ma chaina" for "outside our control") is not a false claim.
        if best and _tokens(best) & _content(fact_tokens) and _negated(best) and not _negated(fact):
            reasons.append(f"“{fact}” says the opposite of the pack: “{best}”")
            continue
        # A number the pack gives only with a condition must come with it. Named by the largest such number:
        # in "3 kW … 390 units, depending on season" the condition is about the 390.
        unmet = []
        for n in numbers(fact):
            conditions = _conditions_on(n, cited)
            if conditions and all(words for _, words in conditions) and not any(words & line_tokens for _, words in conditions):
                unmet.append((float(n), n, conditions[0][0]))
        if unmet:
            _, n, clause = max(unmet)
            reasons.append(f"says {_number_phrase(fact, n)} but leaves out the pack's condition: “{clause}”")
        else:
            words = _condition_words(best) if best and not numbers(fact) else set()
            if words and len(_tokens(best) & _content(fact_tokens)) >= 2 and not words & line_tokens:
                reasons.append(f"“{fact}” leaves out the pack's condition: “{best}”")
    reasons = list(dict.fromkeys(reasons))
    if len(reasons) > MAX_REASONS:
        reasons = reasons[:MAX_REASONS] + [f"and {len(reasons) - MAX_REASONS} more problems like these"]
    out: dict[str, Any] = {"check": "rejected" if reasons else "ok", "reasons": reasons, "sources": valid, "facts": facts}
    if dropped and valid:
        out["note"] = f"Ignored {', '.join(dropped)}: not found in the pack."
    return out


# -- moments: proposals and the gate ---------------------------------------------------


def rules_moments(report: dict[str, Any], pack: Pack) -> list[dict[str, Any]]:
    """Moments from what code already measured: the scorecard grade, coaching findings, claims and promises."""
    transcript = Transcript.from_dict(report["transcript"])
    out: list[dict[str, Any]] = []
    score = report.get("score") or {}
    by_signal = {criterion_signal(c): c for c in pack.scorecard}

    def add(criterion: str, kind: str, seg: str | None, why: str) -> None:
        if seg and criterion:
            out.append({"criterion": criterion, "kind": kind, "segment": seg, "why": why, "by": "rules"})

    # Most specific first: the gate keeps the first proposal for a line, so its "why" wins.
    for f in report.get("findings", []):
        if f.get("kind") == "coaching" and f.get("criterion"):
            segs = f["evidence"].get("segment_ids") or []
            rep = next((s for s in segs if _role(transcript, s) == "rep"), segs[0] if segs else None)
            add(f["criterion"], "weak", rep, f.get("summary") or "")
    claims = by_signal.get("claims")
    rules_only = str((report.get("meta") or {}).get("analyzer", "")).startswith("rules")
    if claims:
        for c in report.get("claims", []):
            seg = (c["evidence"].get("segment_ids") or [None])[0]
            explanation = c.get("explanation") or ""
            if c["verdict"] == "contradicted":
                src = c["sources"][0]["source"] if c.get("sources") else "the pack"
                add(claims.id, "weak", seg, f"“{c['claim']}” is contradicted by {src}.")
            elif c["verdict"] == "unverified":
                # The rules analyzer judges no claim; only its number conflicts and uncovered claims count.
                if not rules_only or "different numbers" in explanation or "No pack passage" in explanation:
                    add(claims.id, "weak", seg, f"“{c['claim']}”: no pack passage backs it. Say you will confirm.")
    for c in score.get("criteria", []):
        if c.get("status") in ("missed", "partial"):
            add(c["id"], "weak", c.get("segment"), f"{c.get('detail', '')}.")
        elif c.get("status") == "met":
            add(c["id"], "strong", c.get("segment"), f"{c.get('detail', '')}.")
    if claims:
        supported = [c for c in report.get("claims", []) if c["verdict"] == "supported"]
        bad = {s for c in report.get("claims", []) if c["verdict"] != "supported" for s in c["evidence"]["segment_ids"]}
        first = next((c for c in supported if not set(c["evidence"]["segment_ids"]) & bad), None)
        if first:
            add(claims.id, "strong", first["evidence"]["segment_ids"][0], f"“{first['claim']}” matches the pack.")
    nxt = by_signal.get("next_step")
    if nxt:
        roles = {s.id: s.role for s in transcript.speakers}
        for k in report.get("commitments", []):
            if roles.get(k["owner"]) == "rep" and (k.get("due") or {}).get("status") == "exact":
                add(nxt.id, "strong", k["evidence"]["segment_ids"][0], f"Agreed a day: “{k.get('due_text')}”.")
                break
    return out


def gate_moments(proposals: list[dict[str, Any]], report: dict[str, Any], pack: Pack,
                 ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Proposed moments → (moments with the transcript's own words, refused proposals with reasons)."""
    transcript = Transcript.from_dict(report["transcript"])
    claim_verdicts: dict[str, list[dict[str, Any]]] = {}
    for c in report.get("claims", []):
        for s in c["evidence"].get("segment_ids") or []:
            claim_verdicts.setdefault(s, []).append(c)
    kept: list[dict[str, Any]] = []
    refused: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    count: dict[tuple[str, str], int] = {}
    for n, p in enumerate(proposals):
        pid = str(p.get("id") or f"p{n + 1}")
        reasons: list[str] = []
        crit = pack.criterion(str(p.get("criterion", "")))
        kind = p.get("kind")
        seg = str(p.get("segment") or "")
        if crit is None:
            reasons.append(f"criterion {p.get('criterion')!r} is not in the pack scorecard")
        if kind not in KINDS:
            reasons.append(f"kind must be weak or strong, not {kind!r}")
        if transcript.segment(seg) is None:
            reasons.append(f"cites unknown segment {seg or '(none)'}")
        elif _role(transcript, seg) != "rep":
            reply = _rep_reply(transcript, seg)
            if reply is None:
                reasons.append(f"{seg} is not the rep's line and the rep did not reply to it")
            else:
                seg = reply  # the moment is how the rep answered what the customer said
        if not reasons and kind == "strong" and crit is not None and criterion_signal(crit) == "claims":
            wrong = [c for c in claim_verdicts.get(seg, []) if c["verdict"] == "contradicted"]
            if wrong:
                reasons.append(f"praises {seg}, but the pack contradicts “{wrong[0]['claim']}” there")
        if reasons:
            refused.append({"item_id": pid, "item_type": "moment", "reasons": reasons,
                            "criterion": p.get("criterion"), "kind": kind, "segment": p.get("segment")})
            continue
        key = (crit.id, kind, seg)
        if key in seen or count.get((crit.id, kind), 0) >= MAX_PER_KEY:
            continue
        seen.add(key)
        count[(crit.id, kind)] = count.get((crit.id, kind), 0) + 1
        text = transcript.segment(seg).text  # type: ignore[union-attr]
        kept.append({
            "id": f"m{len(kept) + 1}", "kind": kind, "criterion": crit.id, "title": crit.title,
            "evidence": {"segment_ids": [seg], "quote": text},
            "customer_said": _customer_before(transcript, seg),
            "start": transcript.segment(seg).start,  # type: ignore[union-attr]
            "why": " ".join(str(p.get("why") or "").split())[:240],
            "by": p.get("by") or "model",
            "proposed_lines": list(p.get("lines") or []),
            "better_line": None, "rejected_lines": [], "state": "sel", "note": "",
        })
    kept.sort(key=lambda m: (m["kind"] != "weak", m["start"]))
    for i, m in enumerate(kept):
        m["id"] = f"m{i + 1}"
    return kept, refused


# -- model agents -------------------------------------------------------------------------


def _obj(props: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def _text(n: int) -> dict[str, Any]:
    return {"type": "string", "maxLength": n}


SEG = {"type": "string", "maxLength": 8}

MOMENT_SYSTEM = """You review a recorded sales call against the business's scorecard, to coach the salesperson (REP).
Lines may mix English, romanized Nepali and Devanagari Nepali. Each line looks like: [s07] REP: words spoken
For each scorecard criterion that came up in the call, find:
- weak: the one REP line where the rep most clearly fell short of the criterion;
- strong: the one REP line where the rep did it well, if there is one.
segment is the id of a REP line, like "s07". why: what happened in that line, under 20 words, specific.
Skip criteria that never came up. Do not repeat a line for the same criterion. Return only JSON."""

LINE_SYSTEM = """You coach a salesperson. Write what the REP could have said instead, at one moment of a recorded call.
- line: only the words the rep would say. One or two short spoken sentences, under 40 words, in the same language
  mix the rep used (English, romanized Nepali, or both). No ids, no brackets, no notes in the line.
- Fix what fell short. Asking the customer a good question is often the best line.
- Use only facts from the numbered passages. Never add a number, price, warranty or timeline that is not in them.
  If a passage gives a fact only with a condition (a paid plan, "only if"), say the condition too.
- passages: the ids (like P2) of every passage whose facts the line uses; [] if it states no product fact.
Return only JSON."""

FOCUS_SYSTEM = """You coach one salesperson. Choose the one scorecard skill they should focus on this week,
from the skills listed with how often they fell short on recent real calls.
Prefer a skill that came up on several calls and that practice conversations can improve.
reason: one sentence to the rep, under 25 words, about what happened on their calls.
Return {"action": "set_focus", ...}, or {"action": "none", ...} if nothing needs focus. Return only JSON."""


def _render(transcript: Transcript, max_chars: int = 300) -> str:
    from .analyzer_llm import render

    return render(transcript.segments, transcript, max_chars=max_chars)


def _checked_summary(report: dict[str, Any]) -> str:
    score = report.get("score") or {}
    lines = [f"- {c['id']}: {c['status']} ({c.get('detail', '')})" for c in score.get("criteria", [])
             if c.get("status") not in ("not_scored",)]
    claims = [f"- {c['evidence']['segment_ids'][0]}: \"{c['claim']}\" → {c['verdict']}" for c in report.get("claims", [])
              if c["evidence"].get("segment_ids")]
    promises = [f"- {k['evidence']['segment_ids'][0]}: {k['action']} (when: {k.get('due_text') or 'not said'}, "
                f"{(k.get('due') or {}).get('status', 'missing')})" for k in report.get("commitments", [])]
    return ("Scorecard so far:\n" + ("\n".join(lines) or "- none") + "\n\nClaims checked against the pack:\n"
            + ("\n".join(claims) or "- none") + "\n\nPromises:\n" + ("\n".join(promises) or "- none"))


class MomentFinder:
    """The model points at weak and strong moments; ``gate_moments`` checks them."""

    def __init__(self, llm: LLMClient):
        self.llm = llm

    @property
    def name(self) -> str:
        return f"model:{self.llm.name}"

    def find(self, report: dict[str, Any], pack: Pack) -> list[dict[str, Any]]:
        transcript = Transcript.from_dict(report["transcript"])
        ids = [c.id for c in pack.scorecard]
        schema = _obj({"moments": {"type": "array", "maxItems": 2 * len(ids) + 2, "items": _obj({
            "criterion": {"type": "string", "enum": ids}, "kind": {"type": "string", "enum": list(KINDS)},
            "segment": SEG, "why": _text(160)})}})
        scorecard = "\n".join(f"- {c.id}: {c.title}. {c.description}" for c in pack.scorecard)
        user = (f"Business: {pack.name}\n\nScorecard:\n{scorecard}\n\n{_checked_summary(report)}\n\n"
                f"Transcript:\n{_render(transcript)}")
        out = self.llm.complete_json(MOMENT_SYSTEM, user, schema, "moments", max_tokens=900)
        from .analyzer_llm import seg_id

        return [{**m, "segment": seg_id(m.get("segment", "")), "by": self.name} for m in out.get("moments", [])]


class LineWriter:
    """Retrieval-augmented: pack passages in, a better line and the passage ids it used out."""

    def __init__(self, llm: LLMClient, index, answers: list[dict[str, Any]] | None = None):
        self.llm = llm
        self.index = index
        self.answers = answers or []  # approved team answers for this pack (the playbook overlay)

    @property
    def name(self) -> str:
        return f"model:{self.llm.name}"

    def passages(self, moment: dict[str, Any], pack: Pack) -> list[Passage]:
        said = moment["evidence"]["quote"]
        cust = (moment.get("customer_said") or {}).get("quote", "")
        crit = pack.criterion(moment["criterion"])
        hits = self.index.search(f"{cust} {said} {crit.description if crit else ''}", k=PASSAGES_PER_LINE)
        return [p for p, _ in hits]

    def write(self, moment: dict[str, Any], pack: Pack, rejection: list[str] | None = None,
              previous: str | None = None) -> dict[str, Any]:
        crit = pack.criterion(moment["criterion"])
        passages = self.passages(moment, pack)
        ids = {f"P{i + 1}": p for i, p in enumerate(passages)}
        listed = "\n".join(f"{pid} ({p.source}): {' '.join(p.text.split())}" for pid, p in ids.items()) or "(none found)"
        cust = moment.get("customer_said")
        moment_lines = (f"[{cust['segment_id']}] CUSTOMER: {cust['quote']}\n" if cust else "") + \
            f"[{moment['evidence']['segment_ids'][0]}] REP: {moment['evidence']['quote']}"
        answers = "\n".join(f"- {a.get('objection', '')}: {a.get('answer', '')}" for a in self.answers[:3])
        user = (f"Business: {pack.name}\nScorecard criterion: {crit.title if crit else moment['criterion']}. "
                f"{crit.description if crit else ''}\n\nThe moment:\n{moment_lines}\nWhat fell short: {moment.get('why', '')}\n\n"
                f"Passages:\n{listed}" + (f"\n\nAnswers the team approved for this:\n{answers}" if answers else ""))
        if rejection:
            user += (f"\n\nSel refused your earlier line “{previous}” because it {'; '.join(rejection)}. "
                     "Write a new line that fixes this.")
        schema = _obj({"line": _text(300), "passages": {"type": "array", "items": SEG, "maxItems": 3}})
        out = self.llm.complete_json(LINE_SYSTEM, user, schema, "line", max_tokens=160)
        sources = [{"source": ids[p].source, "quote": ids[p].text} for p in
                   (str(x).strip().upper() for x in out.get("passages", [])) if p in ids]
        unknown = [str(x) for x in out.get("passages", []) if str(x).strip().upper() not in ids]
        line = {"text": " ".join(str(out.get("line", "")).split())[:MAX_LINE_CHARS], "sources": sources, "by": self.name}
        if unknown:
            line["unknown_passages"] = unknown
        return line


def model_verdicts(llm: LLMClient, lines: list[dict[str, Any]], pack: Pack) -> dict[int, dict[str, Any]]:
    """A second opinion on suggested lines that passed the code gate: index → {verdict, explanation}."""
    from .analyzer_llm import VERIFY_SCHEMA, VERIFY_SYSTEM

    blocks = []
    for i, line in enumerate(lines):
        passages = "\n".join(f"  P{i}.{j + 1} ({s['source']}): {' '.join(s['quote'].split())}"
                             for j, s in enumerate(line["sources"]))
        blocks.append(f"Claim {i}: {line['text']}\n{passages or '  (no passages)'}")
    out = llm.complete_json(VERIFY_SYSTEM, f"Business: {pack.name}\n\n" + "\n\n".join(blocks), VERIFY_SCHEMA,
                            "verify-lines", max_tokens=700)
    return {c["claim_index"]: c for c in out.get("checks", []) if isinstance(c.get("claim_index"), int)}


# -- diagnose: one call --------------------------------------------------------------------


def _finish_line(line: dict[str, Any], pack: Pack, index) -> dict[str, Any]:
    checked = check_line(line.get("text", ""), line.get("sources") or [], pack, index)
    out = {"text": line.get("text", ""), "by": line.get("by") or "model", **checked, "checked_by": ["code"]}
    if line.get("unknown_passages") and out["check"] == "rejected":
        out["reasons"].append(f"cited passage ids it was not given ({', '.join(line['unknown_passages'])})")
    if not out["text"].strip():
        out.update(check="rejected", reasons=["is empty"])
    return out


def _attempt(writer: LineWriter, moment: dict[str, Any], pack: Pack, index,
             previous: dict[str, Any] | None = None) -> dict[str, Any]:
    """One try at a line; a model that fails or runs on past the token limit gives a refused attempt."""
    from .llm import LLMError

    try:
        if previous is None:
            return _finish_line(writer.write(moment, pack), pack, index)
        return _finish_line(writer.write(moment, pack, previous["reasons"], previous["text"]), pack, index)
    except LLMError as e:
        return {"text": "", "by": writer.name, "check": "rejected", "sources": [], "facts": [], "checked_by": ["code"],
                "reasons": [f"was not finished: the model failed ({e})"]}


def diagnose(report: dict[str, Any], pack: Pack, index, finder: MomentFinder | None = None,
             writer: LineWriter | None = None, judge: LLMClient | None = None, fixture: dict[str, Any] | None = None,
             progress: Progress | None = None, max_lines: int = MAX_LINES) -> dict[str, Any]:
    """Moments, better lines and what was refused, for one reviewed call (``report`` as in report.json).

    ``fixture`` is a hand-written stand-in for the models' output (example calls).
    """
    def step(stage: str, frac: float) -> None:
        if progress:
            progress(stage, frac)

    timings: dict[str, float] = {}
    t = time.perf_counter()
    step("coaching: finding moments", 0.0)
    if fixture is not None:
        proposals = [{**m, "by": "fixture"} for m in fixture.get("moments", [])]
        by_moments = "fixture (hand-written, not model output)"
    elif finder is not None:
        proposals = finder.find(report, pack)
        by_moments = finder.name
    else:
        proposals = rules_moments(report, pack)
        by_moments = "rules (no language model)"
    moments, refused = gate_moments(proposals, report, pack)
    timings["moments_s"] = time.perf_counter() - t

    t = time.perf_counter()
    weak = [m for m in moments if m["kind"] == "weak"]
    # The most important criteria get lines first when there are more weak moments than lines to write.
    weight = {c.id: c.weight for c in pack.scorecard}
    order = sorted(weak, key=lambda m: (-weight.get(m["criterion"], 1), m["start"]))
    for n, m in enumerate(order):
        attempts: list[dict[str, Any]] = []
        if m["proposed_lines"]:
            attempts = [_finish_line({**x, "by": x.get("by") or m["by"]}, pack, index) for x in m["proposed_lines"]]
        elif writer is not None and n < max_lines:
            step(f"coaching: writing better line {n + 1} of {min(len(order), max_lines)}", 0.2 + 0.6 * n / max(1, len(order)))
            first = _attempt(writer, m, pack, index)
            attempts = [first]
            if first["check"] == "rejected":
                attempts.append(_attempt(writer, m, pack, index, first))
        ok = [a for a in attempts if a["check"] == "ok"]
        m["better_line"] = ok[0] if ok else None
        m["rejected_lines"] = [a for a in attempts if a["check"] == "rejected"]
    if judge is not None:
        factual = [m for m in moments if m["better_line"] and m["better_line"]["facts"]]
        if factual:
            step("coaching: checking lines", 0.85)
            verdicts = model_verdicts(judge, [m["better_line"] for m in factual], pack)
            for i, m in enumerate(factual):
                v = verdicts.get(i)
                m["better_line"]["checked_by"] = ["code", "model"]
                if v and v.get("verdict") == "contradicted":
                    line = {**m["better_line"], "check": "rejected",
                            "reasons": [f"the model checking it found it contradicts the pack: {v.get('explanation') or ''}".strip()]}
                    m["rejected_lines"].append(line)
                    m["better_line"] = None
    for m in moments:
        m.pop("proposed_lines", None)
    timings["lines_s"] = time.perf_counter() - t
    step("coaching: done", 1.0)
    lines = [m["better_line"] for m in moments if m["better_line"]]
    by_lines = ("fixture (hand-written, not model output)" if fixture is not None
                else writer.name if writer is not None else None)
    return {
        "call_id": report["transcript"]["call_id"], "pack_id": pack.id, "built_at": _now(),
        "by": {"moments": by_moments, "lines": by_lines},
        "moments": moments, "rejected": refused,
        "counts": {"weak": len(weak), "strong": len(moments) - len(weak), "lines": len(lines),
                   "lines_rejected": sum(len(m["rejected_lines"]) for m in moments), "moments_refused": len(refused)},
        "meta": {"timings": {k: round(v, 3) for k, v in timings.items()}},
    }


def carry_over(new: dict[str, Any], old: dict[str, Any] | None) -> dict[str, Any]:
    """Keep a person's endorsements, edits, dismissals and notes when a call is coached again."""
    if not old:
        return new
    prior = {(m["criterion"], m["kind"], m["evidence"]["segment_ids"][0]): m for m in old.get("moments", [])}
    for m in new["moments"]:
        p = prior.get((m["criterion"], m["kind"], m["evidence"]["segment_ids"][0]))
        if not p:
            continue
        for key in ("state", "state_by", "state_at", "note", "state_prev"):
            if key in p:
                m[key] = p[key]
        if p.get("state") == "edited" and p.get("better_line"):
            m["original_line"] = m.get("better_line")
            m["better_line"] = p["better_line"]
    return new


def refresh_quotes(coaching: dict[str, Any], transcript: Transcript) -> dict[str, Any]:
    """After a transcript correction: quotes are the transcript's current words."""
    for m in coaching.get("moments", []):
        seg = transcript.segment(m["evidence"]["segment_ids"][0])
        if seg:
            m["evidence"]["quote"] = seg.text
        cust = m.get("customer_said")
        if cust and transcript.segment(cust["segment_id"]):
            cust["quote"] = transcript.segment(cust["segment_id"]).text  # type: ignore[union-attr]
    return coaching


def act_on_moment(coaching: dict[str, Any], moment_id: str, action: str, body: dict[str, Any], pack: Pack, index,
                  by: str | None) -> dict[str, Any]:
    """endorse | dismiss | edit | note on one moment, in place; returns the moment."""
    m = next((x for x in coaching.get("moments", []) if x["id"] == moment_id), None)
    if m is None:
        raise KeyError(moment_id)
    on = body.get("on", True) is not False
    stamp = {"state_by": by, "state_at": _now()}
    if action == "endorse":
        if on:
            if not m.get("better_line") or m["better_line"].get("check") != "ok":
                raise ValueError("only a line that passed the check against the pack can be endorsed")
            m.update(state="endorsed", **stamp)
        elif m.get("state") == "endorsed":
            m.update(state="sel", **stamp)
    elif action == "dismiss":
        if on and m.get("state") != "dismissed":
            m.update(state_prev=m.get("state", "sel"), state="dismissed", **stamp)
        elif not on and m.get("state") == "dismissed":
            m.update(state=m.pop("state_prev", "sel"), **stamp)
    elif action == "edit":
        text = " ".join(str(body.get("text") or "").split())
        if not text:
            raise ValueError("text is required")
        if len(text) > MAX_LINE_CHARS:
            raise ValueError(f"a line is at most {MAX_LINE_CHARS} characters")
        # A person's line is checked like the model's. Code supplies the passages: the earlier line's sources
        # and what BM25 finds for the new words.
        prior = m.get("better_line") or {}
        sources = list(prior.get("sources") or [])
        for p, _ in (index.search(text, k=3) if index is not None else []):
            if not any(s["source"] == p.source and s["quote"] == p.text for s in sources):
                sources.append({"source": p.source, "quote": p.text})
        checked = check_line(text, sources, pack, index)
        if checked["check"] == "ok" and checked["facts"]:
            used = _tokens(" ".join(checked["facts"]))
            checked["sources"] = [s for s in checked["sources"] if _tokens(s["quote"]) & used] or checked["sources"][:1]
        elif checked["check"] == "ok":
            checked["sources"] = []
        if "original_line" not in m:
            m["original_line"] = m.get("better_line")
        m["better_line"] = {"text": text, "by": f"person:{by}" if by else "person", **checked, "checked_by": ["code"]}
        m.update(state="edited", **stamp)
    elif action == "note":
        m["note"] = str(body.get("note") or "").strip()[:MAX_NOTE]
        m["note_by"], m["note_at"] = by, _now()
    else:
        raise KeyError(action)
    return m


# -- the Coach ----------------------------------------------------------------------------


class CoachStore:
    """``coach/<person>.json`` per rep: focus, the actions log, and (later) transfer checks."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self._lock = threading.RLock()
        self._cache: dict[str, tuple[int, dict[str, Any]]] = {}

    def _path(self, person_id: str) -> Path:
        if not re.fullmatch(r"[a-z0-9-]{1,64}", person_id or ""):
            raise KeyError(person_id)
        return self.root / f"{person_id}.json"

    def get(self, person_id: str) -> dict[str, Any]:
        path = self._path(person_id)
        try:
            mtime = path.stat().st_mtime_ns
        except OSError:
            return {"person_id": person_id, "focus": None, "history": [], "actions": [], "transfer": []}
        hit = self._cache.get(person_id)
        if hit is None or hit[0] != mtime:
            hit = (mtime, read_json(path))
            self._cache[person_id] = hit
        return json.loads(json.dumps(hit[1]))  # a copy the caller may change

    def save(self, person_id: str, data: dict[str, Any]) -> None:
        with self._lock:
            path = self._path(person_id)
            self.root.mkdir(parents=True, exist_ok=True)
            write_json(path, data)
            # Two writes can share an mtime; this process's own write must never be served stale.
            self._cache[person_id] = (path.stat().st_mtime_ns, json.loads(json.dumps(data)))

    @property
    def lock(self) -> threading.RLock:
        return self._lock


def skill_candidates(coachings: list[tuple[dict[str, Any], dict[str, Any]]], pack_lookup) -> list[dict[str, Any]]:
    """Per (pack, criterion): weak and strong moments across a rep's recent calls, newest call first.

    ``coachings`` is ``[(call summary, coaching.json)]``; dismissed moments don't count.
    """
    skills: dict[tuple[str, str], dict[str, Any]] = {}
    for call, coaching in coachings:
        pack = pack_lookup(coaching.get("pack_id") or call.get("pack_id"))
        if pack is None:
            continue
        for m in coaching.get("moments", []):
            if m.get("state") == "dismissed":
                continue
            crit = pack.criterion(m["criterion"])
            if crit is None:
                continue
            s = skills.setdefault((pack.id, crit.id), {
                "key": f"{pack.id}/{crit.id}", "pack_id": pack.id, "criterion": crit.id, "title": crit.title,
                "signal": criterion_signal(crit), "weight": crit.weight, "weak": 0, "strong": 0, "calls": [],
                "weak_calls": [], "moments": []})
            s[m["kind"]] += 1
            if call["id"] not in s["calls"]:
                s["calls"].append(call["id"])
            if m["kind"] == "weak" and call["id"] not in s["weak_calls"]:
                s["weak_calls"].append(call["id"])
            if m["kind"] == "weak" and len(s["moments"]) < 3:
                s["moments"].append({"call_id": call["id"], "moment_id": m["id"], "why": m.get("why"),
                                     "quote": m["evidence"]["quote"], "at": mmss(m.get("start") or 0)})
    # Calls fell short on, not moments: two slips in one call are one call. Ties go to what practice can train.
    return sorted(skills.values(), key=lambda s: (-len(s["weak_calls"]), SIGNAL_PRIORITY.index(s["signal"])
                                                 if s["signal"] in SIGNAL_PRIORITY else 99, -s["weak"], -s["weight"]))


def _focus_key(focus: dict[str, Any] | None) -> str | None:
    return f"{focus['pack_id']}/{focus['criterion']}" if focus else None


def _passed_since(state: dict[str, Any], focus: dict[str, Any]) -> dict[str, Any] | None:
    """The transfer check that shows the focus skill on a real call after the focus was set."""
    return next((t for t in reversed(state.get("transfer", [])) if t.get("criterion") == focus["criterion"]
                 and t.get("pack_id", focus["pack_id"]) == focus["pack_id"] and t.get("verdict") == "pass"
                 and (t.get("at") or "") >= (focus.get("since") or "")), None)


def _escalated(state: dict[str, Any]) -> set[str]:
    return {e["key"] for e in state.get("escalations", [])}


class RuleCoach:
    """The Coach without a model. One turn, in order:

    1. a transfer pass closes the focus (a manager's focus is escalated instead of changed);
    2. a contradicted claim on a real call, or a drill failed three times, is escalated to a manager;
    3. with no focus, the skill fell short on in most calls becomes the focus (``skill_candidates`` order);
    4. the focus gets a drill from its latest weak moment;
    5. a drill passed at its current difficulty gets harder.
    """

    name = "coach:rules"

    def choose(self, state: dict[str, Any], weak: list[dict[str, Any]]) -> tuple[str, str]:
        s = weak[0]
        n, seen = len(s["weak_calls"]), max(len(s["weak_calls"]), state.get("calls_seen", 0))
        return s["key"], f"Fell short on this in {n} of your last {seen} reviewed call{'' if seen == 1 else 's'}."

    def decide(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        acts: list[dict[str, Any]] = []
        focus = state.get("focus") if (state.get("focus") or {}).get("status") == "active" else None
        closed = set(state.get("closed", []))
        done = _escalated(state)
        if focus:
            passed = _passed_since(state, focus)
            if passed and focus.get("set_by_role") == "manager":
                key = f"change_manager_focus:{focus['since']}"
                if key not in done:
                    acts.append({"action": "escalate_to_manager", "reason": "change_manager_focus",
                                 "call_id": passed["call_id"],
                                 "note": f"{focus['title']} showed up on a real call; the focus you set could move on."})
            elif passed:
                acts.append({"action": "close_focus", "call_id": passed["call_id"],
                             "reason": f"Done on a real call: {passed.get('why') or focus['title']}"})
                closed.add(_focus_key(focus))
                focus = None
        for c in state.get("contradicted", []):
            if f"contradicted_claim:{c['call_id']}:{c['claim_id']}" not in done:
                acts.append({"action": "escalate_to_manager", "reason": "contradicted_claim", "call_id": c["call_id"],
                             "claim_id": c["claim_id"], "note": f"Said to a customer: “{c['claim']}”"})
        for d in state.get("drills", []):
            if d.get("fails", 0) >= 3 and f"failed_drill:{d['id']}" not in done:
                acts.append({"action": "escalate_to_manager", "reason": "failed_drill", "drill_id": d["id"],
                             "note": f"{d.get('title')}: {d['fails']} practice attempts without a pass."})
        if focus is None:
            weak = [s for s in state.get("skills", []) if s["weak"] and s["key"] not in closed]
            if weak:
                key, reason = self.choose(state, weak)
                acts.append({"action": "set_focus", "skill": key, "reason": reason})
                focus = {"key": key}
        if focus is not None:
            key = focus.get("key") or _focus_key(focus)
            skill = next((s for s in state.get("skills", []) if s["key"] == key), None)
            drills = [d for d in state.get("drills", []) if f"{d['pack_id']}/{d['criterion']}" == key]
            if skill and skill["moments"] and not drills:
                m = skill["moments"][0]
                acts.append({"action": "create_drill", "call_id": m["call_id"], "moment_id": m["moment_id"]})
            for d in drills:
                if d.get("last_verdict") == "pass" and d.get("last_difficulty") == d.get("difficulty") \
                        and (d.get("difficulty") or 1) < 3:
                    acts.append({"action": "raise_difficulty", "drill_id": d["id"]})
        return acts


class ModelCoach(RuleCoach):
    """The Coach with a model on the fast chat client choosing the focus; the rest follows the rules.

    Its choice, like every action, is validated by ``apply_actions``.
    """

    def __init__(self, llm: LLMClient):
        self.llm = llm

    @property
    def name(self) -> str:  # type: ignore[override]
        return f"coach:model:{self.llm.name}"

    def choose(self, state: dict[str, Any], weak: list[dict[str, Any]]) -> tuple[str, str]:
        keys = [s["key"] for s in weak]
        lines = []
        for s in weak:
            examples = "; ".join(f"“{x['quote'][:80]}” ({x['why']})" for x in s["moments"][:2])
            lines.append(f"- {s['key']}: {s['title']}. Fell short on {len(s['weak_calls'])} calls ({s['weak']} moments), "
                         f"did well {s['strong']} times. Examples: {examples}")
        user = (f"Rep: {state.get('name') or 'the rep'}. Reviewed calls looked at: {state.get('calls_seen', 0)}.\n\n"
                "Skills:\n" + "\n".join(lines))
        schema = _obj({"action": {"type": "string", "enum": ["set_focus", "none"]},
                       "skill": {"type": "string", "enum": keys}, "reason": _text(200)})
        out = self.llm.complete_json(FOCUS_SYSTEM, user, schema, "focus", max_tokens=160)
        if out.get("action") != "set_focus" or out.get("skill") not in keys:
            return super().choose(state, weak)
        return out["skill"], " ".join(str(out.get("reason", "")).split())


ESCALATION_REASONS = ("contradicted_claim", "failed_drill", "change_manager_focus")


def apply_actions(actions: list[dict[str, Any]], state: dict[str, Any], coach_file: dict[str, Any], by: str,
                  ) -> list[dict[str, Any]]:
    """Validate each proposed action against the whitelist and the current state; apply the valid ones in place.

    Returns the log entries (applied or refused, with the reason). Nothing a model says is applied unchecked.
    ``create_drill`` and ``raise_difficulty`` are only checked and logged here: the service makes the change
    for the entries marked applied.
    """
    log = []
    skills = {s["key"]: s for s in state.get("skills", [])}
    drills = {d["id"]: d for d in state.get("drills", [])}
    escalated = _escalated({"escalations": coach_file.get("escalations", [])})
    for a in actions[:6]:
        entry: dict[str, Any] = {"at": _now(), "by": by, "action": a.get("action"), "args": {
            k: v for k, v in a.items() if k != "action"}, "applied": False}
        name = a.get("action")
        focus = coach_file.get("focus") if (coach_file.get("focus") or {}).get("status") == "active" else None
        refused = None
        if name not in COACH_ACTIONS:
            refused = f"{name!r} is not an action the Coach may take"
        elif name == "set_focus":
            skill = skills.get(str(a.get("skill")))
            if focus:
                refused = "a focus is already active"
            elif skill is None or not skill["weak"]:
                refused = f"{a.get('skill')!r} is not a skill this rep fell short on"
            else:
                reason = " ".join(str(a.get("reason") or "").split())[:240] or f"Fell short on {skill['title'].lower()}."
                coach_file["focus"] = {"pack_id": skill["pack_id"], "criterion": skill["criterion"],
                                       "title": skill["title"], "since": _now(), "set_by": by, "status": "active",
                                       "reason": reason, "moments": skill["moments"][:3]}
        elif name == "close_focus":
            passed = _passed_since(state, focus) if focus else None
            if not focus:
                refused = "there is no active focus"
            elif focus.get("set_by_role") == "manager":
                refused = "a manager set this focus; the Coach escalates instead of changing it"
            elif not passed or passed["call_id"] != a.get("call_id"):
                refused = "no transfer check on a real call shows this skill since the focus was set"
            else:
                coach_file.setdefault("history", []).append({**focus, "status": "passed", "ended": _now(),
                                                             "evidence": {k: passed.get(k) for k in (
                                                                 "call_id", "segment_ids", "quote", "why")}})
                coach_file["focus"] = None
        elif name == "create_drill":
            skill = skills.get(_focus_key(focus) or "")
            refs = {(m["call_id"], m["moment_id"]) for m in (skill or {}).get("moments", [])}
            if not focus:
                refused = "drills follow a focus, and there is none"
            elif (a.get("call_id"), a.get("moment_id")) not in refs:
                refused = "the moment is not one of the rep's weak moments for the focus"
            elif any((d.get("source") or {}).get("call_id") == a.get("call_id")
                     and (d.get("source") or {}).get("moment_id") == a.get("moment_id") for d in drills.values()):
                refused = "a drill for this moment already exists"
        elif name == "raise_difficulty":
            d = drills.get(str(a.get("drill_id")))
            if d is None:
                refused = f"no drill {a.get('drill_id')!r} for this rep"
            elif not (d.get("last_verdict") == "pass" and d.get("last_difficulty") == d.get("difficulty")):
                refused = "the last attempt at this difficulty was not a pass"
            elif (d.get("difficulty") or 1) >= 3:
                refused = "already at the hardest level"
        elif name == "escalate_to_manager":
            reason = a.get("reason")
            key = None
            if reason not in ESCALATION_REASONS:
                refused = f"{reason!r} is not a reason to escalate; Sel escalates only {', '.join(ESCALATION_REASONS)}"
            elif reason == "contradicted_claim":
                ok = any(c["call_id"] == a.get("call_id") and c["claim_id"] == a.get("claim_id")
                         for c in state.get("contradicted", []))
                key = f"contradicted_claim:{a.get('call_id')}:{a.get('claim_id')}"
                refused = None if ok else "no contradicted claim on that real call"
            elif reason == "failed_drill":
                d = drills.get(str(a.get("drill_id")))
                key = f"failed_drill:{a.get('drill_id')}"
                refused = None if d and d.get("fails", 0) >= 3 else "the drill has not been failed three times"
            else:
                key = f"change_manager_focus:{(focus or {}).get('since')}"
                refused = None if focus and focus.get("set_by_role") == "manager" else "the focus was not set by a manager"
            if not refused and key in escalated:
                refused = "already escalated"
            if not refused:
                coach_file.setdefault("escalations", []).append({
                    "key": key, "reason": reason, "at": _now(), "by": by, "status": "open",
                    **{k: a.get(k) for k in ("call_id", "claim_id", "drill_id", "note") if a.get(k)}})
                escalated.add(key)
        if refused:
            entry["refused"] = refused
        else:
            entry["applied"] = True
        log.append(entry)
    coach_file.setdefault("actions", []).extend(log)
    coach_file["actions"] = coach_file["actions"][-200:]
    return log
