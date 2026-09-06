# AI Dev Tools Zoomcamp 2026

Homework for the [DataTalksClub](https://github.com/DataTalksClub) AI Dev Tools
Zoomcamp. Each assignment lives in its own folder and stands on its own —
its own dependencies, its own virtualenv, its own README.

## Homework

| | Project | What it is |
|---|---|---|
| 1 | [`hw1_choremanager/`](hw1_choremanager/) | Django app that assigns household chores fairly, using a local LLM agent that balances cumulative effort rather than rotating |

## Layout

```
hw1_choremanager/       # one self-contained project per assignment
├── pyproject.toml      # its own dependencies and lockfile
├── manage.py
└── README.md           # how to run that assignment
.gitignore              # shared across all of them
```

Each folder is worked on independently:

```bash
cd hw1_choremanager
uv sync
uv run python manage.py runserver
```

Nothing is shared between assignments except this README and `.gitignore`, so
a later homework can use a different framework, a different Python version, or
different libraries without disturbing an earlier one.
