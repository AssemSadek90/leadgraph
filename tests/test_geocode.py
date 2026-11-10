"""Any city by name: lookup on Places → CityConfig, storage, runner and API integration."""

import json

import httpx
import pytest
import respx

from leadgraph.config import (
    CityConfig,
    Rect,
    city_slug,
    find_city,
    list_cities,
    load_city,
    save_city,
)
from leadgraph.dedupe import name_similarity
from leadgraph.runner import RunOptions, make_run_id, run_context
from leadgraph.schema import CompanyKPIs
from leadgraph.sources.geocode import (
    CityLookupError,
    city_from_place,
    pick_city,
    rect_size_km,
    resolve_city,
)
from leadgraph.sources.places import CITY_FIELD_MASK, PLACES_URL, PlacesClient

HAMBURG = {
    "id": "ChIJuRMYfoNhsUcRoDrWe_I9JgQ",
    "displayName": {"text": "Hamburg", "languageCode": "de"},
    "formattedAddress": "Hamburg, Deutschland",
    "location": {"latitude": 53.5488282, "longitude": 9.987170299999999},
    "viewport": {"low": {"latitude": 53.3951118, "longitude": 9.7301463},
                 "high": {"latitude": 53.7394389, "longitude": 10.3252805}},
    "types": ["locality", "political"],
    "addressComponents": [
        {"longText": "Hamburg", "shortText": "Hamburg", "types": ["locality", "political"]},
        {"longText": "Deutschland", "shortText": "DE", "types": ["country", "political"]},
    ],
}
BAYERN = {**HAMBURG, "displayName": {"text": "Bayern"}, "types": ["locality", "political"],
          "formattedAddress": "Bayern, Deutschland",
          "viewport": {"low": {"latitude": 47.27, "longitude": 8.97},
                       "high": {"latitude": 50.56, "longitude": 13.84}}}
STREET = {**HAMBURG, "displayName": {"text": "Hamburger Straße"}, "types": ["route"],
          "formattedAddress": "Hamburger Str., Berlin"}


def test_city_slug():
    assert city_slug("München") == "muenchen"
    assert city_slug("  Frankfurt am Main ") == "frankfurt-am-main"
    assert city_slug("Halle (Saale)") == "halle-saale"
    assert city_slug("../../etc/passwd") == "etc-passwd"  # safe as a file name
    assert city_slug("!!!") == ""


def test_city_from_place():
    c = city_from_place(HAMBURG, "hamburg")
    assert c.name == "hamburg" and c.label == "Hamburg"
    assert c.bbox.south == pytest.approx(53.3951118) and c.bbox.east == pytest.approx(10.3252805)
    w, h = rect_size_km(c.bbox)
    assert 35 < w < 45 and 35 < h < 40
    cw, ch = rect_size_km(c.test_areas["center"])
    assert cw == pytest.approx(3.0, abs=0.01) and ch == pytest.approx(3.0, abs=0.01)
    assert c.locality_names == ["Hamburg", "hamburg"]
    assert not c.postcode_ranges and c.resolved_from == "Hamburg, Deutschland"


def test_pick_and_reject():
    assert pick_city([STREET, HAMBURG]) is HAMBURG
    assert pick_city([STREET]) is None
    with pytest.raises(CityLookupError, match="not a region"):
        city_from_place(BAYERN, "Bayern")


@respx.mock
async def test_resolve_city_one_cached_pro_request(cache, usage, settings):
    route = respx.post(PLACES_URL).mock(return_value=httpx.Response(
        200, json={"places": [STREET, HAMBURG]}))
    async with httpx.AsyncClient() as http:
        client = PlacesClient("k", http, cache, usage, settings.places)
        a = await resolve_city("Hamburg", client)
        b = await resolve_city("Hamburg", client)
    assert a == b and a.label == "Hamburg"
    assert route.call_count == 1 and dict(usage.places_by_sku) == {"text_search_pro": 1}
    req = route.calls[0].request
    assert req.headers["X-Goog-FieldMask"] == CITY_FIELD_MASK
    body = json.loads(req.content)
    assert body["textQuery"] == "Hamburg" and "locationRestriction" not in body


