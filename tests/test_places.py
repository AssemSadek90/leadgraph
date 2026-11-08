import httpx
import pytest
import respx

from leadgraph.config import Rect
from leadgraph.sources.places import FIELD_MASK, PLACES_URL, PlacesClient, place_to_candidate
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
        assert h["X-Goog-FieldMask"] == FIELD_MASK and "nextPageToken" in FIELD_MASK
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
