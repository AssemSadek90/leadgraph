import httpx
import pytest
import respx

from leadgraph.config import Rect
from leadgraph.sources.places import (
    DETAILS_FIELD_MASK,
    FIELD_MASK,
    PLACES_URL,
    PlacesClient,
    place_to_candidate,
    search_field_mask,
)
from leadgraph.usage import BudgetExceeded
from tests.fakes import PlacesFake, make_place

RECT = Rect(south=52.50, west=13.36, north=52.54, east=13.43)


@respx.mock
async def test_pagination_field_mask_and_cache(cache, usage, settings):
    fake = PlacesFake([[make_place(i) for i in range(20)], [make_place(i) for i in range(20, 25)]])
    route = respx.post(PLACES_URL).mock(side_effect=fake)
    async with httpx.AsyncClient() as http:
        client = PlacesClient("test-key", http, cache, usage, settings.places)
        res = await client.search("Hausverwaltung", RECT)
        assert len(res.places) == 25 and res.pages == 2 and not res.capped
        assert route.call_count == 2 and usage.places_requests == 2
        h = fake.headers[0]
        assert h["X-Goog-FieldMask"] == search_field_mask("pro")  # default tier
        assert "websiteUri" not in h["X-Goog-FieldMask"] and "nextPageToken" in h["X-Goog-FieldMask"]
        assert usage.places_by_sku == {"text_search_pro": 2}
        assert h["X-Goog-Api-Key"] == "test-key"
        first, second = fake.requests
        assert first["pageSize"] == 20 and "pageToken" not in first
        assert first["locationRestriction"]["rectangle"]["low"] == {"latitude": 52.50, "longitude": 13.36}
        # page 2: everything except pageToken identical (API requirement)
        assert {k: v for k, v in second.items() if k != "pageToken"} == first
        # second search is served from cache: no new paid requests
        res2 = await client.search("Hausverwaltung", RECT)
        assert len(res2.places) == 25 and route.call_count == 2 and usage.places_requests == 2
        assert cache.hits["places"] == 2


@respx.mock
async def test_capped_when_all_pages_full(cache, usage, settings):
    fake = PlacesFake([[make_place(i + 20 * p) for i in range(20)] for p in range(3)])
    respx.post(PLACES_URL).mock(side_effect=fake)
    async with httpx.AsyncClient() as http:
        res = await PlacesClient("k", http, cache, usage, settings.places).search("q", RECT)
    assert len(res.places) == 60 and res.capped


@respx.mock
async def test_budget_guard_blocks_before_paid_call(cache, settings):
    from leadgraph.usage import Usage
    usage = Usage(max_places_requests=1, max_llm_cost_usd=1)
    fake = PlacesFake([[make_place(i) for i in range(20)], [make_place(99)]])
    route = respx.post(PLACES_URL).mock(side_effect=fake)
    async with httpx.AsyncClient() as http:
        with pytest.raises(BudgetExceeded):
            await PlacesClient("k", http, cache, usage, settings.places).search("q", RECT)
    assert route.call_count == 1 and usage.budget_events


@respx.mock
async def test_http_error_message_has_no_key(cache, usage, settings):
    respx.post(PLACES_URL).mock(return_value=httpx.Response(403, json={"error": {"message": "denied"}}))
    async with httpx.AsyncClient() as http:
        with pytest.raises(Exception) as exc:
            await PlacesClient("secret-key-123", http, cache, usage, settings.places).search("q", RECT)
    assert "secret-key-123" not in str(exc.value)


def test_place_to_candidate():
    c = place_to_candidate(make_place(7, postcode="10178"), "places:Hausverwaltung")
    assert c.name == "Hausverwaltung Nummer 7 GmbH" and c.postcode == "10178"
    assert c.locality == "Berlin" and c.google_place_id == "place-7"
    assert c.primary_type == "real_estate_agency" and c.google_review_count == 17


@respx.mock
async def test_enterprise_tier_uses_plan_mask(cache, usage, settings):
    settings.places.tier = "enterprise"
    fake = PlacesFake([[make_place(1)]])
    respx.post(PLACES_URL).mock(side_effect=fake)
    async with httpx.AsyncClient() as http:
        await PlacesClient("k", http, cache, usage, settings.places).search("q", RECT)
    assert fake.headers[0]["X-Goog-FieldMask"] == FIELD_MASK
    for f in ("websiteUri", "nationalPhoneNumber", "rating", "userRatingCount"):
        assert f"places.{f}" in FIELD_MASK
    assert usage.places_by_sku == {"text_search_enterprise": 1}
    assert usage.to_dict()["places_cost_estimate_usd"] == pytest.approx(0.035)


@respx.mock
async def test_details_and_lookup(cache, usage, settings):
    route = respx.get("https://places.googleapis.com/v1/places/place-1").mock(
        return_value=httpx.Response(200, json={"websiteUri": "https://hv1.de", "rating": 4.5}))
    fake = PlacesFake([[make_place(1), make_place(2)]])
    respx.post(PLACES_URL).mock(side_effect=fake)
    async with httpx.AsyncClient() as http:
        client = PlacesClient("k", http, cache, usage, settings.places)
        d = await client.details("place-1")
        d2 = await client.details("place-1")
        assert d == d2 == {"websiteUri": "https://hv1.de", "rating": 4.5} and route.call_count == 1
        assert route.calls[0].request.headers["X-Goog-FieldMask"] == DETAILS_FIELD_MASK
        hits = await client.lookup("Hausverwaltung Nummer 1", RECT)
        assert len(hits) == 2 and fake.requests[0]["pageSize"] == settings.places.lookup_page_size
    assert usage.places_by_sku == {"place_details_enterprise": 1, "text_search_pro": 1}
