# Running Sel

Sel runs as a small local web app. Its parts:

| Part | Default | Alternatives |
|---|---|---|
| Speech recognition | faster-whisper `small`, CPU, int8 | `SEL_WHISPER_MODEL=large-v3-turbo` (more accurate on English, about 3× slower, unreliable on Nepali in our tests) |
| Analysis model | Qwen 2.5 7B through Ollama, on the same machine | Any OpenAI-compatible server (llama.cpp `llama-server`, LM Studio, vLLM) or hosted API; or Claude through the Anthropic SDK |
| Review app | `python -m sel serve` at http://127.0.0.1:8765 | — |
| Storage | one folder per call under `calls/` | `SEL_CALLS_DIR` |

Nothing leaves the machine with the defaults. Pointing `SEL_LLM_BASE_URL` or `SEL_LLM_PROVIDER` at a hosted service sends transcripts and business documents to that service.

## One-time setup (Linux, CPU only)

```bash
scripts/setup_local.sh
```

The script creates `.venv/` with faster-whisper and Piper, downloads the Whisper model and two Piper test voices into `models/`, installs Ollama into `.tools/ollama/` (no root, no system service), starts it, and pulls `qwen2.5:7b` (4.7 GB; about 6 GB of RAM while running). Unpacking Ollama's `.tar.zst` archive needs `zstd`.

On macOS or Windows, install Ollama from https://ollama.com, run `ollama pull qwen2.5:7b`, and `pip install -e ".[speech]"`.

Note: faster-whisper 1.2.1 fails with PyAV 19 (`open() got an unexpected keyword argument 'metadata_errors'`). The `speech` extra pins `av==17.1.0`.

## Start the app

```bash
.venv/bin/python -m sel serve
```

Open http://127.0.0.1:8765, choose **New call**, pick the recording, the business pack and when the call started, and say whether the recording is stereo with one side per channel (best) or mono. Processing runs in the background, one call at a time; the page shows progress.

The two example calls (`example-solar-01`, `example-pos-01`) load from hand-written fixture data so the screens can be explored without models. `--no-examples` skips them.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `SEL_LLM_PROVIDER` | `openai` | `openai` = any OpenAI-compatible `/chat/completions` endpoint; `anthropic` = Claude API (`pip install -e ".[anthropic]"`, `ANTHROPIC_API_KEY`) |
| `SEL_LLM_BASE_URL` | `http://127.0.0.1:11434/v1` | Ollama's OpenAI-compatible endpoint |
| `SEL_LLM_MODEL` | `qwen2.5:7b` (`claude-opus-5-5` for `anthropic`) | model name on that server |
| `SEL_LLM_API_KEY` | — | bearer token for hosted OpenAI-compatible APIs |
| `SEL_LLM_EFFORT` | — | `anthropic` only: `low` … `max` |
| `SEL_WHISPER_MODEL` | `small` | any faster-whisper model name |
| `SEL_WHISPER_DIR` | `models/whisper` | where Whisper models are cached |
| `SEL_WHISPER_LANGUAGE` | auto-detect | `en`, `ne`, …; set it, auto-detection misfired on short Nepali clips |
| `SEL_CALLS_DIR` | `calls` | where calls are stored |

## Command line

```bash
# Full pipeline on a recording, without the web app
.venv/bin/python -m sel analyze call.wav --pack packs/demo-pos \
    --started-at 2026-10-08T15:00:00+05:45 --channels split --rep-name Bikash \
    --customer-name Mina --customer-email mina@example.com --out report.json

# Analysis only, on an existing transcript (skips speech recognition)
python -m sel analyze examples/calls/solar-01/transcript.json --pack packs/demo-solar \
    --started-at 2026-10-07T14:00:00+05:45 --transcript examples/calls/solar-01/transcript.json --out report.json

# Score a saved report against the hand annotations (matches by time when the transcript came from audio)
python -m sel eval examples/calls/solar-01 --pack packs/demo-solar --report report.json

# Make a synthetic stereo test call from a transcript (text-to-speech; for testing only)
.venv/bin/python scripts/synth_call.py examples/calls/pos-02/transcript.json --stereo \
    --out /tmp/pos-02.wav --reference-out /tmp/pos-02.reference.json \
    --rep-voice models/piper/en_US-ryan-medium.onnx --customer-voice models/piper/en_US-lessac-medium.onnx
```

## How the analysis uses the model

Per chunk of the call (about 2,400 characters), three narrow requests: customer questions and objections; promises with the words that say *when*; the rep's factual claims. Then one request judging all claims against passages that keyword search (BM25) found in the business pack, and one coaching request against the pack's scorecard. A 90-second call is five requests.

The model only points at segment ids and writes short summaries. Sel attaches the exact transcript words, the exact passage text, and resolves dates in code. See [architecture.md](architecture.md).

## Measured so far

Machine: 4 virtual CPU cores, 15 GB RAM, no GPU. Whisper `small` (int8), Ollama 0.40.2. Calls: two **synthetic** stereo recordings made with Piper text-to-speech from the example transcripts, scored with `sel eval` against reference annotations written together with the test scripts. This is a smoke test of the whole system, not a benchmark: two calls, clean TTS audio, no independent annotators.

| | pos-02 (English) | solar-01 (romanized Nepali read by an English TTS voice) |
|---|---|---|
| Length | 86 s | 68 s |
| Speech recognition | 54 s | 40 s |
| Analysis (Qwen 2.5 7B, 5 requests) | 359 s | 322 s |
| Customer questions found | 3 of 3, no extras | 2 of 3, 1 extra |
| Objections found | 1 of 1 | 1 of 1, 1 extra |
| Commitments found | 4 of 4, no extras | 3 of 4, no extras |
| Due date right | 3 of 4 (missed "confirm by Saturday") | 0 of 3: Whisper wrote "Bully-Belukasama" for *bholi beluka samma* and "Sukrabhar" for *sukrabar*, so Sel flagged every date for review |
| Invented dates | 0 | 0 |
| Claims found | 5 of 5, 1 extra | 3 of 5, 1 extra |
| Verdict right (of claims found) | 4 of 5 (14-day trial left "unverified") | 1 of 3 |
| False "supported" | 0 | 0 (the number check refused one) |

Qwen 2.5 **3B** on the same pos-02 transcript ran in about 3 minutes but found 0 of 3 questions, 8 "commitments" of which 5 were not promises ("I understand", "okay"), and no correct verdicts, so 7B is the default. On the hand-typed solar-01 transcript (no speech recognition), 3B found 4 of 5 claims with correct verdicts, but got only 1 of 3 due dates right.

These runs predate one later fix: a claim the model pins on the customer's question now moves to the rep's answer next to it (the kitchen-order-ticket claim in pos-02 was shown at the question). Scores match by time with a tolerance, so this does not change the counts above.

What this says so far:

- On clean English audio the whole loop works end to end, and the safeguards hold: no invented dates, no false "supported".
- Nepali speech recognition is the bottleneck. Sel fails safe (asks instead of guessing), but the follow-ups are only useful once dates are transcribed correctly. Next step: real or re-enacted Nepali–English recordings and a Nepali-capable speech model.
- About 7 minutes per 90-second call on 4 CPU cores. A laptop with more cores, a GPU or Apple Silicon will be faster; a smaller model is faster but much worse.

To reproduce: start the app, upload `examples/calls/pos-02/audio.ogg` (pack `demo-pos`, start 2026-10-08 15:00 +05:45, stereo, rep on the left), copy the call's `report.json` out of `calls/<id>/`, and run `python -m sel eval examples/calls/pos-02 --pack packs/demo-pos --report report.json`.
