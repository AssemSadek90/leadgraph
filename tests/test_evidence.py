from leadgraph.evidence import evidence_in_text, non_null_count, normalize, numbers_in, verify_kpis

HOME = "Willkommen bei Muster HV.\nWir verwalten über 1.200 Wohneinheiten in Berlin – seit 1998."
SERV = "Leistungen: WEG-Verwaltung und Mietverwaltung. Abrechnung mit Techem. Mitglied im VDIV."
PAGES = {"https://muster.de/": HOME, "https://muster.de/leistungen": SERV}


def page(url):
    return PAGES.get(url)


def item(value, url, ev, conf="high"):
    return {"value": value, "source_url": url, "evidence": ev, "confidence": conf}


def test_normalize_and_match():
    assert normalize("Über 1.200  „Einheiten“") == 'über 1.200 "einheiten"'
    assert evidence_in_text("über 1.200 Wohneinheiten", HOME)
    assert evidence_in_text("Wir verwalten   über 1.200 Wohneinheiten in Berlin - seit 1998", HOME)
    # small typo in a long snippet is tolerated (≥ 90 % contiguous)
    assert evidence_in_text("Wir verwalten über 1.200 Wohneinheiten in Berlin – seit 1999.", HOME)
    assert not evidence_in_text("über 5.000 Wohneinheiten", HOME)
    assert not evidence_in_text("", HOME)


def test_numbers_in():
    assert numbers_in("über 1.200 Einheiten, ca. 2 500 Whg., 3,000+") >= {1200, 2500, 3000}
    assert 1200 in numbers_in("1200 Wohnungen")


def test_verify_accepts_valid_and_rejects_tampered():
    raw = {
        "units_managed": item(1200, "https://muster.de/", "über 1.200 Wohneinheiten"),
        "management_types": item(["WEG", "MIET"], "https://muster.de/leistungen",
                                 "WEG-Verwaltung und Mietverwaltung"),
        "metering_provider_mentioned": item(["Techem", "ista"], "https://muster.de/leistungen",
                                            "Abrechnung mit Techem"),
        "association_membership": item(["VDIV"], "https://muster.de/leistungen", "Mitglied im VDIV"),
        "employee_count": item(25, "https://muster.de/", "über 1.200 Wohneinheiten"),  # wrong number
        "residential_focus": item(True, "https://muster.de/team", "Wohnungen"),  # page never fetched
        "tenant_portal_or_app": item(True, "https://muster.de/", "Mieterportal rund um die Uhr"),
        "portfolio_scope": item(None, None, None),
    }
    kpis, reasons = verify_kpis(raw, page)
    assert kpis.units_managed.value == 1200 and kpis.units_managed.source_url == "https://muster.de/"
    assert kpis.management_types.value == ["WEG", "MIET"]
    assert kpis.metering_provider_mentioned.value == ["Techem"]  # "ista" not in the quote
    assert kpis.association_membership.value == ["VDIV"]
    assert kpis.employee_count.value is None and "value_not_in_evidence:employee_count" in reasons
    assert kpis.residential_focus.value is None and "evidence_not_found:residential_focus" in reasons
    assert kpis.tenant_portal_or_app.value is None
    assert "evidence_not_found:tenant_portal_or_app" in reasons
    assert kpis.portfolio_scope.value is None and non_null_count(kpis) == 4
    # no value anywhere without evidence
    for name in type(kpis).model_fields:
        f = getattr(kpis, name)
        assert f.value is None or (f.evidence and f.source_url)


def test_tampered_evidence_sets_null_with_reason():
    raw = {"units_managed": item(5000, "https://muster.de/", "über 5.000 Wohneinheiten")}
    kpis, reasons = verify_kpis(raw, page)
    assert kpis.units_managed.value is None
    assert reasons == ["evidence_not_found:units_managed"]


def test_long_evidence_is_truncated_to_contract_limit():
    long_text = "Wir verwalten über 1.200 Wohneinheiten. " * 20
    raw = {"units_managed": item(1200, "u", long_text.strip())}
    kpis, _ = verify_kpis(raw, lambda u: long_text)
    assert kpis.units_managed.value == 1200 and len(kpis.units_managed.evidence) == 300


def test_fragments_and_corrected_source_url():
    page = "Leistungen\nMietverwaltung\nKontakt\nWEG-Verwaltung\nSondereigentumsverwaltung"
    assert evidence_in_text("Mietverwaltung\nWEG-Verwaltung\nSondereigentumsverwaltung", page)
    assert evidence_in_text("Mietverwaltung ... Sondereigentumsverwaltung", page)
    assert not evidence_in_text("Mietverwaltung ... Gewerbeverwaltung", page)
    assert not evidence_in_text("Miet ... WE", page)  # fragments too short
    pages = {"https://x.de/": "Start", "https://x.de/ueber": "Wir verwalten 1.500 Einheiten."}
    raw = {"units_managed": item(1500, "https://x.de/", "Wir verwalten 1.500 Einheiten.")}
    kpis, reasons = verify_kpis(raw, pages.get, list(pages))
    assert kpis.units_managed.value == 1500 and reasons == []
    assert kpis.units_managed.source_url == "https://x.de/ueber"  # corrected
    kpis, reasons = verify_kpis(raw, pages.get)  # without the page list: rejected
    assert reasons == ["evidence_not_found:units_managed"]


def test_site_root():
    from leadgraph.web.fetch import site_root
    assert site_root("https://haveo.de/kontakt/") == "https://haveo.de/"
    assert site_root("http://www.x.de/fr/contact-fr/?a=1") == "http://www.x.de/"
    assert site_root("x.de/a") == "https://x.de/"
