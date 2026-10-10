# Decisions and open questions

## Decided in this repo

| Decision | Why |
|---|---|
| The engine and review app use only the standard library; models are optional extras | Easy to run anywhere. Speech and LLM backends install separately |
| Models sit behind two protocols, `Transcriber` and `Analyzer` | Candidates can be swapped in and measured with the same harness |
| Defaults: faster-whisper `small`, Qwen 2.5 7B through Ollama, both on CPU | 3B was fast but missed most questions and promises on the English test call; 7B found them. See [running.md](running.md#measured-so-far) |
| LLM backends: any OpenAI-compatible endpoint, or Claude via the official SDK | One code path covers local servers and most hosted APIs |
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

## Open

1. **Licence.** The repo has none yet; choose one before the first public release. Apache-2.0 and MIT are common for this kind of tool; AGPL would keep hosted forks open.
2. **Desktop packaging.** The app runs as a local web server today. Wrapping it in Tauri or Electron, and bundling Ollama and the models, is open.
3. **Operating systems.** Which ones are supported in v1?
4. **Speech model for Nepali.** Whisper `small` and `large-v3-turbo` were both weak on a synthetic Nepali clip. Needs measuring on real code-switched calls; Nepali fine-tunes are candidates. Script (romanized or Devanagari) also affects date phrases.
5. **Analysis model on the target laptop.** 7B takes about 5 minutes per 90-second call on 4 CPU cores here. Which machine defines "ordinary laptop", and is a GPU or Apple Silicon assumed?
6. **Diarization for mono recordings.** Today the LLM labels speakers from the words. An acoustic diarization model (e.g. pyannote) would be more reliable but heavier.
7. **Absolute dates.** Should v1 support "Kartik 5" (Bikram Sambat) and "the 12th", or keep them as `ambiguous`?
8. **Recipient details.** Where do customer email addresses come from: typed in at review time, or imported from a contacts file?
9. **Benchmark consent.** We need a written consent form and a policy for removing recordings from the public set.
10. **Coaching volume.** How many coaching points per call is "a small number": three? Should it be configurable per pack?
