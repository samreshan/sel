"""Interfaces for the model-backed stages, plus fixture implementations.

The final speech and language models have not been chosen. The pipeline only
depends on these two small protocols, so candidates can be swapped in and
measured with the same evaluation harness.

The ``Fixture*`` classes read hand-written JSON. They let the rest of the
workflow (grounding, date resolution, drafts, review UI, evaluation) be built
and tested before any model is integrated. They are not AI and must never be
presented as Sel's results.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from .models import Analysis, Transcript
from .pack import Pack


class Transcriber(Protocol):
    name: str

    def transcribe(self, audio_path: str, started_at: str, pack: Pack) -> Transcript:
        """Speech → speaker-labelled, timestamped segments. Runs on-device."""
        ...


class Analyzer(Protocol):
    name: str

    def analyze(self, transcript: Transcript, pack: Pack) -> Analysis:
        """Transcript + pack → proposed findings, claim checks and commitments.

        Implementations must copy quotes and due phrases verbatim from the
        transcript and cite segment ids; Sel verifies both afterwards.
        """
        ...


class FixtureTranscriber:
    name = "fixture-transcript"

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def transcribe(self, audio_path: str, started_at: str, pack: Pack) -> Transcript:
        return Transcript.from_dict(json.loads(self.path.read_text(encoding="utf-8")))


class StoredTranscriber:
    """A transcript that already exists, e.g. captured live by the call simulator. Not a fixture."""

    def __init__(self, path: str | Path, name: str = "live transcript"):
        self.path = Path(path)
        self.name = name

    def transcribe(self, audio_path: str, started_at: str, pack: Pack) -> Transcript:
        return Transcript.from_dict(json.loads(self.path.read_text(encoding="utf-8")))


class FixtureAnalyzer:
    name = "fixture-analysis"

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def analyze(self, transcript: Transcript, pack: Pack) -> Analysis:
        return Analysis.from_dict(json.loads(self.path.read_text(encoding="utf-8")))
