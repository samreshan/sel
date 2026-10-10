"""Model-backed call analysis.

The model is asked for small, separable jobs, each returning JSON that cites
transcript segment ids:

1. **extract** (once per chunk of the call): customer questions, objections,
   commitments with the due phrase copied verbatim, and the rep's factual
   product claims;
2. **verify** (once per call): each claim, with the pack passages that keyword
   search found for it, judged supported / contradicted / unverified;
3. **coach** (once per call): at most three scorecard-based coaching points.

Small local models cannot be trusted to copy text: asked to quote, a 3B
model under schema-constrained decoding paraphrased, translated and then
looped until it ran out of tokens. So the model only *points*: it names
segment ids and writes short summaries (lengths capped in the schema). Sel
attaches the exact transcript words as the quote, attaches the exact passage
text as the claim source, and keeps a spoken due phrase only if those words
really occur in the cited segments; otherwise the commitment has no due
phrase and its draft asks the user for a date. Sel resolves dates itself.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from .dates import is_time_word
from .grounding import normalize
from .llm import LLMClient
from .models import Analysis, ClaimCheck, Commitment, Evidence, Finding, Segment, SourceRef, Speaker, Transcript
from .pack import Pack
from .retrieval import PassageIndex

CHANNELS = ["email", "call", "meeting", "visit", "other"]
ROLE_NAMES = {"rep": "REP", "customer": "CUSTOMER", "other": "OTHER"}
Progress = Callable[[str, float], None]


def _obj(props: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def _arr(item: dict[str, Any]) -> dict[str, Any]:
    return {"type": "array", "items": item}


S = {"type": "string"}
SEG = {"type": "string", "maxLength": 8}


def _text(n: int) -> dict[str, Any]:
    return {"type": "string", "maxLength": n}


def _arr_max(item: dict[str, Any], n: int) -> dict[str, Any]:
    return {"type": "array", "items": item, "maxItems": n}


_COMMON = """You review a recorded sales call. Lines may mix English, romanized Nepali and Devanagari Nepali.
Each line looks like: [s07] REP: words spoken   (s07 is the segment id; REP is the salesperson, CUSTOMER the buyer)
"segment" is only the id, like "s07". Write short English (under 15 words). Only use what is in the lines.
Return only JSON."""

# Three narrow tasks instead of one broad one: small models follow a single instruction far better.
EXTRACT_TASKS: list[tuple[str, str, dict[str, Any]]] = [
    ("customer", _COMMON + """

Task: list what the CUSTOMER asked and pushed back on. Use only CUSTOMER lines.
- questions: each question the customer asked (price, timing, how something works, "kati", "kahile", "cha?").
- objections: concerns or pushback: too expensive ("mahango"), a competitor is cheaper, needs to ask family, doubts.
Example: [s04] CUSTOMER: Ali mahango bhayo, price kati ho?
{"questions":[{"segment":"s04","summary":"Asked the price."}],"objections":[{"segment":"s04","summary":"Thinks it is too expensive."}]}""",
     _obj({"questions": _arr_max(_obj({"segment": SEG, "summary": _text(120)}), 10),
           "objections": _arr_max(_obj({"segment": SEG, "summary": _text(120)}), 8)})),
    ("commitments", _COMMON + """

Task: list every promise to do something AFTER the call, by REP or CUSTOMER: future actions like "I'll email", "will call", "garchu", "pathauchu", "pathaula", "dinchu".
- speaker: who promised. action: what they will do. channel: email, call, meeting, visit or other.
- when: copy the words from that line that say when ("bholi", "aaja", "parsi", "beluka samma", "Friday", "sukrabar", "next week", "Monday at 11 am"), or "" if the line gives no time. Never make up a time.
Not promises: agreeing ("okay", "I understand", "huncha" on its own), describing the product, or what someone did in the past.
Example: [s03] REP: Ma bholi quotation email garchu.
{"commitments":[{"segment":"s03","speaker":"REP","action":"email the quotation","channel":"email","when":"bholi"}]}""",
     _obj({"commitments": _arr_max(_obj({
         "segment": SEG, "speaker": {"type": "string", "enum": ["REP", "CUSTOMER"]},
         "action": _text(100), "channel": {"type": "string", "enum": CHANNELS}, "when": _text(40)}), 8)})),
    ("claims", _COMMON + """

