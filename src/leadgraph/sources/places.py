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
DETAILS_URL = "https://places.googleapis.com/v1/places/{place_id}"

# Text Search Pro fields (5,000 free requests/month).
PRO_FIELDS = ["id", "displayName", "formattedAddress", "addressComponents",
              "businessStatus", "types", "primaryType"]
# Fields that move a request to the Enterprise SKU (Text Search or Place Details).
ENTERPRISE_FIELDS = ["websiteUri", "nationalPhoneNumber", "rating", "userRatingCount"]
PLACE_FIELDS = PRO_FIELDS + ENTERPRISE_FIELDS


def search_field_mask(tier: str) -> str:
    fields = PLACE_FIELDS if tier == "enterprise" else PRO_FIELDS
    return ",".join(f"places.{f}" for f in fields) + ",nextPageToken"


FIELD_MASK = search_field_mask("enterprise")  # the plan's mask
# City lookup by name: all Text Search Pro fields (viewport = the city's bounding box).
CITY_FIELD_MASK = ",".join(f"places.{f}" for f in (
    "id", "displayName", "formattedAddress", "location", "viewport", "types",
    "addressComponents"))
DETAILS_FIELD_MASK = ",".join(ENTERPRISE_FIELDS)


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
        self.tier = settings.tier
        self.field_mask = search_field_mask(settings.tier)

    def _body(self, query: str, rect: Rect, page_token: str | None,
              page_size: int | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {
            "textQuery": query,
            "pageSize": page_size or self._settings.page_size,
            "locationRestriction": rect.to_places(),
            "languageCode": self._settings.language_code,
            "regionCode": self._settings.region_code,
        }
        if page_token:
            body["pageToken"] = page_token
        return body

    async def _request(self, method: str, url: str, field_mask: str, sku: str,
                       body: dict[str, Any] | None = None,
                       params: dict[str, str] | None = None) -> dict[str, Any]:
        self._usage.reserve_places_request(sku)  # budget guard before the paid call
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self._api_key,
            "X-Goog-FieldMask": field_mask,
        }
        async with self._sem:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(4),
                wait=wait_exponential(multiplier=1, min=1, max=20),
                retry=retry_if_exception(_retryable),
                reraise=True,
            ):
                with attempt:
                    resp = await self._http.request(method, url, json=body, params=params,
                                                    headers=headers, timeout=30)
                    if resp.status_code in (429, 500, 502, 503, 504):
                        resp.raise_for_status()
        if resp.status_code != 200:
            # The error body never contains the key (it is sent as a header).
            raise PlacesError(f"Places HTTP {resp.status_code}: {resp.text[:500]}")
        return resp.json()

    async def search_page(self, query: str, rect: Rect, page_token: str | None = None,
                          page_size: int | None = None, tier: str | None = None) -> dict:
        tier = tier or self.tier
        mask, sku = search_field_mask(tier), f"text_search_{tier}"
        body = self._body(query, rect, page_token, page_size)
        cached = self._cache.get("places", mask, body)
        if cached is not None:
            return cached
        data = await self._request("POST", self._url, mask, sku, body=body)
        self._cache.set("places", mask, body, value=data, expire=30 * 86400)
        return data

    async def details(self, place_id: str) -> dict[str, Any]:
        """Enterprise fields (website, phone, rating, reviews) for one place."""
        params = {"languageCode": self._settings.language_code,
                  "regionCode": self._settings.region_code}
        cached = self._cache.get("places_details", DETAILS_FIELD_MASK, place_id, params)
        if cached is not None:
            return cached
        data = await self._request("GET", DETAILS_URL.format(place_id=place_id),
                                   DETAILS_FIELD_MASK, "place_details_enterprise", params=params)
        self._cache.set("places_details", DETAILS_FIELD_MASK, place_id, params, value=data,
                        expire=30 * 86400)
        return data

    async def find_city(self, name: str) -> list[dict[str, Any]]:
        """Text Search for a place name without an area (Pro fields incl. viewport)."""
        body = {"textQuery": name, "pageSize": 5, "languageCode": self._settings.language_code,
                "regionCode": self._settings.region_code}
        cached = self._cache.get("places", CITY_FIELD_MASK, body)
        if cached is None:
            cached = await self._request("POST", self._url, CITY_FIELD_MASK, "text_search_pro",
                                         body=body)
            self._cache.set("places", CITY_FIELD_MASK, body, value=cached, expire=90 * 86400)
        return cached.get("places", []) or []

    async def lookup(self, name: str, rect: Rect) -> list[dict[str, Any]]:
        """One Text Search page for a business name inside `rect` (list-entry matching)."""
        data = await self.search_page(name, rect, page_size=self._settings.lookup_page_size,
                                      tier=self._settings.lookup_tier)
        return data.get("places", []) or []

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


def place_to_candidate(place: dict[str, Any], source: str, round_: int = 1,
                       contact_fields: bool = False) -> Candidate:
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
        contact_fields=contact_fields,
    )
