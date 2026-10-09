"""Recording → transcript → analysis → evidence gate → dates → drafts → report.

The report is what the review screen renders. Nothing in it has been sent or
scheduled; drafts wait for a person to approve them.
"""

from __future__ import annotations

import time
from dataclasses import replace

from .adapters import Analyzer, Transcriber
from .dates import resolve_due
from .drafts import build_drafts
from .grounding import ground
from .models import CallReport
from .pack import Pack


def run(
    audio_path: str,
    started_at: str,
    pack: Pack,
    transcriber: Transcriber,
    analyzer: Analyzer,
) -> CallReport:
    timings: dict[str, float] = {}

    t = time.perf_counter()
    transcript = transcriber.transcribe(audio_path, started_at, pack)
    timings["transcribe_s"] = time.perf_counter() - t

    t = time.perf_counter()
    proposed = analyzer.analyze(transcript, pack)
    timings["analyze_s"] = time.perf_counter() - t

    t = time.perf_counter()
    grounded, rejected = ground(proposed, transcript, pack)
    # Due dates are always recomputed from the spoken phrase; any date the
    # analyzer supplied is discarded.
    commitments = [
        replace(c, due=resolve_due(c.due_text, transcript.started_at, pack.week_starts_on))
        for c in grounded.commitments
    ]
    drafts = build_drafts(commitments, transcript, pack)
    timings["post_process_s"] = time.perf_counter() - t

    return CallReport(
        pack_id=pack.id,
        transcript=transcript,
        findings=sorted(grounded.findings, key=lambda f: _first_start(transcript, f.evidence.segment_ids)),
        claims=grounded.claims,
        commitments=commitments,
        drafts=drafts,
        rejected=rejected,
        meta={
            "transcriber": transcriber.name,
            "analyzer": analyzer.name,
            "timings": {k: round(v, 4) for k, v in timings.items()},
        },
    )


def _first_start(transcript, segment_ids: list[str]) -> float:
    starts = [s.start for sid in segment_ids if (s := transcript.segment(sid))]
    return min(starts, default=0.0)
