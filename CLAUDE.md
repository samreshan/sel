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
python -m unittest discover -s tests -t .                 # 224 tests; the 9 browser tests skip without Playwright
python -m sel serve                                       # http://127.0.0.1:8765, loads the two example calls
python -m sel doctor                                      # models in use, where they run, licences; fails on a proprietary service
python -m sel demo-setup [--next]                         # with Sel running: team, lead, the first real call through the models (docs/scope.md)
python -m sel serve --host 0.0.0.0 --tls-cert c.pem --tls-key k.pem   # simulator across computers (mic needs https)
python -m sel eval examples/calls/solar-01 --pack packs/demo-solar --analyzer rules   # rules vs gold
python -m sel demo examples/calls/pos-01 --pack packs/demo-pos --analyzer rules
python -m sel eval examples/calls/solar-01 --pack packs/demo-solar --coaching fixture   # coaching vs coaching_gold.json (fixture|rules|llm)
python -m sel eval examples/calls/solar-01 --pack packs/demo-solar --transfer rules    # Judge vs transfer_gold.json (rules|llm)
python scripts/drill_latency.py                            # AI customer reply times (--model, --rules, --format text|json)
python scripts/screens_team.py --out shots/                # Leads, Prep, Team, Settings, Playbook screenshots (desktop, 390 px) + keyboard pass
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
| `sel/demo_setup.py` | `sel demo-setup`: checks, then files the demo's team, lead and calls through the running server's API; safe to re-run |
| `sel/asr.py` | faster-whisper: whole recordings (`transcribe`) and live windows (`transcribe_pcm`) |
| `sel/live.py` | Code-only rules: per-line detection, signals over a transcript, `LiveScorer` (score + next actions), `RuleAnalyzer` |
| `sel/scoring.py` | Scorecard grading from signals (live or from a reviewed report) |
| `sel/persona.py`, `sel/ai_customer.py` | Anonymised practice personas; the AI customer (`{say, end_call, mood}`, rules fallback) |
| `sel/drills.py`, `sel/transfer.py` | Drill store, Judge, debrief; transfer checks on real calls. Playground drills (`origin: playground`) practise a pack or heard objection |
| `sel/files.py` | JSON reads and atomic replaces that retry Windows sharing violations |
| `sel/web/practice.js` | Practice (`#practice`, `#practice/<id>`): drills, the AI customer room, debrief; lazy-loaded |
| `scripts/drill_latency.py` | AI customer reply times on this machine |
| `sel/leads.py` | `LeadStore`: `leads/<id>/lead.json` (name, phone, rep, notes, `call_ids`) |
| `sel/prep.py` | `build_brief`: a lead's earlier reports → likely objections with the best answer, promises, gaps, focus; the model picks by id and writes a number-checked summary |
| `sel/playbook.py` | The overlay `playbook/<pack>.json` (`PlaybookStore`), `match`, the Librarian (`candidates`, `propose`): someone's own words, gated by `check_line` |
| `sel/web/team.js` | Leads (`#leads`, `#lead/<id>`), Prep (`#prep/<id>`), Team (`#team`, `#team/<person>`), Settings (`#settings`); lazy-loaded |
| `scripts/screens_team.py` | Screenshots of the Phase 4 pages at desktop and 390 px, and a keyboard-only pass |
| `sel/coach.py` | Coaching: `check_line` (gate for suggested lines), moments (rules, `MomentFinder`, `gate_moments`), `LineWriter` (RAG), `diagnose`, moment actions, `CoachStore`, `RuleCoach` / `ModelCoach`, `apply_actions` |
| `sel/simulator.py` | `SimulatorHub`: rooms, presence, WebRTC signal relay, lines, `PcmSegmenter`, live Whisper worker, filing the call |
| `sel/events.py` | In-process pub/sub behind the Server-Sent Events stream |
| `sel/service.py` | Calls on disk, the analysis job then the coaching stage, review and moment actions, caches, events, `rep_id` and `lead_id` per call, the Coach queue, focus, `today()`; leads, Prep, rehearse, the team view, 1:1 briefs, escalations, the playbook overlay and the Librarian |
| `sel/people.py` | The team (`people.json`): reps and managers behind "Viewing as"; no login |
| `sel/server.py` | HTTP API, static files, `/api/events`, simulator routes, TLS |
| `sel/web/app.js` | Review app; exposes `window.Sel` helpers |
| `sel/web/sim.js` | Simulator UI ("Practice"), lazy-loaded on first `#sim` |
| `sel/web/coach.js` | Today (`#today`, the home page), Playbook (`#playbook/<pack>`), People (`#people`); lazy-loaded |
| `sel/web/pcm-worklet.js` | Audio worklet: microphone → 16 kHz PCM chunks |
| `tests/` | stdlib unittest; `test_e2e_browser.py` drives Chromium |
| `scripts/perf_ui.py`, `scripts/simulate_call.py` | Reproduce the measurements below |

