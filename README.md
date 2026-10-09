# Sel

Sel is an open-source assistant that runs on your own device and helps you review recorded sales calls. Give it a recording and it should produce:

- a transcript with speaker labels and timestamps,
- a few specific coaching points,
- a check of product claims against the business's own documents,
- a record of who promised what, and by when,
- follow-up email and calendar drafts that nobody sends until a person approves them.

It is meant to help a small team answer three questions after every call:

1. **What happened?**
2. **What could I do better?**
3. **What did I promise to do next?**

Every finding links back to the moment in the call that supports it. If the call doesn't support a conclusion, Sel says so and does not guess.

> **Status: early reference pipeline, no models yet.** This repo holds the data contracts, the evidence checks, follow-up date resolution, draft generation, two example business packs and a small evaluation harness. Speech recognition and the language-model analysis are **not integrated**. The example "analysis" is hand-written fixture data that stands in for model output so the rest of the workflow can be built and tested. Nothing here is a measured result, and none of the goals below are claimed to work yet.

## Why Sel

Open-source tools that transcribe calls, score sales conversations or pull out action items already exist. Sel aims to stand out through this combination, each part of which still needs to be proven:

| Goal | Status |
|---|---|
| Fully local processing with lightweight open-weight models | Models not chosen yet. See [docs/architecture.md](docs/architecture.md#model-stages) |
| Calls that mix Nepali and English | Date phrases handled. Transcription accuracy untested |
| Every finding traceable to an exact call moment | Enforced by the evidence gate (`sel/grounding.py`) |
| Product claims checked against the business's own documents | Source quotes verified against the pack. Retrieval not built |
| Follow-ups based only on commitments actually spoken, approved before action | Drafts and date resolution built. Nothing is ever sent |
| Published accuracy and speed numbers | Harness built. No benchmark data yet |

## Try it

You need Python 3.11 or newer. There are no third-party dependencies.

```bash
python -m sel validate-pack packs/demo-solar
python -m sel demo examples/calls/solar-01 --pack packs/demo-solar
python -m sel demo examples/calls/pos-01   --pack packs/demo-pos
python -m sel eval examples/calls/solar-01 --pack packs/demo-solar
python -m unittest discover -s tests -t .
```

The two demos use the same code with two unrelated, fictional businesses: a rooftop-solar installer and restaurant point-of-sale software. Only the business pack changes between them.

The solar call shows the behaviour Sel is built around:

- **A date was agreed, so Sel drafts.** The rep says *"bholi beluka samma … email garchu"* ("I'll email by tomorrow evening"). Sel produces an email draft due 2026-10-08, ready for approval.
- **No date was agreed, so Sel asks for review.** The rep says *"Sometime next week hamro engineer pathaula"* ("We'll send our engineer sometime next week"). The calendar draft is marked `needs_input`, with the reason and the possible window (11–17 Oct). The fixture "model" proposed a specific date; Sel throws that date away and works it out again from the spoken words.
- **Invented evidence is refused.** The fixture also includes an objection quoting words nobody said, and a "supported" verdict that cites a passage missing from the pack. The evidence gate refuses the objection and downgrades the verdict to `unverified`. Both are listed for review; neither is hidden.

## How it works

```
recording → local transcription → speakers + timestamps → call analysis
         → evidence gate → claim checks against the pack → due-date resolution
         → follow-up drafts → human review
```

- **Models propose; code verifies.** A language model may extract findings, but every quote must appear in the cited transcript segments, and every claim verdict must quote a real passage from the business pack.
- **Models copy phrases; code does dates.** The model copies the due phrase word for word, for example *"sukrabar"* ("Friday") or *"sometime next week"*. `sel/dates.py` resolves it relative to when the call happened and reports `exact`, `ambiguous` (with a reason and a window) or `missing`.
- **Nothing is sent.** Drafts are `ready_for_approval` or `needs_input`, and any missing fields are listed. Sel has no code path that sends an email or creates an event.

See [docs/architecture.md](docs/architecture.md).

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
sel/            engine: models, pack loader, dates, evidence gate, drafts, pipeline, eval, CLI
packs/          example business packs (fictional)
examples/calls/ example calls: transcript, fixture analysis, gold annotations
tests/          unit and end-to-end tests (stdlib unittest)
docs/           scope, architecture, pack format, evaluation plan, open decisions
```

## Documents

- [docs/scope.md](docs/scope.md): what the first version includes and leaves out, plus the demo script
- [docs/architecture.md](docs/architecture.md): pipeline stages, data contracts and candidate models
- [docs/business-pack.md](docs/business-pack.md): how to describe a new business
- [docs/evaluation.md](docs/evaluation.md): what we will measure, and how to build a shareable benchmark
- [docs/decisions.md](docs/decisions.md): decisions made so far and questions still open

## Data and consent

Real call recordings must not be committed to this repo; `.gitignore` excludes common audio formats and `recordings/`. A recording goes into a published benchmark only if every speaker agreed to share it. Re-enacted calls are the default for public examples. All businesses, people, prices and policies in `packs/` and `examples/` are fictional.
