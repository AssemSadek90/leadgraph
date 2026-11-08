# LeadGraph — Agentic Lead Research Platform

## Build plan for Claude Code

> Working name, rename freely. Build one milestone per Claude Code session, in order.
> Do not start a milestone until the previous test gate passes.

---

## Deadlines and schedule

| When | Work | Serves |
|---|---|---|
| **Sun 8 Nov** | M1 thin slice, M2 discovery loop, then start the full Berlin discovery run overnight | Heidi Task 1 |
| **Mon 9 Nov (evening)** | M3 research agent, M4 contacts/scoring/validation, then start the full enrichment run overnight | Heidi Task 2 + bonus |
| **Tue 10 Nov (before 18:00 Berlin)** | M5 validation + Google Sheet, M6 approach doc, submit | Heidi Task 3 + submission |
| **Wed 11 – Thu 12 Nov** | M7 evaluation + tracing, M8 API, M9 UI | Siemens Healthineers (Fri 13 Nov), portfolio |
| Later | Phase C | Portfolio |

**Critical path for Heidi: M1 → M2 → M3 → M4 → M5 → M6.** Everything in Phase B is optional for Tuesday.

### Prerequisites (do these today, before M1)

- [ ] Google Cloud project with **Places API (New)** enabled, billing on, API key restricted to the Places API. Check Google's current Places pricing and free usage. The field mask in this plan includes website and phone, which are billed at a higher tier than basic fields.
- [ ] A web search API key whose search supports `site:` queries (e.g. Serper or Brave Search API).
- [ ] Anthropic and/or OpenAI API key.
- [ ] Python 3.12, `uv`, Claude Code installed.

---

## 0. How to work with Claude Code

1. Create the repo `leadgraph/`. Copy this file to `docs/BUILD_PLAN.md`. Copy Section 4 into `CLAUDE.md` at the repo root.
2. One milestone = one session. Send the milestone's prompt as written.
3. Run the test gate yourself. When it passes, commit and tag: `git tag m1-pass`.
4. If Claude Code proposes out-of-scope work, decline and add it to `docs/BACKLOG.md`.
5. Check the cut line (Section 8) at its checkpoints.

---

## 1. Product

**Input:** city · segment preset (v1: `hausverwaltung`) · LLM provider + model + API key · Google Places key · search API key · budget cap

**Output:** deduplicated, classified companies with KPIs, fit score, contacts, per-field evidence and a coverage report → `.xlsx` (Google Sheets export in Phase C)

### Principles

| # | Principle | Why |
|---|---|---|
| P1 | Code for deterministic work, single LLM calls for judgment, agents only where the next step can't be known in advance | Cheaper, predictable, debuggable |
| P2 | Every extracted value carries a source URL and a verbatim evidence snippet. No evidence → `null` | Auditability, anti-hallucination |
| P3 | Never guess personal data. Emails only if published on the company's own site. LinkedIn is never fetched, only found via search results | GDPR, data quality |
| P4 | Coverage is measured, not claimed | Answers "as many as possible" credibly |
| P5 | Every run is cached, resumable and budgeted | Iteration speed, BYOK safety |

---

## 2. Architecture

```
INPUT: city · segment · provider/model/key · Places key · search key · budget
  │
  ▼
┌──────────────────────────── DISCOVERY LOOP (M1–M2) ────────────────────────────┐
│                                                                                 │
│  plan_sweep ──┬──► places_sweep (quadtree) ───────────────────────┐            │
│               │                                                    │            │
│               └──► scout_sources* ──► approve_sources (HITL) ──► extract_lists  │
│                                                                    │            │
│                                                                    ▼            │
│                                                              merge_dedupe       │
│                                                                    │            │
│       ▲ yes: expand queries / sources ◄──── measure_yield: ≥ 2% new this round? │
└───────────────────────────────────────────────────┬─────────────────────────────┘
                                                    │ no → stop
                                                    ▼
                         Send() fan-out — one branch per company (M3–M4)
            ┌────────────────────────────────────────────────────────────────┐
            │ classify ──(not a property manager → excluded)                 │
            │    └──► research_agent* ──► verify_evidence ──► contacts       │
            └──────────────────────────────┬─────────────────────────────────┘
                                           ▼
                     score ──► validate ──► export (xlsx + coverage report)

* = agent with a bounded tool loop. Everything else is code or a single LLM call.
```

