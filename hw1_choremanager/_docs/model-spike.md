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

Measured on a Windows laptop (CPU inference), thinking left **on**.

| Model | Assigned all 4 | Spread (from 9) | Refusals | Load | Run | Verdict |
|---|---|---|---|---|---|---|
| `qwen3:8b` | 4/4 | **2** | 0 | 91s | **289s** | Correct, far too slow |
| `llama3.2:3b` | | | | | | not yet run |
| `deepseek-r1:latest` | | | | | | not yet run |

**Winner:** _still TBD_ — see the two findings below.

### Finding 1 — the quality is genuinely there

`qwen3:8b` assigned all four chores, invented no ids (zero refusals), and
took the effort spread from 9 down to 2 — near the optimum. It gave Alex,
already on 12, nothing at all. This is the behaviour the whole feature exists
for, and a rotation would not have produced it.

### Finding 2 — 289 seconds is not a web page

The run took **4 minutes 49 seconds**, against an `OLLAMA_TIMEOUT` default of
300s. That is a 10-second margin, on the spike's tiny 3-person fixture. A real
household with more history would blow straight through it.

The plan's v1 choice — agent runs inline in the request — assumed the model
answered in something like a spinner's worth of time. At five minutes it does
not. The options, cheapest first:

1. `--no-think` / `OLLAMA_THINK=0`. qwen3 spends most of that time in its
   reasoning block. **Run the spike both ways before deciding anything else.**
2. A smaller model. `llama3.2:3b` is pulled and untested.
3. Take the deferred Celery work off the backlog and make the run async.

### Finding 3 — the explanation did not match the assignment

The reasoning text said Sam took *Clean bathroom + Bins* and Jo took
*Vacuum + Dishes*. That allocation produces a spread of **3**. The harness
measured **2**, so that is not what it actually did — the real assignment was
one of three other combinations, all better than the one described.

The narrative is post-hoc and only roughly true. That matters here because the
plan makes "the agent explains its reasoning" a feature in its own right, and
this explanation would mislead a user comparing it against the assignments on
screen. Worth checking whether it holds across `--runs 3`: an occasional slip
is one thing, a consistent mismatch is a reason to prefer another model, or to
stop presenting the text as an account of what it did.

Note that this is only detectable because the harness scores the *outcome*
independently of what the model says about it.
