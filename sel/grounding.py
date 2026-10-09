"""The evidence gate between an analyzer's proposals and what a user sees.

An analyzer (in practice a small language model) can misquote a call, cite a
document passage that isn't there, or point at the wrong speaker. Nothing it
proposes reaches the review screen until it passes these checks:

* every item cites at least one real transcript segment;
* a quote, when given, appears verbatim (after whitespace/case folding) in
  the cited segments;
* a commitment's owner is a known speaker who actually spoke in a cited segment;
* a ``supported``/``contradicted`` verdict cites at least one pack source, and
  every cited quote appears in that source.

Failures are not silently dropped: findings and commitments go to
``rejected`` with reasons, and a claim whose sources don't check out is
downgraded to ``unverified`` rather than shown with a verdict Sel can't back.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import replace

from .models import Analysis, ClaimCheck, Evidence, Rejection, Transcript
from .pack import Pack


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text).casefold()
    text = re.sub(r"[‘’]", "'", text)
    text = re.sub(r"[“”]", '"', text)
    return re.sub(r"\s+", " ", text).strip()


def check_evidence(evidence: Evidence, transcript: Transcript) -> list[str]:
    if not evidence.segment_ids:
        return ["cites no transcript segment"]
    missing = [sid for sid in evidence.segment_ids if transcript.segment(sid) is None]
    if missing:
        return [f"cites unknown segment(s) {', '.join(missing)}"]
    if evidence.quote:
        spoken = " ".join(transcript.segment(sid).text for sid in evidence.segment_ids)  # type: ignore[union-attr]
        if normalize(evidence.quote) not in normalize(spoken):
            return [f"quote {evidence.quote!r} does not appear in {', '.join(evidence.segment_ids)}"]
    return []


def check_sources(claim: ClaimCheck, pack: Pack) -> list[str]:
    problems: list[str] = []
    if claim.verdict != "unverified" and not claim.sources:
        problems.append(f"verdict {claim.verdict!r} cites no business-pack source")
    for ref in claim.sources:
        text = pack.source_text(ref.source)
        if text is None:
            problems.append(f"source {ref.source!r} is not in pack {pack.id!r}")
        elif normalize(ref.quote) not in normalize(text):
            problems.append(f"quote {ref.quote!r} does not appear in {ref.source}")
    return problems


def ground(analysis: Analysis, transcript: Transcript, pack: Pack) -> tuple[Analysis, list[Rejection]]:
    """Return the subset of ``analysis`` that is backed by evidence, plus what was refused."""
    rejected: list[Rejection] = []
    kept = Analysis()

    for f in analysis.findings:
        reasons = check_evidence(f.evidence, transcript)
        if f.kind == "coaching" and f.criterion and pack.criterion(f.criterion) is None:
            reasons.append(f"criterion {f.criterion!r} is not in the pack scorecard")
        if reasons:
            rejected.append(Rejection(f.id, f"finding:{f.kind}", reasons))
        else:
            kept.findings.append(f)

    for c in analysis.commitments:
        reasons = check_evidence(c.evidence, transcript)
        if transcript.speaker(c.owner) is None:
            reasons.append(f"owner {c.owner!r} is not a speaker in this call")
        elif not reasons and all(transcript.segment(s).speaker != c.owner for s in c.evidence.segment_ids):  # type: ignore[union-attr]
            reasons.append(f"owner {c.owner!r} does not speak in the cited segment(s)")
        if reasons:
            rejected.append(Rejection(c.id, "commitment", reasons))
        else:
            kept.commitments.append(c)

    for claim in analysis.claims:
        reasons = check_evidence(claim.evidence, transcript)
        if reasons:
            rejected.append(Rejection(claim.id, "claim", reasons))
            continue
        source_problems = check_sources(claim, pack)
        if source_problems:
            rejected.append(Rejection(claim.id, "claim-verdict", source_problems))
            claim = replace(
                claim,
                verdict="unverified",
                sources=[],
                explanation="Sel could not confirm the cited source, so no verdict is shown.",
            )
        kept.claims.append(claim)

    return kept, rejected
