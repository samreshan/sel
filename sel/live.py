"""Code-only call analysis: what can be read off a transcript without a model.

Used in three places:

* the **call simulator** scores a call while it is being spoken
  (``LiveScorer``): every new line re-runs these rules, so they must be fast
  and need nothing but the standard library and the pack;
* the **report scorecard** (``sel.scoring.signals_from_report``) uses them for
  what no analyzer reports: the rep's own questions, when the price came up,
  and how the rep replied to an objection;
* the **rules analyzer** (``RuleAnalyzer``) proposes findings, claims and
  commitments when no language model is reachable. Its proposals go through
  the same evidence gate, date resolution and drafts as a model's, and the
  report names it as the analyzer.

The rules are deliberately narrow (English and romanized/Devanagari Nepali
cue words, sentence and clause splitting, BM25 over the pack). They never
judge a claim as supported or contradicted: a numbers check cannot tell
"5-year warranty, extendable to 10" from "10-year warranty". Claims are left
``unverified`` with the closest pack passage attached, and a claim whose
numbers appear in no nearby passage is flagged for the rep to confirm.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .dates import due_phrase_in, resolve_due
from .grounding import numbers
from .models import Analysis, ClaimCheck, Commitment, Evidence, Finding, Segment, SourceRef, Transcript
from .pack import Pack
from .retrieval import PassageIndex
from .scoring import criterion_signal, empty_signals, grade, talk_signals

# -- text --------------------------------------------------------------------

_SENTENCE = re.compile(r"(?<=[.!?।])\s+")
_CLAUSE = re.compile(r"(?:,|;|:)\s+")
_PUNCT = ".,;:!?\"'()“”‘’।"

_TAGS = {"right", "is that right", "isn't it", "is it", "hoina", "haina", "correct", "ok", "okay", "na", "ho ni", "hai"}
_EN_Q = re.compile(r"^(?:so|and|but|ani|then|first|second|also|okay|ok|good)?[\s,]*"
                   r"(what|how|when|why|which|who|where|whose|is|are|was|were|do|does|did|can|could|would|"
                   r"will|should|shall|have|has|may)\b", re.I)
_NE_Q = {"kati", "kahile", "kasari", "kina", "kun", "kaha", "kahaa", "कति", "कहिले", "कसरी", "किन", "कुन", "कहाँ"}

# Cue words for a customer's pushback. "Talk to my partner" style stalls are left out on purpose:
# the reference annotations do not count them as objections.
OBJECTION_CUES = (
    "too expensive", "expensive", "too much", "a lot for", "still a lot", "pricey", "costly", "overpriced",
    "can't afford", "cannot afford", "out of budget", "over budget", "over our budget", "cheaper", "competitor",
    "another company", "other company", "another quote", "other quote", "better price", "better deal",
    "not sure", "not convinced", "not interested", "don't need", "do not need",
    "mahango", "mahngo", "sasto", "arko company", "aru company", "dherai bhayo", "ali dherai", "mahangai",
    "महँगो", "सस्तो", "धेरै भयो",
)

_EN_PROMISE = re.compile(
    r"\b(?:i'll|i’ll|i will|we'll|we’ll|we will|i'm going to|i am going to|we're going to|we are going to|"
    r"let me|i shall)\b|\b(?:our|my)\s+\w+\s+(?:will|'ll)\b", re.I)
_EN_ACTION = re.compile(
    r"\b(?:i'll|i’ll|i will|we'll|we’ll|we will|i'm going to|i am going to|we're going to|we are going to|"
    r"let me|i shall|(?:our|my)\s+\w+\s+will)\s+(.+)$", re.I)
_NE_PROMISE_SUFFIXES = ("chu", "chhu", "aula", "aunla", "unla", "ula", "छु", "उँला", "ौंला", "ुला")
# "I'll understand", "I'll be honest": not something to do after the call (speech recognition
# also writes "I understand" as "I'll understand").
_NOT_ACTIONS = re.compile(r"\b(?:i'll|i’ll|i will|we'll|we will)\s+(?:understand|think|guess|be honest|admit|tell you what)\b", re.I)
_REPORTED = {"bhaneko", "bhanyo", "bhannu", "bhanthyo", "said", "told", "says", "promised"}

CHANNEL_CUES: list[tuple[str, tuple[str, ...]]] = [
    ("visit", ("visit", "engineer", "technician", "trainer", "site", "come by", "come over", "drop by",
               "aaunchu", "aauchu", "aaula", "auchu")),
    ("meeting", ("meeting", "demo", "zoom", "meet", "presentation", "invite")),
    ("call", ("call", "phone", "ring")),
    ("email", ("email", "e-mail", "mail", "inbox")),
]
_SEND = ("send", "share", "forward", "pathau", "pathai", "पठाउ")
GENERIC_ACTION = {"email": "email the details", "call": "call back", "visit": "arrange the visit",
                  "meeting": "send the meeting invite", "other": "follow up"}

# A clause states a product fact when it has a number or one of these words.
CLAIM_WORDS = (
    "warranty", "waranti", "guarantee", "included", "includes", "include", "free", "trial", "offline", "sync",
    "integration", "integrate", "price", "pricing", "cost", "kw", "emi", "financing", "finance", "loan",
    "approval", "install", "setup", "set up", "discount", "refund", "delivery", "parcha", "lagcha", "milcha",
    "barsa", "npr", "lakh", "hajar",
)
PRICE_WORDS = ("price", "pricing", "cost", "npr", "rs", "rupees", "lakh", "hajar", "parcha", "per month", "a month",
               "mahina ko")
# BM25 score below which the "closest passage" is not shown as relevant (calibrated on the example packs).
MIN_PASSAGE_SCORE = 2.0


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE.split(text.strip()) if s.strip()]


def clauses(sentence: str) -> list[str]:
    return [c.strip() for c in _CLAUSE.split(sentence) if c.strip(_PUNCT + " ")]


def _words(text: str) -> list[str]:
    return [w.strip(_PUNCT).lower() for w in text.split() if w.strip(_PUNCT)]


def _has(text: str, cues: tuple[str, ...] | list[str]) -> str | None:
    low = text.lower()
    for cue in cues:
        if re.search(r"(?<!\w)" + re.escape(cue), low):
            return cue
    return None


def is_question(sentence: str) -> bool:
    s = sentence.strip()
    if s.endswith("?"):
        return True
    words = _words(s)
    if any(w in _NE_Q for w in words):
        return True
    return len(words) > 2 and bool(_EN_Q.match(s))


def is_tag_question(sentence: str) -> bool:
    """"..., right?", "hoina?": checking what the rep already knows, not discovery."""
    s = sentence.strip()
    if not s.endswith("?"):
        return False
    last = " ".join(_words(clauses(s)[-1] if clauses(s) else s))
    return last in _TAGS or len(_words(s)) <= 2


def objection_cue(text: str) -> str | None:
    return _has(text, OBJECTION_CUES)


def is_commitment(clause: str, sentence: str) -> bool:
    if clause.strip().endswith("?") or any(w in _REPORTED for w in _words(sentence)):
        return False
    if _EN_PROMISE.search(clause):
        return not _NOT_ACTIONS.search(clause)
    return any(len(w) > 4 and w.endswith(_NE_PROMISE_SUFFIXES) and not w.endswith("rahechu") for w in _words(clause))


def channel_of(text: str) -> str:
    for channel, cues in CHANNEL_CUES:
        if _has(text, cues):
            return channel
    return "email" if _has(text, _SEND) else "other"


def action_of(clause: str, channel: str, due_text: str | None) -> str:
    """The promised action in a few words: the English after "I'll", else a generic phrase per channel."""
    m = _EN_ACTION.search(clause)
    if not m:
        return GENERIC_ACTION.get(channel, "follow up")
    action = m.group(1)
    if due_text:
        action = re.sub(r"\s*(?:\b(?:on|by|at|before|ma)\s+)?" + re.escape(due_text), "", action, flags=re.I)
    action = re.sub(r"\s+", " ", action).strip(_PUNCT + " ")
    return (action[:80].rsplit(" ", 1)[0] if len(action) > 80 else action) or GENERIC_ACTION.get(channel, "follow up")


def is_claimish(clause: str) -> bool:
    return bool(numbers(clause)) or _has(clause, CLAIM_WORDS) is not None


def mentions_price(text: str) -> bool:
    return _has(text, PRICE_WORDS) is not None


# -- per-line detection --------------------------------------------------------


@dataclass
class LineInfo:
    question: bool = False  # any non-tag question in the line
    question_text: str | None = None
    objection: str | None = None  # the cue word
    price: bool = False
    commitments: list[dict[str, Any]] = field(default_factory=list)
    claims: list[str] = field(default_factory=list)  # claim texts, one per group of clauses


def read_line(text: str, role: str) -> LineInfo:
    info = LineInfo()
    claims: list[str] = []
    for sentence in sentences(text):
        groups: list[list[str]] = []
        question = is_question(sentence) and not is_tag_question(sentence)
        if question and not info.question:
            info.question, info.question_text = True, sentence
        if role == "customer" and not info.objection and not question:
            info.objection = objection_cue(sentence)
        parts = clauses(sentence)
        for i, clause in enumerate(parts):
            if role in ("rep", "customer") and is_commitment(clause, sentence):
                due = due_phrase_in(clause) or due_phrase_in(" ".join(parts[:i])) or due_phrase_in(sentence)
                channel = channel_of(clause)
                info.commitments.append({"channel": channel, "due_text": due,
                                         "action": action_of(clause, channel, due), "text": clause})
                continue
            if role != "rep" or is_question(clause):
                continue
            if mentions_price(clause):
                info.price = True
            if is_claimish(clause):
                # One claim per stated number: "25 years on panels, ani 10 years on the inverter" is two
                # claims, while "Our pricing: the plan is NPR 2,000" and clauses without numbers stay one.
                if not groups or (numbers(clause) and numbers(" ".join(groups[-1]))):
                    groups.append([clause])
                else:
                    groups[-1].append(clause)
        claims.extend(", ".join(g).strip(_PUNCT + " ") for g in groups)
    info.claims = claims
    return info


def objection_reply(transcript: Transcript, seg_id: str, roles: dict[str, str]) -> dict[str, Any]:
    """How the rep replied to the objection in ``seg_id``: with a question (handled) or not.

    Looks at the rep's next two lines. ``handled`` is None while the rep hasn't replied yet.
    """
    ids = [s.id for s in transcript.segments]
    if seg_id not in ids:
        return {"reply": None, "handled": None}
    after = [s for s in transcript.segments[ids.index(seg_id) + 1:] if roles.get(s.speaker) == "rep"][:2]
    if not after:
        return {"reply": None, "handled": None}
    for s in after:
        if any(is_question(x) and not is_tag_question(x) for x in sentences(s.text)):
            return {"reply": s.id, "handled": True}
        if is_claimish(s.text):
            # Defended (a price, a warranty) before asking: not "a question first", even if a question follows.
            return {"reply": s.id, "handled": False}
    return {"reply": after[0].id, "handled": False}


# -- signals over a whole transcript ---------------------------------------------


class _Cache:
    """Per-line results keyed by (segment id, text, role), so live re-scoring is linear and cheap."""

    def __init__(self) -> None:
        self.lines: dict[tuple[str, str, str], LineInfo] = {}
        self.claims: dict[str, dict[str, Any]] = {}

    def line(self, seg: Segment, role: str) -> LineInfo:
        key = (seg.id, seg.text, role)
        if key not in self.lines:
            self.lines[key] = read_line(seg.text, role)
        return self.lines[key]


def check_claim(claim: str, index: PassageIndex, cache: _Cache | None = None) -> dict[str, Any]:
    """Closest pack passage for a claim, and whether the claim's numbers appear in any nearby passage."""
    if cache is not None and claim in cache.claims:
        return cache.claims[claim]
    hits = [(p, s) for p, s in index.search(claim, k=3) if s >= MIN_PASSAGE_SCORE]
    nums = numbers(claim)
    out: dict[str, Any] = {"status": "check", "source": None, "quote": None, "note": ""}
    if not hits:
        out.update(status="risk", note="Nothing in the pack covers this; offer to confirm instead.")
    else:
        best = hits[0][0]
        matching = next((p for p, _ in hits if nums and nums <= numbers(p.text)), None)
        if nums and matching is None:
            out.update(status="risk", source=best.source, quote=best.text,
                       note="The pack gives different numbers; check before repeating it.")
        else:
            best = matching or best
            out.update(source=best.source, quote=best.text, note="Closest passage in the pack.")
    if cache is not None:
        cache.claims[claim] = out
    return out