Task: list facts the REP stated about the product or offer: prices, plans and what each includes, warranty years, features (what works offline, integrations, trials), financing, how long things take.
Go through every REP line; most calls have several claims.
One fact per item: if a line states two facts, make two items. Keep the numbers in the claim.
Example: [s05] REP: Panel ma 20 years warranty cha, ani battery ma 3 years.
{"claims":[{"segment":"s05","claim":"Panels have a 20-year warranty."},{"segment":"s05","claim":"The battery has a 3-year warranty."}]}""",
     _obj({"claims": _arr_max(_obj({"segment": SEG, "claim": _text(140)}), 12)})),
]

VERIFY_SCHEMA = _obj({
    "checks": _arr_max(_obj({
        "claim_index": {"type": "integer"},
        "verdict": {"type": "string", "enum": ["supported", "contradicted", "unverified"]},
        "passage_id": SEG,
        "explanation": _text(160),
    }), 20),
})

VERIFY_SYSTEM = """You check a salesperson's product claims against the business's own documents.
For each claim you get numbered candidate passages from the documents.
- supported: a passage states the same fact (same number, term or condition).
- contradicted: a passage states something incompatible (a different number, a missing condition, "we do not offer ...").
- unverified: no passage addresses the claim. When unsure, choose unverified.
passage_id is the id (like P3) of the passage that decides the verdict, or "" for unverified.
explanation: one short sentence. Return one check per claim. Return only JSON."""

COACH_SYSTEM = """You are a sales coach reviewing one recorded call against the business's scorecard.
Give at most {n} coaching points: the most useful specific things the REP could do better next time.
Each point names one scorecard criterion id, the one segment id where it happened (like "s07"), what happened (summary) and what to try instead (suggestion), each under 20 words.
Skip criteria the rep handled well. Base everything on the transcript lines. Return only JSON."""

SPEAKER_SCHEMA = _obj({"labels": _arr(_obj({"segment": SEG, "role": {"type": "string", "enum": ["REP", "CUSTOMER"]}}))})
SPEAKER_SYSTEM = """A sales call was transcribed without speaker labels. The REP sells for {business}; the CUSTOMER is the prospective buyer.
For every line, decide who most likely said it, using content and turn-taking (speakers usually alternate). Return a label for every segment id. Return only JSON."""


def render(segments: list[Segment], transcript: Transcript, max_chars: int | None = None) -> str:
    lines = []
    for s in segments:
        sp = transcript.speaker(s.speaker)
        role = ROLE_NAMES.get(sp.role, "OTHER") if sp else "UNKNOWN"
        text = s.text if max_chars is None or len(s.text) <= max_chars else s.text[:max_chars] + "…"
        lines.append(f"[{s.id}] {role}: {text}")
    return "\n".join(lines)


def chunk_segments(segments: list[Segment], max_chars: int = 2400, overlap: int = 2) -> list[list[Segment]]:
    chunks: list[list[Segment]] = []
    start = 0
    while start < len(segments):
        size, end = 0, start
        while end < len(segments) and (end == start or size + len(segments[end].text) <= max_chars):
            size += len(segments[end].text) + 20
            end += 1
        chunks.append(segments[start:end])
        if end >= len(segments):
            break
        start = max(end - overlap, start + 1)
    return chunks


def seg_id(raw: str) -> str:
    """Small models write "s02 00:07", "[s02]" or "S2"; keep the id part."""
    m = re.search(r"s\d+", str(raw).lower())
    return m.group(0) if m else str(raw)


def locate(phrase: str, seg: str, transcript: Transcript) -> tuple[str, str] | None:
    """Find ``phrase`` in segment ``seg`` or its neighbours: (segment id, exact transcript words) or None.

    Exact (case- and space-insensitive) matches win; otherwise the closest run
    of words with >= 0.85 similarity, so a dropped word or a spelling fix is
    tolerated but a paraphrase or translation is not.
    """
    ids = [s.id for s in transcript.segments]
    if seg not in ids or not phrase.strip():
        return None
    i = ids.index(seg)
    neighbours = [transcript.segments[j] for j in (i, i - 1, i + 1, i - 2, i + 2) if 0 <= j < len(ids)]
    nq = normalize(phrase)
    for cand in neighbours:
        text = normalize(cand.text)
        if nq in text:
            words = cand.text.split()
            n = len(nq.split())
            for k in range(len(words) - n + 1):
                window = " ".join(words[k:k + n])
                if nq in normalize(window):
                    return cand.id, window
            return cand.id, phrase
    best: tuple[float, str, str] = (0.0, seg, phrase)
    qn = len(nq.split())
    for cand in neighbours:
        words = cand.text.split()
        for size in range(max(1, qn - 2), qn + 3):
            for k in range(0, max(1, len(words) - size + 1)):
                window = " ".join(words[k:k + size])
                ratio = SequenceMatcher(None, nq, normalize(window)).ratio()
                if ratio > best[0]:
                    best = (ratio, cand.id, window)
    return (best[1], best[2]) if best[0] >= 0.85 else None


def widen_due_phrase(phrase: str, text: str) -> str:
    """Extend ``phrase`` (exact words from ``text``) over neighbouring time words.

    Models often copy part of a due phrase; "beluka samma" alone reads as
    unclear, while the line said "bholi beluka samma". Only time vocabulary is
    absorbed (days, weekdays, parts of day, "next", clock times), so the phrase
    can grow but never picks up unrelated words.
    """
    words = text.split()
    n = len(phrase.split())
    target = normalize(phrase)
    for k in range(len(words) - n + 1):
        if normalize(" ".join(words[k:k + n])) == target:
            lo, hi = k, k + n
            while lo > 0 and is_time_word(words[lo - 1]) and normalize(words[lo - 1]) not in {"ma", "in", "on", "at", "by"}:
                lo -= 1
            while hi < len(words) and is_time_word(words[hi]):
                hi += 1
            # Trailing connectives ("ma", "at") alone add nothing.
            while hi - 1 > lo and normalize(words[hi - 1]).strip(".,;:!?") in {"ma", "in", "on", "at", "by", "nai"} and hi > k + n:
                hi -= 1
            return " ".join(words[lo:hi]).strip(".,;:!?")
    return phrase


def snap_quote(quote: str, seg: str, transcript: Transcript) -> tuple[str, str, bool]:
    """(segment id, quote, adjusted): the transcript's words when ``quote`` can be located, else unchanged."""
    found = locate(quote, seg, transcript)
    if found is None:
        return seg, quote, False
    return found[0], found[1], found != (seg, quote)


