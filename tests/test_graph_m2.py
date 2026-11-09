"""Offline end-to-end discovery run (M2): scout → approval interrupt → sweep ∥ lists → rounds
→ saturation stop → details → classify → export, including a crash and two resumes."""

import json
import re

import httpx
import pytest
import respx
from langchain_core.messages import AIMessage, ToolMessage
from openpyxl import load_workbook

from leadgraph.llm import LLM
from leadgraph.runner import NeedsApproval, RunOptions, execute, load_run_options
from leadgraph.sources.places import PLACES_URL
from leadgraph.sources.search import SERPER_URL
from tests.fakes import (
    DETAILS_ROUTE,
    DetailsFake,
    ScriptedChat,
    apply_mask,
    html_page,
    make_place,
    mount_site,
    tool_call,
)

DIR1 = "https://verzeichnis.example/hv-berlin"
DIR2 = "https://portal.example/liste"
P = {i: make_place(i) for i in range(1, 15)}
P[12] = make_place(12, displayName={"text": "Spree Makler GmbH"})
P[14] = make_place(14, displayName={"text": "Kiez Hausverwaltung GmbH"})

SWEEP = {
    "Hausverwaltung": range(1, 9), "Immobilienverwaltung": range(5, 11),
    "WEG-Verwaltung": [1, 2, 3, 4, 11], "Mietverwaltung": [2, 12], "Hausverwaltung GmbH": [1, 2, 3],
    "Objektverwaltung": [13], "Immobilienmanagement": [1, 2], "Sondereigentumsverwaltung": [3],
    "Grundstücksverwaltung": [4], "Hausmeisterservice Verwaltung": [5], "Wohnungsverwaltung": [6],
}
LOOKUP = {"Kiez Verwaltung Berlin": [14], "Hausverwaltung Nummer 9 GmbH Berlin": [9]}
EXPANSIONS = {  # keyed by number of queries tried so far (deterministic across resumes)
    # "Immobilienmanagement" is dropped by expansion_must_match, "hausverwaltung" is a repeat
    5: ["Objektverwaltung", "Immobilienmanagement", "Sondereigentumsverwaltung", "hausverwaltung"],
    7: ["Grundstücksverwaltung", "Hausmeisterservice Verwaltung"],
    9: ["Wohnungsverwaltung"],
}

DIR1_P1 = html_page("HV Verzeichnis", """
<ul>
 <li><a href="https://hv1.example.de/">Hausverwaltung Nummer 1 GmbH</a></li>
 <li>Kiez Verwaltung, Kiezstraße 1</li>
 <li><a href="https://altbau-verwaltung.de">Altbau Verwaltung</a>, 10437 Berlin</li>
</ul><a href="/hv-berlin?page=2">weiter</a>""")
DIR1_P2 = html_page("HV Verzeichnis 2", "<ul><li>Hausverwaltung Nummer 9 GmbH</li></ul>")
PM_HTML = html_page("HV", "<p>Ihre Hausverwaltung in Berlin: WEG-Verwaltung und Mietverwaltung.</p>")
MAKLER_HTML = html_page("Makler", "<p>Makler für den Verkauf Ihrer Immobilie.</p>")


class QueryPlaces:
    def __init__(self):
        self.fail_once = {"Sondereigentumsverwaltung"}
        self.queries: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        q = body["textQuery"]
        self.queries.append(q)
        if q in self.fail_once:
            self.fail_once.discard(q)
            return httpx.Response(400, json={"error": {"message": "simulated failure"}})
        ids = LOOKUP.get(q) if q in LOOKUP else SWEEP.get(q, [])
        mask = request.headers["X-Goog-FieldMask"]
        return httpx.Response(200, json={"places": [apply_mask(P[i], mask) for i in ids]})


