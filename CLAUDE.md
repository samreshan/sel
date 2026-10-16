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

## Commands

```bash
python -m unittest discover -s tests -t .                 # 117 tests; the 3 browser tests skip without Playwright
python -m sel serve                                       # http://127.0.0.1:8765, loads the two example calls
python -m sel doctor                                      # models in use, where they run, licences; fails on a proprietary service
python -m sel serve --host 0.0.0.0 --tls-cert c.pem --tls-key k.pem   # simulator across computers (mic needs https)
python -m sel eval examples/calls/solar-01 --pack packs/demo-solar --analyzer rules   # rules vs gold
python -m sel demo examples/calls/pos-01 --pack packs/demo-pos --analyzer rules
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
| `sel/analyzer_llm.py`, `sel/llm.py` | LLM analysis (narrow prompts, schema-constrained); one `/chat/completions` client for local servers or Groq (`SEL_LLM_PROVIDER=groq`), proprietary hosts refused |
| `sel/doctor.py` | `sel doctor`: model, endpoint, where it runs, licence; exit 1 on a proprietary service |
| `sel/asr.py` | faster-whisper: whole recordings (`transcribe`) and live windows (`transcribe_pcm`) |
| `sel/live.py` | Code-only rules: per-line detection, signals over a transcript, `LiveScorer` (score + next actions), `RuleAnalyzer` |
| `sel/scoring.py` | Scorecard grading from signals (live or from a reviewed report) |
| `sel/simulator.py` | `SimulatorHub`: rooms, presence, WebRTC signal relay, lines, `PcmSegmenter`, live Whisper worker, filing the call |
| `sel/events.py` | In-process pub/sub behind the Server-Sent Events stream |
| `sel/service.py` | Calls on disk, the analysis job, review actions, caches, events |
| `sel/server.py` | HTTP API, static files, `/api/events`, simulator routes, TLS |
| `sel/web/app.js` | Review app; exposes `window.Sel` helpers |
| `sel/web/sim.js` | Simulator UI, lazy-loaded on first `#sim` |
| `sel/web/pcm-worklet.js` | Audio worklet: microphone → 16 kHz PCM chunks |
| `tests/` | stdlib unittest; `test_e2e_browser.py` drives Chromium |
| `scripts/perf_ui.py`, `scripts/simulate_call.py` | Reproduce the measurements below |

Call folder (`calls/<id>/`): `call.json`, `audio.<ext>`, `live_transcript.json`
(simulated calls), `transcript.json`, `analysis.json`, `review.json`,
`report.json` (now with a `score` key).

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
