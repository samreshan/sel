# Architecture

## Pipeline

| Stage | Input → output | Built by | Status |
|---|---|---|---|
| 1. Transcribe | audio → timed text | faster-whisper, on device (`sel/asr.py`) | built; `small` by default |
| 2. Speakers | timed text → rep / customer per segment | separate channels of a stereo recording, or the language model reading the words (`label_speakers`) | built |
| 3. Analyze | `Transcript` + pack → proposed findings, claim checks, commitments | local LLM through an OpenAI-compatible API, or Claude (`sel/analyzer_llm.py`, `sel/llm.py`) | built; Qwen 2.5 7B via Ollama by default |
| 3a. Retrieval | claim → candidate pack passages | BM25 over facts, paragraphs and table rows (`sel/retrieval.py`) | built |
| 4. Evidence gate | proposals → kept + `Rejection`s | code (`sel/grounding.py`) | built |
| 5. Due dates | spoken phrase → `DueResolution` | code (`sel/dates.py`) | built (English and Nepali relative dates) |
| 6. Drafts | commitments + templates → `Draft`s; edits and approval | code (`sel/drafts.py`) | built |
| 7. Review | report → person edits, approves, dismisses, corrects the transcript | local web app (`sel/server.py`, `sel/service.py`, `sel/web/`) | built |
| Scorecard | report → each scorecard criterion met, partly, missed or left to coaching | code (`sel/scoring.py`, signals from `sel/live.py`) | built |
| Live (simulator) | lines as they are spoken → live score, next best actions; on hang-up a call through stages 3–7 | code and Whisper (`sel/simulator.py`, `sel/live.py`) | built |

`sel/pipeline.py` runs stages 1–6 and records how long each one takes. `build_report` (stages 4–6) is pure and fast; the app re-runs it whenever someone corrects the transcript or edits a draft, against the analysis the model proposed the first time, so a correction never silently re-asks the model.

## Division of labour between models and code

Small models are cheap to run locally but more likely to misquote or invent. The design limits what a model's output can do on its own:

- **The model** judges meaning and points. It decides what counts as a question, objection, commitment or claim and which segment it is in; which scorecard criterion applies; which retrieved passage decides a claim, and whether it supports or contradicts it.
- **Code** supplies every quoted word and checks everything that can be checked mechanically. Quotes are the transcript's own words; sources are the retrieved passage's own text; cited segments must exist; a commitment's owner must speak in the cited segments; a due phrase must occur in the line; a `supported` claim's numbers must be in its passage. Code also does all the date arithmetic and template filling.
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
| claim verdict | `supported` or `contradicted` with no source; source missing from the pack; quote not in the source; `supported` with a number the passage doesn't contain | downgraded to `unverified`, with the reason listed |

Quote matching ignores case, whitespace and curly-versus-straight quotes, and nothing else. A paraphrase counts as a failure on purpose: the review screen promises the user that what's shown in quotes was actually said or written. With the LLM analyzer, quotes are filled in by code, so this check mostly guards fixtures, other analyzers and transcript corrections: if a person edits a line so that a finding's words no longer occur, that finding moves to the refused list.

What the gate cannot catch: a real segment cited for the wrong reason, or a real passage that the model misjudges as supporting a claim without numbers. Those are what the evaluation measures (`false_supported` in particular).

## Model stages

**Speech.** faster-whisper on CPU with int8 weights. `small` is the default; `large-v3-turbo` was more accurate on English names in a smoke test but about 3× slower, and on a Nepali TTS clip it looped and its language auto-detection picked Gujarati. Nepali–English code-switching accuracy on real calls is still unmeasured. A stereo recording with each side on its own channel gives exact speaker labels; for mono, the language model labels lines from their content, which is weaker and recorded in the report (`speakers_from`).

