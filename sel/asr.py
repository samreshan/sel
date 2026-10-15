"""On-device speech recognition with faster-whisper.

Speakers come from one of two places, recorded in the report:

* **channels**: a stereo recording with each side of the call on its own
  channel (common for call-recording setups). Each channel is transcribed
  separately, so speaker labels are exact.
* **text**: a mono recording. Segments are transcribed unlabeled and the
  language model assigns rep/customer from the words
  (``analyzer_llm.label_speakers``). Less reliable; a single Whisper segment
  can also span two speakers.

Requires ``pip install faster-whisper`` (and ffmpeg).
"""

from __future__ import annotations

import json
import math
import os
import subprocess
from dataclasses import dataclass, field
from typing import Any

from .models import Segment, Speaker, Transcript
from .pack import Pack

DEFAULT_MODEL = "small"
DEFAULT_MODEL_DIR = "models/whisper"


def live_speech_available() -> tuple[bool, str | None]:
    """Whether the call simulator can transcribe speech on this machine, without importing anything heavy."""
    import importlib.util

    if importlib.util.find_spec("faster_whisper") is None:
        return False, "pip install faster-whisper to transcribe speech live; typed lines still work"
    return True, None


# Live transcription runs while people talk, so it favours speed: on 4 CPU cores "base" put a line on
# screen a median 2.2 s after the speaker stopped, "small" 6.1 s, with the same accuracy on an English
# test call (CLAUDE.md). The recording can be re-transcribed afterwards with SEL_WHISPER_MODEL.
DEFAULT_LIVE_MODEL = "base"


def live_model_name() -> str:
    return os.environ.get("SEL_LIVE_WHISPER_MODEL") or DEFAULT_LIVE_MODEL


def audio_channels(path: str) -> int:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=channels", "-of", "json", path],
            capture_output=True, text=True, check=True,
        ).stdout
        return int(json.loads(out)["streams"][0]["channels"])
    except Exception:  # noqa: BLE001 - treat unknown as mono
        return 1


def audio_duration(path: str) -> float | None:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", path],
            capture_output=True, text=True, check=True,
        ).stdout
        return float(json.loads(out)["format"]["duration"])
    except Exception:  # noqa: BLE001
        return None


@dataclass
class WhisperTranscriber:
    speakers: list[Speaker]
    model: str = field(default_factory=lambda: os.environ.get("SEL_WHISPER_MODEL", DEFAULT_MODEL))
    model_dir: str = field(default_factory=lambda: os.environ.get("SEL_WHISPER_DIR", DEFAULT_MODEL_DIR))
    compute_type: str = field(default_factory=lambda: os.environ.get("SEL_WHISPER_COMPUTE", "int8"))
    language: str | None = field(default_factory=lambda: os.environ.get("SEL_WHISPER_LANGUAGE") or None)
    rep_channel: int = 0  # for stereo recordings: which channel is the rep (0 = left)
    split_channels: bool | None = None  # None: split when the file is stereo
    beam_size: int = 5
    info: dict[str, Any] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return f"faster-whisper:{self.model}"

    def _load(self):
        try:
            from faster_whisper import WhisperModel
        except ImportError as e:
            raise RuntimeError("speech recognition needs: pip install faster-whisper") from e
        cache = getattr(WhisperTranscriber, "_models", {})
        key = (self.model, self.compute_type)
        if key not in cache:
            cache[key] = WhisperModel(self.model, device="cpu", compute_type=self.compute_type,
                                      download_root=self.model_dir, cpu_threads=os.cpu_count() or 4)
            WhisperTranscriber._models = cache  # type: ignore[attr-defined]
        return cache[key]

    def _run(self, model, audio) -> tuple[list[tuple[float, float, str, float]], str | None]:
        segments, info = model.transcribe(audio, language=self.language, beam_size=self.beam_size,
                                          vad_filter=True, condition_on_previous_text=False)
        out = [(s.start, s.end, s.text.strip(), math.exp(s.avg_logprob)) for s in segments if s.text.strip()]
        return out, getattr(info, "language", None)

    def transcribe_pcm(self, pcm: bytes, sample_rate: int = 16000) -> list[tuple[float, float, str, float]]:
        """Short live window (16-bit mono PCM) → [(start, end, text, confidence)], times relative to the window.

        Used by the call simulator. Greedy decoding keeps a window well under its
        own length on CPU; segments Whisper itself marks as probably not speech
        are dropped, since short near-silent windows are where it invents
        "Thank you." and the like.
        """
        import numpy as np

        if sample_rate != 16000:
            raise ValueError("live audio must be 16 kHz")
        audio = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        segments, _info = self._load().transcribe(
            audio, language=self.language, beam_size=1, vad_filter=True, condition_on_previous_text=False,
            without_timestamps=False)
        out = []
        for s in segments:
            text = s.text.strip()
            if not text or (s.no_speech_prob > 0.6 and s.avg_logprob < -0.7):
                continue
            out.append((s.start, s.end, text, math.exp(s.avg_logprob)))
        return out

    def transcribe(self, audio_path: str, started_at: str, pack: Pack) -> Transcript:
        from faster_whisper import decode_audio

        model = self._load()
        split = self.split_channels if self.split_channels is not None else audio_channels(audio_path) == 2
        rep = next(s for s in self.speakers if s.role == "rep")
        cust = next(s for s in self.speakers if s.role == "customer")
        rows: list[tuple[float, float, str, float, str]] = []
        languages = []
        if split:
            left, right = decode_audio(audio_path, sampling_rate=16000, split_stereo=True)
            channels = {0: left, 1: right}
            for ch, audio in channels.items():
                spk = rep.id if ch == self.rep_channel else cust.id
                segs, lang = self._run(model, audio)
                languages.append(lang)
                rows.extend((a, b, t, c, spk) for a, b, t, c in segs)
            self.info["speakers_from"] = "channels"
        else:
            segs, lang = self._run(model, decode_audio(audio_path, sampling_rate=16000))
            languages.append(lang)
            rows.extend((a, b, t, c, "S0") for a, b, t, c in segs)
            self.info["speakers_from"] = "text"
        rows.sort(key=lambda r: (r[0], r[1]))
        width = max(2, len(str(len(rows))))
        segments = [Segment(id=f"s{i + 1:0{width}d}", start=round(a, 2), end=round(b, 2), speaker=spk, text=t,
                            lang=None, confidence=round(c, 3))
                    for i, (a, b, t, c, spk) in enumerate(rows)]
        self.info["detected_languages"] = [x for x in languages if x]
        self.info["duration_s"] = audio_duration(audio_path)
        speakers = list(self.speakers) + ([] if split else [Speaker("S0", "other", "unlabeled")])
        return Transcript(call_id="", audio=audio_path, started_at=started_at, speakers=speakers, segments=segments)