### Nodes

| Node | Type | What it does | Built in |
|---|---|---|---|
| `plan_sweep` | code | Builds tile × query work items for this round | M1, M2 |
| `places_sweep` | code | Places Text Search per tile, pagination, splits tiles that hit the result cap | M2 |
| `scout_sources` | **agent** | Finds pages that list many companies in the city, returns candidates | M2 |
| `approve_sources` | **HITL** | `interrupt()` — you approve which sources to use | M2 |
| `extract_lists` | LLM | Extracts company entries from approved list pages, follows pagination | M2 |
| `merge_dedupe` | code | Entity resolution: domain → phone → normalized name + postcode | M1, M2 |
| `measure_yield` | code | New-unique rate, stop decision, coverage statistics | M2 |
| `classify` | LLM | Is this really a property manager? | M1 |
| `research_agent` | **agent** | Navigates the company's own site, extracts KPIs with evidence | M3 |
| `verify_evidence` | code | Checks each evidence snippet exists in the cached page text | M3 |
| `contacts` | LLM + code | People, roles, published emails; LinkedIn URL via search | M4 |
| `score` | code | Weighted, explainable fit score | M4 |
| `validate` | code | Liveness, email MX, domain match, review flags | M4 |
| `export` | code | xlsx with all sheets + `report.json` | M1, M4 |

### Repo layout

```
leadgraph/
├── CLAUDE.md
├── pyproject.toml
├── docs/
│   ├── BUILD_PLAN.md
│   └── BACKLOG.md
├── config/
│   ├── models.yaml                     # provider → model ids
│   ├── segments/hausverwaltung.yaml    # queries, prompts, KPI weights
│   └── cities/berlin.yaml              # bbox, test areas, postcode prefixes
├── src/leadgraph/
│   ├── cli.py
│   ├── config.py
│   ├── llm.py                          # provider abstraction
│   ├── schema.py                       # data contract (Section 3)
│   ├── cache.py
│   ├── coverage.py                     # yield + Chapman estimator
│   ├── graph/
│   │   ├── state.py
│   │   ├── build.py
│   │   └── nodes/                      # one file per node group
│   ├── sources/                        # places.py, search.py, list_extractor.py
│   └── web/                            # fetch.py, robots.py, extract.py
├── tests/
│   └── fixtures/                       # fake sites, fake Places responses
├── eval/                               # Phase B
├── api/                                # Phase B (FastAPI)
├── web/                                # Phase B (Next.js)
└── outputs/                            # gitignored: runs, cache, data
```

---

## 3. Data contract

### 3.1 Schema (`src/leadgraph/schema.py`)

