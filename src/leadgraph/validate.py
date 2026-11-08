"""Validation (pure code, plus a cached DNS lookup): website liveness, email syntax, MX record,
email domain = company domain. Anything suspicious sets needs_review with a reason."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from leadgraph.cache import Cache
from leadgraph.dedupe import normalize_domain
from leadgraph.people import email_domain, valid_email_syntax
from leadgraph.schema import Company

VALIDATION_PREFIXES = ("website_unreachable", "email_invalid", "email_no_mx",
                       "email_domain_mismatch")
Resolver = Callable[[str, str], Awaitable[list[str]]]


def domain_matches(email: str, company_domain: str | None) -> bool:
    if not company_domain:
        return False
    d = email_domain(email).removeprefix("www.")
    return d == company_domain or d.endswith("." + company_domain)


def validate_company(c: Company, website_status: int | None,
                     mx_ok: dict[str, bool | None]) -> Company:
    """Pure: apply validation results to one company (idempotent)."""
    reasons = [r for r in c.review_reasons if not r.startswith(VALIDATION_PREFIXES)]
    if c.website and not (website_status is not None and 200 <= website_status < 400):
        reasons.append(f"website_unreachable:{website_status}")
    domain = normalize_domain(c.website)
    for contact in c.contacts:
        if not contact.email:
            continue
        if not valid_email_syntax(contact.email):
            reasons.append(f"email_invalid:{contact.email}")
            continue
        if not domain_matches(contact.email, domain):
            reasons.append(f"email_domain_mismatch:{contact.email}")
        if mx_ok.get(email_domain(contact.email)) is False:
            reasons.append(f"email_no_mx:{email_domain(contact.email)}")
    c.review_reasons = list(dict.fromkeys(reasons))
    c.needs_review = bool(c.review_reasons)
    return c


async def dns_resolve(name: str, rtype: str) -> list[str]:  # pragma: no cover - network
    import dns.asyncresolver

    answer = await dns.asyncresolver.resolve(name, rtype, lifetime=5.0)
    return [r.to_text() for r in answer]


async def has_mx(domain: str, resolver: Resolver = dns_resolve,
                 cache: Cache | None = None) -> bool | None:
    """True if the domain can receive mail (MX, or A as implicit MX), False if it cannot,
    None if DNS gave no answer in time."""
    import dns.exception
    import dns.resolver

    if cache is not None and (hit := cache.get("dns", "mx", domain)) is not None:
        return hit["ok"]
    try:
        ok: bool | None = bool(await resolver(domain, "MX"))
    except dns.resolver.NXDOMAIN:
        ok = False
    except dns.resolver.NoAnswer:
        try:
            ok = bool(await resolver(domain, "A"))
        except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
            ok = False
        except dns.exception.DNSException:
            ok = None
    except dns.exception.DNSException:
        ok = None
    if cache is not None and ok is not None:
        cache.set("dns", "mx", domain, value={"ok": ok}, expire=7 * 86400)
    return ok
