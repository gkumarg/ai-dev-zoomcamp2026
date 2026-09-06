"""Pure-Python tool functions for the chore-assignment agent.

These are the four tools the plan's Ollama tool-calling loop (task 9) will
expose to the LLM. Nothing here talks to Ollama or does I/O beyond the ORM:
no HTTP, no prompt text, no model-specific formatting. That keeps this
module testable without a model running, and keeps the trust boundary
(validating what the model asks for) in one place.

Contract for task 9's loop
---------------------------
Every function here returns plain dicts/lists of str/int/float/bool/None,
so the result can be handed to `json.dumps()` untouched and fed back to the
model as a tool result.

`assign_chore` is the only tool that writes anything, and a tool-calling
model will occasionally call it with a stale, guessed, or hallucinated id.
Rather than let that raise an uncaught exception and kill the agent loop,
invalid input raises `ToolError` — a plain-language `str(err)` describing
what was wrong and, where it's cheap to compute, what valid ids would have
worked. Task 9's loop should catch `ToolError` and send `str(err)` back to
the model as the tool result, so the model can retry with corrected
arguments; any other exception is a real bug and should be left to
propagate.
"""

from django.db import transaction

from ..models import Chore, Person


class ToolError(Exception):
    """Raised by a tool when the model's arguments can't be honoured.

    Callers (task 9's agent loop) should catch this and feed `str(err)` back
    to the model as the tool's result, rather than let it end the loop.
    """


def get_people():
    """Everyone, with their cumulative completed effort.

    Returns a list of {"id", "name", "effort_total"}, heaviest first (same
    ordering as `Person.objects.with_effort_totals()`) — the shape the
    fairness reasoning wants directly, without a query per person.
    """
    return [
        {"id": person.id, "name": person.name, "effort_total": person.effort_total}
        for person in Person.objects.with_effort_totals()
    ]


def get_pending_chores():
    """Chores waiting to be assigned.

    Returns a list of {"id", "name", "effort"}.
    """
    return [
        {"id": chore.id, "name": chore.name, "effort": chore.effort}
        for chore in Chore.objects.filter(status=Chore.Status.PENDING)
    ]


def get_history(person_id, limit=10):
    """One person's recent completions plus their all-time cumulative effort.

    `limit` bounds how many recent `History` rows come back, so a
    long-lived household can't blow up the model's context window; the
    cumulative `effort_total` is still the true all-time sum, independent of
    that truncation, since that's the number the fairness reasoning needs.

    Returns {"person_id", "name", "effort_total", "history_count", "recent"}
    where "recent" is a list of {"chore_id", "chore_name", "effort",
    "completed_at"} (ISO 8601 strings), most recent first (History's default
    ordering), at most `limit` long. `history_count` is the true total, so
    the model can tell when "recent" has been truncated. Raises `ToolError`
    for an unknown person.
    """
    try:
        person = Person.objects.with_effort_totals().get(pk=person_id)
    except (Person.DoesNotExist, ValueError, TypeError):
        raise ToolError(f"There is no person with id {person_id!r}.")

    entries = person.history.select_related("chore")[:limit]
    return {
        "person_id": person.id,
        "name": person.name,
        "effort_total": person.effort_total,
        "history_count": person.history.count(),
        "recent": [
            {
                "chore_id": entry.chore_id,
                "chore_name": entry.chore.name if entry.chore else None,
                "effort": entry.effort,
                "completed_at": entry.completed_at.isoformat(),
            }
            for entry in entries
        ],
    }


@transaction.atomic
def assign_chore(chore_id, person_id):
    """Assign a pending chore to a person.

    Refuses (raising `ToolError`, never a bare `DoesNotExist`/`TypeError`)
    rather than corrupt state, when:
    - either id isn't a plain integer,
    - the chore or person doesn't exist,
    - the chore isn't currently pending (already assigned, or done).

    On success, returns {"chore_id", "person_id", "chore_name",
    "person_name", "status"} with `status` == "assigned".
    """
    chore_id = _require_int(chore_id, "chore_id")
    person_id = _require_int(person_id, "person_id")

    try:
        # Lock the row for the rest of the transaction so two concurrent
        # assignments can't both read "pending" and both win. This is a no-op
        # on SQLite, which has no row locking — it earns its keep only if this
        # ever moves to Postgres.
        chore = Chore.objects.select_for_update().get(pk=chore_id)
    except Chore.DoesNotExist:
        pending_ids = list(
            Chore.objects.filter(status=Chore.Status.PENDING).values_list("id", flat=True)
        )
        raise ToolError(
            f"There is no chore with id {chore_id}. Valid pending chore ids: {pending_ids}."
        )

    if chore.status != Chore.Status.PENDING:
        raise ToolError(
            f"'{chore.name}' (id {chore_id}) is already {chore.status}, not pending, "
            f"so it can't be assigned again."
        )

    try:
        person = Person.objects.get(pk=person_id)
    except Person.DoesNotExist:
        valid = list(Person.objects.values_list("id", "name"))
        raise ToolError(f"There is no person with id {person_id}. Valid people: {valid}.")

    chore.assigned_to = person
    chore.status = Chore.Status.ASSIGNED
    chore.save(update_fields=["assigned_to", "status"])

    return {
        "chore_id": chore.id,
        "person_id": person.id,
        "chore_name": chore.name,
        "person_name": person.name,
        "status": chore.status,
    }


def _require_int(value, field_name):
    """Coerce the model's id argument to an int, or refuse.

    Deliberately liberal about `"7"`: local tool-calling models routinely
    emit ids as strings, and refusing those would fail the assignment over a
    JSON type rather than anything actually wrong. Bools are still rejected
    even though `bool` subclasses `int` — a model sending `true` for an id is
    as confused as one sending `null`, and coercing it to 1 would silently
    assign a real chore to person 1.
    """
    if isinstance(value, bool):
        raise ToolError(f"{field_name} must be an integer id, got {value!r}.")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            pass
    raise ToolError(f"{field_name} must be an integer id, got {value!r}.")
