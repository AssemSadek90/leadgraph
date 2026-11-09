from leadgraph.dedupe import (
    apply_filters,
    company_id,
    dedupe,
    normalize_domain,
    normalize_name,
    normalize_phone,
    postcode_ok,
)
from leadgraph.schema import Candidate


def C(name, **kw):
    return Candidate(name=name, source=kw.pop("source", "places:Hausverwaltung"), **kw)


def six_records():
    return [
        # 1 + 2 share a domain (www. and scheme differ)
        C("Muster Hausverwaltung GmbH", website="https://www.muster-hv.de/", postcode="10115"),
        C("Muster HV", website="http://muster-hv.de/kontakt", postcode="10117",
          source="directory:vdiv"),
        # 3 + 4 share a phone (different formatting)
        C("Beispiel Immobilienverwaltung", phone="030 1234567", postcode="10178"),
        C("Beispiel Immobilien Verwaltung KG", phone="+49 30 1234567", postcode="10179"),
        # 5, 6 unrelated
        C("Spree Verwaltung UG (haftungsbeschränkt)", website="https://spree-verwaltung.de",
          postcode="10243"),
        C("Hausverwaltung Nord", phone="030 7654321", postcode="13347"),
    ]


def test_six_records_dedupe_to_four():
    companies = dedupe(six_records())
    assert len(companies) == 4
    muster = next(c for c in companies if c.id == "muster-hv.de")
    assert muster.discovery_sources == ["places:Hausverwaltung", "directory:vdiv"]
    beispiel = next(c for c in companies if c.name.startswith("Beispiel"))
    assert beispiel.phone == "030 1234567"


def test_name_and_postcode_key_merges_without_domain_or_phone():
    recs = [C("Hausverwaltung Müller GmbH", postcode="10405"),
            C("Hausverwaltung Mueller", postcode="10405", source="directory:x")]
    assert len(dedupe(recs)) == 1
    # same name, different postcode → separate
    recs.append(C("Hausverwaltung Müller", postcode="12043"))
    assert len(dedupe(recs)) == 2


def test_shared_hosts_are_not_dedupe_keys():
    recs = [C("A Verwaltung", website="https://www.facebook.com/a", postcode="10115"),
            C("B Verwaltung", website="https://facebook.com/b", postcode="10115")]
    assert len(dedupe(recs)) == 2


def test_transitive_merge():
    recs = [C("X", website="https://x.de", postcode="10115"),
            C("X Verwaltung", website="https://x.de", phone="030 111 2222", postcode="10115"),
            C("Totally Different Name", phone="0301112222", postcode="10999")]
    assert len(dedupe(recs)) == 1


def test_normalizers():
    assert normalize_domain("https://WWW.Firma.de/impressum") == "firma.de"
    assert normalize_domain("firma.de") == "firma.de"
    assert normalize_domain("https://sites.google.com/view/x") is None
    assert normalize_domain(None) is None
    assert normalize_phone("030 / 12 34 56-7") == "49301234567"
    assert normalize_phone("+49 (0)30 1234567") == "49301234567"
    assert normalize_phone("0049 30 1234567") == "49301234567"
    assert normalize_phone("123") is None
    assert normalize_name("Hausverwaltung Müller GmbH & Co. KG") == "hausverwaltung mueller"
    assert normalize_name("Spree Verwaltung UG (haftungsbeschränkt)") == "spree verwaltung"
    assert company_id("firma.de", "Firma", "10115") == "firma.de"
    assert company_id(None, "Firma GmbH", "10115") == company_id(None, "firma", "10115")


def test_postcode_filter(city):
    assert postcode_ok("10115", city)
    assert postcode_ok("14199", city)
    assert postcode_ok("12043", city)
    assert not postcode_ok("14467", city)  # Potsdam
    assert not postcode_ok("15711", city)  # Königs Wusterhausen
    assert not postcode_ok("80331", city)  # Munich
    assert not postcode_ok("1011", city)
    assert postcode_ok(None, city)  # unknown postcode is kept


def test_apply_filters_drops_closed_and_non_berlin(city):
    recs = [
        C("Open", postcode="10115", locality="Berlin"),
        C("Closed", postcode="10115", business_status="CLOSED_PERMANENTLY"),
        C("Potsdam HV", postcode="14467", locality="Potsdam"),
        C("Schönefeld HV", postcode="12529", locality="Schönefeld"),
    ]
    kept, excluded = apply_filters(recs, city)
    assert [k.name for k in kept] == ["Open"]
    reasons = {e.name: e.reason for e in excluded}
    assert reasons["Closed"] == "closed_permanently"
    assert reasons["Potsdam HV"].startswith("outside_city_postcode")
    assert reasons["Schönefeld HV"].startswith("outside_city_locality")


def test_name_similarity():
    from leadgraph.dedupe import name_similarity
    assert name_similarity("Hausverwaltung Müller GmbH", "Müller Hausverwaltung") == 1.0
    assert name_similarity("Hausverwaltung Müller", "Hausverwaltung Schmidt") == 0.0
    assert name_similarity("Grieger Immobilien Management", "Grieger Hausverwaltung GmbH") == 1.0
    assert name_similarity("Hausverwaltung", "Hausverwaltung GmbH") == 1.0
    assert name_similarity("Hausverwaltung", "Immobilienverwaltung") == 0.0
    assert 0 < name_similarity("Alpha Beta Verwaltung", "Alpha Gamma Verwaltung") < 0.5
    # live recall probe: one shared word is not enough against a longer distinctive name
    assert name_similarity("Norddeutsche Grundstücksverwaltung Berlin",
                           "NKI Norddeutsche Kapital Immobilien GmbH") < 0.5
    assert name_similarity("VIC GmbH", "vic GmbH Haus- & Grundbesitzverwaltung") == 1.0
