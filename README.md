# LeadGraph

Agentic lead-research pipeline: discovers companies of a segment in a city, classifies them,
extracts KPIs with evidence, scores them, and exports a spreadsheet plus a coverage report.

Plan: [docs/BUILD_PLAN.md](docs/BUILD_PLAN.md) · Standing rules: [CLAUDE.md](CLAUDE.md)

## Status

| Milestone | Scope | State |
|---|---|---|
| M1 | Thin slice: Places search → dedupe → classify → xlsx + report.json | built, offline tests pass |

## Setup (Windows, PowerShell)

```powershell
# once: install uv  →  https://docs.astral.sh/uv/getting-started/installation/
cd leadgraph
uv sync                      # installs Python 3.12 + dependencies into .venv

# keys: persistent user env vars (open a new terminal afterwards)
setx GOOGLE_PLACES_API_KEY "..."
setx ANTHROPIC_API_KEY "..."
setx OPENAI_API_KEY "..."
setx SERPER_API_KEY "..."     # used from M2
```

Keys are read from env vars (or `--places-key` / `--llm-key`) and are never logged or written
to disk. The disk cache lives in `outputs/.cache`; run outputs in `outputs/<run_id>/`.

## Commands

```powershell
uv run pytest                                  # offline tests (no network, no keys)
uv run pytest -m live -s                       # live smoke tests: 1 Places page + 1 LLM call per provider

uv run leadgraph run --city berlin --area mitte --limit 20 --provider anthropic --model claude-haiku-5-5
uv run leadgraph run --city berlin --area mitte --limit 20 --provider openai --model gpt-6-luna
uv run leadgraph run --help
```

Budget flags: `--max-places-requests` (default 300) and `--max-llm-cost` (USD, default 2.0).
Defaults live in `config/settings.yaml`; model ids and prices in `config/models.yaml`.
