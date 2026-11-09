"""M3: research agent against a local fixture server, evidence verification, domain guard,
Send fan-out."""

import functools
import json
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
import respx
from langchain_core.messages import AIMessage, ToolMessage

from leadgraph.cache import Cache
from leadgraph.graph.build import build_graph
from leadgraph.graph.nodes.research import internal_links, link_priority
from leadgraph.graph.state import Deps, Mode, RunContext
from leadgraph.llm import LLM, ProviderUnavailable
from leadgraph.usage import Usage
from leadgraph.web.fetch import Fetcher
from tests.fakes import ScriptedChat, tool_call

SITE_A = Path(__file__).parent / "fixtures" / "site_a"


class FixtureServer:
    """Serves tests/fixtures/site_a on 127.0.0.1 and records requested paths."""

    def __init__(self, root: Path):
        paths = self.paths = []

        class Handler(SimpleHTTPRequestHandler):
            def log_message(self, *a):
                paths.append(self.path)

        handler = functools.partial(Handler, directory=str(root))
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def __enter__(self):
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


def empty_kpis():
    names = ["units_managed", "management_types", "residential_focus", "portfolio_scope",
             "tenant_portal_or_app", "software_mentioned", "metering_provider_mentioned",
             "remote_reading_or_uvi_mentioned", "association_membership", "employee_count"]
    return {n: {"value": None, "source_url": None, "evidence": None, "confidence": None}
            for n in names}


def contacts_answer(base: str):
    return {"people": [
        {"name": "Max Mustermann", "role": "Geschäftsführer", "role_category": "geschaeftsfuehrer",
         "email": "m.mustermann@muster-hv.de", "source_url": f"{base}/team.html"},
        {"name": "Erika Beispiel", "role": "Leitung WEG-Verwaltung", "role_category": "leitung_weg",
         "email": "weg@muster-hv.de", "source_url": f"{base}/team.html"},
        {"name": "Erfundene Person", "role": "Prokurist", "role_category": "prokurist",
         "email": "e.person@muster-hv.de", "source_url": f"{base}/team.html"},  # not on page
        {"name": "Max Mustermann", "role": "GF", "role_category": "geschaeftsfuehrer",
         "email": "max@mustermann-privat.de", "source_url": f"{base}/impressum.html"},  # dup
    ]}


def kpi_responder(base: str, tampered: bool = False):
    def respond(schema, messages):
        if schema.__name__ == "ContactsOut":
            return contacts_answer(base)
        k = empty_kpis()
        k["units_managed"] = {"value": 5000 if tampered else 1200, "source_url": f"{base}/",
                              "evidence": "über 5.000 Wohneinheiten" if tampered
                              else "über 1.200 Wohneinheiten", "confidence": "high"}
        k["management_types"] = {"value": ["WEG", "MIET"], "source_url": f"{base}/leistungen.html",
                                 "evidence": "WEG-Verwaltung und Mietverwaltung",
                                 "confidence": "high"}
        k["residential_focus"] = {"value": True, "source_url": f"{base}/",
                                  "evidence": "über 1.200 Wohneinheiten in Berlin",
                                  "confidence": "medium"}
        return k
    return respond


def research_script(evil_url: str):
    def script(messages):
        n = sum(isinstance(m, ToolMessage) for m in messages)
        steps = [("list_links", {"url": "/"}), ("fetch_page", {"url": "/leistungen.html"}),
                 ("fetch_page", {"url": "/impressum.html"}), ("fetch_page", {"url": evil_url})]
        if n < len(steps):
            name, args = steps[n]
            return tool_call(name, args, f"r{n}")
        return AIMessage(content="done")
    return script


def make_ctx(tmp_path, settings, models, segment, city, chat, limit=None):
    cache = Cache(tmp_path / ".cache")
    usage = Usage(max_places_requests=0, max_llm_cost_usd=1.0)
    fetcher = Fetcher(settings.fetch, cache, usage)
    llm = LLM(chat, provider="anthropic", model="claude-haiku-5-5", cache=cache, usage=usage,
              models_cfg=models)
    async def fake_dns(name, rtype):
        return ["10 mx.example."]

    return RunContext(run_id="enr-test", city=city, segment=segment, settings=settings,
                      models=models, provider="anthropic", model="claude-haiku-5-5",
                      mode=Mode(command="enrich", research=True), limit=limit,
                      outputs_dir=tmp_path,
                      deps=Deps(cache=cache, usage=usage, places=None, fetcher=fetcher, llm=llm,
                                resolver=fake_dns))


def company(cid: str, website: str, pm: bool = True) -> dict:
    return {"id": cid, "name": "Muster Hausverwaltung GmbH", "website": website,
            "is_property_manager": pm, "discovery_sources": ["places:Hausverwaltung"]}