def responder(schema, messages):
    name, text = schema.__name__, messages[-1].content
    if name == "ScoutResult":
        return {"candidates": [
            {"url": DIR1, "source_type": "association_directory", "estimated_count": 4,
             "notes": "paginated"},
            {"url": DIR2, "source_type": "portal_listing", "estimated_count": 50, "notes": ""},
            {"url": "https://made-up.example/list", "source_type": "other",
             "estimated_count": None, "notes": "never seen"},
        ]}
    if name == "ListPage":
        page = re.search(r"PAGE: (\S+)", text).group(1)
        if page == DIR1:
            return {"is_company_list": True, "next_page_url": DIR1 + "?page=2", "entries": [
                {"name": "Hausverwaltung Nummer 1 GmbH", "website": "https://hv1.example.de/",
                 "address": None, "postcode": None},
                {"name": "Kiez Verwaltung", "website": None, "address": "Kiezstraße 1",
                 "postcode": None},
                {"name": "Altbau Verwaltung", "website": "https://altbau-verwaltung.de",
                 "address": "10437 Berlin", "postcode": "10437"},
            ]}
        return {"is_company_list": True, "next_page_url": None, "entries": [
            {"name": "Hausverwaltung Nummer 9 GmbH", "website": None, "address": None,
             "postcode": None}]}
    if name == "QueryExpansion":
        tried = [ln for ln in text.splitlines() if ln.startswith("- ")]
        return {"queries": EXPANSIONS.get(len(tried), [])}
    if name == "CompanyKPIsOut":
        home = next(m.content for m in messages if "Homepage:" in str(m.content))
        url = re.search(r"URL: (\S+)", home).group(1)
        k = {f: {"value": None, "source_url": None, "evidence": None, "confidence": None}
             for f in ("units_managed", "management_types", "residential_focus", "portfolio_scope",
                       "tenant_portal_or_app", "software_mentioned", "metering_provider_mentioned",
                       "remote_reading_or_uvi_mentioned", "association_membership",
                       "employee_count")}
        k["management_types"] = {"value": ["WEG", "MIET"], "source_url": url,
                                 "evidence": "WEG-Verwaltung und Mietverwaltung",
                                 "confidence": "high"}
        return k
    if name == "Classification":
        if "makler" in text.lower() and "Spree Makler" in text:
            return {"category": "makler", "confidence": "high", "reason": "Sales only."}
        return {"category": "hausverwaltung", "confidence": "high", "reason": "WEG-Verwaltung."}
    raise AssertionError(name)


def scout_script(messages):
    n = sum(isinstance(m, ToolMessage) for m in messages)
    if n == 0:
        return tool_call("web_search", {"query": "Hausverwaltungen Berlin Liste"}, "t0")
    if n == 1:
        return tool_call("fetch_page", {"url": DIR1}, "t1")
    return AIMessage(content="Found two list pages.")


def mount_world(places: QueryPlaces):
    respx.post(PLACES_URL).mock(side_effect=places)
    respx.get(url__regex=DETAILS_ROUTE).mock(side_effect=DetailsFake(list(P.values())))
    respx.post(SERPER_URL).mock(return_value=httpx.Response(200, json={"organic": [
        {"title": "Hausverwaltungen in Berlin – Verzeichnis", "link": DIR1, "snippet": "Liste"},
        {"title": "Hausverwaltung Berlin – Portal", "link": DIR2, "snippet": "50 Firmen"},
    ]}))
    respx.get("https://verzeichnis.example/robots.txt").mock(return_value=httpx.Response(404))
    respx.get(url__regex=r"https://verzeichnis\.example/hv-berlin.*").mock(
        side_effect=lambda req: httpx.Response(
            200, text=DIR1_P2 if req.url.params.get("page") == "2" else DIR1_P1,
            headers={"content-type": "text/html"}))
    dir2 = respx.get(url__regex=r"https://portal\.example/.*").mock(
        return_value=httpx.Response(200, text=DIR1_P1, headers={"content-type": "text/html"}))
    for i in range(1, 15):
        mount_site(respx, f"https://hv{i}.example.de", {"/": MAKLER_HTML if i == 12 else PM_HTML})
    mount_site(respx, "https://altbau-verwaltung.de", {"": PM_HTML, "/": PM_HTML})
    return dir2


