"""Web search via Serper (Google results; supports site: queries). Cached and budgeted."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential

from leadgraph.cache import Cache
from leadgraph.usage import Usage

SERPER_URL = "https://google.serper.dev/search"


@dataclass
class SearchHit:
    title: str
    url: str
    snippet: str
    position: int


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    return (isinstance(exc, httpx.HTTPStatusError)
            and exc.response.status_code in (429, 500, 502, 503))


class SearchError(RuntimeError):
    pass


class SerperClient:
    def __init__(self, api_key: str, http: httpx.AsyncClient, cache: Cache, usage: Usage,
                 gl: str = "de", hl: str = "de", url: str = SERPER_URL):
        self._api_key = api_key
        self._http = http
        self._cache = cache
        self._usage = usage
        self._params = {"gl": gl, "hl": hl}
        self._url = url

    async def search(self, query: str, num: int = 10) -> list[SearchHit]:
        body: dict[str, Any] = {"q": query, "num": num, **self._params}
        data = self._cache.get("search", "serper", body)
        if data is None:
            self._usage.reserve_search_request()  # budget guard before the paid call
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10),
                retry=retry_if_exception(_retryable), reraise=True,
            ):
                with attempt:
                    resp = await self._http.post(
                        self._url, json=body, timeout=20,
                        headers={"X-API-KEY": self._api_key, "Content-Type": "application/json"},
                    )
                    if resp.status_code in (429, 500, 502, 503):
                        resp.raise_for_status()
            if resp.status_code != 200:
                raise SearchError(f"Serper HTTP {resp.status_code}: {resp.text[:300]}")
            data = resp.json()
            self._cache.set("search", "serper", body, value=data, expire=14 * 86400)
        return [
            SearchHit(title=o.get("title", ""), url=o.get("link", ""),
                      snippet=o.get("snippet", ""), position=o.get("position", i + 1))
            for i, o in enumerate(data.get("organic", []) or [])
            if o.get("link")
        ]
