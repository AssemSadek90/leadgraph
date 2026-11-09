"""contacts (LLM + code): people, roles and published emails from the company's own pages;
LinkedIn profile URLs only from search results (linkedin.com is never fetched).

Pages: Impressum / Team / Kontakt pages already cached by the research agent, plus up to
`contacts_extra_pages` of them fetched from the homepage links (own domain only).
Code checks every name and email against the cited page text; nothing is guessed.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Literal

from langgraph.runtime import Runtime
from pydantic import BaseModel, Field

from leadgraph.dedupe import _LEGAL_RE, normalize_domain
from leadgraph.evidence import normalize
from leadgraph.graph.nodes.common import timed
from leadgraph.graph.state import RunContext, RunState
from leadgraph.llm import ProviderUnavailable
from leadgraph.people import EMAIL_RE, email_type, linkedin_match
from leadgraph.schema import Company, Contact
from leadgraph.usage import BudgetExceeded
from leadgraph.web.fetch import DomainNotAllowed, on_domain, site_root

CONTACT_PAGE_RE = re.compile(r"impressum|imprint|team|kontakt|contact|ansprechpartner|"
                             r"ueber-uns|uber-uns|über-uns|about|unternehmen", re.I)
ROLE_ORDER = ["geschaeftsfuehrer", "inhaber", "prokurist", "leitung_weg", "leitung_miet",
              "technik", "other"]


class PersonOut(BaseModel):
    name: str = Field(description="Full name exactly as written on the page.")
    role: str | None = Field(description="Role as written (e.g. Geschäftsführer), else null.")
    role_category: Literal[tuple(ROLE_ORDER)] = Field(
        description="geschaeftsfuehrer, inhaber, prokurist, leitung_weg (head of WEG "
                    "management), leitung_miet (head of rental management), technik, other.")
    email: str | None = Field(description="Email written on the page for this person, "
                                          "else null. Never construct one.")
    source_url: str = Field(description="URL of the page where the person is named.")


class ContactsOut(BaseModel):
    people: list[PersonOut]


CONTACTS_SYSTEM = """You extract the people named on pages of a German property management
company's own website ({name}, {domain}): Impressum, Team, Kontakt.
For each staff member or owner: the name exactly as written, the role as written, a
role_category, an email only if it is written on the page for that person, and the page URL.
Do not guess or construct email addresses. Skip people who are not part of this company
(web agencies, external data-protection officers, registry courts, photographers)."""


def contact_pages(company: Company, researched: dict[str, Any] | None, fetcher: Any,
                  domain: str) -> tuple[list[str], list[str]]:
    """(cached contact-like pages, uncached candidates from homepage links)."""
    cached, todo = [], []
    seen: set[str] = set()
    for url in (researched or {}).get("pages", []):
        if url not in seen and CONTACT_PAGE_RE.search(url):
            seen.add(url)
            cached.append(url)
    home = (fetcher.cached(site_root(company.website)) or fetcher.cached(company.website)
            if company.website else None)
    if home and home.ok:
        for url, text in home.links:
            if (url not in seen and on_domain(url, domain)
                    and CONTACT_PAGE_RE.search(f"{url} {text}")):
                seen.add(url)
                (cached if fetcher.cached(url) else todo).append(url)
    return cached, todo


def _search_name(company_name: str) -> str:
    return re.sub(r"\s+", " ", _LEGAL_RE.sub(" ", company_name)).strip(" ,&-")


async def find_linkedin(contact: Contact, company: Company, ctx: RunContext) -> str | None:
    if ctx.deps.search is None:
        return None
    query = f'"{contact.name}" "{_search_name(company.name)}" site:linkedin.com/in'
    hits = await ctx.deps.search.search(query, num=5)
    for h in hits:
        if linkedin_match(h.url, h.title, contact.name):
            return h.url
    return None


async def contacts_for(company: Company, ctx: RunContext, researched: dict[str, Any] | None,
                       meter: dict[str, float] | None = None
                       ) -> tuple[list[Contact], dict[str, Any]]:
    cs = ctx.settings.contacts
    fetcher = ctx.deps.fetcher
    domain = normalize_domain(company.website) or ""
    stats: dict[str, Any] = {"company_id": company.id, "pages": 0, "people_raw": 0,
                             "dropped": [], "linkedin_searches": 0, "error": None}
    cached, todo = contact_pages(company, researched, fetcher, domain)
    for url in todo[: cs.extra_pages]:
        try:
            await fetcher.fetch(url, allowed_domain=domain)
        except DomainNotAllowed:
            continue
        cached.append(url)
    texts: dict[str, str] = {}
    for url in cached:
        page = fetcher.cached(url)
        if page and page.ok:
            texts[url] = page.full_text
    if company.website and (home := fetcher.cached(site_root(company.website))) and home.ok:
        texts.setdefault(home.final_url or home.url, home.full_text)
    stats["pages"] = len(texts)
    if not texts:
        return [], stats

    body = "\n\n".join(f"PAGE: {u}\n{t[: cs.page_chars]}" for u, t in texts.items())
    try:
        out = await ctx.deps.llm.structured(
            ContactsOut, CONTACTS_SYSTEM.format(name=company.name, domain=domain), body,
            meter=meter)
    except BudgetExceeded as exc:
        stats["error"] = f"budget: {exc}"
        return [], stats
    except ProviderUnavailable:
        raise
    except Exception as exc:
        stats["error"] = f"{type(exc).__name__}"
        return [], stats
    stats["people_raw"] = len(out.people)

    norm_pages = {u: normalize(t) for u, t in texts.items()}
    site_emails = {e.lower() for e in EMAIL_RE.findall("\n".join(texts.values()))}
    contacts: list[tuple[int, Contact]] = []
    seen_names: set[str] = set()
    for p in out.people:
        name_n = normalize(p.name)
        # the name must be on the cited page (or, if the URL is off, on another fetched page)
        src = p.source_url if name_n in norm_pages.get(p.source_url, "") else next(
            (u for u, t in norm_pages.items() if name_n in t), None)
        if src is None:
            stats["dropped"].append(f"name_not_on_page:{p.name}")
            continue
        if name_n in seen_names:
            continue
        seen_names.add(name_n)
        email = (p.email or "").strip().lower() or None
        if email and email not in site_emails:  # emails only if published on the site
            stats["dropped"].append(f"email_not_on_site:{email}")
            email = None
        role = (p.role or "").strip()
        role = (role[:77] + "…") if len(role) > 80 else (role or None)
        contacts.append((ROLE_ORDER.index(p.role_category), Contact(
            name=p.name.strip(), role=role, email=email,
            email_type=email_type(email, p.name) if email else None, source_url=src)))
    contacts.sort(key=lambda rc: (rc[0], rc[1].email_type != "personal"))
    ordered = [c for _, c in contacts]

    for c in ordered[: cs.linkedin_top_contacts]:
        try:
            stats["linkedin_searches"] += 1
            c.linkedin_url = await find_linkedin(c, company, ctx)
        except BudgetExceeded:
            break
        except Exception:
            continue
    return ordered, stats


@timed("contacts")
async def contacts(state: RunState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    ctx = runtime.context
    researched = {r["company_id"]: r for r in state.get("researched") or []}
    companies = [Company(**c) for c in state.get("companies") or []]
    targets = [c for c in companies if c.is_property_manager and normalize_domain(c.website)
               and (c.id in researched or not ctx.mode.research)]

    async def one(comp: Company) -> dict[str, Any]:
        found, stats = await contacts_for(comp, ctx, researched.get(comp.id))
        comp.contacts = found
        return stats

    stats = await asyncio.gather(*(one(c) for c in targets))
    with_people = [c for c in targets if c.contacts]
    summary = {
        "companies": len(targets),
        "with_named_person": len(with_people),
        "with_personal_email": sum(any(x.email_type == "personal" for x in c.contacts)
                                   for c in targets),
        "with_linkedin": sum(any(x.linkedin_url for x in c.contacts) for c in targets),
        "linkedin_searches": sum(s["linkedin_searches"] for s in stats),
        "dropped": sum(len(s["dropped"]) for s in stats),
        "errors": [s for s in stats if s["error"]],
    }
    return {"companies": [c.model_dump(mode="json") for c in companies],
            "contact_stats": summary}
