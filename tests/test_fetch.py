import time

import httpx
import pytest
import respx

from leadgraph.web.fetch import DomainNotAllowed, Fetcher, on_domain
from tests.fakes import html_page, mount_site

HOME = html_page("Muster HV", "<nav><a href='/leistungen'>Leistungen</a> <a href='/impressum#top'>"
                 "Impressum</a> <a href='https://www.linkedin.com/company/x'>LinkedIn</a></nav>"
                 "<main><h1>Willkommen</h1><p>Wir verwalten über 1.200 Wohneinheiten in Berlin.</p>"
                 "</main><script>var x=1;</script>")


@respx.mock
async def test_fetch_extracts_text_links_and_caches(cache, usage, settings):
    mount_site(respx, "https://muster.de", {"/": HOME})
    f = Fetcher(settings.fetch, cache, usage)
    r = await f.fetch("https://muster.de/")
    assert r.ok and r.status == 200 and r.title == "Muster HV"
    assert "1.200 Wohneinheiten" in r.full_text and "var x" not in r.full_text
    urls = [u for u, _ in r.links]
    assert "https://muster.de/leistungen" in urls and "https://muster.de/impressum" in urls
    r2 = await f.fetch("https://muster.de/")
    assert r2.from_cache and r2.full_text == r.full_text
    assert usage.http_requests == 2  # robots.txt + page, nothing on the cached call
    await f.aclose()


@respx.mock
async def test_robots_disallow_is_respected(cache, usage, settings):
    mount_site(respx, "https://private.de", {"/": HOME}, robots="User-agent: *\nDisallow: /\n")
    f = Fetcher(settings.fetch, cache, usage)
    r = await f.fetch("https://private.de/")
    assert not r.ok and r.error == "robots_disallowed" and usage.robots_blocked == 1
    assert respx.routes[1].call_count == 0  # the page itself was never requested
    await f.aclose()


@respx.mock
async def test_robots_404_allows(cache, usage, settings):
    respx.get("https://norobots.de/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://norobots.de/").mock(return_value=httpx.Response(
        200, text=HOME, headers={"content-type": "text/html"}))
    r = await Fetcher(settings.fetch, cache, usage).fetch("https://norobots.de/")
    assert r.ok


async def test_linkedin_and_domain_guard(cache, usage, settings):
    f = Fetcher(settings.fetch, cache, usage)
    with pytest.raises(DomainNotAllowed):
        await f.fetch("https://www.linkedin.com/in/someone")
    with pytest.raises(DomainNotAllowed):
        await f.fetch("https://other.de/page", allowed_domain="muster.de")
    assert on_domain("https://www.muster.de/x", "muster.de")
    assert on_domain("https://berlin.muster.de/x", "www.muster.de")
    assert not on_domain("https://muster.de.evil.com/", "muster.de")
    await f.aclose()


@respx.mock
async def test_per_domain_rate_limit(cache, usage, settings):
    settings.fetch.per_domain_interval_s = 0.3
    mount_site(respx, "https://slow.de", {"/a": HOME, "/b": HOME})
    f = Fetcher(settings.fetch, cache, usage)
    t0 = time.monotonic()
    import asyncio
    await asyncio.gather(f.fetch("https://slow.de/a"), f.fetch("https://slow.de/b"))
    # robots + 2 pages on one domain → at least 2 intervals
    assert time.monotonic() - t0 >= 0.55
    await f.aclose()


@respx.mock
async def test_non_html_and_errors(cache, usage, settings):
    mount_site(respx, "https://files.de", {})
    respx.get("https://files.de/doc.pdf").mock(return_value=httpx.Response(
        200, content=b"%PDF", headers={"content-type": "application/pdf"}))
    respx.get("https://files.de/missing").mock(return_value=httpx.Response(404))
    f = Fetcher(settings.fetch, cache, usage)
    assert (await f.fetch("https://files.de/doc.pdf")).error.startswith("not_html")
    assert (await f.fetch("https://files.de/missing")).error == "http_404"
