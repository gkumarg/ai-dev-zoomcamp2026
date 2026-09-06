# Backlog — Household Chore Manager (Django)

Derived from [`_docs/plan.md`](_docs/plan.md). Ordered so each task lands
something runnable. Tasks are sized to roughly one sitting each.

**Status: 13 of 14 done.** The app is complete and usable end to end —
roster, chores, completion, status, and the agent-driven assign page —
with 153 tests and 100% statement coverage of the application code.

**The one open task is [#8](#8-pick-the-ollama-model-spike): choosing the
Ollama model.** It needs a machine with Ollama, which the cloud dev
container does not have. The harness is written and self-tested; see
[`_docs/model-spike.md`](_docs/model-spike.md) to run it and record a
winner. Until then `OLLAMA_MODEL` defaults to `qwen3:8b` as an unverified
placeholder.

---

## Milestone 1 — Data layer ✅

### 1. Define the models ✅
Add `Person`, `Chore`, `History` to `chores/models.py` per the plan.
- `Chore.effort` — small integer, validated 1–5.
- `Chore.status` — `TextChoices` (`pending` / `assigned` / `done`),
  default `pending`.
- `Chore.assigned_to` — nullable FK to `Person` (the plan's `assigned`
  status implies one; `History` alone can't answer "who has this now").
- `History` — FK to `Person` and `Chore`, denormalized `effort` snapshot,
  `completed_at` auto-set.
- `Person.name` unique, so "remove-person by name" stays unambiguous.

**Done when:** `makemigrations` + `migrate` run clean, and models are
registered in `chores/admin.py` so data can be poked at via `/admin`.

> Effort is guarded twice — field validators for forms, and a database
> check constraint so callers bypassing the form (the agent, a shell
> session) cannot write a corrupt weight. `History.chore` is `SET_NULL`
> so deleting a chore preserves the effort someone already put in;
> `History.person` is `CASCADE`, so someone leaving the household leaves
> the fairness picture entirely.

### 2. Model-level helpers + tests ✅
- `Person.total_effort()` (or a queryset annotation) — cumulative effort
  from `History`, the number the fairness logic and status page both need.
- `Chore.mark_complete()` — flips status to `done` and writes the
  `History` row in one transaction.

**Done when:** `chores/tests.py` covers effort totals and that completing
a chore writes exactly one history row.

> Shipped as both: `total_effort()` for one person, and
> `Person.objects.with_effort_totals()` annotating `effort_total` for
> lists. The names differ deliberately — an annotation named
> `total_effort` would shadow the method and break `person.total_effort()`
> on annotated instances. `mark_complete()` refuses a second completion,
> which would otherwise double-count effort and skew every fairness
> number invisibly.

---

## Milestone 2 — CRUD web UI (no agent) ✅

### 3. Base template + URL wiring ✅
`chores/urls.py` included from `chore_manager/urls.py`, a `base.html`
with a nav bar (People / Chores / Assign / Status), and enough plain CSS
to be readable. Home redirects to the chore list.

**Done when:** `runserver` serves every nav link without a 404.

### 4. Roster views ✅
List people, add person (form), remove person (POST + confirm).
Show each person's cumulative effort in the list.

**Done when:** a person can be added and removed end to end in a browser.

> Removing a person returns their in-flight chores to the pending pool in
> the same transaction, so nothing is stranded in `assigned` status with a
> null assignee once `SET_NULL` fires.

### 5. Chore views ✅
List chores (grouped or filterable by status, showing assignee), add
chore with a 1–5 effort field.

**Done when:** chores can be created and are visible with their status.

### 6. Completion tracking ✅
A "Mark complete" action on the chore list/detail that calls
`mark_complete()`, plus the `status` page showing cumulative effort per
person, sorted, so fairness is visible at a glance.

**Done when:** completing a chore moves the numbers on the status page.

> At this point the app is fully usable without any LLM. Good checkpoint
> to commit and tag.

---

## Milestone 3 — Agent

### 7. Tool functions (pure Python, no LLM) ✅
`chores/agent/tools.py` — `get_people()`, `get_pending_chores()`,
`get_history(person)`, `assign_chore(chore_id, person_id)`. Plain
functions over the ORM returning JSON-serializable dicts. `assign_chore`
validates ids and rejects assigning a chore that isn't `pending`.

**Done when:** unit-tested directly, with no Ollama involved.

> Refusals raise `ToolError` carrying a plain-language message that names
> the valid ids, which the loop feeds back so the model can correct
> itself. Numeric-string ids (`"7"`) are coerced rather than refused —
> local models emit them constantly — but booleans are rejected, so
> `true` cannot silently become person 1.

### 8. Pick the Ollama model (spike) ⚠️ **OPEN — needs local hardware**
Try 2–3 local tool-calling models against a scripted 3-people/4-chores
fixture; check they actually emit well-formed tool calls and don't
hallucinate ids. Write the winner and the runners-up into
`_docs/plan.md` under Tech Notes.

**Done when:** a model is named in the repo, with a sentence on why.

> **Blocked here, not skipped.** The dev container has no Ollama and no
> route to one, so no model has actually been measured.
>
> What exists: `scripts/spike_tool_calling.py`, a stdlib-only harness that
> scores candidates on assignment completeness, resulting effort spread,
> hallucinated ids and latency, against the same lopsided fixture
> `seed_demo` creates. Self-tested with stubbed good and bad models
> (spread 2 vs 11). A researched shortlist and an empty results table are
> in [`_docs/model-spike.md`](_docs/model-spike.md).
>
> **To finish:** `ollama serve`, pull a couple of candidates, then
> `python scripts/spike_tool_calling.py --runs 3`. Fill in the table,
> name the winner in `_docs/plan.md`, and set `OLLAMA_MODEL`.

### 9. Agent loop ✅
`chores/agent/runner.py` — a tool-calling loop against the Ollama chat
API: send tool schemas, execute requested calls, feed results back,
repeat to a bounded iteration cap. Returns a structured result: the
assignments made plus the model's plain-language reasoning.
- Ollama host/model read from settings (env-overridable).
- Bounded iterations and a request timeout — this runs inline in a view.
- Fail soft: an unreachable Ollama surfaces as an error message, not a 500.

**Done when:** callable from `manage.py shell` and it assigns chores.

> The client is injected rather than imported, and `OllamaClient` takes an
> injected `urlopen`, so both the loop and the network error translation
> are testable offline. Empty roster or no pending chores short-circuit
> before a client is constructed, so they cost no inference.

### 10. Assign page ✅
A page with an "Assign Chores" button that POSTs, runs the agent
synchronously, and re-renders with the assignments and the reasoning
text. Disable/spinner the button while it's working, and show a clear
message when there are no pending chores or no people.

**Done when:** clicking the button visibly assigns pending chores.

> No POST/redirect/GET, per the plan's "renders on the same page"
> requirement: a browser refresh re-runs the agent, which wastes time but
> cannot double-assign, since `assign_chore` only touches pending chores.

### 11. Agent tests with a stubbed client ✅
Fake Ollama client replaying a canned tool-call sequence, so the loop,
the tool dispatch, and the view are all testable offline.

**Done when:** `manage.py test` passes with no Ollama running.

---

## Milestone 4 — Polish ✅

### 12. Seed data ✅
`manage.py seed_demo` — a few people and chores with some history, so
the app demos meaningfully from a fresh database.

> Deliberately lopsided (Alex 12, Jo 7, Sam 3) so a fair assignment has to
> load up Sam rather than rotate. Idempotent, with `--reset`.

### 13. README ✅
Setup, `uv run python manage.py runserver`, Ollama prerequisites, how to
run tests, and a screenshot or two of the assign page.

> Written except the screenshots — those need the app running against a
> real model, so they belong with task 8.

### 14. Switch to uv-managed dependencies ✅
The plan says `uv run python manage.py runserver`, which wants a
`pyproject.toml` + `uv.lock`. Currently a `.venv` + `requirements.txt`.
Small migration, worth doing before the README is written.

> `requirements.txt` removed. `uv sync` and `uv run` verified against a
> clean environment.

---

## Test coverage

153 tests, 100% statement coverage of application code:

| File | Covers |
|---|---|
| `tests.py` (28) | Effort totals, `mark_complete`, the effort constraints, deletion semantics, `__str__` |
| `tests_forms.py` (13) | Form validation, including whitespace stripping stopping a duplicate person |
| `tests_views.py` (35) | Page rendering, POST-only enforcement, form errors, flash messages, filtering |
| `tests_tools.py` (23) | Each tool's shape, JSON serializability, every refusal path |
| `tests_agent.py` (37) | The loop against stubs: recovery, arg formats, the iteration cap, transport failure |
| `tests_integration.py` (8) | The real stack with only the Ollama HTTP call faked |
| `tests_seed.py` (9) | Demo data correctness and safe re-runs |

Mutation-checked: dropping a tool from the registry, and dropping tool
results before they reach the model, both turn the suite red.

**What coverage does not mean here:** the agent has never run against a
real model. Every agent test uses a stub by necessity. Real tool-calling
behaviour is exactly what task 8 measures.

---

## Notes / open questions

- **Project name.** Scaffolded as `chore_manager`; the plan says
  "e.g. `choremanager`". Kept as-is — the plan's name was an example, and
  renaming now would touch settings, imports and the WSGI/ASGI entry
  points for no functional gain.
- **`SECRET_KEY`** is still the plaintext `startproject` default. Move it
  to an env var before this is hosted anywhere (not needed for grading).
- **Synchronous agent call** is a deliberate v1 choice per the plan. A
  slow local model will block the worker; the iteration cap and timeout
  keep that bounded.
- **Agent trust.** `assign_chore` validating its own inputs is what stops
  a confused model corrupting state — that validation lives in the tool,
  not the prompt.
- **Grading rubric** (evals, tracing) is still unconfirmed per the plan.
  If evals are required, `tests_integration.py`'s `FakeOllama` and the
  spike harness are the two natural places to grow them.
