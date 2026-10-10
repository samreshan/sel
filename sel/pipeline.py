"""Recording → transcript → analysis → evidence gate → dates → drafts → report.

The report is what the review screen renders. Nothing in it has been sent or
scheduled; drafts wait for a person to approve them.

``build_report`` is everything after the model: it is pure and fast, so it is
re-run whenever a person corrects the transcript, against the analysis the
model proposed the first time.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from .adapters import Analyzer, Transcriber
from .dates import resolve_due
from .drafts import build_drafts
from .grounding import ground
from .models import Analysis, CallReport, Transcript
from .pack import Pack

Progress = Callable[[str, float], None]


def build_report(transcript: Transcript, proposed: Analysis, pack: Pack, meta: dict[str, Any] | None = None) -> CallReport:
    grounded, rejected = ground(proposed, transcript, pack)
    # Due dates are always recomputed from the spoken phrase; any date the
    # analyzer supplied is discarded.
    commitments = [
        replace(c, due=resolve_due(c.due_text, transcript.started_at, pack.week_starts_on))
        for c in grounded.commitments
    ]
    return CallReport(
        pack_id=pack.id,
        transcript=transcript,
        findings=sorted(grounded.findings, key=lambda f: _first_start(transcript, f.evidence.segment_ids)),
        claims=grounded.claims,
        commitments=commitments,
        drafts=build_drafts(commitments, transcript, pack),
        rejected=rejected,
        meta=dict(meta or {}),
    )


def analyze_call(
    audio_path: str,
    started_at: str,
    pack: Pack,
    transcriber: Transcriber,
    analyzer: Analyzer,
    call_id: str | None = None,
    speaker_labeler: Callable[[Transcript, Pack], Transcript] | None = None,
    progress: Progress | None = None,
) -> tuple[CallReport, Analysis]:
    def report(stage: str, frac: float) -> None:
        if progress:
            progress(stage, frac)

    timings: dict[str, float] = {}

    report("transcribing", 0.05)
    t = time.perf_counter()
    transcript = transcriber.transcribe(audio_path, started_at, pack)
    if call_id:
        transcript = replace(transcript, call_id=call_id)
    timings["transcribe_s"] = time.perf_counter() - t

    if any(s.speaker not in {sp.id for sp in transcript.speakers if sp.role != "other"} for s in transcript.segments):
        if speaker_labeler is None:
            raise ValueError("transcript has unlabeled segments and no speaker labeler was given")
        report("labelling speakers", 0.35)
        t = time.perf_counter()
        transcript = speaker_labeler(transcript, pack)
        timings["speakers_s"] = time.perf_counter() - t

    report("analysing", 0.45)
    t = time.perf_counter()
    proposed = analyzer.analyze(transcript, pack)
    timings["analyze_s"] = time.perf_counter() - t

    report("checking evidence", 0.95)
    t = time.perf_counter()
    meta: dict[str, Any] = {"transcriber": transcriber.name, "analyzer": analyzer.name}
    result = build_report(transcript, proposed, pack, meta)
    timings["post_process_s"] = time.perf_counter() - t
    result.meta["timings"] = {k: round(v, 3) for k, v in timings.items()}
    for source in (transcriber, analyzer):
        extra = getattr(source, "info", None) or getattr(source, "stats", None)
        if extra:
            result.meta.setdefault("details", {}).update(extra)
    llm = getattr(analyzer, "llm", None)
    if llm is not None:
        result.meta["llm_usage"] = llm.stats.to_dict()
    return result, proposed


def run(audio_path: str, started_at: str, pack: Pack, transcriber: Transcriber, analyzer: Analyzer) -> CallReport:
    return analyze_call(audio_path, started_at, pack, transcriber, analyzer)[0]


def _first_start(transcript: Transcript, segment_ids: list[str]) -> float:
    starts = [s.start for sid in segment_ids if (s := transcript.segment(sid))]
    return min(starts, default=0.0)
