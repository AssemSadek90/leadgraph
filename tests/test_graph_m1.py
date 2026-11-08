"""Offline end-to-end run of the M1 graph: fake Places + fake web + fake LLM."""

import json

import httpx
import respx
from openpyxl import load_workbook

from leadgraph.cache import Cache
from leadgraph.export import EXCLUDED_COLUMNS, LEAD_COLUMNS
from leadgraph.graph.build import build_graph
from leadgraph.graph.state import Deps, Mode, RunContext
from leadgraph.llm import LLM
from leadgraph.sources.places import PLACES_URL, PlacesClient
from leadgraph.usage import Usage
from leadgraph.web.fetch import Fetcher
from tests.fakes import (
    DETAILS_ROUTE,
    DetailsFake,
    FakeChat,
    PlacesFake,
    classify_responder,
    html_page,
    make_place,
    mount_site,
)

PM_HTML = html_page("HV", "<p>Ihre Hausverwaltung in Berlin: WEG-Verwaltung und Mietverwaltung.</p>")
MAKLER_HTML = html_page("Makler", "<p>Makler für den Verkauf Ihrer Immobilie in Berlin.</p>")


def places_pages():
    page = [make_place(i) for i in range(1, 6)]
    page.append(make_place(6, displayName={"text": "Spree Makler GmbH"}))
    page.append(make_place(7, websiteUri="https://www.hv1.example.de/kontakt",
                           displayName={"text": "Hausverwaltung Nummer 1 – Filiale"}))  # dup of 1
    page.append(make_place(8, businessStatus="CLOSED_PERMANENTLY"))
    page.append(make_place(9, postcode="14467", locality="Potsdam"))
    page.append(make_place(10, websiteUri=None, nationalPhoneNumber="030 9999999",
                           displayName={"text": "Hausverwaltung Ohne Web"}))
    return [page]


def mount_web():
    for i in range(1, 6):
        mount_site(respx, f"https://hv{i}.example.de", {"/": PM_HTML})
    mount_site(respx, "https://hv6.example.de", {"/": MAKLER_HTML})


async def run_once(tmp_path, city, segment, settings, models, run_id, chat):
    cache = Cache(tmp_path / ".cache")
    usage = Usage(max_places_requests=10, max_llm_cost_usd=1.0)
    async with httpx.AsyncClient() as http:
        fetcher = Fetcher(settings.fetch, cache, usage)
        ctx = RunContext(
            run_id=run_id, city=city, segment=segment, settings=settings, models=models,
            provider="anthropic", model="claude-haiku-5-5", area_name="mitte", limit=20,
            mode=Mode(command="run", queries=[segment.queries[0]], max_rounds=1),
            outputs_dir=tmp_path,
            deps=Deps(cache=cache, usage=usage,
                      places=PlacesClient("k", http, cache, usage, settings.places),
                      fetcher=fetcher,
                      llm=LLM(chat, provider="anthropic", model="claude-haiku-5-5", cache=cache,
                              usage=usage, models_cfg=models)),
            run_meta={"command": "test"},
        )
        final = await build_graph().ainvoke({"run_id": run_id}, context=ctx)
        await fetcher.aclose()
    cache.close()
    return final, json.loads((tmp_path / run_id / "report.json").read_text())


@respx.mock
async def test_m1_end_to_end_and_cached_rerun(tmp_path, city, segment, settings, models):
    fake = PlacesFake(places_pages())
    respx.post(PLACES_URL).mock(side_effect=fake)
    details = DetailsFake(places_pages()[0])
    respx.get(url__regex=DETAILS_ROUTE).mock(side_effect=details)
    mount_web()

    chat = FakeChat(classify_responder)
    final, report = await run_once(tmp_path, city, segment, settings, models, "r1", chat)

    # --- counts --------------------------------------------------------------------
    c = report["counts"]
    assert c["places_results"] == 10
    assert c["filtered_out"] == 2  # closed + Potsdam
    assert c["unique_companies"] == 8  # before details: no website/phone keys yet
    assert c["final_unique_companies"] == 7  # after details, place 7 merges into 1 by domain
    assert c["property_managers"] == 6 and c["not_property_managers"] == 1
    assert c["leads"] == 6 and c["excluded"] == 3
    # Pro tier: 1 search page + Place Details for the 8 Berlin records (7 companies after the
    # domain merge, but places 1 and 7 only share a domain once details are known)
    assert report["usage"]["places_by_sku"] == {"text_search_pro": 1,
                                                "place_details_enterprise": 8}
    assert sorted(details.calls) == sorted(f"place-{i}" for i in (1, 2, 3, 4, 5, 6, 7, 10))
    assert chat.calls == 7
    assert report["usage"]["llm_cost_estimate_usd"] > 0

    # --- xlsx ----------------------------------------------------------------------
    wb = load_workbook(final["outputs"]["xlsx"])
    assert wb.sheetnames == ["leads", "excluded", "coverage", "sources", "evidence"]
    leads = list(wb["leads"].iter_rows(values_only=True))
    assert list(leads[0]) == LEAD_COLUMNS
    for col in ("name", "address", "postcode", "website", "phone", "google_place_id",
                "is_property_manager", "classification_reason"):
        assert col in leads[0]
    names = {r[0] for r in leads[1:]}
    assert "Hausverwaltung Ohne Web" in names and "Spree Makler GmbH" not in names
    excluded = list(wb["excluded"].iter_rows(values_only=True))
    assert list(excluded[0]) == EXCLUDED_COLUMNS
    reasons = " ".join(str(r[1]) for r in excluded[1:])
    assert "closed_permanently" in reasons and "14467" in reasons and "not_property_manager" in reasons
    for key in ("timings_s", "cache", "usage", "cost_estimate_usd", "duration_s"):
        assert key in report

    # --- rerun: everything from cache, no paid calls ---------------------------------
    chat2 = FakeChat(classify_responder)
    _, report2 = await run_once(tmp_path, city, segment, settings, models, "r2", chat2)
    assert chat2.calls == 0
    assert report2["usage"]["places_requests"] == 0
    assert report2["usage"]["llm_cost_estimate_usd"] == 0
    assert report2["cache"]["llm"]["hits"] == 7 and report2["cache"]["http"]["hits"] >= 6
    assert report2["counts"]["leads"] == 6