Next to the call folders: `calls/people.json` (the team),
`calls/coach/<person>.json` (focus, history, transfer checks, escalations, the
Coach's actions log), `calls/leads/<id>/lead.json` and
`calls/playbook/<pack>.json` (team answers: proposed, approved, rejected,
refused; the pack itself is never written to). Call
folder (`calls/<id>/`): `call.json` (with `coaching_status`), `audio.<ext>`,
`live_transcript.json` (simulated calls), `transcript.json`, `analysis.json`,
`review.json`, `report.json` (with a `score` key), `coaching.json`; `call.json` has
`kind` (real | practice), `lead_id` and, for drills, `drill_id`. `calls/drills/<id>.json`
holds drills and their attempts. Example
calls carry `coaching.json` (hand-written stand-in for the coaching models,
with deliberate mistakes) and `coaching_gold.json`.

## Today, people and the Playbook

Navigation: **Today · Calls · Leads · Playground · Team · Settings**. Practice
stays at `#practice`, opened from Today and from a drill. The browser
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

## Playground objections (P1, 20 Oct)

- **Where they come from.** The pack's `[[objections]]` (`sel/pack.py`
  `Objection`: id, topic, says, optional context, criterion, hint; validated)
  and `CallService.heard_objections`: objection findings on the pack's
  reviewed real calls, the customer's words anonymised (`persona.anonymise`
  with the call's and the team's names; the finding's summary is never shown,
  it can name a price), the same words once, newest first.
- **A Playground drill** (`create_playground_drill`): `origin: "playground"`,
  `source` `{pack_id, objection_id}` or `{pack_id, heard_id, call_id,
  finding_id}`, one per rep and objection (practising again adds attempts).
  Judged on `Pack.objection_criterion` (the objection's own, else the
  objection-handling criterion, else discovery, else the first). The persona is
  `persona.objection_persona`: the pack's words as written, heard words
  anonymised, the model's output always cleaned. The better line is the best
  approved team answer for the criterion, if any; the debrief adds the pack's
  `hint` ("Your playbook suggests").
- **Practice only.** `coach_state` leaves Playground drills out (no
  escalation, difficulty or focus change) and their debrief starts no Coach
  turn. The single typed reply for objections is retired (`POST
  /api/playground` takes `mode: document` only).
- **The opening is the objection.** A drill room's `LiveScorer` takes
  `opening_objection` (Playground drills, and call drills whose skill is
  objection handling): the customer's first line counts as an objection for
  the rules (`transcript_signals(known_objections=)`), cue words or not.
  Found when the warranty objection ("Inverters break all the time…", no cue
  word) debriefed as "Did not come up".
- **API**: `GET /api/playground/objections?pack=&person=` (`pack`, `heard`,
  each with this rep's drill), `POST /api/playground/objections {person_id,
  pack_id, objection_id | heard_id}` → the drill (201); the UI opens
  `#practice/<id>`.

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

