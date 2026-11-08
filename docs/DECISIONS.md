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
