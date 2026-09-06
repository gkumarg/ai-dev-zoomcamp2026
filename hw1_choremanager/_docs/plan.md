# Household Chore Manager — Homework Spec

**Course:** AI Dev Tools Zoomcamp (DataTalksClub)
**Interface:** Django web app
**Persistence:** SQLite (Django ORM)
**LLM:** Local via Ollama, tool-calling agent
**Execution model:** Synchronous (agent runs inline within a Django view)

## Goal

Build a Django web app that manages shared household chores and uses
an LLM agent to fairly assign chores based on effort-weighted
history, not just rotation or raw chore count.

## Project Structure

- Single Django project (e.g. `choremanager`)
- Single Django app (e.g. `chores`) holding all models, views, and
  the agent logic
- App registered in `INSTALLED_APPS` in `settings.py`
- Run with: `uv run python manage.py runserver`

## Data Model (Django models / SQLite)

- `Person` — id, name
- `Chore` — id, name, effort (1–5), status (pending/assigned/done)
- `History` — id, person (FK), chore (FK), effort, completed_at

## Features (4 total)

### 1. Roster management
- Pages/views: list people, add person (form), remove person
- Plain CRUD via Django views + templates, no agent involved.

### 2. Chore management
- Pages/views: list chores, add chore (form, includes effort 1–5)
- Plain CRUD via Django views + templates, no agent involved.

### 3. Agentic assignment (core feature)
- A page with an "Assign Chores" button, triggering a view that runs
  the agent synchronously and renders the result on the same page
  once done.
- Tool-calling agent (Ollama model with function-calling support)
- Agent has access to these tools:
  - `get_people()`
  - `get_pending_chores()`
  - `get_history(person)` — cumulative effort completed recently
  - `assign_chore(chore_id, person_id)`
- Agent reasons over current effort balance across people and
  assigns pending chores to even it out.
- Agent's reasoning is displayed in plain language alongside the
  assignment result on the page.

### 4. Status / completion tracking
- View/page: mark a chore complete (updates status, logs a `History`
  entry)
- View/page: `status` — shows cumulative effort per person, to
  verify fairness over time

## Deferred / Future Work (not built in v1)

- `remind` — natural-language nudge for whoever's behind
- Preference-awareness (factoring in dislikes/likes)
- Separate `report` view (folded into `status` for v1)
- Background/async task execution (e.g. Celery) for the agent call
- Splitting into multiple Django apps (e.g. `roster` + `chores`)

## Tech Notes

- LLM: Ollama, model TBD — must support tool/function calling
  reliably (verify before committing to a specific model).
- No API costs; local inference only.
- Grading rubric (evals, tracing, etc.) not yet confirmed against
  course requirements — to be checked separately.