```python
from typing import Generic, Literal, TypeVar
from pydantic import BaseModel, Field

T = TypeVar("T")

class Sourced(BaseModel, Generic[T]):
    value: T | None = None
    source_url: str | None = None
    evidence: str | None = Field(None, max_length=300)   # verbatim from the page
    confidence: Literal["high", "medium", "low"] | None = None

class Contact(BaseModel):
    name: str
    role: str | None = None
    email: str | None = None
    email_type: Literal["personal", "role", "generic"] | None = None
    linkedin_url: str | None = None
    linkedin_verified: bool = False        # always False until a human checks
    source_url: str

class CompanyKPIs(BaseModel):
    units_managed: Sourced[int]
    management_types: Sourced[list[Literal["WEG", "MIET", "SEV", "GEWERBE"]]]
    residential_focus: Sourced[bool]
    portfolio_scope: Sourced[Literal["berlin", "regional", "national"]]
    tenant_portal_or_app: Sourced[bool]
    software_mentioned: Sourced[list[str]]
    metering_provider_mentioned: Sourced[list[str]]
    remote_reading_or_uvi_mentioned: Sourced[bool]
    association_membership: Sourced[list[str]]          # VDIV, IVD, BVI, ...
    employee_count: Sourced[int]

class Company(BaseModel):
    id: str                                  # normalized domain, else hash(name + postcode)
    name: str
    address: str | None = None
    postcode: str | None = None
    website: str | None = None
    phone: str | None = None
    google_place_id: str | None = None
    google_rating: float | None = None
    google_review_count: int | None = None
    discovery_sources: list[str] = []
    first_seen_round: int | None = None
    is_property_manager: bool | None = None
    classification_reason: str | None = None
    kpis: CompanyKPIs | None = None
    contacts: list[Contact] = []
    fit_score: float | None = None
    tier: Literal["A", "B", "C"] | None = None
    score_breakdown: dict[str, float] = {}
    data_completeness: float | None = None
    needs_review: bool = False
    review_reasons: list[str] = []
```

> If a provider's structured output rejects the generic schema, use a flattened LLM-facing model and map it to this contract in code. The contract above stays the source of truth.

### 3.2 KPIs and why they matter (goes into the `kpi_definitions` sheet and the Task 3 doc)

| KPI | Why it matters to a metering provider | Typical source |
|---|---|---|
| `units_managed` | Size of the potential contract | Homepage / about page ("über 2.000 Einheiten") |
| `management_types` | WEG and rental management both need heating cost billing; WEG decisions need an owners' resolution (longer sales cycle) | Services page |
| `residential_focus` | The Heizkostenverordnung applies to multi-unit buildings with shared heating; commercial-only is a weaker fit | Services page |
| `portfolio_scope` | Berlin-focused managers fit a Berlin-first sales motion | About page |
| `tenant_portal_or_app`, `software_mentioned` | Digital maturity: easier integration, more likely to value real-time data | Website |
| `metering_provider_mentioned` | Shows the incumbent (ista, Techem, Brunata-Metrona, Minol, KALO, …) — a displacement target | FAQ / partner pages |
| `remote_reading_or_uvi_mentioned` | Non-remote-readable meters must be retrofitted by **31.12.2026**; monthly consumption information becomes a general obligation from Jan 2027 | News / FAQ pages |
| `association_membership` | Established, professional operator; VDIV members often show the VDIV logo | Footer / about page |
| `employee_count` | Size proxy when units aren't stated | Team page |
| `google_review_count` | Weak proxy for size and activity | Places API |

`metering_provider_mentioned` and `remote_reading_or_uvi_mentioned` are **informational** columns, not scored: their direction is ambiguous (already equipped vs. needs a retrofit).

### 3.3 Scoring v1 (deterministic, config-driven)

```yaml
# config/segments/hausverwaltung.yaml (excerpt)
weights:                     # sums to 100
  units_managed: 30
  residential_focus: 20
  digital_maturity: 15
  reachable_decision_maker: 15
  berlin_focus: 10
  association_member: 10
buckets:
  units_managed:            { ">3000": 1.0, "1000-3000": 0.8, "300-999": 0.5, "<300": 0.2, unknown: 0.3 }
  residential_focus:        { true: 1.0, false: 0.0, unknown: 0.5 }
  digital_maturity:         { portal_or_software: 1.0, none_found: 0.3 }
  reachable_decision_maker: { personal_email: 1.0, named_person: 0.6, none: 0.0 }
  berlin_focus:             { berlin: 1.0, regional: 1.0, national: 0.5, unknown: 0.7 }
  association_member:       { member: 1.0, unknown: 0.4 }
tiers: { A: 70, B: 45 }      # A ≥ 70, B ≥ 45, else C
```

Unknown values get partial credit, so missing data doesn't masquerade as a bad fit. `data_completeness` (share of non-null KPIs) is reported next to the score.

