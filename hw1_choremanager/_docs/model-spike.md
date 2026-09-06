# Model spike — picking the Ollama model (backlog task 8)

**Status: harness built, shortlist researched, winner NOT yet chosen.**
The spike has to run on a machine with Ollama; it could not be run in the
cloud dev container (no Ollama, no GPU). Run it locally and fill in the
Results table below, then record the winner in `_docs/plan.md`.

## Running it

```bash
ollama serve                  # in another terminal
ollama pull qwen3:8b          # and each candidate you want to try
python scripts/spike_tool_calling.py --runs 3
```

Stdlib only — no venv, no Django, no `ollama` package needed. Options:
`--models qwen3:8b llama3.1:8b`, `--host`, `--runs`, `--timeout`, `--no-think`.

Models you have not pulled are listed and skipped, so only what is actually
installed gets tested. Each model is loaded once before timing starts — a cold
model can spend a minute-plus loading, and counting that against the first run
makes a fast model look slow.

### If it times out

The first real run of this hit `HTTP 500` after exactly `2m0s` on `qwen3:8b`,
with the model taking 82 seconds just to load. That was the client's own
timeout expiring mid-generation; Ollama logged the dropped connection as a 500.

Three things help, in order of how much:

- **`--no-think`.** qwen3 is a reasoning model and writes a long thinking block
  before it ever emits a tool call. Turning that off is usually the difference
  between twenty seconds and a timeout. It costs some reasoning quality, which
  is worth measuring both ways — run it with and without.
- **`--timeout 600`.** Slow hardware, or a big model, simply needs longer.
- **A smaller model.** `granite4:3b` is on the shortlist precisely for this.

A model that cannot answer inside a sane timeout is a finding, not just an
inconvenience: v1 runs the agent inline in a Django request, so whatever it
takes here is what someone stares at a spinner for.

## What it measures

The fixture is deliberately lopsided: Alex has 12 cumulative effort, Jo has
7, Sam has 3, and four chores (effort 2, 3, 1, 5) are pending. A model that
understands the task assigns **all four** chores, gives Alex **none**, and
lands the effort spread well below its starting value of 9. A model that
round-robins scores visibly worse — which is the entire point of using an
agent here rather than a sort.

Per run it reports: chores assigned, final effort spread, tool calls made,
**refusals** (calls naming ids that do not exist — the failure mode that
matters), wall-clock seconds, and whether it produced reasoning text.

Two things worth watching:

- **Refusals.** The harness refuses bad calls exactly the way
  `chores/agent/tools.py` does, and feeds the refusal back as a tool result.
  A good model self-corrects; a bad one loops until the iteration cap. A
  model with a nonzero refusal count is not disqualified — recovering
  gracefully is realistic — but one that never recovers is.
- **Seconds.** v1 runs the agent inline in a Django request (plan's explicit
  choice). Anything much past ~30s makes the assign page feel broken and
  argues for a smaller model or the deferred Celery work.

`--runs 3` matters: tool calling is stochastic, and a model that gets it
right one time in three is not usable.

## Shortlist

Chosen for tool-calling reliability at sizes that run on a laptop. As of
September 2026, the Qwen3 line is the most consistently recommended local
family for structured tool calls, with Llama 3.1+, IBM's Granite 4, and
Mistral Small 3.2 as the alternates. Anything with `vl`/`vision`/`llava` in
the name is out — vision variants generally drop tool support.

| Model | Size | Why it's on the list |
|---|---|---|
| `qwen3:8b` | ~5 GB | Best-reported reliability-per-GB for tool calling; start here |
| `qwen3:14b` | ~9 GB | Fallback if 8b drops or malforms calls |
| `llama3.1:8b` | ~5 GB | Widely deployed baseline; tools supported since the 3.1 line |
| `mistral-small3.2:24b` | ~15 GB | Improved function calling, if you have the VRAM |
| `granite4:3b` | ~2 GB | Apache-2.0, trained for tool calling; the speed option |

Sources: [Ollama tool-calling docs](https://docs.ollama.com/capabilities/tool-calling),
[Best local models for tool calling 2026](https://www.promptquorum.com/power-local-llm/best-local-models-tool-calling-2026),
[Which Ollama models support tool calling](https://www.betterclaw.io/blog/ollama-models-tool-calling-support),
[Best Ollama models 2026](https://www.morphllm.com/best-ollama-models).

These rankings are secondary sources, not our measurements — the point of
the harness is that you don't have to trust them.

## Results

Fill in from your local run:

| Model | Assigned all 4 | Spread | Refusals | Median secs | Verdict |
|---|---|---|---|---|---|
| `qwen3:8b` | | | | | |
| | | | | | |

**Winner:** _TBD_ — record it in `_docs/plan.md` Tech Notes with one
sentence on why, and set it as the default in `settings.py`
(`OLLAMA_MODEL`).
