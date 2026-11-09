"""research_agent (agent, one Send branch per company) → verify_evidence (code).

The agent may only read the company's own domain (enforced in code by the fetcher). It starts
from the homepage (fetched in code), has at most `research_max_tool_calls` tool calls, and
ends with a structured CompanyKPIs answer in which every value carries a source URL and a
verbatim quote. verify_evidence then checks every quote against the cached page text.
"""

from __future__ import annotations

import re
import time
from statistics import mean
from typing import Any, Literal, TypedDict
from urllib.parse import urljoin, urlparse

from langgraph.runtime import Runtime
from langgraph.types import Send
from pydantic import BaseModel, Field, create_model, model_validator

from leadgraph import tracing
from leadgraph.agent import Tool, run_tool_loop
from leadgraph.dedupe import normalize_domain
from leadgraph.evidence import KPI_FIELDS, non_null_count, verify_kpis
from leadgraph.graph.nodes.common import timed
from leadgraph.graph.state import RunContext, RunState
from leadgraph.schema import Company
from leadgraph.usage import BudgetExceeded
from leadgraph.web.fetch import DomainNotAllowed, on_domain, site_root

EXECUTED = ("ok", "error", "bad_args")  # tool calls that used the budget


def executed_calls(trace: dict[str, Any] | None) -> int:
    return sum(t.get("status") in EXECUTED for t in (trace or {}).get("tool_calls", []))

# --- LLM-facing KPI schema (all fields required + nullable: strict-mode friendly) ------
KPI_SPEC: dict[str, tuple[Any, str]] = {
    "units_managed": (int, "Total number of units (Einheiten, Wohneinheiten, Wohnungen) the "
                           "company manages."),
    "management_types": (list[Literal["WEG", "MIET", "SEV", "GEWERBE"]],
                         "Services offered: WEG = WEG-/Wohnungseigentumsverwaltung, MIET = "
                         "Mietverwaltung, SEV = Sondereigentumsverwaltung, GEWERBE = "
                         "commercial property management."),
    "residential_focus": (bool, "True if it mainly manages residential buildings, false if "
                                "mainly commercial."),
    "portfolio_scope": (Literal["berlin", "regional", "national"],
                        "Where the managed properties are: only Berlin, Berlin plus region "
                        "(e.g. Brandenburg) or several regions, or nationwide."),
    "tenant_portal_or_app": (bool, "True if it offers an online portal or app for tenants or "
                                   "owners (Mieterportal, Eigentümerportal, App)."),
    "software_mentioned": (list[str], "Property-management software or platforms named on "
                                      "the site (e.g. Casavi, Domus, Immoware24, Facilioo)."),
    "metering_provider_mentioned": (list[str], "Heating-cost metering/billing providers named "
                                               "(e.g. ista, Techem, Brunata-Metrona, Minol, "
                                               "KALO)."),
    "remote_reading_or_uvi_mentioned": (bool, "True if remote meter reading (Fernablesung, "
                                              "funkbasierte Zähler) or monthly consumption "
                                              "information (unterjährige "
                                              "Verbrauchsinformation, UVI) is mentioned."),
    "association_membership": (list[str], "Industry associations it is a member of (e.g. "
                                          "VDIV, IVD, BVI, DDIV)."),
    "employee_count": (int, "Number of employees, if stated."),
}
assert list(KPI_SPEC) == KPI_FIELDS


def _sourced_out(field: str, value_type: Any, desc: str) -> type[BaseModel]:
    return create_model(
        f"{''.join(p.title() for p in field.split('_'))}Out",
        value=(value_type | None, Field(description=desc + " Null if not stated.")),
        source_url=(str | None, Field(description="Exact URL of the fetched page stating it.")),
        evidence=(str | None, Field(description="Verbatim quote (max 300 characters) copied "
                                                "exactly from that page's text.")),
        confidence=(Literal["high", "medium", "low"] | None,
                    Field(description="How directly the quote states the value.")),
    )