**Worked examples (use as exact test fixtures in M4):**

| Fixture | Inputs | Calculation | Score | Tier |
|---|---|---|---|---|
| A | 2,500 units · residential · tenant portal · personal email · Berlin · VDIV | 24 + 20 + 15 + 15 + 10 + 10 | **94.0** | A |
| B | everything unknown · named person, no email | 9 + 10 + 4.5 + 9 + 7 + 4 | **43.5** | C |
| C | 600 units · residential · nothing digital · named person · national · VDIV | 15 + 20 + 4.5 + 9 + 5 + 10 | **63.5** | B |

### 3.4 Coverage math (`src/leadgraph/coverage.py`)

**Round yield** = new unique companies this round ÷ cumulative unique companies before this round.
Stop when yield < 0.02 for two consecutive rounds, or `max_rounds`, or budget reached.

**Chapman estimator** (capture–recapture between two independent source groups):

```
N̂ = ((n1 + 1)(n2 + 1) / (m + 1)) − 1
n1 = found by Places sweep · n2 = found by directory/list sources · m = found by both
```

Test values: n1 = 900, n2 = 300, m = 200 → **N̂ ≈ 1348.26**; union = 1,000 → **coverage ≈ 0.742**.

**Caveat (write this in the doc):** sources aren't truly independent — larger firms appear in both — so the estimate is optimistic. Pair it with **recall probes**: 10 Berlin property managers found by hand outside the pipeline; recall = how many the pipeline found.

---

## 4. Standing instructions — copy into `CLAUDE.md`

````markdown
# CLAUDE.md — LeadGraph

## Project
Agentic lead-research pipeline: discovers companies of a segment in a city, classifies them,
extracts KPIs with evidence, scores them, and exports a spreadsheet plus a coverage report.
Full plan: docs/BUILD_PLAN.md. Data contract: Section 3 of that file.

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
7. API keys come from env vars or CLI args only. Never log, print or write them to disk.
8. outputs/ is gitignored. Never commit scraped data.
9. Implement only the current milestone's scope. If something out of scope seems necessary,
   stop and ask.
10. Unit-test all pure-code modules. Tests that hit the network are marked `@pytest.mark.live`.
11. Every run writes outputs/<run_id>/report.json: counts, rounds, cache hits, tokens, cost
    estimate, durations.
12. Make small, surgical edits to existing files. Never regenerate a whole existing file.
13. Enforce the budget guard (max Places requests, max LLM cost) before each paid call.
````

---

## 5. Phase A — critical path (Heidi, due Tue 10 Nov 18:00)

### M1 — Thin vertical slice: one area, end to end

**Time box:** 2–3 h (Sun) · **Depends on:** prerequisites

**Prompt:**

```
Read CLAUDE.md and docs/BUILD_PLAN.md (Sections 1–4). Implement Milestone 1 only.

Build a thin end-to-end slice:
1. uv project with the stack from CLAUDE.md; typer CLI entry point `leadgraph`.
2. Config loading: config/models.yaml, config/segments/hausverwaltung.yaml (query variants,
   classification instructions, scoring config from Section 3.3), config/cities/berlin.yaml
   (city bounding box, a small test rectangle named "mitte", valid postcode prefixes 10–14).
3. src/leadgraph/llm.py: get_model(provider, model, api_key) via init_chat_model, plus a
   helper that returns validated Pydantic objects via structured output.
4. src/leadgraph/sources/places.py: async Places API (New) Text Search client. Field mask:
   id, displayName, formattedAddress, addressComponents, websiteUri, nationalPhoneNumber,
   rating, userRatingCount, businessStatus, types, primaryType. pageSize 20, follow
   nextPageToken, locationRestriction rectangle, request counter for the budget guard.
5. src/leadgraph/web/fetch.py: minimal polite fetcher (robots.txt, User-Agent, per-domain
   rate limit) returning trafilatura text and links.
6. src/leadgraph/cache.py wrapping all HTTP and LLM calls.
7. src/leadgraph/schema.py with the models from Section 3.1.
8. LangGraph graph: plan_sweep (one rectangle × first query) → places_sweep →
   merge_dedupe (domain → phone → normalized name + postcode; drop non-Berlin postcodes and
   CLOSED_PERMANENTLY) → classify (one structured LLM call per company using name, address,
   Places types and the first 2,000 characters of homepage text) → export.
9. export.py: outputs/<run_id>/leads.xlsx (sheets: leads, excluded) and report.json.
10. CLI: leadgraph run --city berlin --area mitte --limit 20 --provider anthropic
    --model claude-haiku-5-5
11. pytest tests for dedupe and postcode filtering.

Out of scope: quadtree splitting, scout agent, list extraction, research agent, KPIs,
contacts, scoring, checkpointing, API, UI.
When done, give me the exact commands to run the M1 test gate.
```

