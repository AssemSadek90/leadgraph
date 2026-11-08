"""Directory/list sources: scout_sources (agent) → approve_sources (HITL) → extract_lists (LLM)."""

from __future__ import annotations

import asyncio
import re
from typing import Any, Literal
from urllib.parse import urlparse

from langgraph.runtime import Runtime
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from leadgraph.agent import Tool, run_tool_loop
from leadgraph.dedupe import name_similarity, normalize_domain
from leadgraph.graph.nodes.common import timed
from leadgraph.graph.state import RunContext, RunState
from leadgraph.schema import Candidate
from leadgraph.sources.places import place_to_candidate
from leadgraph.usage import BudgetExceeded
from leadgraph.web.fetch import DomainNotAllowed, host_of

SOURCE_TYPES = ("association_directory", "portal_listing", "curated_list", "other")
POSTCODE_RE = re.compile(r"\b(\d{5})\b")


# --- scout_sources (agent) ------------------------------------------------------------
class SourceCandidate(BaseModel):
    url: str = Field(description="Exact URL of the list page, copied from a tool result.")
    source_type: Literal[SOURCE_TYPES]
    estimated_count: int | None = Field(description="Approximate number of companies listed.")
    notes: str = Field(description="Pagination pattern, data available (websites? addresses?).")


class ScoutResult(BaseModel):
    candidates: list[SourceCandidate]


SCOUT_SYSTEM = """You find web pages that list many {segment} in {city}, for building a lead list.
Good sources: association member directories (e.g. regional VDIV or IVD member lists),
business-directory category listings for {city}, curated lists or rankings.
Prefer pages that show many companies with names plus websites or addresses, and that can be
paginated. Skip single-company pages, news articles, job boards and linkedin.com.
Use web_search to find candidates and fetch_page to check what a page really contains.
You have at most {max_calls} tool calls. Only report URLs you saw in tool results."""

SCOUT_USER = "Find the best list pages of {segment} in {city}. Segment: {label}."

SCOUT_FINAL = ("Now give your final answer: the list pages you found, best first, with "
               "source_type, estimated_count and short notes. Only URLs from tool results.")


def _format_hits(hits: list[Any]) -> str:
    if not hits:
        return "No results."
    return "\n".join(f"{h.position}. {h.title}\n   {h.url}\n   {h.snippet}" for h in hits)


def _format_page(page: Any, chars: int) -> str:
    if not page.ok:
        return f"Could not fetch {page.url}: {page.error}"
    links = "\n".join(f"- {t} <{u}>" for u, t in page.links[:60])
    return (f"URL: {page.final_url}\nTITLE: {page.title}\nLINKS ON PAGE: {len(page.links)}\n"
            f"TEXT:\n{page.full_text[:chars]}\n\nFIRST LINKS:\n{links}")