@respx.mock
async def test_resolve_city_not_found(cache, usage, settings):
    respx.post(PLACES_URL).mock(return_value=httpx.Response(200, json={"places": [STREET]}))
    async with httpx.AsyncClient() as http:
        client = PlacesClient("k", http, cache, usage, settings.places)
        with pytest.raises(CityLookupError, match="no city named 'Hamburger Str'"):
            await resolve_city("Hamburger Str", client)


def test_storage_hand_written_wins(tmp_path):
    cfg, out = tmp_path / "config", tmp_path / "outputs"
    (cfg / "cities").mkdir(parents=True)
    hand = CityConfig(name="hamburg", label="Hamburg (hand)",
                      bbox=Rect(south=53, west=9, north=54, east=10))
    (cfg / "cities" / "hamburg.yaml").write_text(json.dumps(hand.model_dump(mode="json")))
    looked_up = city_from_place(HAMBURG, "Hamburg")
    save_city(looked_up, out)
    assert find_city("Hamburg", cfg, out).label == "Hamburg (hand)"
    assert find_city("münchen", cfg, out) is None
    save_city(city_from_place({**HAMBURG, "displayName": {"text": "München"}}, "Munich"), out)
    assert find_city("München", cfg, out).label == "München"
    assert list(list_cities(cfg, out)) == ["hamburg", "muenchen"]
    with pytest.raises(FileNotFoundError, match="leadgraph city"):
        load_city("Köln", cfg, out)


@respx.mock
async def test_run_context_looks_up_an_unknown_city(tmp_path, monkeypatch):
    route = respx.post(PLACES_URL).mock(return_value=httpx.Response(
        200, json={"places": [HAMBURG]}))
    opts = RunOptions(city="Hamburg", command="discover", area="center", llm_key="x",
                      places_key="p", outputs_dir=tmp_path)
    opts.run_id = make_run_id(opts.city, opts.area, opts.command)
    assert opts.run_id.startswith("disc-hamburg-center-")
    async with run_context(opts) as ctx:
        assert ctx.city.label == "Hamburg" and ctx.area.south > 53.5
        assert ctx.deps.usage.places_by_sku["text_search_pro"] == 1
    assert opts.city == "hamburg"
    assert json.loads((tmp_path / opts.run_id / "run.json").read_text())["city"] == "hamburg"
    assert (tmp_path / "cities" / "hamburg.yaml").exists()
    # the next run (or a resume) finds the saved config: no second lookup
    again = RunOptions(city="hamburg", command="discover", llm_key="x", places_key="p",
                       outputs_dir=tmp_path, run_id="disc-2")
    async with run_context(again) as ctx:
        assert ctx.city.label == "Hamburg"
    assert route.call_count == 1


async def test_berlin_needs_no_lookup(tmp_path):
    opts = RunOptions(city="Berlin", command="discover", area="mitte", llm_key="x",
                      places_key="p", outputs_dir=tmp_path, run_id="disc-b")
    async with run_context(opts) as ctx:  # no respx: any network call would fail
        assert ctx.city.postcode_ranges == [(10115, 14199)]


def test_legacy_berlin_scope_is_city():
    empty = {"value": None, "source_url": None, "evidence": None, "confidence": None}
    k = CompanyKPIs.model_validate({f: empty for f in CompanyKPIs.model_fields} | {
        "portfolio_scope": {**empty, "value": "berlin", "evidence": "nur in Berlin"}})
    assert k.portfolio_scope.value == "city" and k.portfolio_scope.evidence == "nur in Berlin"


def test_city_name_is_not_a_distinctive_token():
    a, b = "Hausverwaltung Hamburg GmbH", "Hamburg Immobilienverwaltung"
    assert name_similarity(a, b) == 1.0  # "hamburg" is the only distinctive token
    assert name_similarity(a, b, frozenset({"hamburg"})) == 0.0
