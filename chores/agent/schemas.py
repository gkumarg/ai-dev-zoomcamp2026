"""What the model is told: the tool schemas and the prompts.

Lifted from `scripts/spike_tool_calling.py`, which is where these were
actually exercised against Ollama. Kept in their own module so the wire
format the model sees can be read (and diffed against the spike) without
wading through the loop that drives it.

The schemas describe the four functions in `tools.py`; `runner.TOOL_REGISTRY`
is what maps a requested name back to one. The two must stay in step —
`tests_agent.py` asserts they do.
"""

SYSTEM_PROMPT = """You assign household chores fairly.

Fairness means evening out CUMULATIVE EFFORT over time, not giving everyone an
equal number of chores. A person who has already done a lot of heavy work
should get less now.

Use the tools to look at the current state, then assign EVERY pending chore to
exactly one person. Call assign_chore once per chore. Only ever use ids that a
tool has given you. When you are done, reply with a short plain-language
explanation of your reasoning."""

USER_PROMPT = "Assign all the pending chores fairly."

# `get_history` deliberately does not expose tools.py's `limit` argument: the
# default keeps the transcript short, and there is nothing useful the model
# would do with a knob for it.
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
