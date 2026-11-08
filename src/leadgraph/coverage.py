"""Coverage math (pure code): round yield, stop rule, source overlap, Chapman estimator.

Round yield = new unique companies this round / cumulative unique before this round.
Stop when yield < threshold for two consecutive rounds, or max_rounds, or budget reached.

Chapman (capture–recapture between two source groups):
    N̂ = ((n1 + 1)(n2 + 1) / (m + 1)) − 1
Sources are not truly independent (large firms appear everywhere), so N̂ is optimistic.
"""

from __future__ import annotations

from collections.abc import Iterable
from itertools import combinations
from typing import Any

from leadgraph.config import CityConfig
from leadgraph.dedupe import apply_filters, dedupe
from leadgraph.schema import Candidate


def round_yield(new_unique: int, cumulative_before: int) -> float | None:
    if cumulative_before <= 0:
        return None  # first round: no baseline
    return new_unique / cumulative_before


def stop_decision(rounds: list[dict[str, Any]], *, threshold: float, max_rounds: int,
                  budget_hit: bool = False) -> str | None:
    """Reason to stop after the last round in `rounds`, or None to continue."""
    if budget_hit:
        return "budget_reached"
    if len(rounds) >= max_rounds:
        return f"max_rounds ({max_rounds})"
    yields = [r.get("yield") for r in rounds]
    if len(yields) >= 2 and all(y is not None and y < threshold for y in yields[-2:]):
        return f"saturated (yield < {threshold:.0%} for two consecutive rounds)"
    return None


def chapman(n1: int, n2: int, m: int) -> float:
    return (n1 + 1) * (n2 + 1) / (m + 1) - 1


def source_group(source: str) -> str:
    """'places:Hausverwaltung' → 'places'; 'list:vdiv.de' → 'list:vdiv.de'."""
    return "places" if source.startswith("places:") else source


def overlap_matrix(company_sources: Iterable[Iterable[str]]) -> dict[str, dict[str, int]]:
    """Matrix[a][b] = companies found by both source groups a and b (diagonal = found by a)."""
    sets = [{source_group(s) for s in srcs} for srcs in company_sources]
    groups = sorted({g for s in sets for g in s}, key=lambda g: (g != "places", g))
    matrix = {a: {b: 0 for b in groups} for a in groups}
    for s in sets:
        for g in s:
            matrix[g][g] += 1
        for a, b in combinations(sorted(s), 2):
            matrix[a][b] += 1
            matrix[b][a] += 1
    return matrix


def coverage_stats(company_sources: list[list[str]]) -> dict[str, Any]:
    """Chapman estimate with n1 = Places sweep, n2 = directory/list sources, m = both."""
    places = [any(s.startswith("places:") for s in srcs) for srcs in company_sources]
    lists = [any(s.startswith("list:") for s in srcs) for srcs in company_sources]
    n1 = sum(places)
    n2 = sum(lists)
    m = sum(p and q for p, q in zip(places, lists, strict=True))
    union = sum(p or q for p, q in zip(places, lists, strict=True))
    out: dict[str, Any] = {
        "n1_places": n1, "n2_lists": n2, "m_both": m, "union": union,
        "total_unique": len(company_sources),
        "overlap_matrix": overlap_matrix(company_sources),
        "chapman_estimate": None, "coverage": None,
        "caveat": "Sources are not independent (larger firms appear in both), so the "
                  "estimate is optimistic. Pair it with recall probes.",
    }
    if n1 and n2:
        est = chapman(n1, n2, m)
        out["chapman_estimate"] = round(est, 2)
        out["coverage"] = round(union / est, 4) if est > 0 else None
    return out


def saturation_steps(candidates: list[Candidate], city: CityConfig) -> list[dict[str, Any]]:
    """Cumulative unique companies after each (round, source) step, in discovery order.

    Data for a saturation chart: each Places query and each list source is one step.
    """
    kept, _ = apply_filters(candidates, city)
    steps: list[tuple[int, str]] = []
    for c in kept:
        if (c.round, c.source) not in steps:
            steps.append((c.round, c.source))
    rows: list[dict[str, Any]] = []
    prev = 0
    for i, (r, src) in enumerate(steps):
        included = set(steps[: i + 1])
        cumulative = len(dedupe([c for c in kept if (c.round, c.source) in included]))
        rows.append({"round": r, "step": src, "new": cumulative - prev, "cumulative": cumulative})
        prev = cumulative
    return rows
