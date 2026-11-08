import pytest

from leadgraph.coverage import (
    chapman,
    coverage_stats,
    overlap_matrix,
    round_yield,
    saturation_steps,
    stop_decision,
)


def test_chapman_plan_values():
    est = chapman(900, 300, 200)
    assert est == pytest.approx(1348.26, abs=0.01)
    assert 1000 / est == pytest.approx(0.742, abs=0.001)


def test_coverage_stats_from_sources():
    # 700 places-only, 100 list-only, 200 both → n1=900, n2=300, m=200, union=1000
    comps = ([["places:Hausverwaltung"]] * 700 + [["list:vdiv.de"]] * 100
             + [["places:WEG-Verwaltung", "list:vdiv.de"]] * 200)
    s = coverage_stats(comps)
    assert (s["n1_places"], s["n2_lists"], s["m_both"], s["union"]) == (900, 300, 200, 1000)
    assert s["chapman_estimate"] == pytest.approx(1348.26, abs=0.01)
    assert s["coverage"] == pytest.approx(0.742, abs=0.001)
    assert s["overlap_matrix"]["places"]["list:vdiv.de"] == 200
    assert s["overlap_matrix"]["places"]["places"] == 900


def test_chapman_needs_both_groups():
    s = coverage_stats([["places:a"], ["places:b"]])
    assert s["chapman_estimate"] is None and s["coverage"] is None


def test_overlap_matrix_groups_places_queries():
    m = overlap_matrix([["places:a", "places:b", "list:x.de"], ["list:x.de", "list:y.de"]])
    assert list(m) == ["places", "list:x.de", "list:y.de"]
    assert m["places"]["places"] == 1 and m["list:x.de"]["list:x.de"] == 2
    assert m["list:x.de"]["list:y.de"] == 1 and m["places"]["list:y.de"] == 0


def test_round_yield_and_stop_rule():
    assert round_yield(100, 0) is None
    assert round_yield(10, 1000) == pytest.approx(0.01)
    r = [{"yield": None}, {"yield": 0.05}]
    assert stop_decision(r, threshold=0.02, max_rounds=5) is None
    r.append({"yield": 0.01})
    assert stop_decision(r, threshold=0.02, max_rounds=5) is None  # only one round below
    r.append({"yield": 0.015})
    assert "saturated" in stop_decision(r, threshold=0.02, max_rounds=5)
    assert stop_decision([{"yield": None}], threshold=0.02, max_rounds=1).startswith("max_rounds")
    assert stop_decision([{"yield": None}], threshold=0.02, max_rounds=5,
                         budget_hit=True) == "budget_reached"
    # a None yield is never "below threshold"
    assert stop_decision([{"yield": None}, {"yield": 0.0}], threshold=0.02, max_rounds=5) is None


def test_saturation_curve(city):
    from leadgraph.schema import Candidate

    def c(name, src, r=1):
        return Candidate(name=name, source=src, round=r, postcode="10115")

    cands = [c("A", "places:q1"), c("B", "places:q1"), c("B", "places:q2"), c("C", "places:q2"),
             c("A", "list:x.de"), c("D", "places:q3", r=2)]
    rows = saturation_steps(cands, city)
    assert [r["step"] for r in rows] == ["places:q1", "places:q2", "list:x.de", "places:q3"]
    assert [r["cumulative"] for r in rows] == [2, 3, 3, 4]
    assert [r["new"] for r in rows] == [2, 1, 0, 1]
