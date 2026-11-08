"""Offline end-to-end `run` (M1 → M4): Places → details → classify → research → contacts
(+ LinkedIn via search) → score → validate → export with all sheets."""

import json
import re

import httpx
import respx
from langchain_core.messages import AIMessage
from openpyxl import load_workbook

from leadgraph.export import KPI_COLUMNS
from leadgraph.llm import LLM
from leadgraph.runner import RunOptions, execute
from leadgraph.sources.places import PLACES_URL
from leadgraph.sources.search import SERPER_URL
from tests.fakes import (
    DETAILS_ROUTE,
    DetailsFake,
    PlacesFake,
    ScriptedChat,
    html_page,
    make_place,
    mount_site,
)

PLACES = [make_place(1), make_place(2), make_place(3, displayName={"text": "Spree Makler GmbH"})]
HV1 = {
    "/": html_page("HV 1", "<p>Wir verwalten über 3.500 Wohneinheiten in Berlin. "
                   "Mieterportal rund um die Uhr. Mitglied im VDIV.</p>"
                   "<a href='/team'>Team</a> <a href='/impressum'>Impressum</a>"),
    "/team": html_page("Team", "<p>Anna Schmidt – Geschäftsführerin – a.schmidt@hv1.example.de</p>"),
    "/impressum": html_page("Impressum", "<p>Geschäftsführerin: Anna Schmidt</p>"),
}
HV2 = {
    "/": html_page("HV 2", "<p>Hausverwaltung für WEG und Miete in Berlin.</p>"
                   "<a href='/impressum'>Impressum</a>"),
    "/impressum": html_page("Impressum", "<p>Inhaber: Bernd Kurz, bernd.kurz@gmail.com</p>"),
}
MAKLER = {"/": html_page("Makler", "<p>Makler für Verkauf.</p>")}


def kpis_for(url: str) -> dict:
    k = {f: {"value": None, "source_url": None, "evidence": None, "confidence": None}
         for f in KPI_COLUMNS}
    if "hv1" in url:
        k["units_managed"] = {"value": 3500, "source_url": url, "confidence": "high",
                              "evidence": "über 3.500 Wohneinheiten in Berlin"}
        k["residential_focus"] = {"value": True, "source_url": url, "confidence": "high",
                                  "evidence": "über 3.500 Wohneinheiten"}
        k["tenant_portal_or_app"] = {"value": True, "source_url": url, "confidence": "high",
                                     "evidence": "Mieterportal rund um die Uhr"}
        k["portfolio_scope"] = {"value": "berlin", "source_url": url, "confidence": "medium",
                                "evidence": "Wohneinheiten in Berlin"}
        k["association_membership"] = {"value": ["VDIV"], "source_url": url,
                                       "confidence": "high", "evidence": "Mitglied im VDIV"}
    return k


def responder(schema, messages):
    name, text = schema.__name__, str(messages[-1].content)
    if name == "Classification":
        if "Spree Makler" in text:
            return {"category": "makler", "confidence": "high", "reason": "Sales only."}
        return {"category": "hausverwaltung", "confidence": "high", "reason": "Manages WEG."}
    if name == "CompanyKPIsOut":
        home = next(str(m.content) for m in messages if "Homepage:" in str(m.content))
        return kpis_for(re.search(r"URL: (\S+)", home).group(1))
    if name == "ContactsOut":
        if "hv1.example.de" in text:
            return {"people": [{"name": "Anna Schmidt", "role": "Geschäftsführerin",
                                "role_category": "geschaeftsfuehrer",
                                "email": "a.schmidt@hv1.example.de",
                                "source_url": "https://hv1.example.de/team"}]}
        return {"people": [{"name": "Bernd Kurz", "role": "Inhaber", "role_category": "inhaber",
                            "email": "bernd.kurz@gmail.com",
                            "source_url": "https://hv2.example.de/impressum"}]}
    raise AssertionError(name)


