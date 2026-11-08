"""measure_yield (code) → expand_queries (LLM) → next round, or stop → place_details (code)."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from langgraph.runtime import Runtime
from pydantic import BaseModel, Field

from leadgraph.coverage import coverage_stats, round_yield, saturation_steps, stop_decision
from leadgraph.dedupe import apply_filters, dedupe_groups, merge_groups
from leadgraph.graph.nodes.common import timed
from leadgraph.graph.state import RunContext, RunState
from leadgraph.schema import Candidate
from leadgraph.usage import BudgetExceeded


@timed("measure_yield")
async def measure_yield(state: RunState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    ctx = runtime.context
    r = state.get("round") or 1
    rounds = list(state.get("rounds") or [])
    before = rounds[-1]["cumulative"] if rounds else 0
    companies = state.get("companies") or []
    after = len(companies)
    new = after - before
    usage = ctx.deps.usage
    rounds.append({
        "round": r,
        "queries": [w["query"] for w in state.get("work_items") or []],
        "new_unique": new,
        "cumulative_before": before,
        "cumulative": after,
        "yield": round_yield(new, before),
        "places_requests_total": usage.places_requests,
    })
    ds = ctx.settings.discovery
    budget_hit = bool(usage.budget_events) or bool(state.get("stopped_reason"))
    stop = stop_decision(rounds, threshold=ds.yield_threshold, max_rounds=ctx.mode.max_rounds,
                         budget_hit=budget_hit)
    cands = [Candidate(**c) for c in state.get("candidates") or []]
    out: dict[str, Any] = {
        "rounds": rounds,
        "coverage": coverage_stats([c["discovery_sources"] for c in companies]),
        "saturation": saturation_steps(cands, ctx.city) if ctx.mode.command == "discover" else [],
    }
    if stop:
        out["stopped_reason"] = state.get("stopped_reason") or stop
    return out


def route_after_yield(state: RunState) -> str:
    return "place_details" if state.get("stopped_reason") else "expand_queries"


class QueryExpansion(BaseModel):
    queries: list[str] = Field(description="New German search phrases, not already tried.")


EXPAND_SYSTEM = """You help find every {segment} in {city} with Google Maps text search.
Propose up to {n} NEW short German search phrases that would surface businesses of this kind
that the phrases already tried might miss (synonyms, sub-services, legal-form variants).
Do not repeat or trivially reword a tried phrase. Do not include the city name."""


def _norm_query(q: str) -> str:
    return re.sub(r"\s+", " ", q.strip().casefold())


@timed("expand_queries")
async def expand_queries(state: RunState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    ctx = runtime.context
    n = ctx.settings.discovery.max_expansion_queries
    tried = state.get("queries_tried") or []
    system = EXPAND_SYSTEM.format(segment=ctx.segment.label, city=ctx.city.label, n=n)
    user = "Already tried:\n" + "\n".join(f"- {q}" for q in tried)
    try:
        out = await ctx.deps.llm.structured(QueryExpansion, system, user)
    except BudgetExceeded as exc:
        return {"next_queries": [], "stopped_reason": f"budget: {exc}"}
    seen = {_norm_query(q) for q in tried}
    new: list[str] = []
    for q in out.queries:
        nq = _norm_query(q)
        if nq and nq not in seen and len(q) <= 60:
            seen.add(nq)
            new.append(q.strip())
    new = new[:n]
    if not new:
        return {"next_queries": [], "stopped_reason": "no_new_queries"}
    return {"next_queries": new}


def route_after_expand(state: RunState) -> str:
    return "plan_sweep" if state.get("next_queries") else "place_details"


@timed("place_details")
async def place_details(state: RunState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    """Final candidate set → (limit) → Place Details for Pro-tier records → final dedupe.

    In the Pro tier the sweep has no website/phone/rating; they are fetched here only for
    companies that survived the city filter, then dedupe runs again so domain and phone
    keys can merge branches.
    """
    ctx = runtime.context
    cands = [Candidate(**c) for c in state.get("candidates") or []]
    kept, _ = apply_filters(cands, ctx.city)
    groups = dedupe_groups(kept)
    if ctx.limit is not None:
        groups = groups[: ctx.limit]

    need: list[str] = []
    if ctx.deps.places is not None and ctx.settings.places.tier == "pro":
        for g in groups:
            if any(c.website or c.phone for c in g):
                continue
            pid = next((c.google_place_id for c in g if c.google_place_id), None)
            if pid:
                need.append(pid)

    details: dict[str, dict[str, Any]] = {}
    stopped: str | None = None

    async def one(pid: str) -> None:
        nonlocal stopped
        if stopped:
            return
        try:
            details[pid] = await ctx.deps.places.details(pid)
        except BudgetExceeded as exc:
            stopped = f"budget: {exc}"
        except Exception as exc:  # one bad place must not stop the run
            details[pid] = {"_error": type(exc).__name__}

    await asyncio.gather(*(one(p) for p in need))

    patched: list[Candidate] = []
    for g in groups:
        for c in g:
            d = details.get(c.google_place_id or "")
            if d and "_error" not in d:
                c = c.model_copy(update={
                    "website": c.website or d.get("websiteUri"),
                    "phone": c.phone or d.get("nationalPhoneNumber"),
                    "google_rating": c.google_rating or d.get("rating"),
                    "google_review_count": c.google_review_count or d.get("userRatingCount"),
                })
            patched.append(c)
    companies = merge_groups(dedupe_groups(patched))
    missing = {pid for pid in need if pid not in details}
    for comp in companies:
        if comp.google_place_id in missing:
            comp.needs_review = True
            comp.review_reasons.append("details_skipped:budget")
    out: dict[str, Any] = {
        "companies": [c.model_dump(mode="json") for c in companies],
        "coverage": coverage_stats([c.discovery_sources for c in companies])
        if ctx.limit is None else state.get("coverage") or {},
        "counts": {"details_requested": len(need), "details_fetched": len(details),
                   "final_unique_companies": len(companies)},
    }
    if stopped:
        out["stopped_reason"] = state.get("stopped_reason") or stopped
    return out
