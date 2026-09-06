# Model spike — picking the Ollama model (backlog task 8)

**Status: run on real hardware. `qwen3:8b` with thinking ON is the choice** —
the only configuration measured that assigns fairly. It takes ~289s per run,
which the synchronous assign page cannot really wear, so the open question is
now latency rather than capability. See [Results](#results).

The spike has to run on a machine with Ollama; it cannot run in the cloud dev
container (no Ollama, no GPU).

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

What helps:

- **`--timeout 600`.** Slow hardware, or a big model, simply needs longer. This
  is the safe fix, and the app's default now matches it.
- **A smaller model.** `granite4:3b` and `llama3.2:3b` are the speed options.
- **`--no-think`** makes qwen3 11× faster — and, measured, wrecks the answer
  (spread 10 against a do-nothing baseline of 9). Use it to characterise a
  model, not to make one usable. See [Finding 1](#finding-1--the-fairness-reasoning-lives-in-the-thinking-block).

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

Measured on a Windows laptop, CPU inference. **Baseline spread is 9** — what
you get by assigning nothing at all. Lower is fairer.

| Model | Thinking | Assigned | Spread | Refusals | Median run | Verdict |
|---|---|---|---|---|---|---|
| `qwen3:8b` | **on** | 4/4 | **2** | 0 | **289s** | Correct. Unusably slow. |
| `qwen3:8b` | **off** | 4/4 | **10** | 0 | **26.5s** | Fast. Worse than useless. |
| `llama3.2:3b` | | | | | | not yet run |
| `deepseek-r1:latest` | | | | | | not yet run |

**Chosen: `qwen3:8b` with thinking ON.** It is the only configuration measured
that actually does the job. The cost is a five-minute page load, which is now
the project's main open problem — see "What this means" below.

**Do not set `OLLAMA_THINK=0` with this model.** It is measured to make the
result worse than not running the agent at all.

### Finding 1 — the fairness reasoning lives in the thinking block

With thinking on, `qwen3:8b` assigned all four chores, invented no ids, and cut
the spread from 9 to 2 — near optimal, giving Alex (already on 12) nothing. A
rotation would not have produced that.

Turning thinking off made it **11x faster and completely wrong**: spread 10,
against a do-nothing baseline of 9. Stable across three runs, so this is the
model's behaviour, not variance.

That is the finding worth keeping. `--no-think` reads like a pure latency
optimisation, and it is not: for this model the thinking block *is* the
fairness reasoning. Removing it does not trade some quality for speed — it
removes the capability while leaving the confident explanation intact.

### Finding 2 — the fast, wrong answer explains itself beautifully

Thinking off, the model said:

> Alex, who has already done the most work, was given the least effort chore
> (Bins). Sam, with the least cumulative effort, was assigned the Dishes. Jo,
> with moderate effort, was given the more demanding tasks.

It states the right principle and then inverts it. Sam, furthest *behind* at 3,
gets the second-smallest chore. Jo, in the middle, gets the two heaviest and
ends up top of the table at 15. Final totals: Alex 13, Sam 5, Jo 15.

Read on its own, that paragraph is entirely convincing. This is why the harness
scores the outcome independently of the model's account of it — eyeballing the
explanation would have passed this straight through.

### Finding 3 — with thinking on, the narrative lags the actions

The thinking-on run described an allocation that computes to a spread of 3,
while the harness measured 2. It under-described work that was actually better
than its summary claimed — the final message appears to be written from the
plan in the thinking block rather than from the tool calls it ended up making.

Less alarming than Finding 2, but the same lesson: the explanation is not a
reliable record of what happened. With thinking off the narrative matched the
actions exactly — and the actions were wrong. Accurate narration and good
decisions are separate properties, and this model has them one at a time.

### What this means

The plan's v1 choice to run the agent inline in a request assumed a model
answered in about a spinner's worth of time. Measured, the only configuration
that works takes five minutes. Something has to give:

1. **Measure `llama3.2:3b`** (pulled, untested). If a small non-reasoning model
   can hold the spread near 2, the problem disappears. Given what thinking-off
   did to qwen3, temper expectations.
2. **Pull the deferred async work forward.** Keep thinking on, run the agent off
   the request path, poll for the result. This is the honest fix, and the plan
   already lists it under Deferred.
3. **Accept the wait**, with a clear "this takes a few minutes" on the page.
   Fine for a graded demo; not for anything real.

Whatever wins, the timeout must clear 289s with headroom — `OLLAMA_TIMEOUT`
now defaults to 600.
