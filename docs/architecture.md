# Architecture

## Pipeline

| Stage | Input → output | Built by | Status |
|---|---|---|---|
| 1. Transcribe | audio → timed text | speech model, on device | not integrated (`Transcriber` protocol) |
| 2. Diarize and align | audio + text → `Segment`s with speaker and start/end | diarization model, or a two-channel recording | not integrated |
| 3. Analyze | `Transcript` + pack → proposed `Finding`, `ClaimCheck`, `Commitment` | small language model, on device | not integrated (`Analyzer` protocol) |
| 4. Evidence gate | proposals → kept + `Rejection`s | code (`sel/grounding.py`) | built |
| 5. Due dates | spoken phrase → `DueResolution` | code (`sel/dates.py`) | built (English and Nepali relative dates) |
| 6. Drafts | commitments + templates → `Draft`s | code (`sel/drafts.py`) | built |
| 7. Review | `CallReport` → user approves, edits or dismisses | UI | not started |

`sel/pipeline.py` runs stages 1–6 and records how long each one takes.

## Division of labour between models and code

Small models are cheap to run locally but more likely to misquote or invent. The design limits what a model's output can do on its own:

- **The model** extracts spans and judges meaning. It decides what counts as a question, objection, commitment or claim; which scorecard criterion applies; which pack passage is relevant; and whether that passage supports or contradicts the claim.
- **Code** checks everything that can be checked mechanically. Cited segments must exist, quotes must appear in them, a commitment's owner must speak in the cited segments, and source quotes must appear in the cited pack document or fact. Code also does all the date arithmetic and template filling.
- **The person** approves every draft and can dismiss any finding.

Any date the model proposes is discarded and worked out again from `due_text`.

## Data contracts

All defined in `sel/models.py`:

- `Transcript`: `call_id`, `started_at` (ISO 8601 with an offset, needed to resolve "bholi"), `speakers` (with a role of rep or customer), and `segments`
- `Evidence`: `segment_ids` plus an optional verbatim `quote`. This drives jump-to-audio.
- `Finding`: `question`, `objection` or `coaching`. A coaching finding cites a scorecard `criterion` and includes a `suggestion`.
- `ClaimCheck`: `supported`, `contradicted` or `unverified`, plus `SourceRef`s (`docs/<file>` or `fact:<id>`, each with a verbatim quote)
- `Commitment`: `owner`, `action`, `channel` (email, call, meeting, visit or other), `due_text` (verbatim) and `due` (computed)
- `Draft`: `email` or `calendar`; `ready_for_approval` or `needs_input`; a list of `missing` fields; and an `.ics` file when complete
- `CallReport`: everything above, plus `rejected` items and `meta` (stage names and timings)

## Evidence gate rules

| Proposal | Fails when | Result |
|---|---|---|
| any item | no segment cited, an unknown segment is cited, or the quote isn't in the cited segments | rejected and listed |
| coaching finding | the criterion isn't in the pack's scorecard | rejected |
| commitment | the owner isn't a speaker, or doesn't speak in the cited segments | rejected |
| claim verdict | `supported` or `contradicted` with no source; source missing from the pack; quote not in the source | downgraded to `unverified`, with the reason listed |

Quote matching ignores case, whitespace and curly-versus-straight quotes, and nothing else. A paraphrase counts as a failure on purpose: the review screen promises the user that what's shown in quotes was actually said or written.

## Model stages

No model has been selected or validated. These are the candidates to measure with `sel eval` on the same calls:

- **Speech recognition:** the Whisper family (for example large-v3 or large-v3-turbo through whisper.cpp or faster-whisper), plus any Nepali fine-tunes with suitable licences. The open question is accuracy on Nepali–English code-switching and on romanized versus Devanagari output. The transcript format must be decided before analysis prompts are written.
- **Diarization:** pyannote-style diarization, compared against the simpler route of recording each side on its own channel where the phone setup allows it.
- **Analysis:** instruction-tuned open-weight models of roughly 3–8B parameters, run through llama.cpp or Ollama with constrained JSON output. Run one extraction task per prompt (questions, commitments, claims) rather than one large prompt, so that failures can be measured separately.
- **Retrieval for claim checks:** start with keyword (BM25) search over pack passages, which needs no model. Add a small multilingual embedding model only if measurements show keyword search misses.

The laptop to benchmark on (CPU, RAM, GPU or none) should be fixed before any timing is published.

## Not decided

The desktop shell (Tauri, Electron or a local web server), packaging, and the UI stack are all open. The engine is a plain Python package with JSON in and out, so it can sit behind any of them. See [decisions.md](decisions.md).
