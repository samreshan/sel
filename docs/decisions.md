# Decisions and open questions

## Decided in this repo

| Decision | Why |
|---|---|
| The engine is a dependency-free Python 3.11+ package with JSON in and out | Easy to run anywhere. Can sit behind whichever desktop or UI shell is chosen later |
| Models sit behind two protocols, `Transcriber` and `Analyzer` | The final models aren't chosen; candidates can be swapped in and measured with the same harness |
| Every finding must cite transcript segments, and quotes must be verbatim | Makes jump-to-audio possible and makes invented evidence detectable |
| Claim verdicts other than `unverified` must quote the pack verbatim | A verdict the user can't check isn't shown |
| The model copies due phrases; code resolves dates | Date arithmetic is cheap to get right in code and easy to get wrong in a small model |
| Unclear dates become `needs_input` with a reason and window; they are never guessed | This is the behaviour the demo needs to show |
| Sel never sends or schedules anything | Approval before action |
| Customer commitments are tracked but get no draft | The rep can't act on them directly. A reminder feature may come later |
| `week_starts_on` is set per pack | "Next week" depends on the local working week (Sunday in Nepal) |
| Example packs and calls are fictional | Safe to publish |

## Open

1. **Licence.** The repo has none yet; choose one before the first public release. Apache-2.0 and MIT are common for this kind of tool; AGPL would keep hosted forks open.
2. **Desktop shell and UI stack.** Options include Tauri, Electron, or a local web server opened in the browser. The choice depends on how audio playback and model processes will be bundled.
3. **Operating systems.** Which ones are supported in v1?
4. **Speech model.** Which model works best on Nepali–English code-switching, and does it output romanized or Devanagari text? This affects quoting and date phrases.
5. **Analysis model.** Which size and quantization fits an "ordinary laptop", and which machine defines that term?
6. **Diarization.** A model, two-channel recordings, or both?
7. **Absolute dates.** Should v1 support "Kartik 5" (Bikram Sambat) and "the 12th", or keep them as `ambiguous`?
8. **Recipient details.** Where do customer email addresses come from: typed in at review time, or imported from a contacts file?
9. **Benchmark consent.** We need a written consent form and a policy for removing recordings from the public set.
10. **Coaching volume.** How many coaching points per call is "a small number": three? Should it be configurable per pack?
