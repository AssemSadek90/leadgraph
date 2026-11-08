"""Discovery nodes: plan_sweep → places_sweep → merge_dedupe (all pure code)."""

from __future__ import annotations

import asyncio
from typing import Any

from langgraph.runtime import Runtime

from leadgraph.config import Rect
from leadgraph.dedupe import apply_filters, dedupe
from leadgraph.graph.nodes.common import timed
from leadgraph.graph.state import RunContext, RunState
from leadgraph.schema import Candidate
from leadgraph.sources.places import place_to_candidate
from leadgraph.usage import BudgetExceeded


@timed("plan_sweep")
async def plan_sweep(state: RunState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    """M1: one rectangle (the area) × the first query variant."""
    ctx = runtime.context
    round_ = state.get("round") or 1
    items = [{
        "rect": ctx.area.model_dump(),
        "query": ctx.segment.queries[0],
        "round": round_,
    }]
    return {"round": round_, "work_items": items}


@timed("places_sweep")
async def places_sweep(state: RunState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    ctx = runtime.context
    places = ctx.deps.places
    stopped: str | None = None

    async def run(item: dict[str, Any]) -> list[dict[str, Any]]:
        nonlocal stopped
        rect = Rect(**item["rect"])
        try:
            result = await places.search(item["query"], rect)
        except BudgetExceeded as exc:
            stopped = f"budget: {exc}"
            return []
        source = f"places:{item['query']}"
        return [place_to_candidate(p, source, item["round"]).model_dump(mode="json")
                for p in result.places]

    batches = await asyncio.gather(*(run(i) for i in state.get("work_items", [])))
    candidates = [c for b in batches for c in b]
    out: dict[str, Any] = {
        "candidates": candidates,
        "counts": {"places_results": len(candidates)},
    }
    if stopped:
        out["stopped_reason"] = stopped
    return out


@timed("merge_dedupe")
async def merge_dedupe(state: RunState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    ctx = runtime.context
    candidates = [Candidate(**c) for c in state.get("candidates", [])]
    kept, excluded = apply_filters(candidates, ctx.city)
    companies = dedupe(kept)
    total_unique = len(companies)
    if ctx.limit is not None:
        companies = companies[: ctx.limit]
    return {
        "companies": [c.model_dump(mode="json") for c in companies],
        "excluded": [e.model_dump(mode="json") for e in excluded],
        "counts": {
            "candidates": len(candidates),
            "filtered_out": len(excluded),
            "unique_companies": total_unique,
            "after_limit": len(companies),
        },
    }
