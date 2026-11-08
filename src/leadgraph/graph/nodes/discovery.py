"""Discovery nodes: plan_sweep → places_sweep → merge_dedupe (all pure code)."""

from __future__ import annotations

import json
from typing import Any

from langgraph.runtime import Runtime

from leadgraph.config import Rect
from leadgraph.dedupe import apply_filters, dedupe
from leadgraph.graph.nodes.common import timed
from leadgraph.graph.state import RunContext, RunState
from leadgraph.schema import Candidate
from leadgraph.sources.places import place_to_candidate
from leadgraph.sources.quadtree import TileResult, quadtree_sweep


def round_queries(state: RunState, ctx: RunContext) -> list[str]:
    if (state.get("round") or 0) == 0:  # planning round 1
        return ctx.mode.queries or ctx.segment.queries
    return state.get("next_queries") or []


@timed("plan_sweep")
async def plan_sweep(state: RunState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    """Tile × query work items for this round: the root area × this round's queries."""
    ctx = runtime.context
    queries = round_queries(state, ctx)
    round_ = (state.get("round") or 0) + 1
    items = [{"rect": ctx.area.model_dump(), "query": q, "round": round_} for q in queries]
    return {
        "round": round_,
        "work_items": items,
        "queries_tried": (state.get("queries_tried") or []) + queries,
        "next_queries": [],
    }


def route_after_plan(state: RunState) -> list[str]:
    """Round 1 goes through source scouting/approval first; later rounds sweep directly."""
    if state.get("round") == 1:
        return ["scout_sources"]
    return ["places_sweep", "extract_lists"]


@timed("places_sweep")
async def places_sweep(state: RunState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    """Quadtree sweep per query (discover) or a single area search (run).

    Queries run one after another so paid requests can be attributed per query; tiles within
    a query run concurrently. Every tile is appended to outputs/<run_id>/tiles.jsonl.
    """
    ctx = runtime.context
    places, usage = ctx.deps.places, ctx.deps.usage
    ps, ds = ctx.settings.places, ctx.settings.discovery
    tiles_path = ctx.outputs_dir / ctx.run_id / "tiles.jsonl"
    tiles_path.parent.mkdir(parents=True, exist_ok=True)

    def persist(tile: TileResult) -> None:
        with tiles_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"round": state.get("round"), **tile.log()}) + "\n")

    candidates: list[dict[str, Any]] = []
    tile_logs: list[dict[str, Any]] = []
    query_stats: list[dict[str, Any]] = []
    stopped: str | None = None
    for item in state.get("work_items", []):
        if stopped:
            break
        before = usage.places_requests
        tiles, stopped = await quadtree_sweep(
            places, item["query"], Rect(**item["rect"]), page_size=ps.page_size,
            max_pages=ps.max_pages, min_tile_km=ds.min_tile_km,
            eager_split_km=ds.eager_split_km if ctx.mode.quadtree else 0.0,
            max_depth=12 if ctx.mode.quadtree else 0, on_tile=persist,
        )
        source = f"places:{item['query']}"
        n = 0
        for t in tiles:
            tile_logs.append({"round": item["round"], **t.log()})
            for p in t.places:
                cand = place_to_candidate(p, source, item["round"])
                candidates.append(cand.model_dump(mode="json"))
                n += 1
        query_stats.append({
            "round": item["round"], "query": item["query"], "tiles": len(tiles),
            "pages": sum(t.pages for t in tiles), "paid_requests": usage.places_requests - before,
            "results": n, "capped_at_min_size": sum(t.capped and not t.split for t in tiles),
        })
    out: dict[str, Any] = {
        "candidates": candidates,
        "tiles": tile_logs,
        "query_stats": query_stats,
        "counts": {"places_results": (state.get("counts") or {}).get("places_results", 0)
                   + len(candidates)},
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
    unique_excluded = list({(e.google_place_id or e.name, e.reason): e for e in excluded}.values())
    return {
        "companies": [c.model_dump(mode="json") for c in companies],
        "filtered": [e.model_dump(mode="json") for e in unique_excluded],
        "counts": {
            "candidates": len(candidates),
            "filtered_out": len(unique_excluded),
            "unique_companies": len(companies),
        },
    }
