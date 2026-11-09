"""classify (LLM): one structured call per company — is this really a property manager?"""

from __future__ import annotations

import asyncio
from functools import lru_cache
from typing import Any, Literal

from langgraph.runtime import Runtime
from pydantic import BaseModel, Field, create_model

from leadgraph.config import CityConfig, SegmentConfig
from leadgraph.graph.nodes.common import timed
from leadgraph.graph.state import RunContext, RunState
from leadgraph.llm import ProviderUnavailable
from leadgraph.schema import Company, ExcludedRecord
from leadgraph.usage import BudgetExceeded
from leadgraph.web.fetch import DomainNotAllowed, site_root

MIN_MAIN_TEXT = 600  # below this, trafilatura likely dropped nav/footer; use full text


@lru_cache(maxsize=8)
def _schema_for(categories: tuple[str, ...]) -> type[BaseModel]:
    # All fields required and unconstrained so strict structured-output modes accept it.
    return create_model(
        "Classification",
        category=(Literal[categories], Field(description="Exactly one category key.")),
        confidence=(Literal["high", "medium", "low"], Field(description="How sure you are.")),
        reason=(str, Field(description="One short English sentence citing the input.")),
    )


def classification_schema(segment: SegmentConfig) -> type[BaseModel]:
    return _schema_for(tuple(segment.categories))


def system_prompt(segment: SegmentConfig, city: CityConfig) -> str:
    cats = "\n".join(f"- {k}: {v}" for k, v in segment.categories.items())
    return (segment.classification_instructions.format(city=city.label)
            + "\nCategories:\n" + cats)


def homepage_excerpt(title: str | None, main: str, full: str, limit: int) -> str:
    body = main if len(main) >= MIN_MAIN_TEXT else full
    text = f"{title}\n{body}" if title else body
    return text[:limit]


def _sources_line(sources: list[str]) -> str:
    """'list:vdiv-bb.de' → 'listed on vdiv-bb.de'; Places queries summarised."""
    lists = [s.removeprefix("list:") for s in sources if s.startswith("list:")]
    queries = [s.removeprefix("places:") for s in sources if s.startswith("places:")]
    parts = []
    if lists:
        parts.append("listed on " + ", ".join(lists) + " (directories of property managers)")
    if queries:
        parts.append(f"Google Maps search ({len(queries)} queries, e.g. '{queries[0]}')")
    return "; ".join(parts) or "unknown"


def user_prompt(company: Company, homepage: str | None, note: str | None) -> str:
    lines = [
        f"Business name: {company.name}",
        f"Address: {company.address or 'unknown'}",
        f"Google Places types: {', '.join(company.google_types) or 'unknown'}",
        f"Website: {company.website or 'none'}",
        f"Found via: {_sources_line(company.discovery_sources)}",
    ]
    if homepage:
        lines += ["Homepage text (start):", '"""', homepage, '"""']
    else:
        lines.append(f"Homepage text: not available ({note or 'no website'})")
    return "\n".join(lines)


async def classify_company(company: Company, ctx: RunContext,
                           meter: dict[str, float] | None = None) -> Company:
    fetcher, llm = ctx.deps.fetcher, ctx.deps.llm
    homepage, note = None, None
    if company.website:
        try:
            page = await fetcher.fetch(site_root(company.website))
            if not page.ok and site_root(company.website) != company.website:
                page = await fetcher.fetch(company.website)
        except DomainNotAllowed as exc:
            page, note = None, str(exc)
        if page is not None:
            if page.ok:
                homepage = homepage_excerpt(page.title, page.text, page.full_text,
                                            ctx.settings.llm.homepage_chars) or None
            else:
                note = page.error
    schema = classification_schema(ctx.segment)
    try:
        out = await llm.structured(schema, system_prompt(ctx.segment, ctx.city),
                                   user_prompt(company, homepage, note), meter=meter)
    except BudgetExceeded:
        company.needs_review = True
        company.review_reasons.append("classify_skipped:budget")
        return company
    except ProviderUnavailable:
        raise  # stop the run; resume redoes the rest
    except Exception as exc:  # provider errors must not kill the whole run
        company.needs_review = True
        company.review_reasons.append(f"classify_error:{type(exc).__name__}")
        return company
    company.classification_category = out.category
    company.is_property_manager = out.category in ctx.segment.include_categories
    company.classification_reason = f"[{out.confidence}] {out.reason}"
    if out.confidence == "low":
        company.needs_review = True
        company.review_reasons.append("classification_low_confidence")
    if homepage is None and company.website:
        company.review_reasons.append(f"homepage_unavailable:{note}")
    return company


@timed("classify")
async def classify(state: RunState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    ctx = runtime.context
    companies = [Company(**c) for c in state.get("companies", [])]
    done = await asyncio.gather(*(classify_company(c, ctx) for c in companies))

    kept, rejected = [], []
    for c in done:
        if c.is_property_manager is False:
            rejected.append(ExcludedRecord(
                name=c.name, address=c.address, postcode=c.postcode, website=c.website,
                google_place_id=c.google_place_id, source=", ".join(c.discovery_sources),
                reason=f"not_property_manager:{c.classification_category} — "
                       f"{c.classification_reason}",
            ).model_dump(mode="json"))
        kept.append(c.model_dump(mode="json"))
    return {
        "companies": kept,
        "rejected": rejected,
        "counts": {
            "classified": sum(c.classification_category is not None for c in done),
            "property_managers": sum(c.is_property_manager is True for c in done),
            "not_property_managers": sum(c.is_property_manager is False for c in done),
            "unclassified": sum(c.is_property_manager is None for c in done),
        },
    }