**Analysis.** The model only *points*: it names segment ids and writes short summaries; string lengths and list sizes are capped in the JSON schema. This came from testing. Asked to copy quotes under schema-constrained decoding, Qwen 2.5 3B paraphrased, translated and then repeated itself until it ran out of tokens. Per chunk of about 2,400 characters there are three narrow requests (customer questions and objections; promises and the words that say when; the rep's claims), then one request judging claims against BM25-retrieved passages and one coaching request: five requests for a short call.

Code then:

- attaches the transcript's own words as each quote;
- keeps a due phrase only if those words are in the cited line (near-verbatim allowed), widened over neighbouring time words ("beluka samma" → "bholi beluka samma");
- attaches the retrieved passage itself as the claim source;
- refuses a `supported` verdict whose numbers are not in the cited passage.

Measured on the example calls (4 CPU cores, no GPU): see [running.md](running.md#measured-so-far).

**Providers.** `SEL_LLM_PROVIDER=openai` talks to any OpenAI-compatible endpoint with `response_format: json_schema` (Ollama, llama.cpp, LM Studio, vLLM, hosted APIs); a server that refuses the schema with HTTP 400 gets JSON mode with the schema in the system prompt instead, and HTTP 429 is retried after `Retry-After` (at most 30 s, three times). `SEL_LLM_PROVIDER=groq` uses the same client against Groq's hosted open-weight models (`GROQ_API_KEY`, default `openai/gpt-oss-20b` with `reasoning_effort: low`). `SEL_LLM_PROVIDER=anthropic` uses the official `anthropic` SDK with structured outputs. Hosted providers receive transcripts and pack documents; the default sends nothing off the machine.

## Without a language model

`RuleAnalyzer` (`sel/live.py`) proposes questions, objections, claims and promises from cue words in English and romanized/Devanagari Nepali, sentence and clause splitting, and BM25 over the pack. It is used when no LLM answers (simulated calls default to "the LLM if reachable, else rules") and is named as the analyzer in the report. Its proposals go through the same evidence gate, date resolution and drafts. It never gives a claim a verdict: a numbers check cannot tell "5-year warranty, extendable to 10" from "10-year warranty", so claims stay `unverified` with the closest pack passage attached, and a claim whose numbers appear in no nearby passage is flagged.

## Scorecard

Five signals can be measured by code: discovery (questions before the price), objections (answered with a question first), claims (conflicts with the pack, or verdicts after review), next step (follow-ups with a fixed day) and talk ratio. A pack's criterion names one (`signal = "discovery"`) or it is inferred from the criterion's title or description; criteria without one are left to coaching. The same grader scores a call live in the simulator and a reviewed report afterwards (`report.json` → `score`).

## Call simulator

Two browser sessions join a room on the Sel server. Audio goes browser to browser over WebRTC; the server relays only the connection setup (offer, answer, ICE candidates) through each tab's event stream. The rep's browser records stereo (rep left, customer right). Each browser streams its own microphone as 16 kHz PCM; a pause detector (`PcmSegmenter`) cuts utterances and one worker runs Whisper on them, so speaker labels are exact. Typed lines and opt-in browser captions feed the same transcript. Every new line re-runs the rules and the grade, and both tabs get the score and the next best actions. Hanging up uploads the recording; once queued speech is transcribed, the hub files a call (`source: simulator`) whose job reads the live transcript and runs stages 3–6. Rooms live in memory.

## App and storage

The review app is a standard-library HTTP server bound to 127.0.0.1 with a vanilla-JS page (no build step, no external resources, works offline). Each tab keeps one Server-Sent Events stream (`/api/events`) for call updates and simulator traffic instead of polling; static files are content-hashed and cached, API reads carry ETags, and recordings and uploads stream through disk. CLAUDE.md has the measurements. Each call is a folder: the recording, the transcript including corrections, the model's proposed analysis, the review log (dismissals, draft edits, approvals, transcript edits) and the rendered report. Jobs run one at a time because speech and language models share the CPU. Packaging as a desktop app (Tauri or Electron around the same server) is still open; see [decisions.md](decisions.md).