async def run_enrich(ctx, companies):
    final = await build_graph().ainvoke({"run_id": ctx.run_id, "companies": companies},
                                        context=ctx, config={"max_concurrency": 4})
    await ctx.deps.fetcher.aclose()
    return final


@pytest.fixture
def local_net(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")


async def test_research_agent_on_fixture_site(tmp_path, settings, models, segment, city,
                                             local_net):
    with FixtureServer(SITE_A) as srv:
        evil = srv.url.replace("127.0.0.1", "localhost") + "/evil"
        chat = ScriptedChat(kpi_responder(srv.url), research_script(evil))
        ctx = make_ctx(tmp_path, settings, models, segment, city, chat)
        final = await run_enrich(ctx, [company("127.0.0.1", srv.url + "/")])

    c = final["companies"][0]
    k = c["kpis"]
    assert k["units_managed"]["value"] == 1200
    assert k["units_managed"]["source_url"] == f"{srv.url}/"
    assert k["units_managed"]["evidence"] == "über 1.200 Wohneinheiten"
    assert set(k["management_types"]["value"]) == {"WEG", "MIET"}
    assert k["management_types"]["source_url"].endswith("/leistungen.html")
    assert k["residential_focus"]["value"] is True
    # no field anywhere has a value without evidence
    for item in k.values():
        assert item["value"] in (None, []) or (item["evidence"] and item["source_url"])
    assert c["data_completeness"] == 0.3
    # validate: the fixture is served from 127.0.0.1, so @muster-hv.de emails are flagged
    assert c["needs_review"] and c["review_reasons"] == [
        "email_domain_mismatch:m.mustermann@muster-hv.de", "email_domain_mismatch:weg@muster-hv.de"]

    # domain guard: the off-domain fetch was refused in code and never reached the server
    trace = final["researched"][0]["trace"]
    assert [t["status"] for t in trace["tool_calls"]] == ["ok", "ok", "ok", "error"]
    assert "/evil" not in srv.paths
    # contacts fetched the team page (own domain) because research had skipped it
    assert set(srv.paths) <= {"/robots.txt", "/", "/leistungen.html", "/impressum.html",
                              "/team.html"}

    # contacts: names must be on the page, emails must be published on the site
    people = {p["name"]: p for p in c["contacts"]}
    assert list(people) == ["Max Mustermann", "Erika Beispiel"]
    assert people["Max Mustermann"]["email"] == "m.mustermann@muster-hv.de"
    assert people["Max Mustermann"]["email_type"] == "personal"
    assert people["Erika Beispiel"]["email_type"] == "role"
    assert all(p["linkedin_verified"] is False for p in c["contacts"])

    # score: 1,200 units (24) + residential (20) + no digital (4.5) + personal email (15)
    #        + unknown scope (7) + unknown association (4) = 74.5 → A
    assert c["fit_score"] == 74.5 and c["tier"] == "A"

    # report numbers: tool calls ≤ 6, cost per company recorded
    stats = final["research_stats"]
    assert stats["avg_tool_calls"] == 4 and stats["max_tool_calls"] <= 6
    assert stats["per_company"][0]["cost_usd"] > 0 and stats["share_with_3plus_kpis"] == 1.0
    report = json.loads((tmp_path / "enr-test" / "report.json").read_text())
    assert report["research"]["avg_tool_calls"] == 4


async def test_tampered_evidence_is_nulled(tmp_path, settings, models, segment, city, local_net):
    with FixtureServer(SITE_A) as srv:
        chat = ScriptedChat(kpi_responder(srv.url, tampered=True), research_script("/x"))
        ctx = make_ctx(tmp_path, settings, models, segment, city, chat)
        final = await run_enrich(ctx, [company("127.0.0.1", srv.url + "/")])
    c = final["companies"][0]
    assert c["kpis"]["units_managed"]["value"] is None
    assert "evidence_not_found:units_managed" in c["review_reasons"] and c["needs_review"]
    assert c["kpis"]["management_types"]["value"] == ["WEG", "MIET"]  # valid fields survive


@respx.mock
async def test_send_fan_out_one_branch_per_property_manager(tmp_path, settings, models, segment,
                                                           city):
    pages = {p.name: p.read_text(encoding="utf-8") for p in SITE_A.glob("*.html")}
    for host in ("hv-a.de", "hv-b.de", "makler.de"):
        respx.get(f"https://{host}/robots.txt").mock(return_value=httpx.Response(404))
        for name, html in pages.items():
            path = "/" if name == "index.html" else f"/{name}"
            respx.get(f"https://{host}{path}").mock(return_value=httpx.Response(
                200, text=html, headers={"content-type": "text/html"}))

    def script(messages):
        return AIMessage(content="done")  # answer from the homepage only

    def respond(schema, messages):
        home = [m.content for m in messages if "Homepage:" in str(m.content)][0]
        base = "https://hv-a.de" if "hv-a.de" in home else "https://hv-b.de"
        return kpi_responder(base)(schema, messages)

    chat = ScriptedChat(respond, script)
    ctx = make_ctx(tmp_path, settings, models, segment, city, chat)
    comps = [company("hv-a.de", "https://hv-a.de/"), company("hv-b.de", "https://hv-b.de/"),
             company("makler.de", "https://makler.de/", pm=False),
             {**company("noweb", None), "id": "h-noweb"}]
    final = await run_enrich(ctx, comps)
    assert sorted(r["company_id"] for r in final["researched"]) == ["hv-a.de", "hv-b.de"]
    by_id = {c["id"]: c for c in final["companies"]}
    assert by_id["hv-a.de"]["kpis"]["units_managed"]["value"] == 1200
    assert by_id["hv-b.de"]["kpis"]["units_managed"]["source_url"] == "https://hv-b.de/"
    # management_types cites /leistungen.html, which these agents never fetched → nulled
    assert "evidence_not_found:management_types" in by_id["hv-a.de"]["review_reasons"]
    assert by_id["makler.de"]["kpis"] is None and by_id["h-noweb"]["kpis"] is None


@respx.mock
async def test_no_credit_stops_the_fan_out_and_resume_finishes(tmp_path, settings, models,
                                                               segment, city):
    """Live Berlin run, 9 Nov: the credit ran out mid fan-out and every later company failed.
    Now the run stops with its checkpoint, and resume redoes only the unfinished branches."""
    from langgraph.checkpoint.memory import InMemorySaver

    pages = {p.name: p.read_text(encoding="utf-8") for p in SITE_A.glob("*.html")}
    hosts = ("hv-a.de", "hv-b.de", "hv-c.de")
    for host in hosts:
        respx.get(f"https://{host}/robots.txt").mock(return_value=httpx.Response(404))
        for name, html in pages.items():
            path = "/" if name == "index.html" else f"/{name}"
            respx.get(f"https://{host}{path}").mock(return_value=httpx.Response(
                200, text=html, headers={"content-type": "text/html"}))

    def home_of(messages):
        home = [m.content for m in messages if "Homepage:" in str(m.content)]
        return next((h for h in hosts if home and h in home[0]), None)

    class NoCredit(Exception):
        status_code = 400

    def broke(schema, messages):
        if home_of(messages) == "hv-b.de":
            raise NoCredit("Error code: 400 - Your credit balance is too low.")
        return kpi_responder(f"https://{home_of(messages) or 'hv-a.de'}")(schema, messages)

    def ok(schema, messages):
        if schema.__name__ == "ContactsOut":
            return {"people": []}
        return kpi_responder(f"https://{home_of(messages) or 'hv-a.de'}")(schema, messages)

    def script(messages):
        return AIMessage(content="done")

    graph = build_graph(InMemorySaver())
    config = {"configurable": {"thread_id": "t1"}, "max_concurrency": 1}
    comps = [company(h, f"https://{h}/") for h in hosts]

    ctx = make_ctx(tmp_path, settings, models, segment, city, ScriptedChat(broke, script))
    with pytest.raises(ProviderUnavailable, match="credit balance"):
        await graph.ainvoke({"run_id": ctx.run_id, "companies": comps}, config, context=ctx)
    snap = await graph.aget_state(config)
    assert snap.next  # stopped mid-run, not finished with errors

    ctx2 = make_ctx(tmp_path, settings, models, segment, city, ScriptedChat(ok, script))
    ctx2.deps.fetcher = ctx.deps.fetcher
    final = await graph.ainvoke(None, config, context=ctx2)
    await ctx.deps.fetcher.aclose()
    assert sorted(r["company_id"] for r in final["researched"]) == sorted(hosts)
    assert not [r for r in final["researched"] if r.get("error")]
    assert all(c["kpis"]["units_managed"]["value"] == 1200 for c in final["companies"])

def test_link_priority():
    assert link_priority("https://x.de/impressum", "") == 0
    assert link_priority("https://x.de/ueber-uns", "") == 1
    assert link_priority("https://x.de/p?id=3", "Leistungen") == 2
    links = [("https://x.de/blog", "Blog"), ("https://x.de/impressum", "Impressum"),
             ("https://other.de/team", "Team"), ("https://x.de/flyer.pdf", "Flyer"),
             ("https://www.x.de/team/", "Team")]
    assert [u for u, _ in internal_links(links, "x.de", 10)] == [
        "https://x.de/impressum", "https://www.x.de/team/", "https://x.de/blog"]