def transcript_signals(transcript: Transcript, pack: Pack, index: PassageIndex | None = None,
                       cache: _Cache | None = None) -> dict[str, Any]:
    cache = cache or _Cache()
    index = index or pack_index(pack)
    roles = {s.id: s.role for s in transcript.speakers}
    sig = empty_signals()
    talk_signals(transcript, sig)
    segs = transcript.segments
    commitments: list[dict[str, Any]] = []
    for i, seg in enumerate(segs):
        role = roles.get(seg.speaker, "other")
        info = cache.line(seg, role)
        if role == "rep":
            if info.question:
                sig["rep_questions"].append(seg.id)
            if info.price and sig["price"] is None:
                sig["price"] = {"segment": seg.id, "start": seg.start}
                sig["rep_questions_before_price"] = len(sig["rep_questions"]) - (1 if info.question else 0)
            for n, claim in enumerate(info.claims):
                checked = check_claim(claim, index, cache)
                sig["claims"].append({"id": f"{seg.id}-{n}", "segment": seg.id, "start": seg.start, "claim": claim, **checked})
        elif role == "customer":
            if info.question:
                answered = any(roles.get(s.speaker) == "rep" for s in segs[i + 1:])
                sig["customer_questions"].append({"segment": seg.id, "start": seg.start, "text": info.question_text,
                                                  "answered": answered})
            if info.objection:
                last = sig["objections"][-1] if sig["objections"] else None
                if last and i > 0 and segs[i - 1].id == last["segments"][-1]:
                    # One objection spoken over two lines (speech recognition splits at pauses).
                    last["segments"].append(seg.id)
                    last.update(objection_reply(transcript, seg.id, roles))
                else:
                    sig["objections"].append({"segment": seg.id, "segments": [seg.id], "start": seg.start, "text": seg.text,
                                              "cue": info.objection, **objection_reply(transcript, seg.id, roles)})
        for c in info.commitments:
            commitments.append({**c, "segment": seg.id, "start": seg.start, "owner": seg.speaker, "owner_role": role})
    if sig["price"] is None:
        sig["rep_questions_before_price"] = len(sig["rep_questions"])
    for c in commitments:
        due = resolve_due(c["due_text"], transcript.started_at, pack.week_starts_on)
        c["due"] = {"status": due.status, "date": due.date, "time": due.time, "reason": due.reason,
                    "window_start": due.window_start, "window_end": due.window_end}
    sig["commitments"] = merge_commitments(commitments)
    return sig