@dataclass
class LLMAnalyzer:
    llm: LLMClient
    chunk_chars: int = 2400
    max_coaching: int = 3
    passages_per_claim: int = 4
    progress: Progress | None = None
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return f"llm:{self.llm.name}"

    def _report(self, stage: str, frac: float) -> None:
        if self.progress:
            self.progress(stage, frac)

    def analyze(self, transcript: Transcript, pack: Pack) -> Analysis:
        self.stats = {"chunks": 0, "unknown_segments": 0, "due_phrases_not_in_call": 0}
        chunks = chunk_segments(transcript.segments, self.chunk_chars)
        self.stats["chunks"] = len(chunks)
        raw: dict[str, list[dict[str, Any]]] = {"questions": [], "objections": [], "commitments": [], "claims": []}
        for n, chunk in enumerate(chunks):
            self._report(f"analysis: reading part {n + 1} of {len(chunks)}", n / max(1, len(chunks)))
            lines = f"Business: {pack.name}\n\nTranscript lines:\n{render(chunk, transcript)}"
            out: dict[str, Any] = {}
            for task, system, schema in EXTRACT_TASKS:
                out.update(self.llm.complete_json(system, lines, schema, task, max_tokens=700))
            valid = {s.id for s in chunk}
            for key in raw:
                for x in out.get(key, []):
                    x["segment"] = seg_id(x.get("segment", ""))
                    if x["segment"] in valid:
                        raw[key].append(x)
                    else:
                        # Kept so the evidence gate refuses it visibly instead of it vanishing.
                        self.stats["unknown_segments"] += 1
                        raw[key].append(x)

        analysis = Analysis()
        seen: set[tuple[str, str]] = set()

        def fresh(kind: str, seg: str) -> bool:
            if (kind, seg) in seen:
                return False
            seen.add((kind, seg))
            return True

        def words(seg: str) -> str | None:
            found = transcript.segment(seg)
            return found.text if found else None

        for kind, key in (("question", "questions"), ("objection", "objections")):
            for x in raw[key]:
                if fresh(kind, x["segment"]):
                    analysis.findings.append(Finding(
                        id=f"f{len(analysis.findings) + 1}", kind=kind, summary=x["summary"],  # type: ignore[arg-type]
                        evidence=Evidence([x["segment"]], words(x["segment"]))))

        for x in raw["commitments"]:
            seg = x["segment"]
            if not fresh("commitment:" + x["action"].lower()[:30], seg):
                continue
            owner = self._owner(x["speaker"], seg, transcript)
            ids = [seg]
            found = transcript.segment(seg)
            if found and owner and found.speaker != owner:
                # "Send it by tomorrow?" / "Huncha": the promise is the owner's reply next door.
                reply = self._adjacent_by(owner, seg, transcript)
                if reply:
                    ids.append(reply)
            due_text = None
            if x["when"].strip():
                located = locate(x["when"], seg, transcript)
                if located:
                    due_text = widen_due_phrase(located[1], transcript.segment(located[0]).text)  # type: ignore[union-attr]
                else:
                    self.stats["due_phrases_not_in_call"] += 1
            analysis.commitments.append(Commitment(
                id=f"k{len(analysis.commitments) + 1}", owner=owner or x["speaker"], action=x["action"],
                channel=x["channel"] if x["channel"] in CHANNELS else "other",  # type: ignore[arg-type]
                evidence=Evidence(ids, words(seg)), due_text=due_text))

        claims = []
        reps = {sp.id for sp in transcript.speakers if sp.role == "rep"}
        for x in raw["claims"]:
            seg = x["segment"]
            found = transcript.segment(seg)
            if found and found.speaker not in reps:
                # Claims are the rep's statements; a model citing the customer's question gets the rep's answer.
                seg = next((r for r in (self._adjacent_by(rep, seg, transcript) for rep in reps) if r), seg)
            if fresh("claim:" + normalize(x["claim"])[:40], seg):
                claims.append((seg, words(seg) or "", x["claim"]))
        self._report("analysis: checking claims", 0.6)
        analysis.claims = self._verify(claims, pack)

        self._report("analysis: coaching", 0.8)
        analysis.findings.extend(self._coach(transcript, pack, analysis, start=len(analysis.findings) + 1))
        return analysis

    # -- helpers ---------------------------------------------------------

    @staticmethod
    def _owner(role_name: str, seg: str, transcript: Transcript) -> str | None:
        role = "rep" if role_name == "REP" else "customer"
        matches = [s.id for s in transcript.speakers if s.role == role]
        found = transcript.segment(seg)
        if found and found.speaker in matches:
            return found.speaker
        return matches[0] if matches else None

    @staticmethod
    def _adjacent_by(speaker: str, seg: str, transcript: Transcript) -> str | None:
        ids = [s.id for s in transcript.segments]
        if seg not in ids:
            return None
        i = ids.index(seg)
        for j in (i + 1, i - 1):
            if 0 <= j < len(ids) and transcript.segments[j].speaker == speaker:
                return ids[j]
        return None

    def _verify(self, claims: list[tuple[str, str, str]], pack: Pack) -> list[ClaimCheck]:
        if not claims:
            return []
        index = PassageIndex.from_pack(pack)
        blocks, passage_ids = [], {}
        for i, (_, said, claim) in enumerate(claims):
            hits = index.search(f"{claim} {said}", k=self.passages_per_claim)
            lines = []
            for passage, _score in hits:
                pid = f"P{len(passage_ids) + 1}"
                passage_ids[pid] = passage
                lines.append(f"  {pid} ({passage.source}): {' '.join(passage.text.split())}")
            blocks.append(f"Claim {i}: {claim}\n  said: \"{said}\"\n" + ("\n".join(lines) or "  (no passages found)"))
        out = self.llm.complete_json(VERIFY_SYSTEM, f"Business: {pack.name}\n\n" + "\n\n".join(blocks),
                                     VERIFY_SCHEMA, "verify", max_tokens=1024)
        by_index = {c["claim_index"]: c for c in out.get("checks", []) if isinstance(c.get("claim_index"), int)}
        results = []
        for i, (seg, said, claim) in enumerate(claims):
            check = by_index.get(i, {"verdict": "unverified", "passage_id": "", "explanation": "The model gave no verdict."})
            verdict = check["verdict"]
            passage = passage_ids.get(str(check.get("passage_id", "")).strip().upper())
            sources = [SourceRef(passage.source, passage.text)] if passage and verdict != "unverified" else []
            # A verdict with no real passage behind it is passed through as-is: the gate downgrades it.
            results.append(ClaimCheck(id=f"c{i + 1}", claim=claim, verdict=verdict, evidence=Evidence([seg], said or None),
                                      sources=sources, explanation=check.get("explanation") or None))
        return results

    def _coach(self, transcript: Transcript, pack: Pack, analysis: Analysis, start: int) -> list[Finding]:
        ids = [c.id for c in pack.scorecard]
        schema = _obj({"coaching": _arr_max(_obj({
            "criterion": {"type": "string", "enum": ids}, "segment": SEG,
            "summary": _text(140), "suggestion": _text(140)}), self.max_coaching)})
        scorecard = "\n".join(f"- {c.id}: {c.title}. {c.description}" for c in pack.scorecard)
        checked = "\n".join(f"- {c.evidence.segment_ids[0]}: \"{c.claim}\" → {c.verdict}" for c in analysis.claims) or "- none"
        promises = "\n".join(f"- {c.evidence.segment_ids[0]}: {c.action} (when: {c.due_text or 'not said'})"
                             for c in analysis.commitments) or "- none"
        user = (f"Business: {pack.name}\n\nScorecard:\n{scorecard}\n\nClaim checks so far:\n{checked}\n\n"
                f"Promises made:\n{promises}\n\nTranscript:\n{render(transcript.segments, transcript, max_chars=300)}")
        out = self.llm.complete_json(COACH_SYSTEM.format(n=self.max_coaching), user, schema, "coach", max_tokens=800)
        findings = []
        for x in out.get("coaching", [])[: self.max_coaching]:
            seg = seg_id(x.get("segment", "")) or "?"
            findings.append(Finding(id=f"f{start + len(findings)}", kind="coaching", summary=x["summary"],
                                    evidence=Evidence([seg]), criterion=x["criterion"],
                                    suggestion=x["suggestion"]))
        return findings


