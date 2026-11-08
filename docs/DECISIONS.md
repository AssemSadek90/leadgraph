# Decisions and deviations from the build plan

Short log of choices made while building, with the reason. Useful for the Task 3 doc.

| # | Milestone | Decision | Why |
|---|---|---|---|
| D1 | M1 | Postcode filter uses the numeric range 10115–14199 plus Places `locality == Berlin`, not only the prefixes 10–14 | Prefix 14 includes Potsdam (14467…); prefix 12 includes Schönefeld (12529, Brandenburg) |
| D2 | M1 | `classify` picks one of 7 categories (config); `is_property_manager` = category in `include_categories`. Added `classification_category` and `google_types` to `Company` | Decision rule lives in config, not in the prompt; the category is useful in the sheet |
| D3 | M1 | LLM-facing models have only required, unconstrained fields | Works with OpenAI strict json_schema and Anthropic tool use without a flattened copy |
| D4 | M1 | `send_temperature: false` for OpenAI in models.yaml | Newer reasoning models reject a non-default temperature |
| D5 | M1 | selectolax `lexbor` backend | The default `modest` backend is removed in selectolax 1.x |
| D6 | M2 | **Places tier `pro` (default):** sweep with the Pro field mask, then Place Details (website, phone, rating, reviews) only for deduplicated Berlin companies; `--places-tier enterprise` restores the plan's mask | Text Search Pro has 5,000 free requests/month vs 1,000 for Enterprise; details are paid only for companies that survive the city filter. Approved by Asem on 8 Nov |
| D7 | M2 | Eager split: tiles larger than 6 km split after one full page | The 4 children re-cover the tile anyway; saves 2 paid pages per large tile |
| D8 | M2 | Round 1 order: scout → approval → (Places sweep ∥ list extraction) | The one human step comes first, so the long sweep can run unattended overnight |
| D9 | M2 | Yield uses counts (cumulative unique before/after), not company ids | Ids can change when a later record adds a website (hash id → domain id) |
| D10 | M2 | Final coverage (Chapman, overlap) is computed after Place Details + re-dedupe | In the Pro tier, domain/phone keys exist only after details |
| D11 | M2 | `usage.json` persists paid-call counters; resume seeds them | The budget guard must hold across kills and resumes (BYOK safety) |
| D12 | M2 | List entries keep a website only if its domain literally appears in the page chunk | Anti-hallucination for extract_lists (P2) |
| D13 | M2 | `leadgraph recall` + `eval/recall_probes.csv` | Recall-probe gate item, reusable in M5/M6 |
| D14 | M3 | The homepage is fetched in code and given to the research agent; the ≤ 6 tool calls go to subpages | "Start at the homepage" without spending a tool call on it |
| D15 | M3 | verify_evidence also checks values against their quote: numbers must appear in the evidence, list items must be named in it | Catches a real quote paired with a wrong value |
| D16 | M3 | KPI values are columns in `leads`; quotes and URLs go to an `evidence` sheet (one row per value) | Keeps `leads` readable while every value stays auditable |
| D17 | M3 | `leadgraph enrich <discover_run>` starts from the discovery run's final checkpoint state; `run` includes research | Monday's overnight enrichment needs Sunday's discovery as input; discovery stats carry over into the final sheet |
| D18 | M4 | contacts reads pages the research agent cached and fetches up to 3 more Impressum/Team/Kontakt pages from the homepage links (own domain) | The Impressum is the legally required contact page; the agent may have spent its calls elsewhere |
| D19 | M4 | One LinkedIn search per company (top contact by role); URL kept only for a `/in/` result whose title names first and last name | Cost control (cut line allows dropping LinkedIn entirely); P3 |
| D20 | M4 | A personal email published on the site but on another domain (e.g. gmail) still counts for `reachable_decision_maker`, and is flagged `email_domain_mismatch` | It is published, so it is usable; the flag puts it in the review queue |
| D21 | M4 | MX check accepts an A record as implicit MX; a DNS timeout is "unknown", not a failure; a robots-disallowed homepage counts as alive | Avoids false review flags |
| D22 | M4 | Sheets: leads (PMs, tier then score), review, excluded, coverage, sources, kpi_definitions, plus evidence and contacts | Plan's six sheets, plus long-format audit sheets |
