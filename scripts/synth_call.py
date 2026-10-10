#!/usr/bin/env python3
"""Make a synthetic test recording from a Sel transcript.

The audio is synthetic text-to-speech for testing only. Each segment is read
by a Piper voice chosen from its speaker's role. Nothing here is a real call
and no real person is recorded.

Turns are laid end to end with a fixed silence between them, so they never
overlap. With --stereo the rep is on the left channel and the customer on the
right, with silence on the other channel. Without it, both go to one mono
channel. Output is 16 kHz 16-bit PCM WAV.

Usage:
    python scripts/synth_call.py transcript.json --out audio.wav \\
        --rep-voice models/piper/en_US-ryan-medium.onnx \\
        --customer-voice models/piper/en_US-lessac-medium.onnx \\
        [--stereo] [--gap 0.4] [--reference-out reference.json]
"""

from __future__ import annotations

import argparse
import copy
import io
import json
import wave
from pathlib import Path

import numpy as np
from piper import PiperVoice

OUT_RATE = 16000
ROLE_CHANNEL = {"rep": 0, "customer": 1}  # stereo channel per role


def synthesize(voice: PiperVoice, text: str) -> tuple[np.ndarray, int]:
    """Return one utterance as mono float32 samples and their sample rate."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        voice.synthesize_wav(text, wf)
    buf.seek(0)
    with wave.open(buf, "rb") as rf:
        rate = rf.getframerate()
        channels = rf.getnchannels()
        width = rf.getsampwidth()
        raw = rf.readframes(rf.getnframes())
    if width != 2:
        raise SystemExit(f"unexpected sample width from piper: {width} bytes")
    pcm = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        pcm = pcm.reshape(-1, channels).mean(axis=1)
    return pcm, rate


def resample(x: np.ndarray, src: int, dst: int) -> np.ndarray:
    """Linear-interpolation resampling. Adequate for speech test audio."""
    if src == dst or len(x) == 0:
        return x.astype(np.float32)
    n_out = int(round(len(x) * dst / src))
    positions = np.arange(n_out) * (src / dst)
    return np.interp(positions, np.arange(len(x)), x).astype(np.float32)


def build(transcript: dict, voices: dict[str, PiperVoice], gap: float,
          stereo: bool) -> tuple[np.ndarray, list[tuple[float, float]]]:
    """Synthesize every segment in order.

    Returns the (frames, channels) float32 signal and the actual (start, end)
    time in seconds of each segment, rounded to 0.01 s.
    """
    roles = {s["id"]: s["role"] for s in transcript["speakers"]}
    gap_samples = int(round(gap * OUT_RATE))
    channels = 2 if stereo else 1
    clips: list[tuple[str, np.ndarray]] = []
    for seg in transcript["segments"]:
        role = roles.get(seg["speaker"])
        if role not in voices:
            raise SystemExit(
                f"segment {seg['id']}: speaker {seg['speaker']} has role {role!r}; "
                "only 'rep' and 'customer' can be voiced")
        samples, rate = synthesize(voices[role], seg["text"])
        clips.append((role, resample(samples, rate, OUT_RATE)))

    total = sum(len(c) for _, c in clips) + gap_samples * (len(clips) - 1)
    signal = np.zeros((max(total, 0), channels), dtype=np.float32)
    timings: list[tuple[float, float]] = []
    cursor = 0
    for i, (role, clip) in enumerate(clips):
        col = ROLE_CHANNEL[role] if stereo else 0
        signal[cursor:cursor + len(clip), col] = clip
        timings.append((round(cursor / OUT_RATE, 2),
                        round((cursor + len(clip)) / OUT_RATE, 2)))
        cursor += len(clip) + (gap_samples if i < len(clips) - 1 else 0)
    return signal, timings


def write_wav(path: Path, signal: np.ndarray) -> None:
    pcm = np.clip(signal, -1.0, 1.0)
    pcm = np.round(pcm * 32767).astype("<i2")  # rows are frames, interleaved
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(signal.shape[1])
        wf.setsampwidth(2)
        wf.setframerate(OUT_RATE)
        wf.writeframes(pcm.tobytes())


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("transcript", type=Path, help="Sel transcript JSON")
    p.add_argument("--out", type=Path, required=True, help="output WAV path")
    p.add_argument("--rep-voice", type=Path, required=True,
                   help="Piper .onnx voice for rep segments")
    p.add_argument("--customer-voice", type=Path, required=True,
                   help="Piper .onnx voice for customer segments")
    p.add_argument("--stereo", action="store_true",
                   help="rep on left channel, customer on right")
    p.add_argument("--gap", type=float, default=0.4,
                   help="silence between turns, seconds (default 0.4)")
    p.add_argument("--reference-out", type=Path,
                   help="write a transcript copy with actual synthesized times")
    args = p.parse_args()

    if args.gap < 0:
        p.error("--gap must be zero or positive")

    transcript = json.loads(args.transcript.read_text(encoding="utf-8"))
    voices = {
        "rep": PiperVoice.load(str(args.rep_voice)),
        "customer": PiperVoice.load(str(args.customer_voice)),
    }

    signal, timings = build(transcript, voices, args.gap, args.stereo)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    write_wav(args.out, signal)

    duration = signal.shape[0] / OUT_RATE
    print(f"wrote {args.out}: {signal.shape[1]} ch, {OUT_RATE} Hz, "
          f"{duration:.2f} s, {len(timings)} segments")

    if args.reference_out:
        ref = copy.deepcopy(transcript)
        for seg, (start, end) in zip(ref["segments"], timings):
            seg["start"] = start
            seg["end"] = end
        ref["audio"] = args.out.name
        args.reference_out.parent.mkdir(parents=True, exist_ok=True)
        args.reference_out.write_text(
            json.dumps(ref, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {args.reference_out}")


if __name__ == "__main__":
    main()