**Test gate:**
- [ ] The CLI command finishes and writes `leads.xlsx` and `report.json`.
- [ ] `leads.xlsx` has: name, address, postcode, website, phone, google_place_id, is_property_manager, classification_reason.
- [ ] Dedupe test: fixture of 6 synthetic records (2 share a domain, 2 share a phone) → exactly **4** unique.
- [ ] You check 10 rows by hand: classification correct for **≥ 9**.
- [ ] Re-running the same command is much faster, `report.json` shows cache hits > 0, and LLM cost ≈ 0.
- [ ] `--provider openai --model <id>` works without code changes.

---

### M2 — Discovery coverage loop (Task 1)

**Time box:** 3–4 h (Sun evening) · **Depends on:** M1

**Prompt:**

```
Read CLAUDE.md and docs/BUILD_PLAN.md (Sections 2, 3.4). Implement Milestone 2 only.

1. Quadtree sweep in places_sweep: start from the city bbox; for each tile × query variant,
   run Text Search with locationRestriction. If a tile's result set is full (all pages
   fetched and the cap is reached), split it into 4 and recurse, down to a configurable
   minimum tile size (default ~1 km). Persist per-tile results.
2. scout_sources agent (bounded tool loop, max 15 tool calls): tools web_search(query) and
   fetch_page(url). Goal: find pages that list many property managers in the city
   (association member directories, portal listings, curated lists). Output a list of
   {url, source_type, estimated_count, notes}.
3. approve_sources: LangGraph interrupt() that shows the candidates in the CLI and resumes
   with the approved subset. Log approvals in report.json.
4. extract_lists: for each approved page (and its pagination, max N pages from config),
   one structured LLM call extracting [{name, website?, address?}].
5. merge_dedupe across sources. List entries without a website get one Places Text Search
   lookup by name to attach website/place_id. Track discovery_sources per company.
6. measure_yield (src/leadgraph/coverage.py): per-round yield, stop rule (Section 3.4),
   overlap matrix between sources, Chapman estimate (Places vs. directory sources),
   cumulative-unique-per-round data for a saturation chart.
7. Query expansion between rounds: one LLM call proposes up to 5 new query variants given
   the segment and the variants already tried; dedupe against tried variants.
8. Checkpointing with the SQLite checkpointer (thread_id = run_id) and
   `leadgraph resume <run_id>`. Add `leadgraph discover --city berlin` that runs discovery
   + classification only.
9. Export adds sheets: coverage, sources.
10. Tests: quadtree recursion with a fake Places client; Chapman estimator with the test
    values in Section 3.4; yield/stop rule.

Out of scope: research agent, KPIs, contacts, scoring, API, UI.
When done, give me the exact commands to run the M2 test gate.
```

