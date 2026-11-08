"""Recall probes (pure code): companies found by hand outside the pipeline vs. a run.

eval/recall_probes.csv columns: name, website (optional), notes (optional).
A probe counts as found if a discovered company has the same domain or a similar name.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from leadgraph.dedupe import name_similarity, normalize_domain


def load_probes(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return [row for row in csv.DictReader(f) if (row.get("name") or "").strip()]


def load_companies(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / "companies.jsonl"
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def match_probe(probe: dict[str, str], companies: list[dict[str, Any]]) -> dict[str, Any] | None:
    domain = normalize_domain(probe.get("website"))
    for c in companies:
        if domain and normalize_domain(c.get("website")) == domain:
            return c
    best, best_sim = None, 0.0
    for c in companies:
        sim = name_similarity(probe["name"], c["name"])
        if sim > best_sim:
            best, best_sim = c, sim
    return best if best_sim >= 0.5 else None


def recall(probes: list[dict[str, str]], companies: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for p in probes:
        m = match_probe(p, companies)
        rows.append({
            "probe": p["name"], "probe_website": p.get("website") or None,
            "found": m is not None, "matched_name": m["name"] if m else None,
            "matched_sources": m.get("discovery_sources") if m else None,
            "is_property_manager": m.get("is_property_manager") if m else None,
        })
    found = sum(r["found"] for r in rows)
    return {"probes": len(rows), "found": found,
            "recall": round(found / len(rows), 4) if rows else None, "rows": rows}
