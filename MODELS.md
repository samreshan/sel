# Models and AI dependencies

Sel uses **open-weight models only**, across the whole pipeline, including
small auxiliary steps. Nothing calls a proprietary model service (OpenAI,
Anthropic, Google, Azure OpenAI, Cohere or similar). This file lists every
model and AI-related dependency, what Sel uses it for, where it runs, and its
licence.

Sel itself is Apache-2.0 ([LICENSE](LICENSE)).

## Check it yourself

```bash
python -m sel doctor            # model, endpoint, where it runs, licence; exit code 1 on a proprietary service
python -m sel doctor --json     # the same, machine-readable
```

- `sel/llm.py` refuses proprietary hosts by name (`PROPRIETARY_HOSTS`), even
  when `SEL_LLM_BASE_URL` points at one (tested in `tests/test_llm_client.py`).
- The engine and server have no Python dependencies (`dependencies = []` in
  `pyproject.toml`); every model library below is an optional extra.
- The web app loads nothing from outside the Sel server, so it works offline.
- Retrieval over the business pack is BM25 written in Sel (`sel/retrieval.py`):
  no embedding model and no embedding service.

## Models used by the product

| Role | Default | Runs on | Licence | Notes |
|---|---|---|---|---|
| Call analysis: questions, objections, claims, promises, coaching; claim checks; speaker labels | `qwen2.5:7b` (Qwen2.5-7B-Instruct) through Ollama | this machine | Apache-2.0 | `SEL_LLM_MODEL` picks another model on the same server. Measured on 4 CPU cores: [docs/running.md](docs/running.md#measured-so-far) |
| Speech recognition, uploaded recordings | Whisper `small` through faster-whisper (CTranslate2, int8) | this machine | MIT (weights and both libraries) | `SEL_WHISPER_MODEL` |
| Speech recognition, live simulator calls | Whisper `base` through faster-whisper | this machine | MIT | `SEL_LIVE_WHISPER_MODEL`; why `base`: [CLAUDE.md](CLAUDE.md#live-speech-measured) |
| Analysis when no model answers | `RuleAnalyzer` (cue words + BM25) | this machine | Apache-2.0 (Sel's own code) | Not a model; named as the analyzer in the report |
| Coaching: moment finder, line writer, line judge | the analysis model (`SEL_LLM_*`) | this machine | as above | Background, after the call is reviewable. qwen2.5:7b on an i7-1255U: about 3 minutes per 90-second call ([CLAUDE.md](CLAUDE.md#coaching-measured)) |
| Conversation speed: the Coach's focus choice, practice personas, the AI customer, the debrief Judge | `qwen2.5:1.5b` (Qwen2.5-1.5B-Instruct) through Ollama, set with `SEL_CHAT_LLM_MODEL`; unset `SEL_CHAT_LLM_*` values inherit `SEL_LLM_*` | this machine | Apache-2.0 | 986 MB. On an i7-1255U: median 4.4 s per customer reply (qwen2.5:7b: 12 s) against a 2 to 3 s target (`scripts/drill_latency.py`). Falls back to rules on any error or timeout (60 s) |
| Transfer checks on real calls (Judge) | the analysis model (`SEL_LLM_*`) | this machine | as above | Background, after coaching; a pass needs Sel's rules to agree |
| Prep writer: picks a lead's likely objections and gaps, writes a two-sentence summary | the analysis model (`SEL_LLM_*`), when its server answers | this machine | as above | On the agent queue; Sel's rules brief shows at once. A summary with a number not in the items is dropped. Not yet measured on qwen2.5 |
| Librarian: picks which strong moments and endorsed lines become proposed team answers, labels the objection | the chat model (`SEL_CHAT_LLM_*`) | this machine | as above | On the agent queue; never writes the answer (the rep's own words); falls back to rules (propose every candidate) |
| 1:1 brief summary for a manager | the chat model | this machine | as above | Optional; dropped if it has a number the talking points don't |

### Other open-weight models that work

Any model served by a `/chat/completions` server works (Ollama, llama.cpp
`llama-server`, LM Studio, vLLM). Licences differ by size, so check before
choosing one:

| Model | Licence |
|---|---|
| Qwen2.5 0.5B, 1.5B, 7B, 14B, 32B | Apache-2.0 |
| **Qwen2.5 3B** | **Qwen Research License (non-commercial)**: fine for experiments, not for a business deployment |
| Qwen2.5 72B | Qwen License |
| Qwen3 family | Apache-2.0 |
| Llama 3.x | Llama Community License (open weights, with use restrictions) |
| gpt-oss 20B / 120B | Apache-2.0 (open weights) |

`sel doctor` knows these licences; for any other model it says
"unknown: check the model card".

### Optional hosted inference: Groq

`SEL_LLM_PROVIDER=groq` (with `GROQ_API_KEY`) runs **open-weight** models on
Groq's servers through the same client. The default there is
`openai/gpt-oss-20b`, OpenAI's open-weight model under Apache-2.0 (the
`openai/` prefix is Groq's model name, not OpenAI's API). Transcripts and pack
text leave the machine when this is on, and `sel doctor` says so. The local
server is the default and is what the demo uses.

## Development and test tools (not part of the product)

| Tool | Used for | Licence |
|---|---|---|
| Piper (`piper-tts`) and the voices `en_US-lessac-medium`, `en_US-ryan-medium` | Making the fictional example recordings (`scripts/synth_call.py`) | Piper engine: MIT up to 1.2; newer `piper-tts` releases are GPL-3.0. Each voice has its own model card and dataset licence on Hugging Face (`rhasspy/piper-voices`); check it before reusing the voices |
| Playwright with Chromium | Browser tests and performance scripts | Apache-2.0 |
| Ollama | Local model server installed by `scripts/setup_local.sh` | MIT |

## Removed

- **Claude API client** (`SEL_LLM_PROVIDER=anthropic`): removed. Proprietary.
- **Browser captions** in the simulator (`webkitSpeechRecognition`): removed.
  Chrome sends that audio to its vendor's speech service. Live speech now goes
  only through Whisper on the Sel machine, or people type their lines.