@timed("scout_sources")
async def scout_sources(state: RunState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    ctx = runtime.context
    if not ctx.mode.use_scout:
        return {"source_candidates": []}
    agents = ctx.settings.agents
    seen_urls: set[str] = set()

    async def web_search(query: str) -> str:
        if ctx.deps.search is None:
            return "ERROR: web search is not configured."
        hits = await ctx.deps.search.search(query)
        seen_urls.update(h.url for h in hits)
        return _format_hits(hits)

    async def fetch_page(url: str) -> str:
        page = await ctx.deps.fetcher.fetch(url)
        seen_urls.add(url)
        if page.final_url:
            seen_urls.add(page.final_url)
        seen_urls.update(u for u, _ in page.links)
        return _format_page(page, agents.tool_result_chars // 2)

    tools = [
        Tool("web_search", "Google web search. Supports operators like site:.",
             {"query": (str, Field(description="Search query"))}, web_search),
        Tool("fetch_page", "Fetch a web page: text, title and links.",
             {"url": (str, Field(description="Absolute URL"))}, fetch_page),
    ]
    fmt = {"segment": ctx.segment.label, "city": ctx.city.label, "label": ctx.segment.name,
           "max_calls": agents.scout_max_tool_calls}
    try:
        result, trace = await run_tool_loop(
            ctx.deps.llm, system=SCOUT_SYSTEM.format(**fmt), user=SCOUT_USER.format(**fmt),
            tools=tools, final_schema=ScoutResult, max_tool_calls=agents.scout_max_tool_calls,
            final_instruction=SCOUT_FINAL, result_chars=agents.tool_result_chars,
        )
    except BudgetExceeded as exc:
        return {"source_candidates": [], "scout_trace": {"error": str(exc)}}

    candidates, dropped = [], []
    for c in result.candidates:
        url = c.url.strip()
        if url in seen_urls and host_of(url) != "linkedin.com" and url not in {
                x["url"] for x in candidates}:
            candidates.append(c.model_dump())
        else:
            dropped.append(url)
    return {"source_candidates": candidates,
            "scout_trace": {**trace.to_dict(), "dropped_unseen_urls": dropped}}


# --- approve_sources (human in the loop) ----------------------------------------------
def parse_selection(selection: str, n: int) -> list[int]:
    """'all' | 'none' | '' | '1,3-4' (1-based) → sorted 0-based indices."""
    s = selection.strip().lower()
    if s in ("all", "a", "*"):
        return list(range(n))
    if s in ("", "none", "n", "-"):
        return []
    out: set[int] = set()
    for part in s.replace(" ", "").split(","):
        if "-" in part:
            lo, hi = part.split("-", 1)
            out.update(range(int(lo) - 1, int(hi)))
        elif part:
            out.add(int(part) - 1)
    return sorted(i for i in out if 0 <= i < n)


@timed("approve_sources")
async def approve_sources(state: RunState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    ctx = runtime.context
    cands = state.get("source_candidates") or []
    extra = [{"url": u, "source_type": "other", "estimated_count": None,
              "notes": "added on the command line", "approved_by": "cli"}
             for u in ctx.mode.extra_sources]
    if not cands:
        return {"approved_sources": extra}

    if ctx.mode.approve is not None:
        chosen = parse_selection(ctx.mode.approve, len(cands))
        how, added = "auto", []
    else:
        # Pauses the graph; the CLI shows the candidates and resumes with the decision.
        decision = interrupt({"type": "approve_sources", "candidates": cands})
        chosen = [i for i in decision.get("approved", []) if 0 <= i < len(cands)]
        how, added = "user", decision.get("extra", [])
    approved = [{**cands[i], "approved_by": how} for i in chosen]
    approved += [{"url": u, "source_type": "other", "estimated_count": None,
                  "notes": "added at approval", "approved_by": "user"} for u in added]
    return {"approved_sources": approved + extra}


# --- extract_lists (LLM) --------------------------------------------------------------
class ListEntry(BaseModel):
    name: str = Field(description="Company name exactly as written on the page.")
    website: str | None = Field(description="Company's own website URL if shown, else null.")
    address: str | None = Field(description="Street address if shown, else null.")
    postcode: str | None = Field(description="5-digit postcode if shown, else null.")


class ListPage(BaseModel):
    is_company_list: bool = Field(description="True if the text lists companies.")
    entries: list[ListEntry]
    next_page_url: str | None = Field(
        description="URL of the next page of this list (from the text), else null.")


LIST_SYSTEM = """You extract company entries from the text of a directory or list page.
Links appear inline as `anchor <url>`. Extract every {segment} entry in the text: the name
exactly as written, the company's own website only if its URL appears next to the entry,
the address and postcode only if shown. Never invent or complete values: null if absent.
Ignore navigation, ads and the directory's own links. If the page has a link to the next
page of results (e.g. "weiter", "nächste", "»", page numbers), return its URL."""


def chunk_text(text: str, size: int, max_chunks: int) -> list[str]:
    chunks, start = [], 0
    while start < len(text) and len(chunks) < max_chunks:
        end = min(len(text), start + size)
        if end < len(text):
            nl = text.rfind("\n", start + size // 2, end)
            end = nl if nl > 0 else end
        chunks.append(text[start:end])
        start = end
    return chunks


def clean_website(website: str | None, chunk: str, source_host: str) -> str | None:
    """Keep a website only if its domain literally appears in the chunk (no inventions)."""
    if not website:
        return None
    domain = normalize_domain(website)
    if not domain or domain == source_host or domain not in chunk.lower():
        return None
    return website if "://" in website else "https://" + website


def entry_to_candidate(e: ListEntry, chunk: str, source: str, source_host: str,
                       round_: int) -> Candidate:
    postcode = e.postcode if e.postcode and POSTCODE_RE.fullmatch(e.postcode.strip()) else None
    if not postcode and e.address and (m := POSTCODE_RE.search(e.address)):
        postcode = m.group(1)
    return Candidate(name=e.name.strip(), source=source, round=round_,
                     website=clean_website(e.website, chunk, source_host),
                     address=e.address, postcode=postcode)


async def lookup_on_places(cand: Candidate, ctx: RunContext) -> Candidate:
    """One Places search by name for list entries without a website (attach place data)."""
    places = await ctx.deps.places.lookup(f"{cand.name} {ctx.city.label}", ctx.city.bbox)
    for p in places:
        pc = place_to_candidate(p, cand.source, cand.round)
        if name_similarity(cand.name, pc.name) >= 0.5:
            return cand.model_copy(update={
                "google_place_id": pc.google_place_id, "address": cand.address or pc.address,
                "postcode": cand.postcode or pc.postcode, "locality": pc.locality,
                "business_status": pc.business_status, "types": pc.types,
                "primary_type": pc.primary_type, "website": pc.website, "phone": pc.phone,
                "google_rating": pc.google_rating, "google_review_count": pc.google_review_count,
            })
    return cand


async def extract_source(src: dict[str, Any], ctx: RunContext, round_: int
                         ) -> tuple[list[Candidate], dict[str, Any]]:
    ds = ctx.settings.discovery
    host = host_of(src["url"])
    source = f"list:{host}"
    stats: dict[str, Any] = {"url": src["url"], "source": source, "pages": 0, "entries": 0,
                             "errors": [], "looked_up": 0, "matched_on_places": 0}
    system = LIST_SYSTEM.format(segment=ctx.segment.label)
    url: str | None = src["url"]
    visited: set[str] = set()
    entries: list[Candidate] = []
    while url and url not in visited and stats["pages"] < ds.max_list_pages:
        visited.add(url)
        try:
            page = await ctx.deps.fetcher.fetch(url)
        except DomainNotAllowed as exc:
            stats["errors"].append(str(exc))
            break
        if not page.ok:
            stats["errors"].append(f"{url}: {page.error}")
            break
        stats["pages"] += 1
        next_url = None
        page_links = {u for u, _ in page.links}
        for chunk in chunk_text(page.text_with_links, ds.list_chunk_chars, ds.max_chunks_per_page):
            try:
                out = await ctx.deps.llm.structured(ListPage, system, f"PAGE: {url}\n\n{chunk}")
            except BudgetExceeded as exc:
                stats["errors"].append(f"budget: {exc}")
                url = None
                break
            except Exception as exc:
                stats["errors"].append(f"extract_error: {type(exc).__name__}")
                continue
            entries += [entry_to_candidate(e, chunk, source, host, round_)
                        for e in out.entries if e.name.strip()]
            nxt = out.next_page_url
            if nxt and nxt in page_links and urlparse(nxt).hostname == urlparse(url).hostname:
                next_url = nxt
        else:
            url = next_url
    stats["entries"] = len(entries)

    async def maybe_lookup(c: Candidate) -> Candidate:
        if c.website:
            return c
        stats["looked_up"] += 1
        try:
            found = await lookup_on_places(c, ctx)
        except BudgetExceeded as exc:
            if f"budget: {exc}" not in stats["errors"]:
                stats["errors"].append(f"budget: {exc}")
            return c
        if found.google_place_id:
            stats["matched_on_places"] += 1
        return found

    entries = list(await asyncio.gather(*(maybe_lookup(c) for c in entries)))
    return entries, stats


@timed("extract_lists")
async def extract_lists(state: RunState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    ctx = runtime.context
    done = state.get("source_stats") or {}
    todo = [s for s in state.get("approved_sources") or [] if s["url"] not in done]
    if not todo:
        return {}
    round_ = state.get("round") or 1
    results = await asyncio.gather(*(extract_source(s, ctx, round_) for s in todo))
    candidates = [c.model_dump(mode="json") for entries, _ in results for c in entries]
    return {
        "candidates": candidates,
        "source_stats": {stats["url"]: stats for _, stats in results},
    }
