# Decisions and open questions

## Decided in this repo

| Decision | Why |
|---|---|
| The engine and review app use only the standard library; models are optional extras | Easy to run anywhere. Speech and LLM backends install separately |
| Models sit behind two protocols, `Transcriber` and `Analyzer` | Candidates can be swapped in and measured with the same harness |
| Defaults: faster-whisper `small`, Qwen 2.5 7B through Ollama, both on CPU | 3B was fast but missed most questions and promises on the English test call; 7B found them. See [running.md](running.md#measured-so-far) |
| Open-weight models only, everywhere: a local `/chat/completions` server by default, Groq-hosted open-weight models as an opt-in. Proprietary model services (OpenAI, Anthropic, Google, Azure OpenAI, Cohere) are refused by host name, and browser captions were removed because Chrome sends that audio to its vendor | Frogtoberfest rule (open, self-hostable AI across the whole pipeline) and the privacy promise. `sel doctor` checks it; [MODELS.md](../MODELS.md) lists every model and licence |
| Licence: Apache-2.0 | Permissive, compatible with the Apache-2.0 models Sel defaults to, usable by businesses |
| The model points at segments; code supplies the quoted words | Small models under schema-constrained decoding paraphrased and looped when asked to copy text |
| Three narrow extraction prompts per chunk instead of one broad one | The broad prompt returned nothing useful from a 3B model; narrow ones did |
| A `supported` verdict must share its numbers with the cited passage | Cheap guard against the costliest error; caught a wrong "supported" on NPR 2,000 vs 2,500 |
| The review app is a local web server bound to 127.0.0.1, vanilla JS, no external resources | Works offline; packaging into a desktop shell stays open |
| Stereo recordings (one side per channel) give speaker labels; mono falls back to the LLM | Exact when the phone setup allows it; the report says which method was used |
| Every finding must cite transcript segments, and quotes must be verbatim | Makes jump-to-audio possible and makes invented evidence detectable |
| Claim verdicts other than `unverified` must quote the pack verbatim | A verdict the user can't check isn't shown |
| The model names the due words; code checks they were said and resolves dates | Date arithmetic is cheap to get right in code and easy to get wrong in a small model |
| Unclear dates become `needs_input` with a reason and window; they are never guessed | This is the behaviour the demo needs to show |
| Sel never sends or schedules anything | Approval before action |
| Customer commitments are tracked but get no draft | The rep can't act on them directly. A reminder feature may come later |
| `week_starts_on` is set per pack | "Next week" depends on the local working week (Sunday in Nepal) |
| Example packs and calls are fictional | Safe to publish |
| The call simulator is part of the app: two Sel sessions call each other over WebRTC, the server only relays setup | No media server; audio stays between the two browsers and the Sel machine |
| Simulator next actions are set, not sent: Sel files the call, analyses it and drafts follow-ups; people approve and send | Same rule as uploaded calls |
| A code-only rules analyzer stands in when no language model answers, and is named in the report | Simulated calls get drafts and a scorecard on any machine; it never gives a claim a verdict |
| Live transcription uses Whisper `base` by default | Median 2.2 s from end of speech to line on 4 CPU cores, against 6.1 s for `small`, same accuracy on the English test call |
| Server-Sent Events, not WebSockets, for pushes and WebRTC signalling | Works with the standard-library server; one stream per tab stays within browsers' six connections per origin |
| Static files are content-hashed and cached; API reads carry ETags | A reload transfers about 1 kB instead of 450 kB |
| Coaching is the product: moments, better lines and a weekly focus per rep, built on the reviewed call | Review answers "what happened"; coaching answers "what next", and needs the models (README, "How the AI works") |
| Suggested lines go through a code gate (`check_line`): cite the pack, same numbers, same subject, no flipped "does not", conditions kept; refused lines are shown with reasons | The model writes words now, so the "no false supported" rule extends to what Sel tells a rep to say |
| A call is marked done before it is coached | A model coaching pass takes minutes on CPU; the review should not wait for it |
| The Coach only proposes; code validates against a whitelist and state; one active focus; it never replaces an active focus (a manager's included) | Agents act through code, so a wrong model answer cannot change a rep's plan |
| Focus ranking without a model: calls fell short on, then objections, discovery, next step, talk ratio, claims | Practice drills train conversation skills; wrong claims are fixed by the playbook and escalated |
| Two clients: `SEL_LLM_*` for analysis and coaching, `SEL_CHAT_LLM_*` for the Coach and AI customer | Conversation needs a fast model; analysis can be slower and more accurate |
| The AI customer replies in plain text over chat turns, warmed up when the drill room opens; code decides when the call ends and the mood (`SEL_CUSTOMER_FORMAT=json` keeps the schema format) | Fewer tokens, no JSON grammar, and each prompt extends the last so the server reuses it; qwen2.5:1.5b set `end_call` and mood unreliably anyway. To be measured on the i7 and M2 |
| The demo is prepared by `sel demo-setup` against the running server, and its calls go through the real models | The example call carries hand-written stand-ins; a judge should see model output, and setup should not be a page of manual steps |
| Practice personas are anonymised by code before any prompt and named from a fictional list | Real calls are private; the practice customer must not carry a real customer's name, number or price |
| Drills are typed first; the AI customer is labelled on every line | Text works on any machine and is easy to test; voice is a stretch |
| The Judge's pass needs Sel's rules to agree where they measure the skill; disagreement is "unsure" | A false pass would close a focus the rep has not earned |
| A transfer pass on one later real call closes the focus (owners, 16 Oct 2026) | The loop's measure is the skill showing up on a real call; more calls can be required later |
| Demo machine: an Intel i7-1255U laptop (CPU only) or an Apple M2; measure on both. Chat model qwen2.5:1.5b, analysis model qwen2.5:7b (owners, 16 Oct 2026) | On the i7 the AI customer answers in 4.4 s median against a 2 to 3 s target; the M2 is expected to be faster and is still to be measured |
| Drills are English only for now (owners, 16 Oct 2026) | Nepali personas and speech come after the English loop works |
| Switzer stays under its Personal Use Only licence for now (owners, 16 Oct 2026) | Fine for the Frogtoberfest demo; a commercial release must license it or switch to an open font (Inter and Geist are already bundled) |
| Escalate to a manager only for a contradicted claim on a real call, a drill failed three times, or moving on from a manager's focus | Managers see what needs them; the Coach handles the rest |
| Practice calls (simulator, drills) never count towards skills, focus or transfer | Practice is where reps try things out |
| Playground objections come from the pack (`[[objections]]`) and from the team's reviewed calls (anonymised), and are practised as drills with the AI customer; the Coach never sees them (no escalation, difficulty or focus change). The single typed reply for objections was retired | Generic objections hard-coded in the engine broke "business behaviour lives in packs", and one typed reply with a structure check was the weakest use of AI in the product |
| In a drill on an objection, the customer's opening counts as the objection for Sel's rules, cue words or not | The rules spot objections by cue words; "Inverters break all the time" has none, so the drill's own objection could not be judged |
| What the team learns lives in an overlay next to the calls (`playbook/<pack>.json`); packs are never written to | Packs are the business's documents; team answers are reviewed separately and can be rejected |
| A team answer is always someone's own words (a rep's line from a real call, or a better line a person endorsed); the Librarian's model only picks and labels | The model points; code supplies the words. A rep's line that only survives anonymisation by losing an amount is skipped |
| Only a manager approves or rejects a team answer, or marks an escalation handled ("Viewing as", no login) | Approved answers coach every rep, so a person who owns the team decides; without login this is a convenience, not access control |
| Approved answers become the better line when no line passed (or no model wrote one), labelled "Team answer" and checked by `check_line` | The flywheel: what worked on a real call coaches the next one, with the same claim gate |
| Prep and the 1:1 brief show Sel's rules at once; the model's version is built on the agent queue and replaces it | A 7B model takes tens of seconds on CPU; a page should not wait for it |
| The AI customer softens only when Sel's rules say the rep has earned it: one real question per difficulty level, one more if the rep defended first; it won't fix a day before that. Code tells the model where it stands each turn and replaces a line that gives in early | qwen2.5:1.5b gave in after one exchange, so drills tested nothing. The model points; code decides, as for the ending and the mood |
| The coaching model looks for moments one scorecard skill at a time, with the lines Sel's rules noticed as hints (`SEL_MOMENTS_PROMPT=single` keeps the one-request prompt) | qwen2.5:7b, asked for every skill at once, found no strong moments and missed the NEA-timing claim on solar-01. One skill per request is a smaller task for a small model; the transcript comes first so the server reuses it across the requests. To be measured on the i7 and M2 |
| Leads are thin (name, phone, rep, notes, calls); not a CRM | Prep needs a lead's earlier calls, nothing more (scope: no CRM integrations) |

## Open

1. ~~**Licence.**~~ Decided: Apache-2.0 (table above).
2. **Desktop packaging.** The app runs as a local web server today. Wrapping it in Tauri or Electron, and bundling Ollama and the models, is open.
3. **Operating systems.** Which ones are supported in v1?
4. **Speech model for Nepali.** Whisper `small` and `large-v3-turbo` were both weak on a synthetic Nepali clip. Needs measuring on real code-switched calls; Nepali fine-tunes are candidates. Script (romanized or Devanagari) also affects date phrases.
5. **Analysis model on the target laptop.** 7B takes about 5 minutes per 90-second call on 4 CPU cores here. Which machine defines "ordinary laptop", and is a GPU or Apple Silicon assumed?
6. **Diarization for mono recordings.** Today the LLM labels speakers from the words. An acoustic diarization model (e.g. pyannote) would be more reliable but heavier.
7. **Absolute dates.** Should v1 support "Kartik 5" (Bikram Sambat) and "the 12th", or keep them as `ambiguous`?
8. **Recipient details.** Where do customer email addresses come from: typed in at review time, or imported from a contacts file?
9. **Benchmark consent.** We need a written consent form and a policy for removing recordings from the public set.
10. **Coaching volume.** How many coaching points per call is "a small number": three? Should it be configurable per pack?
11. **Simulator across the internet.** No STUN/TURN server is configured by default, so calls work on one machine or one network. Run one, or document a provider?
12. **Login.** Serving to the network (`--host 0.0.0.0`) has no authentication. Needed before the simulator is used beyond a trusted office.
13. **Live Nepali transcription.** Whisper `base` garbles romanized Nepali; try `initial_prompt` with pack vocabulary and time words, or a Nepali model.
14. ~~**Demo laptop.**~~ Decided: i7-1255U or Apple M2 (table above). Still to do: measure on the M2.
15. **Who confirms a focus.** Today the Coach sets it and a person can change it; should a manager confirm each one?
16. ~~**How many real-call passes close a focus.**~~ Decided: one (table above).
17. ~~**Drill language.**~~ Decided: English only for now. ~~**Switzer licence.**~~ Decided: Personal Use Only for now (table above).
18. **Leads**: delete and merge, and whether a lead's rep should follow its latest call.
19. **Team answers across packs**: an answer is per pack today; a team selling two products may want to share some.
