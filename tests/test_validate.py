import dns.resolver

from leadgraph.schema import Company, Contact
from leadgraph.validate import domain_matches, has_mx, validate_company


def contact(email):
    return Contact(name="Max Mustermann", email=email, source_url="https://x.de/impressum")


def company(*emails, website="https://www.x.de/"):
    return Company(id="x.de", name="X", website=website, is_property_manager=True,
                   contacts=[contact(e) for e in emails])


def test_domain_match():
    assert domain_matches("m.m@x.de", "x.de") and domain_matches("m.m@mail.x.de", "x.de")
    assert not domain_matches("m.m@gmail.com", "x.de") and not domain_matches("a@x.de", None)


def test_validate_flags_foreign_domain_and_dead_site():
    c = validate_company(company("info@x.de", "max@gmail.com", "bad@@x"), 200,
                         {"x.de": True, "gmail.com": True})
    assert c.needs_review
    assert c.review_reasons == ["email_domain_mismatch:max@gmail.com", "email_invalid:bad@@x"]
    dead = validate_company(company("info@x.de"), None, {"x.de": True})
    assert dead.review_reasons == ["website_unreachable:None"]
    nomx = validate_company(company("info@x.de"), 301, {"x.de": False})
    assert nomx.review_reasons == ["email_no_mx:x.de"]
    ok = validate_company(company("info@x.de"), 200, {"x.de": True})
    assert not ok.needs_review and ok.review_reasons == []


def test_validate_is_idempotent_and_keeps_other_reasons():
    c = company("max@gmail.com")
    c.review_reasons = ["evidence_not_found:units_managed"]
    for _ in range(2):
        c = validate_company(c, 200, {"gmail.com": True})
    assert c.review_reasons == ["evidence_not_found:units_managed",
                                "email_domain_mismatch:max@gmail.com"]


async def test_mx_with_mocked_resolver(cache):
    calls = []

    async def resolver(name, rtype):
        calls.append((name, rtype))
        if name == "good.de" and rtype == "MX":
            return ["10 mx.good.de."]
        if name == "aonly.de":
            if rtype == "MX":
                raise dns.resolver.NoAnswer()
            return ["1.2.3.4"]
        if name == "slow.de":
            raise dns.exception.Timeout()
        raise dns.resolver.NXDOMAIN()

    assert await has_mx("good.de", resolver, cache) is True
    assert await has_mx("aonly.de", resolver, cache) is True  # implicit MX via A record
    assert await has_mx("nope.de", resolver, cache) is False
    assert await has_mx("slow.de", resolver, cache) is None  # unknown, not flagged
    n = len(calls)
    assert await has_mx("good.de", resolver, cache) is True and len(calls) == n  # cached
