# LeadGraph

Agentic lead-research pipeline: discovers companies of a segment in a city, classifies them,
extracts KPIs with evidence, scores them, and exports a spreadsheet plus a coverage report.

Plan: [docs/BUILD_PLAN.md](docs/BUILD_PLAN.md) · Rules: [CLAUDE.md](CLAUDE.md) ·
Deviations: [docs/DECISIONS.md](docs/DECISIONS.md)

## Status

| Milestone | Scope | State |
|---|---|---|
| M1 | Thin slice: Places search → dedupe → classify → xlsx + report.json | built, offline tests pass |
| M2 | Discovery loop: quadtree sweep, scout agent + approval, list extraction, rounds, Chapman, checkpoints | built, offline tests pass |
| M3 | Research agent (own domain only, ≤ 6 tool calls) → KPIs with evidence → verify_evidence; Send fan-out; `enrich` | built, offline tests pass |
| M4 | Contacts (+ LinkedIn via search), scoring v1, validation (liveness, MX, domain match), review queue, all sheets | built, offline tests pass |
| M5 | Validation helpers: stratified sample → `eval/gold.csv`, per-field accuracy + recall + Chapman | built; needs the live run |
| M7 | Evaluation harness (model comparison on the gold set) + optional Langfuse tracing | built, offline tests pass; needs gold labels |

## Setup (Windows, PowerShell)

```powershell
# once: install uv  →  https://docs.astral.sh/uv/getting-started/installation/
cd leadgraph
uv sync                      # installs Python 3.12 + dependencies into .venv

# keys: persistent user env vars (open a new terminal afterwards)
setx GOOGLE_PLACES_API_KEY "..."
setx ANTHROPIC_API_KEY "..."
setx OPENAI_API_KEY "..."
setx SERPER_API_KEY "..."
```

Keys are read from env vars (or `--places-key` / `--llm-key` / `--search-key`) and are never
logged or written to disk. The disk cache lives in `outputs/.cache`; each run writes to
`outputs/<run_id>/`: `run.json` (options, no keys), `checkpoints.sqlite`, `usage.json`,
`tiles.jsonl`, `companies.jsonl`, `leads.xlsx`, `report.json`.

## Commands

```powershell
uv run pytest                    # offline tests (no network, no keys)
uv run pytest -m live -s         # live smoke tests: 1 Places page, 1 detail, 1 search, 1 LLM call/provider

# M1 thin slice
uv run leadgraph run --city berlin --area mitte --limit 20 --provider anthropic --model claude-haiku-5-5

# M2 discovery (asks once which scouted list sources to use, then runs unattended)
uv run leadgraph discover --city berlin --area mitte --max-places-requests 300
uv run leadgraph discover --city berlin --max-places-requests 4000 --max-llm-cost 5
uv run leadgraph resume <run_id>                  # after Ctrl+C, a crash, or a pause

# M3 enrichment of a finished discovery run (research agent + evidence checks)
uv run leadgraph enrich <discover_run_id> --limit 30 --max-llm-cost 2
uv run leadgraph recall <run_id>                  # recall probes from eval/recall_probes.csv

# M5 validation sample (10 per tier) → label by hand in Excel → accuracy
uv run leadgraph sample <enrich_run_id>           # writes eval/gold.csv (gitignored)
uv run leadgraph accuracy <enrich_run_id>         # per-field accuracy, recall, Chapman

# M7 model comparison on the labelled gold set (needs: uv sync --extra eval)
uv run leadgraph eval <enrich_run_id>             # → eval/results.md + eval/results.png
uv run leadgraph eval <enrich_run_id> --models anthropic:claude-haiku-5-5,openai:gpt-6-luna
# tracing: setx LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY (+ LANGFUSE_HOST) → one trace per company
```

Useful flags: `--approve all|none|1,3` (non-interactive approval), `--source URL` (add a list
page by hand, repeatable), `--no-scout`, `--max-rounds N`, `--places-tier pro|enterprise`.

## How discovery works

1. **Scout agent** (≤ 15 tool calls: `web_search`, `fetch_page`) proposes list pages; you approve.
2. **Places quadtree sweep** per query: tiles whose results hit the 60-result cap split into 4,
   down to 1 km. **List extraction** reads approved pages (with pagination); entries without a
   website get one Places lookup by name.
3. **merge_dedupe** (domain → phone → name + postcode), **measure_yield**; if the round still
   added ≥ 2 % new companies, an LLM proposes new query variants and another round runs.
4. **Place Details** for the final Berlin set (Pro tier), re-dedupe, **classify**, **export**.

Coverage is measured: per-round yield, a saturation curve, a source overlap matrix and a
Chapman capture–recapture estimate (Places vs. list sources), plus hand-picked recall probes.

## Cost notes

Places (New) prices per 1,000 requests (checked 8 Nov 2026): Text Search Pro $32 (5,000
free/month), Text Search Enterprise $35 (1,000 free), Place Details Enterprise $20 (1,000 free).
The default Pro tier sweeps cheaply and pays for details only for Berlin companies.
Calibrate on `--area mitte` first: `report.json → query_stats` shows paid requests per query.