def merge_commitments(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One entry per promise: "I'll send the details" then "I'll email it by tomorrow evening" is one email.

    Same owner and channel merge when either has no due phrase or both resolve to the same day.
    """
    out: list[dict[str, Any]] = []
    for c in items:
        target = None
        for m in out:
            if m["owner"] != c["owner"] or m["channel"] != c["channel"]:
                continue
            same_day = (m["due"]["status"] == c["due"]["status"] == "exact" and m["due"]["date"] == c["due"]["date"])
            if same_day or not m["due_text"] or not c["due_text"]:
                target = m
                break
        if target is None:
            out.append({**c, "segments": [c["segment"]]})
            continue
        if not target["due_text"] and c["due_text"]:
            # The dated mention leads: its words, its day, its line first.
            for key in ("due_text", "due", "action", "text", "segment", "start"):
                target[key] = c[key]
            target["segments"] = [c["segment"]] + target["segments"]
        else:
            target["segments"].append(c["segment"])
    for n, c in enumerate(out):
        c["id"] = f"k{n + 1}"
    return out


_INDEXES: dict[tuple[str, int], PassageIndex] = {}


def pack_index(pack: Pack) -> PassageIndex:
    """BM25 index per loaded pack; packs are cached by the service, so this is built once per pack version."""
    key = (pack.id, id(pack))
    if key not in _INDEXES:
        if len(_INDEXES) > 32:
            _INDEXES.clear()
        _INDEXES[key] = PassageIndex.from_pack(pack)
    return _INDEXES[key]


# -- live scoring and next actions --------------------------------------------------


class LiveScorer:
    """Scores a call as it happens. ``snapshot`` is cheap to call after every new line."""

    def __init__(self, pack: Pack):
        self.pack = pack
        self.index = pack_index(pack)
        self.cache = _Cache()

    def snapshot(self, transcript: Transcript, now: float | None = None, final: bool = False) -> dict[str, Any]:
        sig = transcript_signals(transcript, self.pack, self.index, self.cache)
        now = max(now or 0.0, sig["duration"])
        score = grade(sig, self.pack, final=final)
        return {"score": score, "events": events(sig), "actions": [] if final else next_actions(sig, score, self.pack, now)}


def events(sig: dict[str, Any]) -> list[dict[str, Any]]:
    """What the rules noticed, in call order, for the live feed."""
    out: list[dict[str, Any]] = []
    for q in sig["customer_questions"]:
        out.append({"kind": "question", "segment": q["segment"], "start": q["start"], "text": q["text"]})
    for o in sig["objections"]:
        out.append({"kind": "objection", "segment": o["segment"], "start": o["start"], "text": o["text"], "cue": o["cue"],
                    "handled": o["handled"]})
    for c in sig["claims"]:
        out.append({"kind": "claim", "segment": c["segment"], "start": c["start"], "text": c["claim"],
                    "status": c["status"], "note": c["note"], "source": c["source"], "quote": c["quote"]})
    for c in sig["commitments"]:
        out.append({"kind": "commitment", "segment": c["segment"], "start": c["start"], "text": c["action"],
                    "owner_role": c["owner_role"], "channel": c["channel"], "due_text": c["due_text"], "due": c["due"]})
    if sig["price"]:
        out.append({"kind": "price", "segment": sig["price"]["segment"], "start": sig["price"]["start"],
                    "text": f"Price first mentioned after {sig['rep_questions_before_price']} discovery question(s)"})
    return sorted(out, key=lambda e: e["start"])


def _criterion_for(pack: Pack, signal: str):
    return next((c for c in pack.scorecard if criterion_signal(c) == signal), None)


def next_actions(sig: dict[str, Any], score: dict[str, Any], pack: Pack, now: float) -> list[dict[str, Any]]:
    """What the rep could do next, most urgent first. Wording for scorecard items comes from the pack."""
    acts: list[dict[str, Any]] = []

    def add(kind: str, priority: int, title: str, text: str, segment: str | None = None, signal: str | None = None,
            **extra: Any) -> None:
        crit = _criterion_for(pack, signal) if signal else None
        acts.append({"id": f"{kind}:{segment or signal or len(acts)}", "kind": kind, "priority": priority,
                     "title": crit.title if crit and kind != "claim" else title, "text": text, "segment": segment,
                     "criterion": crit.id if crit else None, **extra})

    pending = [q for q in sig["customer_questions"] if not q["answered"]]
    if pending:
        add("answer", 1, "Answer the customer", f"They asked: “{pending[-1]['text']}”", pending[-1]["segment"])

    if sig["objections"]:
        o = sig["objections"][-1]
        if o["handled"] is not True:
            crit = _criterion_for(pack, "objections")
            text = crit.description if crit else "Ask what is behind the concern before defending the price or product."
            add("objection", 1 if o["handled"] is None else 2, "Handle the objection", text, o["segment"], "objections")

    for c in [c for c in sig["claims"] if c["status"] == "risk"][-2:][::-1]:
        add("claim", 2, "Check this claim", f"“{c['claim']}”: {c['note']}", c["segment"], "claims",
            source=c["source"], quote=c["quote"])

    for c in sig["commitments"]:
        if c["owner_role"] == "rep" and c["due"]["status"] != "exact":
            said = f"“{c['due_text']}”" if c["due_text"] else "no day was said"
            why = c["due"]["reason"] or ""
            add("date", 2, "Pin down a day", f"{c['action'].capitalize()}: {said}. {why}".strip(), c["segment"], "next_step")

    if not sig["rep_questions"] and now >= 20:
        crit = _criterion_for(pack, "discovery")
        add("discovery", 2 if sig["price"] else 3, "Ask about their situation",
            crit.description if crit else "Ask about the customer's needs before quoting a price.",
            sig["price"]["segment"] if sig["price"] else None, "discovery")

    share = score["talk"].get("rep_share")
    if share is not None and share > 0.65 and now >= 60:
        add("talk", 3, "Let the customer talk",
            f"You have spoken {round(share * 100)}% of the time. Ask an open question and listen.", None, "talk_ratio")

    rep_dated = [c for c in sig["commitments"] if c["owner_role"] == "rep" and c["due"]["status"] == "exact"]
    if not rep_dated and now >= 90:
        crit = _criterion_for(pack, "next_step")
        add("next_step", 3, "Agree a next step",
            crit.description if crit else "Before hanging up, agree a follow-up on a specific day.", None, "next_step")

    acts.sort(key=lambda a: a["priority"])
    return acts[:6]


# -- rules analyzer ----------------------------------------------------------------


@dataclass
class RuleAnalyzer:
    """Proposes an analysis with rules only; used when no language model is reachable.

    Questions, objections and promises come from cue words; claims are left
    ``unverified`` with the closest pack passage; coaching comes from the
    scorecard grade. Everything passes the evidence gate like any analyzer.
    """

    max_coaching: int = 3
    name: str = "rules (no language model)"
    stats: dict[str, Any] = field(default_factory=dict)

    def analyze(self, transcript: Transcript, pack: Pack) -> Analysis:
        sig = transcript_signals(transcript, pack)
        words = {s.id: s.text for s in transcript.segments}
        out = Analysis()
        for q in sig["customer_questions"]:
            out.findings.append(Finding(f"f{len(out.findings) + 1}", "question", _short(f"Asked: {q['text']}"),
                                        Evidence([q["segment"]], words[q["segment"]])))
        for o in sig["objections"]:
            out.findings.append(Finding(f"f{len(out.findings) + 1}", "objection", f"Pushed back (“{o['cue']}”).",
                                        Evidence(o["segments"], words[o["segment"]])))
        for n, c in enumerate(sig["claims"]):
            if c["status"] == "risk" and c["source"]:
                why = "The closest pack passage gives different numbers."
            elif c["status"] == "risk":
                why = "No pack passage covers it."
            else:
                why = "The closest pack passage is shown."
            sources = [SourceRef(c["source"], c["quote"])] if c["source"] else []
            out.claims.append(ClaimCheck(f"c{n + 1}", _short(c["claim"], 140), "unverified",
                                         Evidence([c["segment"]], words[c["segment"]]), sources,
                                         f"Not judged: no language model ran. {why}"))
        for c in sig["commitments"]:
            out.commitments.append(Commitment(c["id"], c["owner"], c["action"], c["channel"],  # type: ignore[arg-type]
                                              Evidence(c["segments"], words[c["segment"]]), c["due_text"]))
        # Coaching from the grade. Unjudged claims count as fine here; only number conflicts call for coaching.
        coach_sig = {**sig, "claims": [{**c, "status": "ok" if c["status"] == "check" else c["status"]}
                                       for c in sig["claims"]]}
        graded = grade(coach_sig, pack, final=True)
        weak = [c for c in graded["criteria"] if c["status"] in ("missed", "partial") and c["segment"]]
        weak.sort(key=lambda c: (c["status"] != "missed", -c["weight"]))
        for c in weak[: self.max_coaching]:
            out.findings.append(Finding(f"f{len(out.findings) + 1}", "coaching", _short(c["detail"] + "."),
                                        Evidence([c["segment"]]), criterion=c["id"], suggestion=c["suggestion"]))
        self.stats = {"rules": graded["counts"]}
        return out


def _short(text: str, n: int = 120) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1].rsplit(" ", 1)[0] + "…"
