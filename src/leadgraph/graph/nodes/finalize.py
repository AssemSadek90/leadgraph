"""score (code) → validate (code) before export."""

from __future__ import annotations

import asyncio
from collections import Counter
from typing import Any

from langgraph.runtime import Runtime

from leadgraph.graph.nodes.common import timed
from leadgraph.graph.state import RunContext, RunState
from leadgraph.people import email_domain, valid_email_syntax
from leadgraph.schema import Company
from leadgraph.score import score_company
from leadgraph.validate import dns_resolve, has_mx, validate_company
from leadgraph.web.fetch import site_root


@timed("score")
async def score(state: RunState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    seg = runtime.context.segment
    companies = [Company(**c) for c in state.get("companies") or []]
    for c in companies:
        if c.is_property_manager:
            score_company(c, seg)
    tiers = Counter(c.tier for c in companies if c.is_property_manager)
    return {"companies": [c.model_dump(mode="json") for c in companies],
            "score_stats": {"tiers": {t: tiers.get(t, 0) for t in ("A", "B", "C")}}}


@timed("validate")
async def validate(state: RunState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    ctx = runtime.context
    fetcher = ctx.deps.fetcher
    companies = [Company(**c) for c in state.get("companies") or []]
    targets = [c for c in companies if c.is_property_manager]

    async def status(c: Company) -> int | None:
        if not c.website:
            return None
        page = (fetcher.cached(site_root(c.website)) or fetcher.cached(c.website)
                or await fetcher.fetch(site_root(c.website)))
        if page.error == "robots_disallowed":
            return 200  # site is up; we just may not read it
        return page.status

    statuses = await asyncio.gather(*(status(c) for c in targets))
    domains = sorted({email_domain(x.email) for c in targets for x in c.contacts
                      if x.email and valid_email_syntax(x.email)})
    resolver = ctx.deps.resolver or dns_resolve
    mx = dict(zip(domains, await asyncio.gather(*(has_mx(d, resolver, ctx.deps.cache)
                                                  for d in domains)), strict=True))
    for c, st in zip(targets, statuses, strict=True):
        validate_company(c, st, mx)
    reasons = Counter(r.split(":", 1)[0] for c in targets for r in c.review_reasons)
    return {"companies": [c.model_dump(mode="json") for c in companies],
            "validation_stats": {"needs_review": sum(c.needs_review for c in targets),
                                 "reasons": dict(reasons.most_common()),
                                 "mx_checked": len(domains),
                                 "mx_failed": sorted(d for d, ok in mx.items() if ok is False)}}
