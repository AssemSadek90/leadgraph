"""M7: evaluation harness on a tiny gold set with two fake models; rerun from cache."""

import json

import respx
from langchain_core.messages import AIMessage

from leadgraph.evaluation import ModelSpec, contact_correct, run_eval
from leadgraph.export import KPI_COLUMNS
from leadgraph.validation_sample import GOLD_COLUMNS, write_gold
from tests.fakes import ScriptedChat, html_page, mount_site

SITES = {
    "hv1.de": html_page("HV1", "<p>Wir verwalten über 1.200 Wohneinheiten.</p>"
                        "<a href='/impressum'>Impressum</a>"),
    "hv2.de": html_page("HV2", "<p>WEG-Verwaltung seit 1990, 450 Einheiten.</p>"
                        "<a href='/impressum'>Impressum</a>"),
}
IMPRESSUM = {"hv1.de": "Geschäftsführer: Max Mustermann", "hv2.de": "Inhaberin: Eva Klein"}


def make_responder(good: bool):
    def respond(schema, messages):
        name, text = schema.__name__, str(messages[-1].content)
        if name == "Classification":
            return {"category": "hausverwaltung", "confidence": "high", "reason": "WEG."}
        if name == "CompanyKPIsOut":
            home = next(str(m.content) for m in messages if "Homepage:" in str(m.content))
            k = {f: {"value": None, "source_url": None, "evidence": None, "confidence": None}
                 for f in KPI_COLUMNS}
            if "hv1.de" in home:
                k["units_managed"] = ({"value": 1200, "evidence": "über 1.200 Wohneinheiten"}
                                      if good else
                                      {"value": 5000, "evidence": "über 5.000 Wohneinheiten"})
                k["units_managed"] |= {"source_url": "https://hv1.de/", "confidence": "high"}
            else:
                k["units_managed"] = {"value": 450, "evidence": "450 Einheiten",
                                      "source_url": "https://hv2.de/", "confidence": "high"}
            return k
        if name == "ContactsOut":
            if "hv1.de" in text:
                return {"people": [{"name": "Max Mustermann", "role": "Geschäftsführer",
                                    "role_category": "geschaeftsfuehrer", "email": None,
                                    "source_url": "https://hv1.de/impressum"}]}
            return {"people": [{"name": "Eva Klein", "role": "Inhaberin",
                                "role_category": "inhaber", "email": None,
                                "source_url": "https://hv2.de/impressum"}]}
        raise AssertionError(name)
    return respond


def test_contact_correct_rules():
    assert contact_correct("Max Müller", {"true_contact_name": "max müller"})
    assert contact_correct("Max", {"pred_contact_name": "Max", "true_contact_ok": "y"})
    assert not contact_correct("Max", {"pred_contact_name": "Max", "true_contact_ok": "n"})
    assert contact_correct("Eva", {"pred_contact_name": "Max", "true_contact_ok": "n"})
    assert contact_correct("Max", {"pred_contact_name": "Max"}) is None


@respx.mock
async def test_eval_two_models_and_cached_rerun(tmp_path, monkeypatch):
    for host, html in SITES.items():
        mount_site(respx, f"https://{host}", {"/": html, "/impressum": html_page(
            "Impressum", f"<p>{IMPRESSUM[host]}</p>")})
    run = tmp_path / "outputs" / "enr-x"
    run.mkdir(parents=True)
    (run / "run.json").write_text(json.dumps({"city": "berlin", "segment": "hausverwaltung"}))
    comps = [{"id": h, "name": f"Hausverwaltung {h}", "website": f"https://{h}/",
              "google_types": ["real_estate_agency"]} for h in SITES]
    (run / "companies.jsonl").write_text("\n".join(json.dumps(c) for c in comps))
    gold = tmp_path / "gold.csv"
    rows = [{c: "" for c in GOLD_COLUMNS} | {"id": "hv1.de", "name": "HV1", "tier": "A",
            "pred_contact_name": "Max Mustermann", "true_is_property_manager": "y",
            "true_units_managed": "1200", "true_contact_ok": "y"},
            {c: "" for c in GOLD_COLUMNS} | {"id": "hv2.de", "name": "HV2", "tier": "B",
            "true_is_property_manager": "y", "true_units_managed": "450",
            "true_contact_name": "Eva Klein"}]
    write_gold(gold, rows)

    script = lambda messages: AIMessage(content="done")  # noqa: E731
    good, bad = ScriptedChat(make_responder(True), script), ScriptedChat(make_responder(False),
                                                                          script)
    specs = [ModelSpec.parse("anthropic:claude-haiku-5-5"), ModelSpec.parse("openai:gpt-6-luna")]
    chats = {specs[0].label: good, specs[1].label: bad}
    eval_dir = tmp_path / "eval"

    import leadgraph.evaluation as ev
    orig = ev.load_settings

    def fast_settings():
        s = orig()
        s.fetch.per_domain_interval_s = 0
        return s
    monkeypatch.setattr(ev, "load_settings", fast_settings)

    out = await run_eval("enr-x", gold, specs, max_llm_cost=1.0,
                         outputs_dir=tmp_path / "outputs", eval_dir=eval_dir, chats=chats)
    a, b = out["summaries"]
    assert a["classification"]["accuracy"] == 1.0 and a["units_managed"]["accuracy"] == 1.0
    assert a["contact"]["accuracy"] == 1.0 and a["evidence_valid"]["accuracy"] == 1.0
    assert b["units_managed"]["accuracy"] == 0.5  # tampered quote on hv1 → null → wrong
    assert b["evidence_valid"]["accuracy"] == 0.5 and a["cost_per_lead_usd"] > 0
    md = (eval_dir / "results.md").read_text()
    assert "| anthropic:claude-haiku-5-5 | 100% (n=2)" in md and (eval_dir / "results.png").exists()

    # rerun: same table, no new model calls, cost/latency reused from the first run
    calls = good.calls + bad.calls
    out2 = await run_eval("enr-x", gold, specs, max_llm_cost=1.0,
                          outputs_dir=tmp_path / "outputs", eval_dir=eval_dir, chats=chats)
    assert good.calls + bad.calls == calls
    assert out2["summaries"] == [s | {"cached_share": 1.0} for s in out["summaries"]]
