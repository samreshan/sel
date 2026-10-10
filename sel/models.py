"""Data contracts shared by every pipeline stage.

Everything Sel shows a user is one of these records. The rule that ties them
together: a finding, claim or commitment must carry ``Evidence`` that points
at transcript segments, and a claim verdict other than ``unverified`` must
carry at least one ``SourceRef`` into the business pack.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Role = Literal["rep", "customer", "other"]
FindingKind = Literal["question", "objection", "coaching"]
Verdict = Literal["supported", "contradicted", "unverified"]
Channel = Literal["email", "call", "meeting", "visit", "other"]
DueStatus = Literal["exact", "ambiguous", "missing"]
DraftKind = Literal["email", "calendar"]
DraftStatus = Literal["ready_for_approval", "needs_input", "approved"]


@dataclass
class Speaker:
    id: str
    role: Role
    name: str | None = None
    email: str | None = None  # entered by the user, never inferred from audio


@dataclass
class Segment:
    id: str
    start: float  # seconds from start of recording
    end: float
    speaker: str  # Speaker.id
    text: str
    lang: str | None = None  # e.g. "ne", "en", "ne-en" (code-switched)
    confidence: float | None = None


@dataclass
class Transcript:
    call_id: str
    audio: str | None
    started_at: str  # ISO 8601 with UTC offset, e.g. 2026-10-07T14:00:00+05:45
    speakers: list[Speaker]
    segments: list[Segment]

    def segment(self, seg_id: str) -> Segment | None:
        return next((s for s in self.segments if s.id == seg_id), None)

    def speaker(self, speaker_id: str) -> Speaker | None:
        return next((s for s in self.speakers if s.id == speaker_id), None)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Transcript:
        return cls(
            call_id=d["call_id"],
            audio=d.get("audio"),
            started_at=d["started_at"],
            speakers=[Speaker(**s) for s in d["speakers"]],
            segments=[Segment(**s) for s in d["segments"]],
        )


@dataclass
class Evidence:
    """A pointer to the call moment that supports a finding."""

    segment_ids: list[str]
    quote: str | None = None  # verbatim span from those segments


@dataclass
class SourceRef:
    """A pointer into the business pack: a document path or a fact id."""

    source: str  # "docs/warranty.md" or "fact:panel-warranty"
    quote: str  # verbatim span from that source


@dataclass
class Finding:
    id: str
    kind: FindingKind
    summary: str
    evidence: Evidence
    criterion: str | None = None  # scorecard criterion id, for coaching
    suggestion: str | None = None  # what to try next time, for coaching


@dataclass
class ClaimCheck:
    id: str
    claim: str
    verdict: Verdict
    evidence: Evidence
    sources: list[SourceRef] = field(default_factory=list)
    explanation: str | None = None


@dataclass
class DueResolution:
    status: DueStatus
    date: str | None = None  # ISO date, set only when status == "exact"
    time: str | None = None  # HH:MM, set only when the time was stated unambiguously
    part_of_day: str | None = None  # "morning" | "afternoon" | "evening" | "night"
    window_start: str | None = None  # ISO date range for an ambiguous due phrase
    window_end: str | None = None
    reason: str | None = None


@dataclass
class Commitment:
    id: str
    owner: str  # Speaker.id of the person who promised
    action: str
    channel: Channel
    evidence: Evidence
    due_text: str | None = None  # the words used in the call, verbatim
    due: DueResolution | None = None  # filled in by Sel, never by the model


@dataclass
class Draft:
    id: str
    kind: DraftKind
    commitment_id: str
    status: DraftStatus
    fields: dict[str, str]
    missing: list[str] = field(default_factory=list)
    ics: str | None = None


@dataclass
class Rejection:
    """An item the analyzer proposed that Sel refused to show as fact."""

    item_id: str
    item_type: str
    reasons: list[str]


@dataclass
class Analysis:
    """What an analyzer (an LLM, or a fixture) proposes for one call."""

    findings: list[Finding] = field(default_factory=list)
    claims: list[ClaimCheck] = field(default_factory=list)
    commitments: list[Commitment] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Analysis:
        def ev(x: dict[str, Any]) -> Evidence:
            return Evidence(**x)

        return cls(
            findings=[Finding(**{**f, "evidence": ev(f["evidence"])}) for f in d.get("findings", [])],
            claims=[
                ClaimCheck(
                    **{
                        **c,
                        "evidence": ev(c["evidence"]),
                        "sources": [SourceRef(**s) for s in c.get("sources", [])],
                    }
                )
                for c in d.get("claims", [])
            ],
            commitments=[
                Commitment(
                    **{
                        **{k: v for k, v in c.items() if k != "due"},
                        "evidence": ev(c["evidence"]),
                        "due": DueResolution(**c["due"]) if c.get("due") else None,
                    }
                )
                for c in d.get("commitments", [])
            ],
        )


@dataclass
class CallReport:
    pack_id: str
    transcript: Transcript
    findings: list[Finding]
    claims: list[ClaimCheck]
    commitments: list[Commitment]
    drafts: list[Draft]
    rejected: list[Rejection]
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
