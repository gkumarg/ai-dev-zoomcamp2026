# Homework 1 — Household Chore Manager

A Django web app that shares out household chores **fairly** — by evening out
cumulative *effort* over time, not by rotating or counting chores. The
assignment itself is made by a local LLM agent (via [Ollama](https://ollama.com))
that reads the current state through tools and explains its reasoning in
plain language.

Built for the [AI Dev Tools Zoomcamp](https://github.com/DataTalksClub) 2026.
Full spec in [`_docs/plan.md`](_docs/plan.md); implementation plan in
[`backlog.md`](backlog.md).

## Why an agent

Every chore carries an effort weight from 1 to 5. Rotating chores or splitting
them evenly by count is unfair the moment the chores differ in size — five
minutes of taking the bins out is not an hour of cleaning the bathroom. The
agent looks at what everyone has *already* completed and hands the next round
to whoever is behind.

## Setup

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync                                   # create the venv and install Django
uv run python manage.py migrate
uv run python manage.py seed_demo         # optional: demo people, chores, history
uv run python manage.py runserver
```

Then open http://localhost:8000.

`seed_demo` creates a deliberately lopsided starting point (Alex on 12 effort,
Jo on 7, Sam on 3) so the agent has something real to balance. Re-running it is
safe; `--reset` starts clean.

### The agent (optional but it's the point)

The four CRUD features work without any of this. For the **Assign** page you
need Ollama running locally with a tool-calling model:

```bash
ollama serve
ollama pull qwen3:8b
```

No API keys, no costs — inference is entirely local.

Configuration, all via environment variables:

| Variable | Default | |
|---|---|---|
| `OLLAMA_HOST` | `http://localhost:11434` | where Ollama is listening |
| `OLLAMA_MODEL` | `qwen3:8b` | must support tool calling |
| `OLLAMA_TIMEOUT` | `600` | seconds per model call |
| `OLLAMA_THINK` | unset | `0` disables thinking — see the warning below |
| `OLLAMA_MAX_ITERATIONS` | `12` | model turns before the run is cut off |

> **Do not set `OLLAMA_THINK=0` with qwen3.** It makes the run 11x faster and
> the answer wrong — the spike measured an effort spread of 10, worse than the
> 9 you get by assigning nothing, explained just as confidently. For this model
> the thinking block *is* the fairness reasoning. Details in
> [`_docs/model-spike.md`](_docs/model-spike.md).

**Expect the assign page to take several minutes.** `qwen3:8b` was measured at
~289s per run on a CPU laptop, plus up to 90s to load the model the first time.
That is the honest cost of a local model that reasons correctly; making it
responsive is the project's main open problem. If the page reports that Ollama
did not finish in time, raise `OLLAMA_TIMEOUT` rather than disabling thinking.

`qwen3:8b` is the default because it was measured, not because it was
recommended: see [`_docs/model-spike.md`](_docs/model-spike.md) for the numbers
and `scripts/spike_tool_calling.py` to run the comparison on your own hardware.

## Using it

- **People** — add and remove household members; shows each person's
  cumulative completed effort.
- **Chores** — add chores with an effort of 1–5; filter by pending / assigned /
  done; mark them complete.
- **Assign** — press the button and the agent assigns every pending chore,
  showing what it decided and why.
- **Status** — cumulative effort per person, so you can check the fairness is
  actually holding up over time.

The agent runs **synchronously inside the request** (a deliberate v1 choice —
see the plan). Measured, that is around five minutes of the page sitting there
while the model thinks. It was a reasonable simplification before anyone knew
the latency; it is now the main thing wrong with the design.

## How the agent works

```
chores/agent/
├── tools.py     # the 4 functions the model can call — pure ORM, no LLM
├── schemas.py   # the JSON tool schemas and prompts the model is sent
└── runner.py    # the tool-calling loop against Ollama's /api/chat
```

The model gets four tools: `get_people()`, `get_pending_chores()`,
`get_history(person_id)` and `assign_chore(chore_id, person_id)`. It reads the
current effort balance and calls `assign_chore` once per pending chore.

`assign_chore` is the trust boundary. A local model will occasionally invent a
chore id or try to reassign something already done — every tool validates its
arguments and refuses with a plain-language message naming the valid options,
which is fed back to the model so it can correct itself. A confused model
costs a turn; it cannot corrupt the database.

Everything that can go wrong on the way to a local model — it isn't running,
the model isn't pulled, it times out, it loops forever — comes back as a
rendered message on the page rather than an exception.

## Tests

```bash
uv run python manage.py test
```

The suite runs entirely offline: the agent tests inject a fake Ollama client,
so no model and no network are needed.

## Project layout

```
chore_manager/       # Django project (settings, root urls)
chores/              # the single app: models, views, templates, agent
scripts/             # spike_tool_calling.py — the model comparison harness
_docs/               # plan.md (spec), model-spike.md (model selection)
backlog.md           # implementation plan and remaining work
```
