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

- Live, in-call coaching
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

## Done means

- The workflow runs end to end on an ordinary laptop with no network connection.
- Both packs work with the same build.
- Every finding on screen has a working jump-to-audio link.
- On the internal test set, the numbers in [evaluation.md](evaluation.md) are measured and published, including bad ones.
