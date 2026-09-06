"""The tool-calling loop: ask Ollama to assign the pending chores.

This runs inline in a Django request (the plan's explicit v1 choice), so
everything here is built around not hanging or exploding in a web view:

* the loop is bounded by `OLLAMA_MAX_ITERATIONS` and each HTTP call by
  `OLLAMA_TIMEOUT`;
* every way the model or the network can misbehave comes back as an
  `AgentResult` with `ok=False` and a readable `error`, never as an
  exception for the view to turn into a 500;
* `ToolError` from `tools.py` is fed back to the model as the tool's result,
  per that module's contract, so a hallucinated id costs one turn instead of
  the whole run. Any other exception out of a tool is a real bug and is left
  to propagate.

The client is injected rather than imported, so the tests can replay a canned
Ollama transcript with no server and no monkeypatching.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from django.conf import settings

from . import tools
from .schemas import SYSTEM_PROMPT, TOOLS, USER_PROMPT


class TransportError(Exception):
    """Ollama could not be reached, or did not answer with usable JSON.

    The client normalises every network-level failure into this so the loop
    has exactly one thing to catch.
    """


# Name → adapter. The adapters exist so a model that omits an argument gets a
# `ToolError` refusal (which it can recover from) rather than a `TypeError`
# out of the tool function, which the loop would treat as a bug and re-raise.
TOOL_REGISTRY = {
    "get_people": lambda arguments: tools.get_people(),
    "get_pending_chores": lambda arguments: tools.get_pending_chores(),
    "get_history": lambda arguments: tools.get_history(arguments.get("person_id")),
    "assign_chore": lambda arguments: tools.assign_chore(
        arguments.get("chore_id"), arguments.get("person_id")
    ),
}


@dataclass(frozen=True)
class Assignment:
    """One chore the model actually got assigned (as `assign_chore` confirmed it)."""

    chore_id: int
    chore_name: str
    person_id: int
    person_name: str


@dataclass(frozen=True)
class ToolCall:
    """One tool the model asked for, and how it went. Diagnostics only."""

    name: str
    arguments: Any
    ok: bool
    result: Any = None
    error: str = ""


@dataclass
class AgentResult:
    """Everything the assign page needs to render one agent run.

    `ok` is about the *run*, not the outcome: a run that reached the model and
    came back is `ok` even if the model assigned nothing. Only a transport or
    protocol failure sets it False, and only then is `error` non-empty.
    `message` is always a complete sentence safe to show a user.
    """

    ok: bool = True
    message: str = ""
    assignments: list[Assignment] = field(default_factory=list)
    reasoning: str = ""
    error: str = ""
    unassigned: list[str] = field(default_factory=list)
    iterations: int = 0
    tool_calls: list[ToolCall] = field(default_factory=list)
    refusals: list[str] = field(default_factory=list)


class OllamaClient:
    """Minimal `/api/chat` client — stdlib only, no `ollama` package.

    The loop only ever calls `chat(messages=..., tools=...)`, which returns the
    decoded response body or raises `TransportError`. Anything with that one
    method can be injected in its place.
    """

    def __init__(self, host=None, model=None, timeout=None, urlopen=urllib.request.urlopen):
        # Settings are read here rather than at import time so `override_settings`
        # (and a plain env change between runs) actually takes effect.
        self.host = (host or settings.OLLAMA_HOST).rstrip("/")
        self.model = model or settings.OLLAMA_MODEL
        self.timeout = timeout if timeout is not None else settings.OLLAMA_TIMEOUT
        self._urlopen = urlopen

    def chat(self, messages, tools):
        payload = json.dumps(
            {"model": self.model, "messages": messages, "tools": tools, "stream": False}
        ).encode()
        request = urllib.request.Request(
            f"{self.host}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
        )

        try:
            with self._urlopen(request, timeout=self.timeout) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            # HTTPError subclasses URLError, so it has to be caught first. A 404
            # here almost always means the model was never pulled.
            raise TransportError(
                f"Ollama at {self.host} returned HTTP {exc.code} for model "
                f"'{self.model}'. Is the model pulled (`ollama pull {self.model}`)?"
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise TransportError(
                f"Could not reach Ollama at {self.host} ({exc}). Is `ollama serve` running?"
            ) from exc

        try:
            return json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as exc:
            raise TransportError(
                f"Ollama at {self.host} returned a response that was not JSON."
            ) from exc


def run_assignment(client=None, *, max_iterations=None):
    """Run one assignment pass and return an `AgentResult`.

    `client` is anything with `chat(messages, tools)`; the default builds a real
    `OllamaClient` — but only after the empty-state guards below, so an empty
    roster never costs a network call.
    """
    people = tools.get_people()
    if not people:
        return AgentResult(
            message="There is nobody on the roster yet, so there is nothing to assign.",
        )

    pending = tools.get_pending_chores()
    if not pending:
        return AgentResult(message="No chores are pending, so there is nothing to assign.")

    if client is None:
        client = OllamaClient()
    if max_iterations is None:
        max_iterations = settings.OLLAMA_MAX_ITERATIONS

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": USER_PROMPT},
    ]
    result = AgentResult()
    finished = False

    for iteration in range(1, max_iterations + 1):
        result.iterations = iteration
        try:
            response = client.chat(messages=messages, tools=TOOLS)
        except TransportError as err:
            return _failed(result, str(err))

        reply = response.get("message") if isinstance(response, dict) else None
        if not isinstance(reply, dict):
            return _failed(
                result,
                "Ollama's reply did not contain a message. This usually means the "
                "host is not Ollama, or is running an incompatible version.",
            )

        if reply.get("content"):
            # Keep the newest non-empty text: models often narrate mid-run and
            # then summarise at the end, and it is the summary we want to show.
            result.reasoning = reply["content"].strip()

        calls = reply.get("tool_calls") or []
        messages.append(
            {
                "role": "assistant",
                "content": reply.get("content") or "",
                **({"tool_calls": calls} if calls else {}),
            }
        )

        if not calls:
            finished = True
            break

        for call in calls:
            name, arguments = _parse_call(call)
            messages.append(
                {
                    "role": "tool",
                    "name": name,
                    "content": json.dumps(_execute(name, arguments, result)),
                }
            )

    result.unassigned = [chore["name"] for chore in tools.get_pending_chores()]
    result.message = _summarise(result, finished)
    return result


def _execute(name, arguments, result):
    """Dispatch one tool call, recording it, and return what to send back.

    The return value is always JSON-serialisable — a refusal goes back as
    `{"error": ...}` so the model sees the reason and can correct itself.
    """
    try:
        handler = TOOL_REGISTRY.get(name)
        if handler is None:
            # An invented tool name is refused exactly like an invented id.
            raise tools.ToolError(
                f"There is no tool named {name!r}. Available tools: "
                f"{sorted(TOOL_REGISTRY)}."
            )
        if not isinstance(arguments, dict):
            raise tools.ToolError(
                f"Arguments to {name} must be a JSON object, got {arguments!r}."
            )
        value = handler(arguments)
    except tools.ToolError as err:
        message = str(err)
        result.tool_calls.append(ToolCall(name=name, arguments=arguments, ok=False, error=message))
        result.refusals.append(message)
        return {"error": message}

    result.tool_calls.append(ToolCall(name=name, arguments=arguments, ok=True, result=value))
    if name == "assign_chore":
        result.assignments.append(
            Assignment(
                chore_id=value["chore_id"],
                chore_name=value["chore_name"],
                person_id=value["person_id"],
                person_name=value["person_name"],
            )
        )
    return value


def _parse_call(call):
    """Pull (name, arguments) out of one tool call the model emitted.

    Some models send `arguments` as a JSON string rather than an object. An
    unparseable string is passed through untouched so `_execute` refuses it
    with a message the model can act on.
    """
    function = call.get("function") or {}
    name = function.get("name") or ""
    arguments = function.get("arguments")
    if arguments is None:
        arguments = {}
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            pass
    return name, arguments


def _failed(result, error):
    result.ok = False
    result.error = error
    result.unassigned = [chore["name"] for chore in tools.get_pending_chores()]
    made = len(result.assignments)
    kept = f" {made} assignment(s) made before the failure were kept." if made else ""
    result.message = f"The agent could not finish: {error}{kept}"
    return result


def _summarise(result, finished):
    made = len(result.assignments)
    if not finished:
        return (
            f"The model stopped after the {result.iterations}-step limit without "
            f"finishing. {made} chore(s) assigned, {len(result.unassigned)} still pending."
        )
    if result.unassigned:
        return (
            f"Assigned {made} chore(s). The model left "
            f"{len(result.unassigned)} pending: {', '.join(result.unassigned)}."
        )
    return f"Assigned {made} chore(s) — nothing left pending."