**Test gate:**
- [ ] Quadtree test: fake client returns a full result set for tiles larger than a threshold → recursion stops exactly at that threshold.
- [ ] Chapman test: n1 = 900, n2 = 300, m = 200 → **1348.26 ± 0.01**; coverage **0.742 ± 0.001**.
- [ ] The scout pauses for approval, then continues with only the approved sources.
- [ ] Kill the process mid-run (Ctrl+C) → `leadgraph resume <run_id>` finishes, with cache hits logged for completed work.
- [ ] The full Berlin discovery run completes; `report.json` has rounds, yields, per-source counts, overlap matrix and Chapman estimate.
- [ ] The final round's yield is < 2%, or the report states why the run stopped.
- [ ] **Recall probes:** find 10 Berlin property managers by hand outside the pipeline → record how many are in the list.

➡️ **Start the full Berlin discovery run tonight.**

---

### M3 — Research agent and KPI extraction (Task 2)

**Time box:** ~3 h (Mon evening) · **Depends on:** M2

**Prompt:**

```
Read CLAUDE.md and docs/BUILD_PLAN.md (Sections 2, 3.1, 3.2). Implement Milestone 3 only.

1. research_agent per company (bounded tool loop, max 6 tool calls): tools
   fetch_page(url) and list_links(url), both restricted to the company's own domain
   (enforce in code). Start at the homepage. Pass link-priority hints: impressum,
   über uns, leistungen, team, kontakt, referenzen, aktuelles/news, faq.
   The agent finishes by emitting CompanyKPIs via structured output, every field Sourced.
2. verify_evidence (pure code): for each non-null field, check that the evidence snippet
   appears in the cached text of source_url (normalize whitespace and case; allow minor
   fuzzy matching). If not found → set the value to null and add review reason
   "evidence_not_found:<field>".
3. Fan out with LangGraph Send, one branch per company classified as a property manager.
   Global max concurrency from config.
4. Record tool calls, tokens and cost per company in report.json.
5. Tests with fixtures in tests/fixtures/site_a/: home page ("über 1.200 Wohneinheiten"),
   services page (WEG-Verwaltung and Mietverwaltung), Impressum (Geschäftsführer named).
   Run the agent against a local fixture server. Also add a tampered-evidence test.

Out of scope: contacts, scoring, validation beyond evidence checks, API, UI.
When done, give me the exact commands to run the M3 test gate.
```

**Test gate:**
- [ ] Fixture test: `units_managed = 1200`, `management_types = {WEG, MIET}`, each with `source_url` and evidence.
- [ ] Tampered-evidence test: value is set to null and `evidence_not_found:<field>` is added.
- [ ] Domain-guard test: the agent's attempt to fetch another domain is refused.
- [ ] On the M1 Mitte set: target ≥ 80% of companies with ≥ 3 non-null KPIs. Adjust the target after the first run, but write down the number.
- [ ] No field anywhere has a value without evidence.
- [ ] `report.json` shows average tool calls per company ≤ 6, plus cost per company.

---

### M4 — Contacts, scoring, validation and review queue

**Time box:** 2–3 h (Mon evening) · **Depends on:** M3

**Prompt:**

```
Read CLAUDE.md and docs/BUILD_PLAN.md (Sections 3.1–3.3). Implement Milestone 4 only.

1. contacts node: from the cached Impressum / team / contact pages, extract people with
   roles (Geschäftsführer, Inhaber, Prokurist, Leitung WEG-/Mietverwaltung, Technik).
   Emails only if they appear on the company's site. email_type: personal (name-based
   local part), role (e.g. weg@, verwaltung@), generic (info@, kontakt@, office@).
   LinkedIn: one search query per top contact
   ("<name>" "<company>" site:linkedin.com/in); keep the URL only if both name tokens
   appear in the result title; linkedin_verified stays False.
2. score.py (pure code) from the segment config: fit_score, tier, score_breakdown,
   data_completeness. Use the three worked examples in Section 3.3 as exact tests.
3. validate.py (pure code): website alive (status 200–399), email syntax + MX record
   (dnspython) + email domain matches company domain; set needs_review with reasons.
4. Export: sheets leads (sorted by tier, then score), review, excluded, coverage, sources,
   kpi_definitions (column dictionary with the rationale from Section 3.2).
   Freeze the header row and enable filters.
5. Tests: email_type table, scoring fixtures, MX check with a mocked resolver.

Out of scope: API, UI, evaluation harness, Google Sheets API.
When done, give me the exact commands to run the M4 test gate.
```

