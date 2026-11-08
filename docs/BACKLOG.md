# Backlog

Out-of-scope ideas parked during milestones.

- ~~Places cost: Pro-tier sweep + Place Details~~ → done in M2 (docs/DECISIONS.md D6).
- Details cost: skip Place Details for obvious non-matches (name/types say Maler, Anwalt, Bank, …)
  with a cheap name-only pre-classification. Could cut details requests on the full run.
- `tiles.jsonl` appends on every (re)run of places_sweep; dedupe by (round, query, rect) when reading.
