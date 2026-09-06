#!/usr/bin/env python3
"""Spike harness for backlog task 8: which local Ollama model can actually
drive our tool-calling agent?

Runs each candidate model against a fixed, deliberately unbalanced fixture
and scores what matters for this app:

  * does it emit well-formed tool calls at all?
  * does it invent chore/person ids that do not exist? (the failure mode that
    makes `assign_chore` refuse and the loop spin)
  * does every pending chore get assigned exactly once?
  * does the result actually even out the effort, or does it just round-robin?
  * how slow is it? this runs inline in a Django request in v1.

Deliberately standalone: stdlib only, no Django, no `ollama` package. Run it
before wiring anything up.

    ollama serve                       # in another terminal
    ollama pull qwen3:8b               # and each other candidate
    python scripts/spike_tool_calling.py

    python scripts/spike_tool_calling.py --models qwen3:8b llama3.1:8b
    python scripts/spike_tool_calling.py --runs 3   # tool calling is stochastic

Write the winner into _docs/plan.md under Tech Notes.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.error
import urllib.request

DEFAULT_HOST = "http://localhost:11434"

# Shortlist as of Sept 2026 — see _docs/model-spike.md for why these five.
DEFAULT_MODELS = [
    "qwen3:8b",
    "qwen3:14b",
    "llama3.1:8b",
    "mistral-small3.2:24b",
    "granite4:3b",
]

MAX_ITERATIONS = 12
REQUEST_TIMEOUT = 120

SYSTEM_PROMPT = """You assign household chores fairly.

Fairness means evening out CUMULATIVE EFFORT over time, not giving everyone an
equal number of chores. A person who has already done a lot of heavy work
should get less now.

Use the tools to look at the current state, then assign EVERY pending chore to
exactly one person. Call assign_chore once per chore. When you are done, reply
with a short plain-language explanation of your reasoning."""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_people",
            "description": "List everyone in the household with their cumulative completed effort.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_pending_chores",
            "description": "List chores that are not yet assigned to anyone.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_history",
            "description": "Recent completed chores for one person, and their cumulative effort.",
            "parameters": {
                "type": "object",
                "properties": {
                    "person_id": {
                        "type": "integer",
                        "description": "The id of the person, from get_people.",
                    }
                },
                "required": ["person_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "assign_chore",
            "description": "Assign one pending chore to one person.",
            "parameters": {
                "type": "object",
                "properties": {
                    "chore_id": {
                        "type": "integer",
                        "description": "The id of the chore, from get_pending_chores.",
                    },
                    "person_id": {
                        "type": "integer",
                        "description": "The id of the person, from get_people.",
                    },
                },
                "required": ["chore_id", "person_id"],
            },
        },
    },
]


class Fixture:
    """In-memory stand-in for the ORM, with the same refusal contract as
    `chores.agent.tools` — so a model that trips over this will trip over the
    real thing too."""

    def __init__(self):
        # Alex is way ahead on effort; a fair result gives Alex little or nothing.
        self.people = {
            1: {"id": 1, "name": "Alex", "total_effort": 12},
            2: {"id": 2, "name": "Sam", "total_effort": 3},
            3: {"id": 3, "name": "Jo", "total_effort": 7},
        }
        self.chores = {
            10: {"id": 10, "name": "Dishes", "effort": 2, "status": "pending"},
            11: {"id": 11, "name": "Vacuum", "effort": 3, "status": "pending"},
            12: {"id": 12, "name": "Bins", "effort": 1, "status": "pending"},
            13: {"id": 13, "name": "Clean bathroom", "effort": 5, "status": "pending"},
        }
        self.assignments: dict[int, int] = {}
        self.refusals: list[str] = []

    def get_people(self):
        return list(self.people.values())

    def get_pending_chores(self):
        return [c for c in self.chores.values() if c["status"] == "pending"]

    def get_history(self, person_id=None, **_):
        person = self.people.get(person_id)
        if person is None:
            return self._refuse(f"No person with id {person_id}. Valid ids: {sorted(self.people)}.")
        return {"person": person["name"], "total_effort": person["total_effort"], "recent": []}

    def assign_chore(self, chore_id=None, person_id=None, **_):
        chore = self.chores.get(chore_id)
        if chore is None:
            return self._refuse(
                f"No chore with id {chore_id}. Pending chore ids: "
                f"{sorted(c['id'] for c in self.get_pending_chores())}."
            )
        if person_id not in self.people:
            return self._refuse(f"No person with id {person_id}. Valid ids: {sorted(self.people)}.")
        if chore["status"] != "pending":
            return self._refuse(f"'{chore['name']}' is already {chore['status']}.")

        chore["status"] = "assigned"
        self.assignments[chore_id] = person_id
        self.people[person_id]["total_effort"] += chore["effort"]
        return {"ok": True, "assigned": chore["name"], "to": self.people[person_id]["name"]}

    def _refuse(self, message):
        self.refusals.append(message)
        return {"error": message}

    def dispatch(self, name, arguments):
        handler = getattr(self, name, None)
        if handler is None or name not in {t["function"]["name"] for t in TOOLS}:
            return self._refuse(f"No tool named {name!r}.")
        if not isinstance(arguments, dict):
            return self._refuse(f"Arguments to {name} must be an object, got {type(arguments).__name__}.")
        try:
            return handler(**arguments)
        except TypeError as exc:
            return self._refuse(f"Bad arguments to {name}: {exc}")


def is_timeout(exc):
    """Did this fail because we ran out of patience, or because nothing answered?

    Worth distinguishing: "Ollama is not running" and "the model is still
    generating" look identical in a traceback and call for opposite fixes.
    urllib reports read timeouts either directly or wrapped in a URLError.
    """
    return isinstance(exc, TimeoutError) or isinstance(
        getattr(exc, "reason", None), TimeoutError
    )


def installed_models(host, timeout=10):
    """What Ollama already has locally, or None if we couldn't ask."""
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=timeout) as response:
            body = json.loads(response.read())
    except Exception:  # noqa: BLE001 - preflight is a convenience, never fatal
        return None
    return {entry.get("name", "") for entry in body.get("models", [])}


