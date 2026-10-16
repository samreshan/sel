# Sel

Sel is an open-source assistant that runs on your own device and helps you review recorded sales calls. Give it a recording and it should produce:

- a transcript with speaker labels and timestamps,
- a few specific coaching points,
- a check of product claims against the business's own documents,
- a record of who promised what, and by when,
- follow-up email and calendar drafts that nobody sends until a person approves them.

It also has a **call simulator**: two open Sel sessions (two windows, or two computers on the same network) call each other, Sel records the call, transcribes and scores it live against the business's scorecard with next best actions, and when you hang up it files the call with its follow-up drafts set up and waiting for approval.

It is meant to help a small team answer three questions after every call:

1. **What happened?**
2. **What could I do better?**
3. **What did I promise to do next?**

Every finding links back to the moment in the call that supports it. If the call doesn't support a conclusion, Sel says so and does not guess.

> **Status: working prototype, measured only on two synthetic test calls.** The full pipeline runs locally: upload a recording, get a transcript (faster-whisper), analysis by a local open-weight model (Qwen 2.5 7B through Ollama), claims checked against the business pack, follow-up drafts, and a review screen to correct, approve or dismiss. On a 90-second English test call it found every question, objection and promise and invented no dates. Nepali speech recognition is the clear weak point, and nothing has been measured on real calls yet. See [docs/running.md](docs/running.md#measured-so-far).

## Why Sel

Open-source tools that transcribe calls, score sales conversations or pull out action items already exist. Sel aims to stand out through this combination:

| Goal | Status |
|---|---|
| Fully local processing with lightweight open-weight models | Works on CPU: Whisper `small` + Qwen 2.5 7B, about 7 minutes for a 90-second call on 4 cores. Hosted APIs are optional |
| Calls that mix Nepali and English | Nepali date phrases handled in code. Speech recognition of Nepali is weak in tests so far |
| Every finding traceable to an exact call moment | Every card jumps to the audio; quotes are the transcript's own words |
| Product claims checked against the business's own documents | BM25 retrieval + model verdict + number check; no false "supported" in tests so far |
| Follow-ups based only on spoken commitments, approved before action | Built. Unclear dates become "needs input"; nothing is ever sent |
| Published accuracy and speed numbers | Harness built; first numbers on two synthetic calls. Real calls still needed |

## Run it

The engine and review app need only Python 3.11+. The models are installed separately:

```bash
scripts/setup_local.sh          # Linux: .venv with faster-whisper, Ollama in .tools/, qwen2.5:7b
.venv/bin/python -m sel serve   # http://127.0.0.1:8765
```

Then choose **New call**, pick a recording and a business pack, and wait for the analysis. Details, configuration (including other local servers and Groq-hosted open-weight models) and the command-line tools are in [docs/running.md](docs/running.md). `python -m sel doctor` shows every model Sel will use, where it runs and its licence; see [MODELS.md](MODELS.md).

To try the **call simulator**, open http://127.0.0.1:8765/#sim in two windows: start a call in one, join it from the other, turn on the microphones (or type lines) and talk. Without faster-whisper installed people type their lines; without a language model the filed call is analysed by Sel's rules. Across two computers, serve over https (`--host 0.0.0.0 --tls-cert … --tls-key …`) so browsers allow the microphone. See [docs/running.md](docs/running.md#call-simulator).

Without any models, you can still explore the screens and the logic using the hand-written example calls:

```bash
python -m sel serve                                   # loads example-solar-01 and example-pos-01
python -m sel demo examples/calls/solar-01 --pack packs/demo-solar
python -m sel eval examples/calls/solar-01 --pack packs/demo-solar
python -m sel eval examples/calls/solar-01 --pack packs/demo-solar --analyzer rules   # Sel's code-only analyzer
python -m unittest discover -s tests -t .
```

The examples use two unrelated, fictional businesses: a rooftop-solar installer and restaurant point-of-sale software. Only the business pack changes between them. They show the behaviour Sel is built around:

- **A date was agreed, so Sel drafts.** *"bholi beluka samma … email garchu"* ("I'll email by tomorrow evening") becomes an email draft due 2026-10-08, ready for approval.
- **No date was agreed, so Sel asks.** *"Sometime next week hamro engineer pathaula"* becomes a calendar draft marked `needs_input`, with the reason and the possible window (11–17 Oct).
- **Invented evidence is refused.** Words nobody said, a source passage that isn't in the pack, or a "supported" verdict whose numbers aren't in its passage are refused or downgraded, and listed under **Refused**.

## How it works

```
recording → local transcription → speakers + timestamps → call analysis
         → evidence gate → claim checks against the pack → due-date resolution
         → follow-up drafts → human review
```

- **The model points; code supplies the words.** The language model names which transcript segment holds each question, promise or claim. Sel attaches the transcript's exact words, the business document's exact passage, and refuses anything it can't tie back.
- **Code does dates.** The model names the words that say *when*; Sel checks they were actually said, then `sel/dates.py` resolves them relative to when the call happened: `exact`, `ambiguous` (with a reason and a window) or `missing`.
- **Nothing is sent.** Drafts are `ready_for_approval` or `needs_input`, and any missing fields are listed. Sel has no code path that sends an email or creates an event.

See [docs/architecture.md](docs/architecture.md).

## How the AI works

Sel's product is a **coaching loop**, and every step of it is an open-weight
model whose output is consumed by code, not just shown:

```
real call → DIAGNOSE (moments, better line, gate) → FOCUS (one skill) → PRACTISE (AI customer)
          → PREPARE (brief) → PERFORM (next real call) → MEASURE (did the skill transfer?)
```

| Agent | Reads | Returns (JSON) | Consumed by | Status |
|---|---|---|---|---|
| Analyst | transcript + pack | questions, objections, claims, promises as segment ids | evidence gate → drafts, scorecard | built |
| Moment finder | report + scorecard grade | weak and strong moments per criterion, segment ids | `gate_moments` → coaching.json, Coaching tab, the Coach | built |
| Line writer (RAG) | a weak moment + BM25 pack passages | a better line + passage ids | `check_line` (code) → shown with its passage, or refused with the reason | built |
| Coach | the rep's moments, drills and transfer checks | actions from a whitelist: set_focus, create_drill, raise_difficulty, close_focus, escalate_to_manager | `apply_actions` validates each against state and evidence → Today, Practice | built |
| Persona builder | a weak moment, anonymised by code | a short customer persona | the AI customer's system prompt | built |
| AI customer | the persona + the drill so far | `{say, end_call, mood}` | the practice room as the customer (rules customer if the model fails) | built |
| Judge | a drill transcript, or a newly filed real call | pass / fail / not applicable + a segment id | debrief; transfer check → the Coach closes the focus (needs the rules to agree) | built |
| Librarian, Prep writer | strong moments; a lead's past reports | playbook answers; a brief | manager approval → retrieval; Prep page | later |

The model points; code supplies the words and checks the facts. Quotes are the
transcript's own words, sources are the pack's own text, and a suggested line
that states a product fact must cite the pack and match it: "the inverter has a
10-year warranty" is refused because the pack gives 10 years only with the paid
Care Plan. Remove the models and Sel falls back to rules that find the moments
but write no lines, pick no persona and judge no claim: there is no coach left.

## Business packs

Everything specific to one business lives in a directory:

```
packs/demo-solar/
  pack.toml        identity, languages, week start, scorecard, template paths
  facts.toml       approved short statements with ids
  docs/            product and service documents claims are checked against
  templates/       follow-up email and calendar templates
```

See [docs/business-pack.md](docs/business-pack.md).

## Repository layout

```
sel/            engine: data models, pack loader, retrieval, dates, evidence gate, drafts, pipeline, eval, CLI
sel/asr.py      speech recognition (faster-whisper)
sel/llm.py      LLM client for /chat/completions servers: local (Ollama, llama.cpp, LM Studio, vLLM) or Groq; proprietary services refused
sel/doctor.py   `sel doctor`: models, where they run, licences
sel/analyzer_llm.py  model-backed analysis
sel/server.py, sel/service.py, sel/web/   local review app and call storage
sel/people.py, sel/web/coach.js   the team ("Viewing as"), Today, Playbook
sel/simulator.py, sel/live.py, sel/scoring.py, sel/events.py   call simulator, live scoring and rules, scorecard, event stream
packs/          example business packs (fictional)
examples/calls/ example calls: transcripts, synthetic recordings, fixture analysis, gold annotations
scripts/        local setup; synthetic test-call generator; UI performance and live-call measurements
tests/          unit and end-to-end tests (stdlib unittest; browser tests run when Playwright is installed)
docs/           running, scope, architecture, pack format, evaluation plan, open decisions
```

## Documents

- [docs/running.md](docs/running.md): setup, configuration, command line, and measured results
- [docs/scope.md](docs/scope.md): what the first version includes and leaves out, plus the demo script
- [docs/architecture.md](docs/architecture.md): pipeline stages, data contracts, how models are used
- [docs/business-pack.md](docs/business-pack.md): how to describe a new business
- [docs/evaluation.md](docs/evaluation.md): what we will measure, and how to build a shareable benchmark
- [docs/decisions.md](docs/decisions.md): decisions made so far and questions still open
- [MODELS.md](MODELS.md): every model and AI dependency, its licence, and how to verify what Sel runs
- [CLAUDE.md](CLAUDE.md): working notes for contributors: invariants, code map, measurements and every finding so far

## Data and consent

Real call recordings must not be committed to this repo; `.gitignore` excludes common audio formats and `recordings/`. A recording goes into a published benchmark only if every speaker agreed to share it. Re-enacted calls are the default for public examples. All businesses, people, prices and policies in `packs/` and `examples/` are fictional, and the example recordings are text-to-speech.

## Licence

Apache-2.0 ([LICENSE](LICENSE)). Models and optional dependencies have their own licences, listed in [MODELS.md](MODELS.md).
