# CLAUDE.md

Working notes for anyone (person or agent) changing Sel. It records how the
code is laid out, the rules it must keep, how to check a change, and every
finding from the work so far: what was measured, what broke, and why things
are the way they are. Numbers are from the machine described under
[Measurements](#measurements); re-measure before quoting them elsewhere.

## What Sel is

An on-device assistant for reviewing sales calls (see README.md). A recording
goes through transcription → analysis → evidence gate → due dates → follow-up
drafts → human review in a local web app. The **call simulator** lets two open
Sel sessions call each other over the network, records the call, scores it
live against the pack's scorecard and, on hang-up, files it as a normal call
whose follow-up drafts wait for approval.

## Rules that must not break

- **Nothing is sent.** There is no code path that sends email or creates a
  calendar event. Drafts are `needs_input`, `ready_for_approval` or
  `approved`, and approval only marks them. "Next actions" in the simulator
  are *set* (drafts, tracked customer promises) and Sel *does* only the safe
  steps itself (save the recording, file the transcript, analyse, draft, work
  out dates). Keep it that way unless the owners decide otherwise
  (docs/decisions.md).
- **The model points; code supplies the words.** Quotes are the transcript's
  own words, sources are the pack's own text, dates are computed by
  `sel/dates.py`. The evidence gate (`sel/grounding.py`) refuses anything it
  can't tie back. Any new analyzer (including `RuleAnalyzer`) goes through the
  same gate.
- **Words Sel suggests are labelled and checked.** Better lines (and, later,
  the AI customer and briefs) show where they came from ("AI-suggested",
  "Example line", "Written by a person"). Every suggested line goes through
  `check_line` in `sel/coach.py`: a product fact must cite a pack passage, its
  numbers must be in what it cites, it must not flip a "does not", and a figure
  the pack gives only with a condition must carry it. A refused line is kept
  and shown with its reasons. A model judge may refuse more; never pass more.
- **Agents propose, code applies.** The Coach returns actions; `apply_actions`
  checks each against a whitelist (`COACH_ACTIONS`) and the current state, logs
  refusals too, and only then writes `coach/<person>.json`.
- **No invented dates, no false "supported".** Unclear due phrases become
  `ambiguous` with a reason and window. `RuleAnalyzer` never returns
  `supported` or `contradicted` (see [Rules analyzer](#rules-analyzer)).
- **Open-weight models only, everywhere** (Frogtoberfest rule and the privacy
  promise): a local `/chat/completions` server by default, Groq-hosted
  open-weight models as an opt-in. No proprietary model service anywhere,
  including small auxiliary calls (embeddings, classification, browser speech
  services). `sel/llm.py` refuses known proprietary hosts; `python -m sel
  doctor` must say OK. List every new model or AI dependency in MODELS.md
  with its licence.
- **Standard library only** for the engine and server (`dependencies = []` in
  pyproject). faster-whisper, Piper and Playwright are optional.
- **No external resources in the web app**: vanilla JS, no build step, works
  offline. Server text goes into the DOM with `textContent` (via `el()`); the
  only `innerHTML` is constant markup (the transcript row template).
- **Business-specific behaviour lives in packs**, never in the engine.
  Scorecard wording shown to reps comes from the pack's criterion
  descriptions; code only knows five generic signals.
- Real call recordings are never committed (`.gitignore`). Example data is
  fictional.
- **No AI attribution trailers in commits.** Commit messages end with the
  description of the change: no `Co-Authored-By: Claude …`, `Claude-Session:`
  or "Generated with Claude Code" lines. This applies to every commit, made by
  a person or an agent.

## Commands

```bash
python -m unittest discover -s tests -t .                 # 154 tests; the 5 browser tests skip without Playwright
python -m sel serve                                       # http://127.0.0.1:8765, loads the two example calls
python -m sel doctor                                      # models in use, where they run, licences; fails on a proprietary service
python -m sel serve --host 0.0.0.0 --tls-cert c.pem --tls-key k.pem   # simulator across computers (mic needs https)
python -m sel eval examples/calls/solar-01 --pack packs/demo-solar --analyzer rules   # rules vs gold
python -m sel demo examples/calls/pos-01 --pack packs/demo-pos --analyzer rules
python -m sel eval examples/calls/solar-01 --pack packs/demo-solar --coaching fixture   # coaching vs coaching_gold.json (fixture|rules|llm)
python scripts/perf_ui.py                                 # UI performance on a generated 2-hour call (Playwright)
python scripts/simulate_call.py examples/calls/pos-02 --pack demo-pos --model base   # two browsers, live Whisper
```

Browser tests and scripts need `pip install playwright` (Chromium is
pre-installed in the cloud sandbox at `/opt/pw-browsers`; elsewhere run
`playwright install chromium`). Live speech needs `pip install -e ".[speech]"`
and ffmpeg. A self-signed certificate for LAN testing:
`openssl req -x509 -newkey rsa:2048 -nodes -keyout k.pem -out c.pem -days 30 -subj /CN=sel.local`.

## Map

| Path | What it does |
|---|---|
| `sel/models.py` | Data contracts (Transcript, Finding, ClaimCheck, Commitment, Draft, CallReport) |
| `sel/pipeline.py` | `analyze_call` (stages 1–6, timed) and `build_report` (pure: gate, dates, drafts) |
| `sel/grounding.py` | Evidence gate |
| `sel/dates.py` | Due-phrase resolution; `due_phrase_in` finds the spoken due words in a line |
| `sel/drafts.py` | Email/calendar drafts, edits, approval, `.ics` |
| `sel/retrieval.py` | BM25 over pack facts/docs; passages are verbatim substrings |
| `sel/analyzer_llm.py`, `sel/llm.py` | LLM analysis (narrow prompts, schema-constrained); one `/chat/completions` client for local servers or Groq (`SEL_LLM_PROVIDER=groq`), proprietary hosts refused; `chat_client_from_env` (`SEL_CHAT_LLM_*`, inherits `SEL_LLM_*`) for the Coach and AI customer |
| `sel/doctor.py` | `sel doctor`: model, endpoint, where it runs, licence; exit 1 on a proprietary service |
| `sel/asr.py` | faster-whisper: whole recordings (`transcribe`) and live windows (`transcribe_pcm`) |
| `sel/live.py` | Code-only rules: per-line detection, signals over a transcript, `LiveScorer` (score + next actions), `RuleAnalyzer` |
| `sel/scoring.py` | Scorecard grading from signals (live or from a reviewed report) |
| `sel/coach.py` | Coaching: `check_line` (gate for suggested lines), moments (rules, `MomentFinder`, `gate_moments`), `LineWriter` (RAG), `diagnose`, moment actions, `CoachStore`, `RuleCoach` / `ModelCoach`, `apply_actions` |
| `sel/simulator.py` | `SimulatorHub`: rooms, presence, WebRTC signal relay, lines, `PcmSegmenter`, live Whisper worker, filing the call |
| `sel/events.py` | In-process pub/sub behind the Server-Sent Events stream |
| `sel/service.py` | Calls on disk, the analysis job then the coaching stage, review and moment actions, caches, events, `rep_id` per call, the Coach queue, focus, `today()` |
| `sel/people.py` | The team (`people.json`): reps and managers behind "Viewing as"; no login |
| `sel/server.py` | HTTP API, static files, `/api/events`, simulator routes, TLS |
| `sel/web/app.js` | Review app; exposes `window.Sel` helpers |
| `sel/web/sim.js` | Simulator UI ("Practice"), lazy-loaded on first `#sim` |
| `sel/web/coach.js` | Today (`#today`, the home page), Playbook (`#playbook/<pack>`), People (`#people`); lazy-loaded |
| `sel/web/pcm-worklet.js` | Audio worklet: microphone → 16 kHz PCM chunks |
| `tests/` | stdlib unittest; `test_e2e_browser.py` drives Chromium |
| `scripts/perf_ui.py`, `scripts/simulate_call.py` | Reproduce the measurements below |

Next to the call folders: `calls/people.json` (the team) and
`calls/coach/<person>.json` (focus, history, the Coach's actions log). Call
folder (`calls/<id>/`): `call.json` (with `coaching_status`), `audio.<ext>`,
`live_transcript.json` (simulated calls), `transcript.json`, `analysis.json`,
`review.json`, `report.json` (with a `score` key), `coaching.json`. Example
calls carry `coaching.json` (hand-written stand-in for the coaching models,
with deliberate mistakes) and `coaching_gold.json`.

## Today, people and the Playbook

Navigation: **Today · Practice · Playbook**, then the call list. The browser
picks who it is **viewing as** (`localStorage` `sel.viewer`); there is no
login, so this is a convenience, not access control. UI follows DESIGN.md
(see "Navigation, Today and the Playbook" there).

- **People** (`people.json`, `sel/people.py`): name and role (`rep` or
  `manager`). Calls carry `rep_id`: set from the upload form, from the
  simulator (the rep's `person_id`), or later with "Whose call" on the call
  page (`PUT /api/calls/<id>/rep`). Example calls start with no rep.
- **Today** (`GET /api/today?person=`): for a rep, their follow-up drafts
  that need input or approval, the scorecard skills they fell short on across
  their last 10 reviewed calls (each linked to the calls), calls being
  analysed, and recent calls. A manager gets the whole team, a per-rep table
  and the calls with no rep. Everything is read from the calls' own files
  (`report.json` cached by mtime); nothing is inferred. "Skills to work on" is
  the seed of the coaching loop: the next stage replaces "Practise this"
  with a drill built from the weak moment.
- **Playbook** (`GET /api/packs/<id>`): the pack as reps use it: skills
  (scorecard), approved facts, documents.
- A tab's own changes are not echoed back on the event stream, so the page
  re-reads after its own writes (`Sel.refreshPeople()`; Today re-checks each
  time it is opened). A first load of Today that fails shows the reason and
  "Try again" instead of loading forever.

## The call simulator

### Flow

1. **Join.** One tab opens a call (`POST /api/sim/rooms`, choosing rep or
   customer); it appears in every lobby through the `rooms` event. Another tab
   or computer joins (`/join`) or uses the invite link `#sim/<code>`. Each
   participant gets a secret client token, kept in `sessionStorage`, so a
   reload resumes the call.
2. **Talk.** The rep's browser makes the WebRTC offer; the customer's answers.
   Offer, answer and ICE candidates go through `POST .../signal` and arrive on
   the other tab's event stream (addressed to that client only).
3. **Record.** The rep's browser mixes its microphone (left) and the remote
   stream (right) with a `ChannelMergerNode` into a `MediaRecorder`
   (webm/opus, 1 s chunks), the same stereo layout uploads call
   `channels=split`. The filed recording is verified stereo in the browser test.
4. **Transcribe and score live.** Each browser streams its *own* microphone as
   16 kHz PCM (worklet, 0.5 s chunks, `POST .../audio?t=`). `PcmSegmenter` cuts
   utterances at pauses; one worker thread runs Whisper on them. Typed lines
   feed the same transcript. Every line re-runs
   `LiveScorer` and both tabs get the score, next best actions and a feed.
5. **Hang up and file.** The rep's browser uploads the recording to
   `.../finish`; the hub flushes the segmenters, waits (up to 90 s) for queued
   utterances, and calls `CallService.create_simulated`. The job reads the live
   transcript (`transcribe: live`), analyses with the LLM if its server answers
   and otherwise with `RuleAnalyzer` (`analyzer: auto`), then builds the report,
   drafts and scorecard. The room's "After the call" panel lists what Sel did
   and what waits for a person. "Re-transcribe recording" on the review page
   re-runs Whisper over the stereo file.

Workflow rail in the UI: Join → Talk → Score live → Save → Analyse → Next actions.

### Findings and gotchas

- **Microphones need a secure context.** `getUserMedia` works on
  `localhost`/`127.0.0.1` over http, but a browser on another computer needs
  https: hence `--tls-cert/--tls-key`. Without a mic the simulator still
  works with typed lines; the UI says why.
- **No STUN/TURN by default** (no external resources). Host candidates connect
  two tabs on one machine and machines on one LAN. Across NATs set
  `SEL_ICE_SERVERS='[{"urls":"stun:..."}]'`; it is passed to the browsers.
- **Avoid renegotiation**: both sides use one `sendrecv` audio transceiver and
  `replaceTrack` when the mic turns on later. Only the rep offers (no glare);
  it re-offers when the customer comes back online or the link fails. A
  transient `disconnected` state is left alone (it often recovers).
- **Chrome only feeds a remote WebRTC stream into Web Audio if the stream is
  also playing in a media element**, so the remote audio is attached to a
  hidden `<audio>` (which is also how the rep hears the customer).
- **AudioContext autoplay**: created in the "Start"/"Turn on microphone" click,
  or after the page has had a user gesture (joining counts); otherwise it
  starts suspended and the recording would be silent.
- **Six connections per origin across all tabs** (HTTP/1.1). Two Sel tabs each
  holding a stream for calls and another for the room would leave little for
  requests, so a tab keeps exactly one `EventSource` and the room topic is
  added to it (`/api/events?room=&client=`).
- **Two tabs on one computer hear each other** through the speakers and both
  mics; echo cancellation helps, headphones fix it. Typed lines are the
  reliable way to test on one machine.
- **Clock alignment**: lines carry call time. The server owns the call clock
  (`t0` at start); browsers map AudioContext time to call time when PCM
  capture starts. Speech windows can finish out of order, so lines are kept
  sorted by start and the UI inserts rows in place.
- **Finishing waits for speech to catch up**; otherwise the last sentence of
  the call (often the promise) would be missing from the filed transcript.
- **Bounded work**: 2,000 characters per line, 5,000 lines per call, 2 MB per
  audio chunk, 200 rooms per server, 40 utterances queued for Whisper per room
  (newer audio is dropped rather than building a backlog).
- Rooms live in memory: a server restart forgets open calls (filed calls stay).
- Browser captions (`webkitSpeechRecognition`) were **removed**: Chrome sends
  the audio to its vendor's speech service, which breaks the open-models rule.
  The server accepts only `source: typed` lines; speech goes through Whisper.
- Testing without hardware: Chromium flags `--use-fake-ui-for-media-stream
  --use-fake-device-for-media-stream` give a beeping fake mic;
  `--use-file-for-fake-audio-capture=x.wav` plays a file as the mic (it loops
  and starts when the mic opens, so open both together).

### Live speech: measured

`scripts/simulate_call.py`, two Chromium processes, each side of the stereo
example recording as one microphone, Whisper int8 on 4 vCPU:

| | pos-02 (English TTS, 86 s) | solar-01 (romanized Nepali, English TTS voice, 70 s) |
|---|---|---|
| Live model | `base` | `base` |
| Line on screen after the speaker stopped | median 2.2 s, p90 4.6 s, max 7.8 s | median 2.4 s, p90 9.4 s, max 14.6 s |
| Same with `small` | median 6.1 s, p90 11.6 s, max 16.6 s | – |
| Questions / objections / promises / claims found (vs gold) | 3/3, 1/1, 4/4, 5/5; no extras | 1/3, 1/1, 2/4, 5/5 (+1 extra claim) |
| Due dates right; invented dates | 4/4; 0 | 0 of 2 matched; 0 invented |
| False "supported" | 0 | 0 |

So `base` is the live default (`SEL_LIVE_WHISPER_MODEL` overrides; the live
model no longer follows `SEL_WHISPER_MODEL`, which may be set to a slow, more
accurate model for uploads). English accuracy was the same as `small` on this
call. Romanized Nepali is garbled ("ma bholi beluka samma" → "Mobily Balooka
Sam"), so dates are lost and Sel asks instead of guessing, which is the
existing weak point (docs/running.md). Not yet tried: Whisper's
`initial_prompt` with pack vocabulary and Nepali time words, and a
Nepali-tuned model.

Whisper invents "Thank you." on near-silent windows; segments it marks as
probably not speech (`no_speech_prob > 0.6` with low log-prob) are dropped,
and the segmenter only emits windows with at least 0.35 s of speech.

## Rules analyzer

`sel/live.py` reads cue words (English, romanized and Devanagari Nepali),
splits sentences and clauses, and uses BM25 over the pack. It powers the live
score, the report scorecard's extra signals, and `RuleAnalyzer` when no LLM
answers.

Against the gold files on the hand-typed example transcripts
(`python -m sel eval ... --analyzer rules`, also `tests/test_live.py`): every
question, objection, claim and commitment found with no extras on all three
calls, every due date right, 0 invented dates, 0 false "supported"; coaching
6 of 8 with one extra (pos-02 `book-demo`, flagged because the trainer visit
has no day). Claim verdict accuracy is only 0.2: by design, every claim is
`unverified`.

Why rules never judge claims: a numbers check cannot tell the solar pack's
"5-year warranty, extendable to 10 years with the paid Care Plan" from a rep
saying "10 years warranty" (the numbers match, the claim is wrong). Rules flag
a claim only when its numbers appear in **none** of the nearby passages
("NPR 2,000" vs the pack's 2,500), or when no passage covers it, and attach
the closest passage for the reviewer.

Lessons that are now encoded in the rules:
- Tag questions ("..., right?", "hoina?") are not discovery questions.
- Reported speech ("dinchu bhaneko thiyo", "he said") is not a promise.
- One claim per stated number: "25 years on panels, ani 10 years on the
  inverter" is two claims; "Our pricing: the plan is NPR 2,000" is one.
- Promises merge when the owner and channel match and one has no day or both
  fall on the same day ("I'll send the details" + "I'll email it by tomorrow
  evening" + "Tomorrow I'll email" = one email, dated by the second).
- Speech recognition splits one objection over two lines; consecutive lines
  from the customer merge.
- Speech recognition writes "I understand" as "I'll understand"; "I'll" +
  understand/think/guess/be honest is not a promise.
- "Talk to my partner" style stalls are not counted as objections (the gold
  annotations don't).

Scorecard signals (`sel/scoring.py`): `discovery`, `objections`, `claims`,
`next_step`, `talk_ratio`. A criterion names one with `signal = "..."` in
`pack.toml`, or it is inferred from its title, then description ("Discovery
before price" → discovery, "Book a demo with a time" → next_step). Criteria
without a signal show "In coaching". While live, criteria that can't be judged
yet are `open` and don't count; at the end they count as missed.

## Coaching (Phase 2: diagnose and focus)

The loop: real call filed → **diagnose** (moments, better line, gate) →
**focus** (one skill) → practise → prepare → perform → measure. Phase 2 built
the first two for every scorecard criterion; price-objection handling is the
skill the demo follows.

- **When it runs.** After a call's report is ready the job marks the call
  `done` (reviewable at once), then coaches it in the same job:
  `coaching_status` queued → running → done | error. The Coaching tab shows
  the stage meanwhile. A model failure falls back to rules moments and keeps
  the error. Re-analysing keeps a person's endorsements, edits, dismissals and
  notes (`carry_over`); a transcript correction refreshes moment quotes.
- **Moments.** With a model: `MomentFinder` (one request, criterion enum,
  segment ids). Without: rules from the coaching findings, contradicted or
  uncovered claims, the scorecard grade and dated promises. `gate_moments`
  refuses unknown segments and criteria, moves a moment cited on the
  customer's line to the rep's reply, refuses praise for a line the pack
  contradicts, caps two per (criterion, kind), and supplies the quotes.
- **Better lines.** `LineWriter` gets BM25 passages for the customer line, the
  rep line and the criterion, and returns `{line, passages}`. Code resolves ids
  to passage text, runs `check_line`, retries once with the reasons, and keeps
  refused attempts. At most 4 lines per call, most heavily weighted criteria
  first. The model judge (`verify-lines`) can only refuse more. No model: no
  line; the tab shows the criterion's own description.
- **Focus.** `run_coach` (its own queue, never waits behind an analysis) builds
  the rep's skills from their last 10 coached calls, ranked by calls fell short
  on, then `SIGNAL_PRIORITY` (objections, discovery, next step, talk ratio,
  claims), then moments, then weight. `ModelCoach` uses the chat client; any
  error falls back to `RuleCoach`. Phase 2 whitelist: `set_focus` only, and only
  when no focus is active. A person can set or clear a focus (`PUT
  /api/coach/<person>/focus`); the Coach never replaces an active one.
- **API.** `GET /api/calls/<id>/coaching`, `POST
  /api/calls/<id>/moments/<m>/{endorse|dismiss|edit|note}` (`by` = viewer),
  `GET /api/coach/<person>`, `PUT /api/coach/<person>/focus`; `/api/today`
  has `focus` (with the latest moment and line) and each rep's focus for a
  manager. Events on `calls`: `coaching` {id}, `coach` {person_id, focus}.

### Coaching: measured

`python -m sel eval <example> --pack <pack> --coaching fixture|rules|llm`
scores moments against `coaching_gold.json` (same kind and criterion,
overlapping segments), counts lines accepted and refused, and counts accepted
lines that match a `forbidden_lines` pattern (e.g. "10 years" without "Care
Plan").

| solar-01 | Weak moments found | Strong found | Lines accepted / refused | Forbidden claims accepted | Time |
|---|---|---|---|---|---|
| Fixture (hand-written) | 5/5, 0 extra | 2/2 | 5 / 1 (the 10-year line) | 0 | – |
| Rules (no model) | 5/5, 0 extra | 2/2 | no lines | 0 | < 0.1 s |
| qwen2.5:7b, first prompt (Ollama busy with a second job) | 4/5, 0 extra | 0/2 | 2 / 4 | 0 | 9 min 25 s |
| qwen2.5:7b, current prompt, Ollama idle | 4/5 (missed NEA timing), 0 extra | 0/2 | 4 / 0 | 0 | 3 min 12 s (moments 51 s, 4 lines 140 s) |

pos-01: fixture and rules both 2/2 weak, 2/2 strong; the fixture's wrong
"connects to your accounting software" line is refused. Machine: Intel
i7-1255U (10 cores), 16 GB, CPU only, Ollama, Windows. Gold annotations were
written with the fixtures, not independently.

### Coaching: findings

- **qwen2.5:7b loops citations into the line** ("… please? [s02] [P2] [P4]
  [s03] …" until max tokens). The gate refused all of it, but with dozens of
  "says 3" reasons. Now: the prompt says no ids in the line, `max_tokens` is
  160, a line with ids is refused with one reason, reasons are deduplicated and
  capped at 4, and an `LLMError` on one line is a refused attempt, not a failed
  call.
- **Cue words miss claims.** "It connects directly to your accounting
  software" has no claim word, so it passed. A clause now also counts as a fact
  when BM25 matches a pack passage (score at least 2) with two shared content
  words.
- **Questions are not claims, mostly.** "Before I quote a price: how big is
  your bill?" was refused for citing nothing. In a question only a clause with
  a number and a claim word counts ("Did you know it has a 10-year warranty?").
- **Conditions trail.** "330 to 390 units per month, depending on season": the
  qualifier is its own clause, so it attaches to the number's clause when it
  starts with depending, if, unless, only or subject to.
- **Polarity in one direction only.** "The timing is not in our hands" for
  "outside our control" tripped a both-ways negation check. A line is refused
  only when the pack says "does not" and the line doesn't.
- **The number check alone is not enough** (as with the rules analyzer): "10
  years" is in the inverter fact. The condition check is what refuses it.
- The Coach on qwen2.5:7b timed out (60 s chat timeout) while Ollama was busy
  with an eval; the rules set the focus. A small chat model is needed for
  Phase 3's AI customer anyway (target 2 to 3 s per reply).
- Tests must wait for the job queue before deleting temp folders now that
  coaching runs after `done` (`CallService.wait_idle`); Windows refuses to
  delete a file another thread is writing.

## Performance

### Measurements

Machine: 4 vCPU, 15 GB RAM, Linux, headless Chromium (Playwright 1.56).
Call: 2 hours, 1,188 lines, 108 findings, 54 claims, 36 commitments (pos-01
repeated; `scripts/perf_ui.py` generates it). Medians of repeated runs; the
first cold run is discarded as warm-up. Interactions are timed in the page
from the click to the frame after the change is on screen.

| | Before | After |
|---|---|---|
| Cold load until the call is ready | 412 ms | 263–301 ms |
| Main-thread long tasks during that load | 249 ms | 79–87 ms |
| Data transferred, first load | 377 kB | 73 kB |
| Reload until ready | 437 ms | 170–198 ms |
| Data transferred, reload | 450 kB | ~1 kB (assets from cache, API 304s) |
| Switch to the long call from another call | 257 ms | 99–125 ms |
| Switch to a short call | 43 ms | 25–29 ms |
| Open / close a line editor | 202 / 214 ms | 25 / 11–21 ms |
| Tab switch; evidence click | 42; 28 ms | 38–47; 30 ms (unchanged) |
| Main-thread time per 8 s of playback | 439 ms | 167–199 ms |
| Requests per 25 s while idle | 3 (polling) | 0 |

### What made it slow (found in the original code)

- Every response was `Cache-Control: no-store` and the server spoke HTTP/1.0:
  a new connection per request, and every reload re-downloaded app.js,
  style.css and the full call JSON.
- The page polled `/api/calls` every 2–10 s, and the server re-read every
  `call.json` for each poll.
- `CallService.packs()` re-parsed every pack (TOML and all documents) on each
  call to `pack()`: on every draft edit, transcript edit and job.
- Audio responses read the whole file into memory (`f.read()`), and uploads
  were read whole into memory (up to 500 MB) before being written.
- `/api/health` asked the LLM server each time (5 s timeout when unreachable).
- Boot was sequential: packs, then the call, then the list.
- `segmentById` was a linear search, used per card and per render.
- Playback highlighting scanned all segments on every `timeupdate` and
  toggled classes on every row and call-map block; evidence marking did too.
- Opening or closing the line editor re-rendered the whole transcript (1,188
  rows), and every row had two listeners.

### What fixed it

Server (`sel/server.py`, `sel/service.py`):
- HTTP/1.1 keep-alive. A request whose body wasn't read gets
  `Connection: close`, otherwise its body bytes would be parsed as the next
  request (tested).
- Static files from memory with gzip precomputed. `index.html` pins each
  `/static/x` to `x?v=<content hash>`, served `immutable`; unhashed URLs get
  `no-cache` + ETag.
- ETags from file metadata for the call view, list, packs and pack sources,
  so an unchanged call answers 304 without being read. JSON over 1 kB is
  gzipped when accepted.
- Recordings stream in 256 kB chunks with Range and ETag; uploads stream to
  `calls/.incoming/` and are moved into place.
- Packs are cached by a signature of every file's size and mtime; call
  summaries by `call.json` mtime; health for 30 s.
- `GET /api/events`: one Server-Sent Events stream per tab carrying call
  status, "changed by another tab" notices (a tab's own changes are not
  echoed back, via `X-Sel-Tab`), lobby rooms, room updates, lines, live
  scores and WebRTC signals. A slow subscriber is dropped and reconnects to a
  fresh snapshot (`hello`).

Browser (`sel/web/app.js`):
- Packs, the list and the routed call load in parallel; a skeleton shows at once.
- Call views are cached in memory (24, LRU) and revalidated; sidebar links
  prefetch on hover or focus, so opening a call renders immediately.
- Sidebar rows are reused when unchanged and moved, not rebuilt.
- A segment index (Map plus a `Float64Array` of starts); the playhead is a
  binary search, and the highlight moves by touching two rows.
- The playhead is driven by one timer armed for the next line's start (re-armed
  on play, seek and rate change) instead of a per-frame loop: script time during
  playback fell from 42 ms to 5 ms per 8 s.
- Transcript rows are cloned from a constant template (about 4× cheaper than
  building each element) and one delegated listener handles all rows.
- Editing a line replaces that row only; saving re-renders the side panels
  but not the transcript.
- Rows are grouped in chunks of 40 with `content-visibility: auto` on the
  chunk.
- Dismiss and restore are optimistic and roll back on error.
- Upload shows progress (XHR), accepts drag and drop, and remembers pack,
  offset, rep name and channel layout (localStorage, best effort).
- Keyboard: Space play/pause, 1–4 tabs, J/K next/previous card (highlights
  its evidence), Enter plays it, Esc clears, ? shows the keys.
- The simulator script loads on first use (and is prefetched at idle
  priority); the live room batches renders to one per animation frame and
  inserts new transcript lines instead of redrawing.

### Performance traps found on the way (keep them in mind)

- **`content-visibility: auto` per row backfired.** Cold load improved, but
  smooth auto-scroll during playback cost 20× more layout (317 ms vs 17 ms per
  8 s), and every unrelated layout (tab switch, evidence click) got slower,
  because the browser re-checked 1,188 placeholder rows. Putting it on chunks
  of 40 rows kept the cold-load gain and removed both regressions. Auto-scroll
  while playing uses instant `nearest` scrolling; smooth scrolling is kept for
  jumps the user asks for.
- **A `requestAnimationFrame` playhead loop** costs about 35 ms of script per
  8 s for nothing; a timer per line change is enough.
- **Request de-duplication can serve stale data.** Sharing an in-flight GET is
  fine for prefetch, but a load triggered by a server event must not reuse a
  request that started before the change, or the page can stay stuck on
  "Analysing" after the job finished (found by the browser test). Event-driven
  loads pass `fresh`, and responses carry a sequence number so an older one
  never replaces a newer one.
- The first stream `hello` re-validated the call the page had just loaded (a
  wasted request on every load); only reconnects resync now.
- The simulator lobby rendered before packs arrived and kept an empty pack
  picker; a `packs` event refills it.

## Other bugs found and fixed

- `_CLOCK_EN` in `sel/dates.py` accepted "am" and "a.m." but not "a.m"
  (Whisper `base` writes "11 a.m.", and phrase trimming drops the final dot),
  so "Monday at 11 a.m." lost its time; it also matched "11 amazing". Both fixed.
- `due_phrase_in` stopped at "a.m." for the same reason.

## Open issues and follow-ups

- Coaching with qwen2.5:7b found no strong moments on solar-01 and missed the
  NEA-timing moment; the line for the inverter moment was a safe "I'll
  confirm", not a corrected fact. Try a criterion-at-a-time prompt.
- The chat model for the Coach and AI customer is not chosen (a decision for
  the owners: the demo laptop). `SEL_CHAT_LLM_*` inherits the 7B model today.
- One browser run saw a 409 in the simulator test under heavy CPU load
  (another model job running); it passed on reruns. Likely a PCM chunk posted
  after hang-up.

- Nepali speech recognition, live and offline (see the table above). Try
  Whisper `initial_prompt` with pack vocabulary and Nepali time words, and
  Nepali fine-tunes.
- The server binds to localhost by default and has **no login**; `--host
  0.0.0.0` exposes calls, recordings and the simulator to the network (the CLI
  warns). Add authentication before recommending LAN use beyond a trusted
  office.
- Rooms are in memory; one event-stream thread per open tab
  (`ThreadingHTTPServer`). Fine for a small team on one machine.
- Live re-scoring re-runs the rules over the whole transcript per line (cached
  per line, so linear); very long live calls (thousands of lines) would want
  incremental signals.
- Live score and actions refresh only when a line arrives; time-based actions
  ("agree a next step") don't appear during silence.
- The rules analyzer's coaching for `next_step` flags any undated follow-up,
  which the pos-02 annotation does not count.
- Customer promises are tracked, not drafted (unchanged decision).
- The perf numbers are from headless Chromium on one machine; check on the
  target laptop (docs/decisions.md, open question 5).
