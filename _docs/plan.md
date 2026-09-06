# Household Chore Manager — Homework Spec

**Course:** AI Dev Tools Zoomcamp (DataTalksClub)
**Interface:** CLI (Python)
**Persistence:** SQLite
**LLM:** Local via Ollama, tool-calling agent

## Goal

Build a CLI tool that manages shared household chores and uses an LLM
agent to fairly assign chores based on effort-weighted history, not
just rotation or raw chore count.

## Data Model (SQLite)

- `people` — id, name
- `chores` — id, name, effort (1–5), status (pending/assigned/done)
- `history` — id, person_id, chore_id, effort, completed_at

## Features (4 total)

### 1. Roster management
- `add-person <name>`
- `remove-person <name>`
- `list-people`
- Plain CRUD, no agent involved.

### 2. Chore management
- `add-chore <name> --effort <1-5>`
- `list-chores`
- Plain CRUD, no agent involved.

### 3. Agentic assignment (core feature)
- `assign`
- Tool-calling agent (Ollama model with function-calling support)
- Agent has access to these tools:
  - `get_people()`
  - `get_pending_chores()`
  - `get_history(person)` — cumulative effort completed recently
  - `assign_chore(chore_id, person_id)`
- Agent reasons over current effort balance across people and
  assigns pending chores to even it out.
- Agent prints its reasoning in plain language alongside the
  assignment result.

### 4. Status / completion tracking
- `complete-chore <id>` — marks a chore done, logs an entry to `history`
- `status` — shows cumulative effort per person, to verify fairness
  over time

## Deferred / Future Work (not built in v1)

- `remind` — natural-language nudge for whoever's behind
- Preference-awareness (factoring in dislikes/likes)
- Separate `report` command (folded into `status` for v1)

## Tech Notes

- LLM: Ollama, model TBD — must support tool/function calling
  reliably (verify before committing to a specific model).
- No API costs; local inference only.
- Grading rubric (evals, tracing, etc.) not yet confirmed against
  course requirements — to be checked separately.