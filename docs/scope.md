# Scope of the first version

## Who it's for

A small sales team, initially two to ten people, selling by phone. Many calls mix Nepali and English. The team reviews calls after they end. Recordings are sensitive, so the team does not want to upload them to a hosted AI service.

## The one workflow to finish

1. **Set up a business.** Load a business pack with documents, approved facts, a scorecard and templates.
2. **Add a recording.** Recordings are processed after the call ends.
3. **Review the call.** An audio player sits next to a timestamped, speaker-labelled transcript.
4. **Review insights.** Sel shows customer questions, objections, commitments and a small number of coaching points. Clicking one jumps to that moment in the audio.
5. **Check claims.** Each factual product claim is marked supported, contradicted or unverified, and shows the passage from the business pack.
6. **Prepare follow-ups.** Sel drafts editable emails and calendar events from commitments. A person approves every draft. If the call didn't fix a date, Sel asks for one instead of guessing.

Then run the same workflow with a second, unrelated business pack and no code changes.

## Out of scope for v1

- Live, in-call coaching on real customer calls. (The call simulator scores practice calls live; real calls are still reviewed after they end.)
- Sending email or creating calendar events automatically. Sel's output is a draft, an `.ics` file, or a `mailto:` link, all waiting for approval.
- CRM integrations
- Team dashboards and trends across many calls
- Absolute dates, including Bikram Sambat dates. These resolve as `ambiguous` so a person checks them.
- Languages other than Nepali and English

## Proposed main screen (not designed yet)

```
┌───────────────────────────────┬──────────────────────────────┐
│ ▶ ───●──────────── 01:13/01:30 │ [Coaching] [Claims] [Follow-ups] │
│                               │                              │
│ 00:51 Asha   NEA approval chai │ ⚠ contradicted              │
│              normally duita    │ "inverter ma pani 10 years   │
│              hapta ma aaucha.  │  warranty cha"   ▶ 00:27     │
│ 01:02 Asha   Huncha, ma bholi  │ docs/warranty.md: "Inverters │
│              beluka samma …    │  carry a 5-year manufacturer │
│ 01:13 Asha   Sometime next     │  warranty …"                 │
│              week hamro …      │                              │
└───────────────────────────────┴──────────────────────────────┘
```

Each card shows evidence first: the call moment, which plays when clicked, and, for a claim, the passage from the pack.

## Demo script (about 5 minutes)

1. Open the solar pack and show its documents, facts and scorecard.
2. Load `solar-01`. Click the price objection to jump to 00:22.
3. Claims tab: the inverter warranty is **contradicted**, with the warranty document shown. The NEA approval timeline is **unverified**, and Sel shows that it refused the model's invented source.
4. Follow-ups tab: the email due tomorrow evening is ready for approval. The site visit "sometime next week" is **needs input**. Sel shows the words used and the possible window, then asks the user to pick a day.
5. Switch to the POS pack and load `pos-01` without changing any code. The demo agreed on Sunday at 3pm (*"diuso 3 baje"*), so Sel produces a ready calendar invite.
6. Show the evaluation table and the timings measured on the demo laptop.

## Coaching loop demo (5 minutes, English, network off)

Recorded with Wi-Fi off on either demo machine (the i7-1255U laptop or the
M2). Everything below runs on the laptop: Ollama with `qwen2.5:7b` (analysis
and coaching) and `qwen2.5:1.5b` (`SEL_CHAT_LLM_MODEL`, the Coach and the AI
customer), Whisper through faster-whisper.

**Before recording (network on, once).**

1. `scripts/setup_local.sh`, then `ollama pull qwen2.5:1.5b` and set
   `SEL_CHAT_LLM_MODEL=qwen2.5:1.5b`. Run one upload so Whisper `small` is
   downloaded and cached.

**Getting ready (network off).**

2. `python -m sel serve`, then in a second terminal `python -m sel demo-setup`.
   It checks that every model is open and answering and that speech
   recognition is installed (it stops and says what to fix otherwise), adds
   **Asha Gurung** (rep), **Mina Shrestha** (manager) and the lead **Ramesh
   Thapa**, uploads the solar-01 recording as an ordinary call for Asha, and
   waits while the real models transcribe, analyse and coach it (about 6
   minutes on the i7 with qwen2.5:7b; not yet measured on the M2). The Coach
   then sets Asha's focus (price objections) and makes a drill; Mina's Team
   page shows the escalation for the 10-year inverter claim if the model
   judged it contradicted. Running it again reuses everything already there.
3. About 10 minutes before recording: `python -m sel demo-setup --next`. It
   makes the "next real call" with Piper (the rep asks what the other quote
   includes before defending the price, the `asks what the other quote
   includes` case in `examples/calls/solar-01/transfer_gold.json`) into
   `demo/demo-solar-asks.wav` and uploads it for Asha after her focus is set,
   so its transfer check runs after its coaching. `--next-audio file.wav` uses
   your own recording instead.

The calls `demo-setup` files are analysed by the models on the laptop. The
example call `example-solar-01` (loaded at start-up) carries hand-written
stand-ins labelled "Example line"; use it only if the model's lines on the
real call include no refused line to show (qwen2.5:7b wrote a safe "I'll
confirm" line for the inverter moment in our run, so nothing was refused).

**The demo.**

| Time | Screen | What to show |
|---|---|---|
| 0:00 | Calls → Ramesh Thapa (the call `demo-setup` filed) → Coaching tab | The price objection: what the customer said, what Asha said, why it fell short, the better line (AI-suggested, "Checked against your pack", with its passage). Open "Sel refused 1 line": the 10-year warranty line and the reason (the pack gives 10 years only with the paid Care Plan). |
| 1:00 | Practise this (90 s) | The drill: the real moment, the anonymised AI persona. Start the call; the AI customer opens with the objection (labelled AI). Type "What does the other quote include: which panels and inverter?" The customer warms up. End and get feedback: Passed, judged by AI and Sel's rules, the real answer beside the practice answer and the better line. |
| 2:30 | Today (Viewing as Asha) | This week's focus, the real moment and the better line, the drill and its attempt. |
| 3:15 | Calls → the new call (solar-asks) | Reviewed and coached on this laptop. Today: the transfer check passed on the real call, quoting the line; the Coach closed the focus ("Done") and chose the next skill. Switch Viewing as to Mina → Team → Asha: the skill timeline (missed, then met) and the 1:1 brief. |
| 4:15 | Settings | Every model, what it is for, "runs on this machine", its licence; "Open models only". In a terminal: `python -m sel doctor` prints the same and ends with OK. |
| 5:00 | | End. Nothing was sent and nothing left the laptop. |

If the transfer check is not done yet at 3:15, show the call's progress and
come back to it after Settings. Rehearse the whole run once on each machine
before recording: it has been checked end to end with Sel's rules and
scripted models (`tests/test_practice.py`, `tests/test_team.py`), not yet with
the live models on the demo laptops.

## Done means

- The workflow runs end to end on an ordinary laptop with no network connection.
- Both packs work with the same build.
- Every finding on screen has a working jump-to-audio link.
- On the internal test set, the numbers in [evaluation.md](evaluation.md) are measured and published, including bad ones.