@respx.mock
async def test_full_run_pipeline(tmp_path, models):
    respx.post(PLACES_URL).mock(side_effect=PlacesFake([PLACES]))
    respx.get(url__regex=DETAILS_ROUTE).mock(side_effect=DetailsFake(PLACES))
    serper = respx.post(SERPER_URL).mock(side_effect=lambda req: httpx.Response(200, json={
        "organic": [{"title": "Anna Schmidt – Geschäftsführerin – HV | LinkedIn",
                     "link": "https://de.linkedin.com/in/anna-schmidt-hv"}]
        if "Anna Schmidt" in json.loads(req.content)["q"] else
        [{"title": "Bernd Lang – LinkedIn", "link": "https://de.linkedin.com/in/bernd-lang"}]}))
    mount_site(respx, "https://hv1.example.de", HV1)
    mount_site(respx, "https://hv2.example.de", HV2)
    mount_site(respx, "https://hv3.example.de", MAKLER)
    chat = ScriptedChat(responder, lambda messages: AIMessage(content="done"))

    async def fake_dns(name, rtype):
        return ["10 mx.example."]

    def patch(ctx):
        ctx.settings.fetch.per_domain_interval_s = 0
        ctx.deps.resolver = fake_dns
        ctx.deps.llm = LLM(chat, provider="anthropic", model="claude-haiku-5-5",
                           cache=ctx.deps.cache, usage=ctx.deps.usage, models_cfg=models)

    opts = RunOptions(city="berlin", command="run", area="mitte", limit=20, llm_key="x",
                      places_key="x", search_key="x", run_id="run-m4", outputs_dir=tmp_path,
                      approve="none")
    final = await execute(opts, patch_ctx=patch)
    report = json.loads((tmp_path / "run-m4" / "report.json").read_text())
    by_name = {c["name"]: c for c in final["companies"]}

    hv1 = by_name["Hausverwaltung Nummer 1 GmbH"]
    assert hv1["fit_score"] == 100.0 and hv1["tier"] == "A"  # 30+20+15+15+10+10
    assert hv1["contacts"][0]["email_type"] == "personal"
    assert hv1["contacts"][0]["linkedin_url"] == "https://de.linkedin.com/in/anna-schmidt-hv"
    assert hv1["contacts"][0]["linkedin_verified"] is False and not hv1["needs_review"]

    hv2 = by_name["Hausverwaltung Nummer 2 GmbH"]
    assert hv2["contacts"][0]["linkedin_url"] is None  # result title did not name him
    assert hv2["needs_review"] and "email_domain_mismatch:bernd.kurz@gmail.com" in \
        hv2["review_reasons"]
    # unknown units 9 + unknown residential 10 + no digital 4.5 + personal email 15
    # + unknown scope 7 + unknown association 4 = 49.5 → B (the gmail address is flagged)
    assert hv2["fit_score"] == 49.5 and hv2["tier"] == "B"
    assert serper.call_count == 2  # one LinkedIn query per company's top contact

    wb = load_workbook(final["outputs"]["xlsx"])
    assert {"leads", "review", "excluded", "coverage", "sources", "kpi_definitions"} <= set(
        wb.sheetnames)
    leads = list(wb["leads"].iter_rows(values_only=True))
    header = leads[0]
    tiers = [r[header.index("tier")] for r in leads[1:]]
    assert tiers == sorted(tiers, key="ABC".index) and len(leads) == 3  # 2 PMs, best first
    defs = {r[0] for r in wb["kpi_definitions"].iter_rows(values_only=True)}
    assert set(KPI_COLUMNS) <= defs
    review = [r[0] for r in wb["review"].iter_rows(values_only=True)][1:]
    assert review == ["Hausverwaltung Nummer 2 GmbH"]
    # no email from another domain in leads without a review flag
    hdr = {h: i for i, h in enumerate(header)}
    for r in leads[1:]:
        email = r[hdr["top_contact_email"]]
        if email and not email.endswith(r[hdr["website"]].split("//")[1].strip("/")):
            assert r[hdr["needs_review"]] is True
    for key in ("research", "contacts", "scoring", "validation"):
        assert report[key]
    assert report["scoring"]["tiers"]["A"] == 1
