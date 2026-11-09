# CLAUDE.md — LeadGraph

## Project
Agentic lead-research pipeline: discovers companies of a segment in a city, classifies them,
extracts KPIs with evidence, scores them, and exports a spreadsheet plus a coverage report.
Full plan: docs/BUILD_PLAN.md. Data contract: Section 3 of that file.
Deviations from the plan (read before changing behaviour): docs/DECISIONS.md.

## Stack (do not substitute without asking)
Python 3.12, uv, LangGraph (+ langgraph-checkpoint-sqlite), langchain init_chat_model with
langchain-anthropic and langchain-openai, Pydantic v2, httpx (async), trafilatura, selectolax,
tenacity, diskcache, dnspython, typer, rich, openpyxl, pytest.

## Rules
1. Before using LangGraph, LangChain or provider APIs, check the installed version's docs or
   source. APIs change; do not rely on memory for signatures (e.g. Send, interrupt, Command).
2. LLM calls only in nodes marked LLM or agent in docs/BUILD_PLAN.md. Dedupe, scoring,
   validation, coverage and export are pure code.
3. All LLM output goes through structured output into Pydantic models. No free-text parsing.
4. Every extracted value has source_url + verbatim evidence. No evidence → null.
   Never infer or guess names or email addresses.
5. Web access: respect robots.txt, send an identifying User-Agent, max 1 request/second per
   domain, global concurrency from config, 15 s timeout, retries via tenacity.
   Never fetch linkedin.com. The research agent may only fetch the company's own domain.
6. Cache every HTTP fetch and LLM call (diskcache, keyed by request hash) in outputs/.cache.
7. API keys come from env vars or CLI args only (a gitignored `.env` is loaded into the
   environment at CLI start). Never log, print or write them to disk from code.
8. outputs/ is gitignored. Never commit scraped data.
9. Implement only the current milestone's scope. If something out of scope seems necessary,
   stop and ask.
10. Unit-test all pure-code modules. Tests that hit the network are marked `@pytest.mark.live`.
11. Every run writes outputs/<run_id>/report.json: counts, rounds, cache hits, tokens, cost
    estimate, durations.
12. Make small, surgical edits to existing files. Never regenerate a whole existing file.
13. Enforce the budget guard (max Places requests, max LLM cost) before each paid call.
