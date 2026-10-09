# Evaluation

We plan to publish accuracy and speed numbers, including weak ones. `python -m sel eval <example> --pack <pack>` scores one call's `CallReport` against a hand-annotated `gold.json`. The current examples score fixture output, so their numbers say nothing about any model; they only check that the harness works.

## What is measured

| Metric | Why it matters |
|---|---|
| Commitment precision and recall (matched by owner and overlapping segments) | Missing a promise is the core failure Sel exists to prevent |
| Due-status agreement (`exact`, `ambiguous` or `missing`) | Sel must tell "Friday" apart from "sometime next week" |
| **Invented dates**: predicted `exact` where the annotator said it wasn't | Should be zero. This is the "refuses to invent" claim |
| Claim detection precision and recall; verdict accuracy | Accuracy of the claim checker |
| **False supported**: `supported` where gold says contradicted or unverified | The costliest claim error, because it tells a rep a wrong claim is fine |
| Question, objection and coaching precision and recall | Each false finding costs the reviewer's trust |
| Rejected by the evidence gate | How often the model tries to cite something that isn't there |
| Stage timings (`transcribe_s`, `analyze_s`, `post_process_s`) | "Runs on an ordinary laptop" needs numbers |

Still to add once audio is in the loop:

- Transcription word error rate (WER) and character error rate (CER), reported separately for Nepali, English and code-switched segments
- How accurately speakers are labelled
- Real-time factor: processing time divided by call length, on a named machine

## Building the test set

- **Internal set.** Real sales recordings, kept private and never committed (see `.gitignore`), used for development numbers.
- **Public set.** Re-enacted calls written to cover the hard cases, plus real calls where every speaker gave written consent to publication.

Each call needs:

1. `transcript.json`: a corrected reference transcript in the `Transcript` format, which is also the reference for WER
2. `gold.json`: questions, objections, coaching points (with scorecard criterion), claims (with verdict), and commitments (with owner, channel, `due_status` and `due_date`)
3. The business pack it belongs to

Hard cases to cover deliberately:

- A commitment where the date is clearly agreed (control case)
- *"sometime next week"*, *"pachhi"*, *"next Friday"*, *"3 baje"* with no morning or afternoon
- A customer commitment, which should be tracked but get no draft
- A claim that is true, one that is wrong, and one that the documents don't cover
- A claim that is almost right, such as a 10-year warranty that is only available with a paid plan
- Code-switching in the middle of a sentence; romanized and Devanagari Nepali
- Crosstalk, a poor phone line, background noise

Two annotators should label a sample of calls independently. Report how often they agree; where agreement is low, the metric is unreliable.

## Reporting

For each model combination, publish the machine, the model versions and quantization, the test set version, and every metric above. Add a short list of example failures with links to the call moments.