class _LenientKPIs(BaseModel):
    """Models sometimes send null or the string "null" for a whole field object."""

    @model_validator(mode="before")
    @classmethod
    def _nulls(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return {k: (None if v in (None, "null", "None", "") else v) for k, v in data.items()}
        return data


KPIsOut: type[BaseModel] = create_model(
    "CompanyKPIsOut", __base__=_LenientKPIs,
    **{f: (_sourced_out(f, t, d) | None, ...) for f, (t, d) in KPI_SPEC.items()})

# --- prompts ----------------------------------------------------------------------------
LINK_HINTS = [
    r"impressum|imprint", r"ueber|über|uber|about|wir-|unternehmen|philosophie",
    r"leistung|service|angebot|verwaltung", r"team|mitarbeiter|ansprechpartner",
    r"kontakt|contact", r"referenz", r"aktuell|news|blog|presse", r"faq|fragen",
]
SKIP_EXT = re.compile(r"\.(pdf|jpe?g|png|gif|svg|webp|zip|docx?|xlsx?|mp4)$", re.I)

RESEARCH_SYSTEM = """You research one German property management company, {name}, on its own
website ({domain}) for a B2B sales team that sells heating-cost metering and billing.
Tools: fetch_page(url) and list_links(url), only for pages on {domain}; other domains are
refused. You already have the homepage below. Visit the pages most likely to state the facts,
most useful first: Impressum, Über uns, Leistungen, Team, Kontakt, Referenzen, Aktuelles/News,
FAQ. You have at most {max_calls} tool calls; never fetch the same page twice.
Facts to find:
{facts}
Rules: use only what the fetched pages say. Every value needs a verbatim quote from a page you
fetched: copy one contiguous passage exactly (no paraphrase, no "..." between distant parts).
If a fact is not stated, leave it null. Do not guess or use outside knowledge."""

RESEARCH_FINAL = ("Now output the facts. For every field give value, source_url (the exact URL "
                  "of the fetched page that states it), evidence (a verbatim quote of at most "
                  "300 characters copied exactly from that page's text) and confidence. Use "
                  "null for value, source_url and evidence when no fetched page states it.")


def link_priority(url: str, text: str) -> int:
    hay = f"{urlparse(url).path} {text}".lower()
    for i, pattern in enumerate(LINK_HINTS):
        if re.search(pattern, hay):
            return i
    return len(LINK_HINTS)


def internal_links(links: list[tuple[str, str]], domain: str, limit: int) -> list[tuple[str, str]]:
    seen: set[str] = set()
    out = []
    for url, text in links:
        clean = url.split("?")[0].rstrip("/")
        if not on_domain(url, domain) or SKIP_EXT.search(clean) or clean in seen:
            continue
        seen.add(clean)
        out.append((url, text))
    out.sort(key=lambda lt: link_priority(*lt))
    return out[:limit]


def _links_block(links: list[tuple[str, str]]) -> str:
    return "\n".join(f"- {t or '(no text)'} <{u}>" for u, t in links) or "(none)"


def page_for_agent(page: Any, domain: str, chars: int, n_links: int = 25) -> str:
    if not page.ok:
        return f"Could not fetch {page.url}: {page.error}"
    return (f"URL: {page.final_url or page.url}\nTITLE: {page.title}\nTEXT:\n"
            f"{page.full_text[:chars]}\n\nLINKS:\n"
            f"{_links_block(internal_links(page.links, domain, n_links))}")


# --- research_agent ---------------------------------------------------------------------
class ResearchTask(TypedDict):
    company: dict[str, Any]


async def research_company(company: Company, ctx: RunContext) -> dict[str, Any]:
    t0 = time.perf_counter()
    a = ctx.settings.agents
    domain = normalize_domain(company.website)
    result: dict[str, Any] = {"company_id": company.id, "domain": domain, "raw": None,
                              "trace": None, "meter": {}, "pages": [], "error": None}
    fetcher = ctx.deps.fetcher
    home = await fetcher.fetch(site_root(company.website), allowed_domain=domain)
    if not home.ok and site_root(company.website) != company.website:
        home = await fetcher.fetch(company.website, allowed_domain=domain)
    if not home.ok:
        result["error"] = f"homepage_unavailable:{home.error}"
        result["duration_s"] = round(time.perf_counter() - t0, 2)
        return result
    base = home.final_url or home.url
    result["pages"].append(base)

    async def fetch_page(url: str) -> str:
        url = urljoin(base, url)
        page = await fetcher.fetch(url, allowed_domain=domain)  # raises off-domain
        result["pages"].append(page.final_url or url)
        return page_for_agent(page, domain, a.research_page_chars)

    async def list_links(url: str) -> str:
        url = urljoin(base, url)
        page = await fetcher.fetch(url, allowed_domain=domain)
        if not page.ok:
            return f"Could not fetch {url}: {page.error}"
        return _links_block(internal_links(page.links, domain, 80))

    tools = [
        Tool("fetch_page", f"Fetch a page on {domain}: its text and links.",
             {"url": (str, Field(description=f"URL on {domain} (absolute or relative)"))},
             fetch_page),
        Tool("list_links", f"List the internal links of a page on {domain}.",
             {"url": (str, Field(description=f"URL on {domain}"))}, list_links),
    ]
    facts = "\n".join(f"- {f}: {d}" for f, (_, d) in KPI_SPEC.items())
    system = RESEARCH_SYSTEM.format(name=company.name, domain=domain, facts=facts,
                                    max_calls=a.research_max_tool_calls)
    user = f"Homepage:\n{page_for_agent(home, domain, a.research_page_chars, 40)}"
    meter: dict[str, float] = {}
    try:
        final, trace = await run_tool_loop(
            ctx.deps.llm, system=system, user=user, tools=tools, final_schema=KPIsOut,
            max_tool_calls=a.research_max_tool_calls, final_instruction=RESEARCH_FINAL,
            result_chars=a.research_page_chars + 3000, meter=meter)
        result["raw"] = final.model_dump(mode="json")
        result["trace"] = trace.to_dict()
    except BudgetExceeded as exc:
        result["error"] = f"budget: {exc}"
    except DomainNotAllowed as exc:  # homepage redirected off-domain
        result["error"] = f"domain: {exc}"
    except Exception as exc:  # provider errors must not kill the fan-out
        result["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    result["meter"] = meter
    result["duration_s"] = round(time.perf_counter() - t0, 2)
    return result


def research_targets(state: RunState) -> list[Company]:
    done = {r["company_id"] for r in state.get("researched") or []}
    out = []
    for c in state.get("companies") or []:
        comp = Company(**c)
        if (comp.is_property_manager and comp.id not in done
                and normalize_domain(comp.website)):
            out.append(comp)
    return out


def route_research(state: RunState, runtime: Runtime[RunContext]) -> list[Send] | str:
    """Send() fan-out: one research_agent branch per property manager with a website."""
    if not runtime.context.mode.research:
        return "export"
    targets = research_targets(state)
    ctx = runtime.context
    if ctx.mode.command == "enrich" and ctx.limit is not None:
        targets = targets[: max(0, ctx.limit - len(state.get("researched") or []))]
    if not targets:
        return "verify_evidence"
    return [Send("research_agent", {"company": c.model_dump(mode="json")}) for c in targets]


async def research_agent(task: ResearchTask, runtime: Runtime[RunContext]) -> dict[str, Any]:
    company = Company(**task["company"])
    with tracing.company_trace(company.name, company_id=company.id, step="research"):
        result = await research_company(company, runtime.context)
    return {"researched": [result]}


# --- verify_evidence ----------------------------------------------------------------------
def _page_text_lookup(fetcher: Any):
    def page_text(url: str) -> str | None:
        page = fetcher.cached(url)
        if page is None or not page.ok:
            return None
        return "\n".join(x for x in (page.title or "", page.full_text, page.text) if x)
    return page_text


def research_summary(results: list[dict[str, Any]], companies: list[Company]) -> dict[str, Any]:
    by_id = {c.id: c for c in companies}
    ok = [r for r in results if r.get("raw") is not None]
    calls = [executed_calls(r.get("trace")) for r in ok]
    costs = [r.get("meter", {}).get("cost_usd", 0.0) for r in results]
    filled = [non_null_count(by_id[r["company_id"]].kpis) for r in ok if r["company_id"] in by_id]
    per_field = {f: sum(getattr(by_id[r["company_id"]].kpis, f).value not in (None, [])
                        for r in ok if r["company_id"] in by_id and by_id[r["company_id"]].kpis)
                 for f in KPI_FIELDS}
    rejected: dict[str, int] = {}
    for c in companies:
        for reason in c.review_reasons:
            if reason.startswith(("evidence_not_found:", "value_not_in_evidence:")):
                rejected[reason] = rejected.get(reason, 0) + 1
    return {
        "researched": len(results), "succeeded": len(ok),
        "errors": [{"company_id": r["company_id"], "error": r["error"]}
                   for r in results if r.get("error")],
        "avg_tool_calls": round(mean(calls), 2) if calls else None,
        "max_tool_calls": max(calls) if calls else None,
        "avg_cost_usd": round(mean(costs), 5) if costs else None,
        "total_cost_usd": round(sum(costs), 4),
        "avg_duration_s": round(mean(r.get("duration_s", 0) for r in results), 2)
        if results else None,
        "share_with_3plus_kpis": round(sum(n >= 3 for n in filled) / len(filled), 3)
        if filled else None,
        "avg_non_null_kpis": round(mean(filled), 2) if filled else None,
        "non_null_per_field": per_field,
        "evidence_rejections": rejected,
        "per_company": [{
            "company_id": r["company_id"],
            "tool_calls": executed_calls(r.get("trace")),
            "pages": len(r.get("pages", [])), "error": r.get("error"),
            "non_null_kpis": non_null_count(by_id[r["company_id"]].kpis)
            if r["company_id"] in by_id else None,
            **{k: round(v, 6) if isinstance(v, float) else v
               for k, v in (r.get("meter") or {}).items()},
        } for r in results],
    }


@timed("verify_evidence")
async def verify_evidence(state: RunState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    ctx = runtime.context
    results = {r["company_id"]: r for r in state.get("researched") or []}
    page_text = _page_text_lookup(ctx.deps.fetcher)
    companies = []
    for c in state.get("companies") or []:
        comp = Company(**c)
        r = results.get(comp.id)
        if r is not None:
            comp.review_reasons = [x for x in comp.review_reasons
                                   if not x.startswith(("evidence_not_found:",
                                                        "value_not_in_evidence:", "research_"))]
            if r.get("raw") is not None:
                kpis, reasons = verify_kpis(r["raw"], page_text, r.get("pages"))
                comp.kpis = kpis
                comp.data_completeness = round(non_null_count(kpis) / len(KPI_FIELDS), 2)
                comp.review_reasons += reasons
            else:
                comp.review_reasons.append(f"research_failed:{r.get('error')}")
            comp.needs_review = bool(comp.review_reasons)
        companies.append(comp)
    summary = research_summary(list(results.values()), companies)
    return {"companies": [c.model_dump(mode="json") for c in companies],
            "research_stats": summary}