@respx.mock
async def test_discovery_loop_with_approval_crash_and_resume(tmp_path, models):
    places = QueryPlaces()
    dir2_route = mount_world(places)
    chat = ScriptedChat(responder, scout_script)

    def patch(ctx):
        ctx.settings.fetch.per_domain_interval_s = 0
        ctx.deps.llm = LLM(chat, provider="anthropic", model="claude-haiku-5-5",
                           cache=ctx.deps.cache, usage=ctx.deps.usage, models_cfg=models)

    opts = RunOptions(city="berlin", command="discover", area="mitte", use_scout=True,
                      max_rounds=5, llm_key="x", places_key="x", search_key="x",
                      run_id="disc-test", outputs_dir=tmp_path)

    # 1) the run pauses at the approval interrupt (no approver available)
    with pytest.raises(NeedsApproval) as paused:
        await execute(opts, patch_ctx=patch)
    offered = paused.value.args[0]["candidates"]
    assert [c["url"] for c in offered] == [DIR1, DIR2]  # made-up URL was dropped
    assert places.queries == []  # nothing paid before approval

    # 2) resume with approval of DIR1 only; crashes in round 2 (simulated Places error)
    decisions = []

    def approver(value):
        decisions.append(value)
        return {"approved": [0], "extra": []}

    with pytest.raises(Exception, match="simulated failure"):
        await execute(opts, resume=True, approver=approver, patch_ctx=patch)
    assert len(decisions) == 1

    # 3) resume again → finishes; completed work comes from the cache
    final = await execute(opts, resume=True, approver=approver, patch_ctx=patch)
    report = json.loads((tmp_path / "disc-test" / "report.json").read_text())

    # approvals: only DIR1 was extracted; DIR2 was never fetched
    assert [s["url"] for s in final["approved_sources"]] == [DIR1]
    assert set(final["source_stats"]) == {DIR1} and dir2_route.call_count == 0
    st = final["source_stats"][DIR1]
    assert (st["pages"], st["entries"], st["looked_up"], st["matched_on_places"]) == (2, 4, 2, 2)

    # rounds and stop rule
    rounds = report["rounds"]
    # Enterprise sweep returns websites, so the DIR1 entry for P1 merges with P1 in round 1
    assert [r["new_unique"] for r in rounds] == [14, 1, 0, 0]
    assert rounds[0]["yield"] is None and rounds[1]["yield"] == pytest.approx(1 / 14)
    assert "saturated" in report["stopped_reason"]
    assert report["queries_tried"][5:] == ["Objektverwaltung", "Sondereigentumsverwaltung",
                                           "Grundstücksverwaltung",
                                           "Hausmeisterservice Verwaltung", "Wohnungsverwaltung"]

    # coverage after details (P1 list entry merges with the Places record by domain)
    cov = report["coverage"]
    assert (cov["n1_places"], cov["n2_lists"], cov["m_both"], cov["union"]) == (13, 4, 2, 15)
    assert cov["chapman_estimate"] == pytest.approx(14 * 5 / 3 - 1, abs=0.01)
    assert cov["overlap_matrix"]["places"]["list:verzeichnis.example"] == 2

    # crash + resume: budget counters are cumulative, completed pages were cache hits
    # sweep (Enterprise): 5 + 3 (one failed) + 2 + 1; lookups (Pro): 2; Place Details: only
    # the list entry found by lookup alone (P14) — sweep records already carry website/phone
    assert report["usage"]["places_by_sku"] == {"text_search_enterprise": 11,
                                                "text_search_pro": 2,
                                                "place_details_enterprise": 1}
    assert report["usage"]["search_requests"] == 1
    assert report["cache"]["places"]["hits"] >= 1
    assert report["run_id"] == "disc-test" and report["resumed"] is True

    # classification + export
    assert report["counts"]["leads"] == 14 and report["counts"]["not_property_managers"] == 1
    wb = load_workbook(final["outputs"]["xlsx"])
    assert wb.sheetnames == ["leads", "review", "excluded", "coverage", "sources",
                             "kpi_definitions", "evidence", "contacts"]
    src_rows = list(wb["sources"].iter_rows(values_only=True))
    assert len(src_rows) == 3  # header + DIR1 (approved) + DIR2 (not approved)
    approved_by = {r[0]: r[4] for r in src_rows[1:]}
    assert approved_by == {DIR1: "user", DIR2: "not approved"}
    cov_cells = [c for row in wb["coverage"].iter_rows(values_only=True) for c in row if c]
    assert "chapman_estimate" in cov_cells and "saturated" in " ".join(map(str, cov_cells))

    # run.json never contains keys; it reloads for `leadgraph resume`
    raw = (tmp_path / "disc-test" / "run.json").read_text()
    assert '"x"' not in raw and "key" not in raw
    assert load_run_options("disc-test", tmp_path).command == "discover"

    # 4) resuming a finished run is a no-op
    again = await execute(opts, resume=True, approver=approver, patch_ctx=patch)
    assert again.get("_already_finished") is True


@respx.mock
async def test_enrich_from_finished_discovery(tmp_path, models):
    places = QueryPlaces()
    places.fail_once = set()
    mount_world(places)
    chat = ScriptedChat(responder, scout_script)

    def patch(ctx):
        ctx.settings.fetch.per_domain_interval_s = 0
        ctx.deps.llm = LLM(chat, provider="anthropic", model="claude-haiku-5-5",
                           cache=ctx.deps.cache, usage=ctx.deps.usage, models_cfg=models)

    disc = RunOptions(city="berlin", command="discover", area="mitte", use_scout=True,
                      approve="1", max_rounds=5, llm_key="x", places_key="x", search_key="x",
                      run_id="disc-e", outputs_dir=tmp_path)
    await execute(disc, patch_ctx=patch)
    calls_before = places.queries[:]

    enr = RunOptions(city="berlin", command="enrich", source_run="disc-e", limit=3,
                     llm_key="x", run_id="enr-e", outputs_dir=tmp_path, approve="none")
    final = await execute(enr, patch_ctx=patch)
    assert places.queries == calls_before  # enrich makes no Places calls
    assert len(final["researched"]) == 3  # --limit 3
    stats = final["research_stats"]
    assert stats["succeeded"] == 3 and stats["max_tool_calls"] <= 6
    researched = [c for c in final["companies"] if c.get("kpis")]
    assert len(researched) == 3
    assert all(c["kpis"]["management_types"]["value"] == ["WEG", "MIET"] for c in researched)
    # discovery results are carried over into the enrich export
    report = json.loads((tmp_path / "enr-e" / "report.json").read_text())
    assert report["coverage"]["union"] == 15 and len(report["rounds"]) == 4
    wb = load_workbook(final["outputs"]["xlsx"])
    ev = list(wb["evidence"].iter_rows(values_only=True))
    assert len(ev) == 1 + 3 and ev[1][1] == "management_types"
