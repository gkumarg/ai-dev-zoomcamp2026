# Backlog — Household Chore Manager (Django)

Derived from [`_docs/plan.md`](_docs/plan.md). Ordered so each task lands
something runnable. Tasks are sized to roughly one sitting each.

**Done so far:** Django 5.2 installed, project `chore_manager` + app
`chores` scaffolded, app registered in `INSTALLED_APPS`, initial
`migrate` clean.

---

## Milestone 1 — Data layer

### 1. Define the models
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

### 2. Model-level helpers + tests
- `Person.total_effort()` (or a queryset annotation) — cumulative effort
  from `History`, the number the fairness logic and status page both need.
- `Chore.mark_complete()` — flips status to `done` and writes the
  `History` row in one transaction.

**Done when:** `chores/tests.py` covers effort totals and that completing
a chore writes exactly one history row.

---

## Milestone 2 — CRUD web UI (no agent)

### 3. Base template + URL wiring
`chores/urls.py` included from `chore_manager/urls.py`, a `base.html`
with a nav bar (People / Chores / Assign / Status), and enough plain CSS
to be readable. Home redirects to the chore list.

**Done when:** `runserver` serves every nav link without a 404.

### 4. Roster views
List people, add person (form), remove person (POST + confirm).
Show each person's cumulative effort in the list.

**Done when:** a person can be added and removed end to end in a browser.

### 5. Chore views
List chores (grouped or filterable by status, showing assignee), add
chore with a 1–5 effort field.

**Done when:** chores can be created and are visible with their status.

### 6. Completion tracking
A "Mark complete" action on the chore list/detail that calls
`mark_complete()`, plus the `status` page showing cumulative effort per
person, sorted, so fairness is visible at a glance.

**Done when:** completing a chore moves the numbers on the status page.

> At this point the app is fully usable without any LLM. Good checkpoint
> to commit and tag.

---

## Milestone 3 — Agent

### 7. Tool functions (pure Python, no LLM)
`chores/agent/tools.py` — `get_people()`, `get_pending_chores()`,
`get_history(person)`, `assign_chore(chore_id, person_id)`. Plain
functions over the ORM returning JSON-serializable dicts. `assign_chore`
validates ids and rejects assigning a chore that isn't `pending`.

**Done when:** unit-tested directly, with no Ollama involved.

### 8. Pick the Ollama model (spike)
Try 2–3 local tool-calling models against a scripted 3-people/4-chores
fixture; check they actually emit well-formed tool calls and don't
hallucinate ids. Write the winner and the runners-up into
`_docs/plan.md` under Tech Notes.

**Done when:** a model is named in the repo, with a sentence on why.

### 9. Agent loop
`chores/agent/runner.py` — a tool-calling loop against the Ollama chat
API: send tool schemas, execute requested calls, feed results back,
repeat to a bounded iteration cap. Returns a structured result: the
assignments made plus the model's plain-language reasoning.
- Ollama host/model read from settings (env-overridable).
- Bounded iterations and a request timeout — this runs inline in a view.
- Fail soft: an unreachable Ollama surfaces as an error message, not a 500.

**Done when:** callable from `manage.py shell` and it assigns chores.

### 10. Assign page
A page with an "Assign Chores" button that POSTs, runs the agent
synchronously, and re-renders with the assignments and the reasoning
text. Disable the button and show a spinner while it's working, and show a clear
message when there are no pending chores or no people.

**Done when:** clicking the button visibly assigns pending chores.

### 11. Agent tests with a stubbed client
Fake Ollama client replaying a canned tool-call sequence, so the loop,
the tool dispatch, and the view are all testable offline.

**Done when:** `manage.py test` passes with no Ollama running.

---

## Milestone 4 — Polish

### 12. Seed data
`manage.py seed_demo` — a few people and chores with some history, so
the app demos meaningfully from a fresh database.

### 13. README
Setup, `uv run python manage.py runserver`, Ollama prerequisites, how to
run tests, and a screenshot or two of the assign page.

### 14. Switch to uv-managed dependencies
The plan says `uv run python manage.py runserver`, which wants a
`pyproject.toml` + `uv.lock`. Currently a `.venv` + `requirements.txt`.
Small migration, worth doing before the README is written.

---

## Notes / open questions

- **Project name.** Scaffolded as `chore_manager`; the plan says
  "e.g. `choremanager`". Renaming is cheap now and annoying later — decide
  in task 1 or accept the current name.
- **`SECRET_KEY`** is still the plaintext `startproject` default. Move it
  to an env var before this is hosted anywhere (not needed for grading).
- **Synchronous agent call** is a deliberate v1 choice per the plan. A
  slow local model will block the worker; the iteration cap and timeout in
  task 9 are what keep that bounded.
- **Agent trust.** `assign_chore` validating its own inputs (task 7) is
  what stops a confused model from corrupting state — keep that
  validation in the tool, not in the prompt.
- **Grading rubric** (evals, tracing) is still unconfirmed per the plan.
  If evals are required, the stubbed client from task 11 is the natural
  place to grow them.