def chat(host, model, messages, timeout=REQUEST_TIMEOUT, think=None, tools=TOOLS):
    body = {"model": model, "messages": messages, "stream": False}
    if tools is not None:
        body["tools"] = tools
    if think is not None:
        # Reasoning models (qwen3 and friends) emit a long thinking block before
        # the tool call. Turning it off is often the difference between 20s and
        # a timeout — at some cost to reasoning quality, which is the tradeoff
        # this spike exists to measure.
        body["think"] = think
    request = urllib.request.Request(
        f"{host}/api/chat",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def warm_up(host, model, timeout, think=None):
    """Load the model before timing anything.

    A cold model can spend a minute-plus loading before it generates a token.
    Counting that against the first run makes a fast model look slow and can
    blow the timeout outright.
    """
    started = time.monotonic()
    chat(
        host,
        model,
        [{"role": "user", "content": "Reply with the word ready."}],
        timeout=timeout,
        think=think,
        tools=None,
    )
    return time.monotonic() - started


def run_once(host, model, timeout=REQUEST_TIMEOUT, think=None):
    """One full agent loop. Returns a scorecard."""
    fixture = Fixture()
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "Assign all the pending chores fairly."},
    ]
    tool_calls_made = 0
    started = time.monotonic()

    for _ in range(MAX_ITERATIONS):
        reply = chat(host, model, messages, timeout=timeout, think=think)["message"]
        messages.append(reply)

        calls = reply.get("tool_calls") or []
        if not calls:
            break

        for call in calls:
            function = call.get("function", {})
            name = function.get("name", "")
            arguments = function.get("arguments", {})
            # Some models emit arguments as a JSON string rather than an object.
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError:
                    arguments = {"_unparseable": arguments}
            tool_calls_made += 1
            result = fixture.dispatch(name, arguments)
            messages.append({"role": "tool", "name": name, "content": json.dumps(result)})

    elapsed = time.monotonic() - started
    totals = [p["total_effort"] for p in fixture.people.values()]
    final_text = next(
        (m.get("content", "") for m in reversed(messages)
         if m.get("role") == "assistant" and m.get("content")),
        "",
    )

    return {
        "seconds": round(elapsed, 1),
        "tool_calls": tool_calls_made,
        "refusals": len(fixture.refusals),
        "assigned": len(fixture.assignments),
        "of_pending": 4,
        "spread": max(totals) - min(totals),  # lower is fairer; 9 if it does nothing
        "gave_alex_least": fixture.assignments and
            sum(1 for p in fixture.assignments.values() if p == 1) == 0,
        # The plan requires the agent to explain itself, so the only failure
        # here is saying nothing at all. Judging brevity by a word count marks
        # good, concise answers as failures — read the quoted text instead.
        "explained": bool(final_text.strip()),
        "reasoning": final_text.strip()[:300],
        "refusal_samples": fixture.refusals[:3],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--runs", type=int, default=1,
                        help="Repeat each model; tool calling is stochastic.")
    parser.add_argument("--timeout", type=int, default=REQUEST_TIMEOUT,
                        help=f"Seconds per model call (default {REQUEST_TIMEOUT}). "
                             "Raise this on slow hardware.")
    parser.add_argument("--no-think", dest="think", action="store_const", const=False,
                        default=None,
                        help="Ask reasoning models (qwen3 and friends) to skip their "
                             "thinking block. Much faster, and often the difference "
                             "between a result and a timeout.")
    args = parser.parse_args()

    print(f"Ollama at {args.host}, {args.runs} run(s) per model, "
          f"{args.timeout}s timeout"
          f"{', thinking off' if args.think is False else ''}\n")
    print("A fair result assigns all 4 chores, gives Alex (effort 12) none,")
    print("and lands the spread well below the starting 9.\n")

    # Ask up front rather than firing a request per missing model and reading
    # four identical 404s.
    available = installed_models(args.host)
    if available is None:
        print(f"! Could not read {args.host}/api/tags — is `ollama serve` running?\n")
    else:
        missing = [model for model in args.models if model not in available]
        if missing:
            print("Not pulled, skipping: " + ", ".join(missing))
            print("   " + "  ".join(f"ollama pull {model}" for model in missing) + "\n")
            args.models = [model for model in args.models if model in available]
        if not args.models:
            print("Nothing to test. Pull one of the candidates and re-run.")
            return

    for model in args.models:
        print(f"── {model}")

        try:
            load_seconds = warm_up(args.host, model, args.timeout, args.think)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:200].strip()
            print(f"   HTTP {exc.code} on warm-up — {detail or 'no detail'}\n")
            continue
        except Exception as exc:  # noqa: BLE001 - a spike should survive a bad model
            if is_timeout(exc):
                print(f"   timed out loading after {args.timeout}s. This model is very "
                      f"slow to load here — try `--timeout {args.timeout * 3}`, a smaller "
                      f"model, or check it fits in memory.\n")
            else:
                print(f"   warm-up failed — {type(exc).__name__}: {exc}\n")
            continue
        print(f"   loaded in {load_seconds:.0f}s (not counted below)")

        results = []
        for run in range(args.runs):
            try:
                results.append(run_once(args.host, model, args.timeout, args.think))
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode(errors="replace")[:200].strip()
                print(f"   run {run + 1}: HTTP {exc.code} — {detail or 'no detail'}\n")
                break
            except Exception as exc:  # noqa: BLE001 - a spike should survive a bad model
                if is_timeout(exc):
                    # The server is up — it answered the warm-up. This is the
                    # model being too slow, which is itself a finding.
                    print(f"   run {run + 1}: timed out after {args.timeout}s mid-run. "
                          f"Retry with `--timeout {args.timeout * 3}`"
                          f"{'' if args.think is False else ' or `--no-think`'}. "
                          f"A model this slow is a poor fit for a synchronous view.\n")
                elif isinstance(exc, urllib.error.URLError):
                    print(f"   run {run + 1}: lost the connection to Ollama ({exc.reason}).\n")
                else:
                    print(f"   run {run + 1}: crashed — {type(exc).__name__}: {exc}\n")
                break

        if not results:
            continue

        for run, result in enumerate(results, start=1):
            print(f"   run {run}: {result['assigned']}/{result['of_pending']} assigned, "
                  f"spread {result['spread']}, {result['tool_calls']} tool calls, "
                  f"{result['refusals']} refused, {result['seconds']}s"
                  f"{'' if result['explained'] else ', NO reasoning text'}")
            for refusal in result["refusal_samples"]:
                print(f"      refused: {refusal}")
        if results[0]["reasoning"]:
            print(f'   says: "{results[0]["reasoning"]}"')
        if args.runs > 1:
            print(f"   median time {statistics.median(r['seconds'] for r in results)}s, "
                  f"assigned all {sum(1 for r in results if r['assigned'] == 4)}/{len(results)} runs")
        print()


if __name__ == "__main__":
    main()
