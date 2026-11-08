"""Polite, cached web fetcher.

robots.txt is respected, an identifying User-Agent is sent, each domain gets at most one
request per `per_domain_interval_s`, global concurrency is capped, timeouts are 15 s and
transient errors are retried with tenacity. linkedin.com is never fetched.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential

from leadgraph.cache import Cache
from leadgraph.config import FetchSettings
from leadgraph.usage import Usage
from leadgraph.web import extract
from leadgraph.web.robots import allowed, parse_robots

BLOCKED_DOMAINS = ("linkedin.com",)
HTML_TYPES = ("text/html", "application/xhtml+xml")


def host_of(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def is_blocked(url: str) -> bool:
    host = host_of(url)
    return any(host == d or host.endswith("." + d) for d in BLOCKED_DOMAINS)


def on_domain(url: str, domain: str) -> bool:
    """True if url's host is `domain` or a subdomain of it (www. ignored)."""
    host, domain = host_of(url), domain.lower().removeprefix("www.")
    return host == domain or host.endswith("." + domain)


class DomainNotAllowed(PermissionError):
    pass


@dataclass
class FetchResult:
    url: str
    final_url: str | None = None
    status: int | None = None
    ok: bool = False
    title: str | None = None
    text: str = ""  # trafilatura main text
    full_text: str = ""  # all visible text (selectolax)
    links: list[tuple[str, str]] = field(default_factory=list)
    error: str | None = None
    from_cache: bool = False

    def to_cache(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("from_cache")
        return d


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 502, 503, 504)
    return False


class Fetcher:
    def __init__(self, settings: FetchSettings, cache: Cache, usage: Usage,
                 client: httpx.AsyncClient | None = None):
        self.settings = settings
        self.cache = cache
        self.usage = usage
        self._client = client or httpx.AsyncClient(
            headers={"User-Agent": settings.user_agent, "Accept-Language": "de,en;q=0.8"},
            follow_redirects=True,
            timeout=settings.timeout_s,
        )
        self._owns_client = client is None
        self._global = asyncio.Semaphore(settings.global_concurrency)
        self._domain_locks: dict[str, asyncio.Lock] = {}
        self._domain_last: dict[str, float] = {}
        self._robots: dict[str, Any] = {}

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # --- low level ----------------------------------------------------------------
    async def _get(self, url: str) -> httpx.Response:
        host = host_of(url)
        lock = self._domain_locks.setdefault(host, asyncio.Lock())
        async with lock, self._global:  # domain lock first so waiting tasks hold no global slot
            interval = self.settings.per_domain_interval_s
            wait = self._domain_last.get(host, 0) + interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                async for attempt in AsyncRetrying(
                    stop=stop_after_attempt(self.settings.max_retries),
                    wait=wait_exponential(multiplier=1, min=1, max=10),
                    retry=retry_if_exception(_retryable),
                    reraise=True,
                ):
                    with attempt:
                        self.usage.http_requests += 1
                        resp = await self._client.get(
                            url, headers={"User-Agent": self.settings.user_agent},
                            timeout=self.settings.timeout_s,
                        )
                        if resp.status_code in (429, 502, 503, 504):
                            resp.raise_for_status()
            finally:
                self._domain_last[host] = time.monotonic()
        return resp

    async def _robots_for(self, url: str):
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin in self._robots:
            return self._robots[origin]
        cached = self.cache.get("robots", origin)
        if cached is None:
            try:
                resp = await self._get(origin + "/robots.txt")
                cached = {"status": resp.status_code, "body": resp.text[:200_000]}
            except (httpx.HTTPError, OSError):
                cached = {"status": None, "body": None}
            self.cache.set("robots", origin, value=cached, expire=7 * 86400)
        rp = parse_robots(cached["status"], cached["body"])
        self._robots[origin] = rp
        return rp

    # --- public -------------------------------------------------------------------
    async def fetch(self, url: str, allowed_domain: str | None = None) -> FetchResult:
        if not urlparse(url).scheme:
            url = "https://" + url
        if is_blocked(url):
            raise DomainNotAllowed(f"blocked domain: {host_of(url)}")
        if allowed_domain and not on_domain(url, allowed_domain):
            raise DomainNotAllowed(f"{host_of(url)} is outside {allowed_domain}")

        cached = self.cache.get("http", url)
        if cached is not None:
            return FetchResult(**cached, from_cache=True)

        result = FetchResult(url=url)
        rp = await self._robots_for(url)
        if not allowed(rp, self.settings.user_agent, url):
            self.usage.robots_blocked += 1
            result.error = "robots_disallowed"
            self.cache.set("http", url, value=result.to_cache(), expire=7 * 86400)
            return result

        try:
            resp = await self._get(url)
        except (httpx.HTTPError, OSError) as exc:
            self.usage.fetch_errors += 1
            result.error = f"{type(exc).__name__}: {str(exc)[:200]}"
            return result  # transport errors are not cached

        result.status = resp.status_code
        result.final_url = str(resp.url)
        if is_blocked(result.final_url) or (
            allowed_domain and not on_domain(result.final_url, allowed_domain)
        ):
            result.error = "redirected_off_domain"
        elif resp.status_code >= 400:
            result.error = f"http_{resp.status_code}"
        elif not any(t in resp.headers.get("content-type", "text/html") for t in HTML_TYPES):
            result.error = f"not_html:{resp.headers.get('content-type', '')[:50]}"
        else:
            html = resp.text[: self.settings.max_bytes]
            result.ok = True
            result.title = extract.title(html)
            result.text = extract.main_text(html, result.final_url)
            result.full_text = extract.full_text(html)
            result.links = extract.links(html, result.final_url)
        if not result.ok:
            self.usage.fetch_errors += 1
        self.cache.set("http", url, value=result.to_cache(), expire=30 * 86400)
        return result