**Test gate:**
- [ ] `info@x.de` → generic · `m.mueller@x.de` → personal · `weg@x.de` → role.
- [ ] Scoring fixtures: A = **94.0 / A**, B = **43.5 / C**, C = **63.5 / B**.
- [ ] No email from another domain is in `leads` without a review flag.
- [ ] The xlsx opens with all 6 sheets; `kpi_definitions` explains every KPI column.

➡️ **Start the full enrichment run overnight**, with a budget cap set.

---

### M5 — Production run and validation sample

**Time box:** overnight run + ~1.5 h (Tue) · **Depends on:** M4

**Steps (mostly manual):**
1. Check `report.json` from the overnight run: counts, cost, failures.
2. **Validation sample:** 30 random leads stratified by tier (10 A, 10 B, 10 C). For each, check by hand: is it a property manager? Are the units correct? Is the contact correct? Save the results to `eval/gold.csv` — this becomes the Phase B gold set.
3. Compute accuracy per field, plus the recall-probe result from M2.
4. Upload the xlsx to Google Drive → open it as a Google Sheet → share as "anyone with the link can view". Add conditional formatting on `tier`.

**Test gate:**
- [ ] Validation numbers are written down (per-field accuracy, recall, Chapman coverage).
- [ ] The sheet link opens in an incognito window.

---

### M6 — Task 3 approach doc

**Time box:** 1–1.5 h (Tue) · **Depends on:** M5

**Prompt (to Claude, attaching report.json and the validation numbers):**

```
Draft a concise approach doc (2 pages or 6–8 slides) for the Heidi Systems case:
1. Result in one line: N companies, X Tier A, coverage estimate with caveat.
2. Architecture diagram (from BUILD_PLAN Section 2) and why agents only where needed.
3. Sources: Places quadtree sweep + approved directories, and why both.
4. KPIs and why (Section 3.2), including the 31.12.2026 remote-reading deadline as the
   "why now" for Heidi's customers.
5. Validation: evidence verification, manual stratified sample with per-field accuracy,
   recall probes, Chapman estimate with its caveat.
6. Limitations and next steps (BYOK web app, evaluation harness, Google Sheets export).
7. Repo link.
```

**Before sending:** check that every link opens for "anyone with the link," then submit before **18:00 Berlin time**.

---

## 6. Phase B — platform (Wed 11 – Thu 12 Nov, before Siemens on Fri 13 Nov)

### M7 — Evaluation harness and tracing (highest interview value, do first)

**Prompt:**

```
Read CLAUDE.md. Implement Milestone 7: eval/run_eval.py runs classification, research and
contacts on the companies in eval/gold.csv for each model listed in config/models.yaml.
Metrics per model: per-field accuracy (exact for bool/enum, ±15% for units_managed),
evidence-valid rate, cost per lead, latency per lead, average tool calls. Write
eval/results.md with a comparison table and a bar chart PNG. Add Langfuse tracing via a
callback handler (one trace per company), enabled only when env keys are present.
Out of scope: API, UI.
```

**Test gate:**
- [ ] `eval/results.md` compares ≥ 2 models (e.g. `claude-haiku-5-5` vs `claude-sonnet-5-5`, plus one OpenAI model).
- [ ] A rerun is reproducible from cache.
- [ ] Traces are visible in Langfuse.

Example shape of the results table (values come from your run):

| Model | Classification acc. | Units acc. | Contact acc. | Evidence-valid | Cost / lead | Latency / lead |
|---|---|---|---|---|---|---|
| model A | … | … | … | … | … | … |
| model B | … | … | … | … | … | … |

### M8 — FastAPI service

**Prompt:**