## Practice and measure (Phase 3)

The loop continues: **practise** (a drill with an AI customer built from the
weak moment) → **perform** (the next real call) → **measure** (a transfer
check on that call) → the Coach closes the focus or keeps going.

- **Drills** (`sel/drills.py`, `drills/<id>.json`): one weak moment of a real
  call, for that call's rep. Created by the Coach (`create_drill` for the
  focus) or by "Practise this (90 s)" on a moment or on Today. The persona is
  built on the agent queue (status preparing → ready).
- **Persona** (`sel/persona.py`): code anonymises first (`anonymise`: the
  call's and the team's names, phone numbers, emails, amounts such as "NPR
  3,60,000" or "3 lakh 60" become "a different price"), then the chat model
  writes about/wants/opening/if_good/if_poor; code anonymises its output again
  and picks the name from a fictional list that avoids every real name. Without
  a model the opening is the anonymised customer line itself.
- **AI customer** (`sel/ai_customer.py`) in a `SimulatorHub` drill room
  (`create_drill_room`): the rep and an AI participant, ready to start. The
  room queues a **warm-up** on the AI worker when it opens (one token over the
  persona and the opening), so the model is loaded and the persona read before
  the rep's first line. Two formats (`SEL_CUSTOMER_FORMAT`): **text**
  (default) sends the drill as chat turns (`turns()`: a constant persona
  system prompt, a constant "the call connects" turn, the rep as user, the
  customer as assistant), so each prompt extends the last; the reply is the
  spoken line only (`complete_chat`, 48 tokens, stop at a newline), cleaned by
  `clean_line` (labels, quotes, stage directions such as "(pauses)", ids);
  code decides the ending (a dated next step, or 8 customer lines) and the
  mood (Sel's rules). **json** is the earlier format: the transcript as one
  user message, `{say, end_call, mood}` under a schema (80 tokens). A
  rep line queues one reply on the AI worker thread; a reply that arrives
  while the rep spoke again is recomputed; `ai_busy` is cleared under the same
  lock that adds the reply. A model error, an empty line or a line with ids
  falls back to the rules customer (softens after `difficulty` real
  questions, pushes back otherwise, says goodbye when a dated next step is
  agreed or after 8 turns). `end_call` ends the room; the rep's tab files it.
- **Filing and debrief.** A drill room is filed as a call of kind
  `practice` with `drill_id`, analysed by the rules (keeps the CPU free) and
  never coached; `on_drill_finished` records the attempt (status judging) and
  queues the debrief: the Judge on the practice transcript, the practice
  answer it cites, the real answer and the better line. Then a Coach turn.
- **Judge** (`drills.judge`): code verdict from the scorecard grade (met →
  pass, partly/missed → fail, not raised → not_applicable). A model verdict
  must cite a real segment (a customer line moves to the rep's reply). Where
  code measures the skill and disagrees, the verdict is `unsure` and counts
  for nothing; "never came up" is code's call alone.
- **Transfer** (`sel/transfer.py`): after a real call of a rep with an active
  focus set before the call is coached (or when its rep is set later), the
  Judge grades the focus criterion on it (analysis model in the job; chat
  model when the rep is set later). Stored in the coach file; a pass lets the
  Coach close the focus.
- **Coach actions** (`COACH_ACTIONS`): `set_focus`, `close_focus` (needs a
  transfer pass since the focus was set; never a manager's focus),
  `create_drill` (a weak moment of the focus, one drill per moment),
  `raise_difficulty` (last attempt passed at the current level, max 3),
  `escalate_to_manager` (only `contradicted_claim` on a real call,
  `failed_drill` after three fails, `change_manager_focus`; each once).
  `RuleCoach` runs the triggers in that order; `ModelCoach` only chooses the
  next focus. Applied create/raise actions are carried out by the service.
- **Kinds.** `call_kind`: real (uploads, examples) or practice (the
  simulator, drills). Skills, the Coach, transfer checks and Today's skills
  and averages count real calls only.
- **UI**: `sel/web/practice.js` (lazy, `#practice`, `#practice/<id>`), "Practise
  this" on weak moments and Today's focus, manager Today "Needs you".
- **API**: `POST /api/calls/<id>/moments/<m>/drill`, `GET /api/drills?person=`,
  `GET /api/drills/<id>`, `POST /api/drills/<id>/start` (→ room + client),
  `POST /api/drills/<id>/debrief` (re-run); event `drill` on `calls`.

### Practice: measured

`python -m sel eval examples/calls/solar-01 --pack packs/demo-solar --transfer rules|llm`
runs the Judge on six labelled variants of solar-01 (`transfer_gold.json`):
the call as recorded, asking first, asking then explaining, a tag question,
no objection at all, and defending first then asking a turn later.

| Judge | Right | Unsure | False pass | Time |
|---|---|---|---|---|
| Rules, before the fix below | 5/6 | 0 | 1 (defended first, asked later) | < 0.1 s |
| Rules, now | 6/6 | 0 | 0 | < 0.1 s |
| qwen2.5:7b checked by the rules (two runs, same result) | 4/6 | 2 | 0 | 235 s for 5 requests |

The model said "pass" on both calls where the rep defended the price before
asking; the rules disagreed, so the verdict became "unsure" instead of a pass
that would have closed the focus.

`scripts/drill_latency.py` (the solar-01 price objection, four rep lines, the
AI worker of a real drill room), Intel i7-1255U, CPU only, Ollama, Windows:

| AI customer | Reply after the rep's line | First reply (cold) | Persona |
|---|---|---|---|
| Rules (no model) | under 0.01 s | – | under 0.01 s |
| qwen2.5:7b (the chat client inherits the analysis model) | median 12.0 s, p90 17.3 s (4 replies) | 28.4 s | 52 s |
| qwen2.5:1.5b (`SEL_CHAT_LLM_MODEL`, chosen 16 Oct 2026) | median 4.4 s, p90 4.8 s, max 5.3 s (8 replies); about 2 s on the first turn | 7.0 s | 8 to 17 s |

Target: 2 to 3 s. 7B is four to six times too slow. 1.5B is about three
times faster and fine for typed practice, but still above the target once
the transcript grows (each turn re-reads it); its customer also gives in
easily ("Absolutely, that's very helpful"), so drills are less demanding
than the persona asks. The replies themselves were in character ("Can you tell
me what their quote includes, like panel brand and warranty details?"), but
one ended with the stage direction "End call.", now stripped.

- **qwen2.5:1.5b set `end_call` on its first reply** in every drill. Code now
  decides (`ai_customer.may_end`): the model may hang up only after a dated
  next step is agreed or after 4 customer lines.

### Practice: findings

- **Typed and AI lines can start in the same 10 ms.** Sorting by (start, end)
  put a reply before its question. Lines now tie-break by arrival, and typed
  and AI lines never start before the latest line.
- **A rep line could fall between "reply added" and "busy cleared"** and get
  no answer (and a later reply was duplicated). The flag is now cleared under
  the lock that adds the reply.
- **The objection rule passed a rep who defended first and asked a turn
  later** (the transfer eval's "false pass", the error that would close a
  focus unearned). A reply that makes a claim before any question now counts
  as not handled. Rules analyzer numbers on the gold files are unchanged.
- **Windows: `os.replace` and a reader collide** (`PermissionError`, which the
  server maps to 403). `sel/files.py` retries reads and replaces briefly, and
  uses a unique temp name per write.
- The drill page rendered before the team list arrived and showed "choose who
  you are"; it re-renders on `people-changed`.
- Today's skills counted practice calls; they count real calls only.
- **The simulator's "After the call" panel could stay on "Analysing… 100%"**
  (the simulator browser test failed about 1 run in 4). With Sel's rules the
  analysis finishes in milliseconds: the "done" event arrived while the rep's
  tab was still fetching the call, the handler skipped because a load was
  marked under way, and the fetch answered "running". `loadReport` now keeps
  "loaded" (`reportFor`) apart from "loading" (`reportLoading`), fetches again
  when "done" arrives mid-fetch, and fetches fresh. 0 failures in 20 runs.
- **The last microphone chunk got a 409.** Hang-up flushes the worklet; its
  chunk can reach the server after the room is saving. Chrome logs every 4xx
  to the console, which the browser tests treat as a page error. The server
  now drops audio for a saving, saved or failed room (`queued: 0`, as when
  Whisper is behind); audio before the call is live is still a 409, and the
  client stops sending once the call is over.
- **A drill line published while the tab's event stream reconnected was lost**
  (the drill test's "AI customer's second line" timeout). Starting a drill
  reconnects the stream to add the room, and the bus ends a slow subscriber's
  stream; the reconnect's first event is a snapshot with the room's lines, but
  the drill page ignored it. It now adds the snapshot's missing lines
  (`practice.js`, `hello`). A browser test ends the stream on the server,
  publishes a line, and waits for it.

## Prepare, team and the flywheel (Phase 4)

The loop gains **prepare** (a brief before the next call with a lead, and a
rehearsal) and a manager's view, and closes on itself: answers that worked on
real calls, once a manager approves them, coach the next call.

- **Leads** (`sel/leads.py`) are thin: name, phone, rep, notes and their calls.
  A call joins a lead from the upload form or the call page (`PUT
  /api/calls/<id>/lead` writes `call.json` `lead_id` and the lead's
  `call_ids`; moving a call unlinks it from the old lead; deleting a call
  unlinks it).
- **Prep** (`GET /api/leads/<id>/prep`, `sel/prep.py`) reads the lead's
  reviewed real calls (reports and coaching, never raw transcripts). Each
  earlier objection gets the best answer Sel has: an approved team answer for
  that criterion that shares words with it, else the call's better line. Sel's
  rules answer at once; when the analysis model answers
  (`default_analysis_llm`, health-checked once a minute) a model brief is
  built on the agent queue (it picks objections and gaps by id and writes a
  summary whose numbers must all be in the items), announced with a `lead`
  event, and cached in memory against a signature of the lead's calls (their
  ETags), the approved answer ids and the focus.
- **Rehearse** (`POST /api/leads/<id>/rehearse`): a drill from the latest
  weak moment on an `objections`-signal criterion, newest call first, through
  `create_drill` (so one drill per rep and moment); 400 when there is none.
- **Team** (`GET /api/team`, managers): reps × criteria over each rep's last
  10 real calls (met / scored per cell), patterns (a skill at least two reps
  fell short on; objections grouped by Sel's cue words, with up to three
  quotes), open escalations, and the review queue (proposed answers, calls with
  no rep). `GET /api/team/<person>`: focus, skill timelines (each real call
  oldest first, with its status and the matching weak or strong moment),
  transfer checks, drills and a 1:1 brief: talking points from code, plus a
  two-sentence summary from the chat model on the agent queue that is dropped
  if it has a number the points don't.
- **Escalations**: `PUT /api/coach/<person>/escalations/<key>` `{status:
  "resolved", by}`; only a manager (403 otherwise). Keys hold ":" and "+",
  so the route percent-decodes them.
- **The playbook overlay** (`GET /api/playbook/<pack>`, `POST
  .../answers {action: propose|approve|reject, id, by}`). `propose` queues the
  Librarian (202, then a `playbook` event): candidates are strong moments with
  a customer line and better lines a person endorsed, on the team's real calls
  of that pack; the chat model only picks candidates and labels the objection
  (by number); the answer is always the rep's or the endorsed line's words,
  anonymised for names, then gated by `check_line` (with BM25 passages when it
  states a fact). Refused answers are stored as `refused` with reasons and
  can't be approved. Approve and reject need a manager.
- **The flywheel**: `_coach_call` passes the pack's approved answers to
  `diagnose(answers=)` and `LineWriter(answers=)`. The writer's prompt lists
  the approved answers for the moment's criterion, closest first. When no line
  passed (no model, or both attempts refused), the best-matching approved
  answer becomes the better line as `playbook:<id>`, through `check_line`
  like any line, labelled "Team answer".
- **Settings** (`#settings`, `GET /api/doctor`): `doctor.check(probe=True)`,
  cached 30 s.
- **UI**: `team.js` (lazy, like practice.js), navigation Leads · Team
  (managers only) · Settings, lead pickers on the upload form and the call
  page, Team answers and (managers) the review queue on the Playbook. New
  primitives `.table-scroll`, `.matrix`, `.timeline` (DESIGN.md, design-system
  `#patterns`).

### Phase 4: measured

Rules only (no model server in the cloud sandbox): 4 vCPU, Linux, the solar
fixture filed 30 times across 3 reps, 5 of them for one lead.

| | Time |
|---|---|
| `team()` (matrix, patterns, escalations, queue) | median 4.1 ms |
| `team_member()` (timelines, 1:1 points) | median 1.3 ms |
| `prep()` rules brief, 5 calls | median 1.0 ms |
| Librarian, rules, 30 calls (62 strong moments) | 5 ms; 1 answer proposed |

Screens (`scripts/screens_team.py`, Chromium 1440 and 390 px wide): Leads,
lead, Prep, Team, a rep, Settings, Playbook and the call page, 0 px horizontal
overflow at both widths, no page errors. Keyboard only: Today → Leads → a
lead's Prep → Rehearse this call → the drill page, with a visible focus ring.
Not measured: the Prep writer, the Librarian's model pass and the 1:1 summary
on qwen2.5 (no model server here; tested with scripted models).

### Phase 4: findings

- **The Librarian flooded the queue.** Thirty calls with the same strong line
  gave thirty proposals. The same words for the same criterion are now
  proposed once, also against answers already on file (any status).
- **Anonymising a rep's answer broke it.** "Our 3 kW system costs NPR
  4,20,000" became "costs a different price". A rep's line that only survives
  anonymisation by losing an amount, phone number or email is skipped: a team
  answer must be someone's own words, not a mangled version.
- **Proposed answers looked approved.** The Playbook's review queue showed the
  "Team answer" pill on proposals; they now say "Proposed" until a manager
  approves.
- **A wide table widened the phone page** by 550 px even inside an
  `overflow-x: auto` wrapper: the `.sr-only` "Not scored" text is absolutely
  positioned and its containing block was outside the scroller.
  `.table-scroll` is `position: relative`, and bento children get
  `min-width: 0`.
- Prep labelled the example call's hand-written line "AI-suggested"; it now
  uses the Coaching tab's `lineOrigin` (Example line, Team answer, Written by
  a person, AI-suggested).
- The upload browser test had been failing since the restyle (customer name
  moved into the "More about this call" disclosure); it opens it now.

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
- The chat model is qwen2.5:1.5b on the i7-1255U (set as a user environment
  variable, `SEL_CHAT_LLM_MODEL`): 4.4 s median per AI-customer reply in the
  json format, above the 2 to 3 s target. The text format and the warm-up
  (20 Oct) are not yet measured on a real model: run `scripts/drill_latency.py
  --format text` and `--format json` on the i7 and the M2 and record both here.
  Windowing to the last N lines was not done: it would change the start of
  every prompt and stop the server reusing it.
- Phase 4's model passes (Prep writer, Librarian, 1:1 summary) are tested only
  with scripted models; measure them on the demo machines.
- Model briefs (Prep, 1:1) are cached in memory: a restart rebuilds them on
  the next view.
- Leads have no delete and no merge yet; a lead's rep is not inferred from its
  calls.

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
