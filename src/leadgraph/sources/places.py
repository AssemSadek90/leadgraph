"""Google Places API (New) Text Search client.

Docs: https://developers.google.com/maps/documentation/places/web-service/text-search
- POST /v1/places:searchText, key in X-Goog-Api-Key, field mask in X-Goog-FieldMask.
- pageSize ≤ 20, follow nextPageToken; at most 60 results across pages.
- On page 2+, every parameter except pageSize/pageToken must match the first request.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from leadgraph.cache import Cache
from leadgraph.config import PlacesSettings, Rect
from leadgraph.schema import Candidate
from leadgraph.usage import Usage

PLACES_URL = "https://places.googleapis.com/v1/places:searchText"
PLACE_FIELDS = [
    "id",
    "displayName",
    "formattedAddress",
    "addressComponents",
    "websiteUri",
    "nationalPhoneNumber",
    "rating",
    "userRatingCount",
    "businessStatus",
    "types",
    "primaryType",
]
FIELD_MASK = ",".join(f"places.{f}" for f in PLACE_FIELDS) + ",nextPageToken"


class PlacesError(RuntimeError):
    pass


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503, 504)
    return False


@dataclass
class SearchResult:
    query: str
    rect: Rect
    places: list[dict[str, Any]] = field(default_factory=list)
    pages: int = 0
    capped: bool = False  # all pages full: the area may hold more results than returned


class PlacesClient:
    def __init__(self, api_key: str, http: httpx.AsyncClient, cache: Cache, usage: Usage,
                 settings: PlacesSettings, url: str = PLACES_URL):
        self._api_key = api_key
        self._http = http
        self._cache = cache
        self._usage = usage
        self._settings = settings
        self._url = url
        self._sem = asyncio.Semaphore(settings.concurrency)

    def _body(self, query: str, rect: Rect, page_token: str | None) -> dict[str, Any]:
        body: dict[str, Any] = {
            "textQuery": query,
            "pageSize": self._settings.page_size,
            "locationRestriction": rect.to_places(),
            "languageCode": self._settings.language_code,
            "regionCode": self._settings.region_code,
        }
        if page_token:
            body["pageToken"] = page_token
        return body

    async def search_page(self, query: str, rect: Rect, page_token: str | None = None) -> dict:
        body = self._body(query, rect, page_token)
        cached = self._cache.get("places", FIELD_MASK, body)
        if cached is not None:
            return cached

        self._usage.reserve_places_request()  # budget guard before the paid call
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self._api_key,
            "X-Goog-FieldMask": FIELD_MASK,
        }
        async with self._sem:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(4),
                wait=wait_exponential(multiplier=1, min=1, max=20),
                retry=retry_if_exception(_retryable),
                reraise=True,
            ):
                with attempt:
                    resp = await self._http.post(self._url, json=body, headers=headers, timeout=30)
                    if resp.status_code in (429, 500, 502, 503, 504):
                        resp.raise_for_status()
        if resp.status_code != 200:
            # The error body never contains the key (it is sent as a header).
            raise PlacesError(f"Places HTTP {resp.status_code}: {resp.text[:500]}")
        data = resp.json()
        self._cache.set("places", FIELD_MASK, body, value=data)
        return data

    async def search(self, query: str, rect: Rect) -> SearchResult:
        result = SearchResult(query=query, rect=rect)
        token: str | None = None
        full_pages = 0
        for _ in range(self._settings.max_pages):
            data = await self.search_page(query, rect, token)
            places = data.get("places", []) or []
            result.places.extend(places)
            result.pages += 1
            if len(places) >= self._settings.page_size:
                full_pages += 1
            token = data.get("nextPageToken")
            if not token:
                break
        result.capped = full_pages == self._settings.max_pages or bool(token)
        return result


def _component(place: dict[str, Any], kind: str) -> str | None:
    for comp in place.get("addressComponents", []) or []:
        if kind in (comp.get("types") or []):
            return comp.get("longText") or comp.get("shortText")
    return None


def place_to_candidate(place: dict[str, Any], source: str, round_: int = 1) -> Candidate:
    name = (place.get("displayName") or {}).get("text") or "(unnamed)"
    return Candidate(
        name=name,
        source=source,
        round=round_,
        address=place.get("formattedAddress"),
        postcode=_component(place, "postal_code"),
        locality=_component(place, "locality"),
        website=place.get("websiteUri"),
        phone=place.get("nationalPhoneNumber"),
        google_place_id=place.get("id"),
        google_rating=place.get("rating"),
        google_review_count=place.get("userRatingCount"),
        business_status=place.get("businessStatus"),
        types=place.get("types") or [],
        primary_type=place.get("primaryType"),
    )