```
Read CLAUDE.md. Implement Milestone 8 in api/: FastAPI app wrapping the graph.
POST /runs {city, segment, area?, provider, model, keys, budget} → run_id (background task).
GET /runs/{id} → status. GET /runs/{id}/events → Server-Sent Events: stage changes,
counters, per-round yield. POST /runs/{id}/approve-sources → resumes the interrupt.
GET /runs/{id}/export.xlsx. GET /models → from config/models.yaml.
Keys live only in the run's memory and are dropped when the run ends; add a test that
captures logs and asserts no key appears. Dockerfile for the API.
Out of scope: UI, auth, multi-tenant storage.
```

**Test gate:**
- [ ] A curl flow on `--area mitte` works end to end, including source approval.
- [ ] SSE events stream.
- [ ] Restarting the server mid-run → the run resumes from its checkpoint.
- [ ] The key-leak test passes.

### M9 — Next.js UI

**Prompt:**

```
Read CLAUDE.md. Implement Milestone 9 in web/ (Next.js App Router, TypeScript).
Pages: (1) New run: city, segment, provider dropdown → model dropdown from /models,
key fields with the note "keys stay in memory for this run only". (2) Run view: stage
timeline, live counters, saturation chart from SSE, source-approval panel (HITL).
(3) Results: table with tier filter; hovering a KPI cell shows its evidence snippet and
source link; review-queue approve/reject; download xlsx.
Out of scope: auth, payments, deployment.
```

**Test gate:**
- [ ] A full demo on Mitte works in the browser.
- [ ] Record a 60–90 s screen capture for the README and the interview.

---

## 7. Phase C — portfolio polish (no deadline)

- Deploy: API in Docker on Railway / Render / Fly; long runs need a worker, not a serverless function.
- "Sign in with Google" export to Google Sheets (OAuth, `drive.file` scope). A plain API key cannot write sheets.
- New segment and city configs (YAML only, no code changes) to prove generality.
- README: problem → architecture → evaluation table → demo GIF → limitations.
- Cost dashboard per run; more providers.

---

## 8. Cut line (if behind schedule)

| Checkpoint | If not done | Cut |
|---|---|---|
| **Sun 23:00** | M2 not passed | Skip query expansion; use Places sweep + 1–2 manually chosen directories |
| **Mon 21:00** | M3 not passed | Drop the LinkedIn lookup and the association KPI; enrich only companies with a website |
| **Tue 12:00** | Full enrichment run incomplete | Submit the complete discovered list (Task 1) with KPIs for the enriched subset; state the enrichment coverage honestly in the doc |

**Never cut:** dedupe, classification, evidence verification, the validation sample, the Task 3 doc.

---

## 9. Interview talking points (Siemens Healthineers)

1. **LangGraph over CrewAI:** the workflow is known, so a typed graph with checkpoints beats role-playing agents for cost, predictability and debugging.
2. **Agents only where judgment is needed:** two bounded agents (scout, research); everything else is code.
3. **Evidence verification:** every value is traceable to a verbatim snippet and checked against the source text — the same idea as an audit trail in regulated software.
4. **Human in the loop:** source approval via `interrupt()` plus a review queue for low-confidence rows.
5. **Evaluation:** a hand-verified gold set and a model comparison of accuracy vs. cost per lead.
6. **Measured coverage:** saturation stopping, Chapman estimate and recall probes — "measured, not claimed."
7. **Privacy by design:** BYOK keys never persisted, no guessed personal data, scraped data never committed.

---

## 10. References

- Heizkostenverordnung remote-reading deadline and monthly consumption information (UVI): https://www.haufe.de/immobilien/verwaltung/unterjaehrige-verbrauchsinformation-fristen-fuer-verwalter_258_644426.html
- Places API (New) Text Search: https://developers.google.com/maps/documentation/places/web-service/text-search
- VDIV Verwaltersuche (directory candidate for the scout): https://vdiv.de/immobilienverwaltung/verwaltersuche
