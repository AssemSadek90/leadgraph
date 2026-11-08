import pytest

from leadgraph.schema import Company, CompanyKPIs, Contact, Sourced
from leadgraph.score import score_company, units_bucket


def kpis(**values):
    fields = {f: Sourced() for f in CompanyKPIs.model_fields}
    for f, v in values.items():
        fields[f] = Sourced(value=v, source_url="https://x.de/", evidence="quote")
    return CompanyKPIs(**fields)


def person(email=None, email_type=None):
    return Contact(name="Max Mustermann", role="Geschäftsführer", email=email,
                   email_type=email_type, source_url="https://x.de/impressum")


FIXTURES = {
    # A: 2,500 units · residential · tenant portal · personal email · Berlin · VDIV
    "A": (Company(id="a.de", name="A", kpis=kpis(units_managed=2500, residential_focus=True,
                                                   tenant_portal_or_app=True,
                                                   portfolio_scope="berlin",
                                                   association_membership=["VDIV"]),
                  contacts=[person("m.mustermann@a.de", "personal")]), 94.0, "A"),
    # B: everything unknown · named person, no email
    "B": (Company(id="b.de", name="B", kpis=None, contacts=[person()]), 43.5, "C"),
    # C: 600 units · residential · nothing digital · named person · national · VDIV
    "C": (Company(id="c.de", name="C", kpis=kpis(units_managed=600, residential_focus=True,
                                                   portfolio_scope="national",
                                                   association_membership=["VDIV"]),
                  contacts=[person()]), 63.5, "B"),
}


@pytest.mark.parametrize("key", ["A", "B", "C"])
def test_plan_fixtures_exact(key, segment):
    company, score, tier = FIXTURES[key]
    scored = score_company(company.model_copy(deep=True), segment)
    assert scored.fit_score == score and scored.tier == tier
    assert sum(scored.score_breakdown.values()) == pytest.approx(score)


def test_breakdown_fixture_a(segment):
    s = score_company(FIXTURES["A"][0].model_copy(deep=True), segment)
    assert s.score_breakdown == {"units_managed": 24.0, "residential_focus": 20.0,
                                 "digital_maturity": 15.0, "reachable_decision_maker": 15.0,
                                 "berlin_focus": 10.0, "association_member": 10.0}
    assert s.data_completeness == 0.5


def test_units_buckets():
    assert [units_bucket(v) for v in (None, 100, 300, 999, 1000, 3000, 3001)] == [
        "unknown", "<300", "300-999", "300-999", "1000-3000", "1000-3000", ">3000"]


def test_weights_sum_to_100(segment):
    assert sum(segment.weights.values()) == 100