def label_speakers(transcript: Transcript, pack: Pack, llm: LLMClient, chunk_chars: int = 3000) -> Transcript:
    """Assign rep/customer to unlabeled segments (mono recordings) from the words alone.

    Less reliable than separate audio channels; the report records which method was used.
    """
    rep = next((s for s in transcript.speakers if s.role == "rep"), None) or Speaker("S1", "rep")
    cust = next((s for s in transcript.speakers if s.role == "customer"), None) or Speaker("S2", "customer")
    speakers = [rep, cust]
    tmp = Transcript(transcript.call_id, transcript.audio, transcript.started_at,
                     speakers + [Speaker("S0", "other")], transcript.segments)
    labels: dict[str, str] = {}
    for chunk in chunk_segments(transcript.segments, chunk_chars, overlap=0):
        text = "\n".join(f"[{s.id}] {s.text}" for s in chunk)
        out = llm.complete_json(SPEAKER_SYSTEM.format(business=pack.name), text, SPEAKER_SCHEMA, "speakers", 1024)
        labels.update({seg_id(x["segment"]): x["role"] for x in out.get("labels", [])})
    segments = [
        Segment(**{**s.__dict__, "speaker": rep.id if labels.get(s.id, "CUSTOMER") == "REP" else cust.id})
        for s in tmp.segments
    ]
    return Transcript(transcript.call_id, transcript.audio, transcript.started_at, speakers, segments)
