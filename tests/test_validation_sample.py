from leadgraph.validation_sample import (
    accuracy,
    gold_row,
    read_gold,
    stratified_sample,
    units_correct,
    write_gold,
)


def comp(i, tier, pm=True, units=None, contact=None):
    return {"id": f"c{i}", "name": f"C{i}", "tier": tier, "is_property_manager": pm,
            "kpis": {"units_managed": {"value": units, "source_url": "u", "evidence": "e"}},
            "contacts": [contact] if contact else []}


def test_stratified_sample_is_seeded_and_balanced():
    comps = ([comp(i, "A") for i in range(15)] + [comp(i, "B") for i in range(15, 18)]
             + [comp(i, "C") for i in range(18, 40)] + [comp(i, None, pm=False) for i in range(40, 45)])
    s1 = stratified_sample(comps, per_tier=10, rejected=2, seed=1)
    s2 = stratified_sample(comps, per_tier=10, rejected=2, seed=1)
    assert [c["id"] for c in s1] == [c["id"] for c in s2]
    tiers = [c["tier"] for c in s1]
    assert tiers.count("A") == 10 and tiers.count("B") == 3 and tiers.count("C") == 10
    assert sum(c["is_property_manager"] is False for c in s1) == 2


def test_units_tolerance():
    assert units_correct(1200, "1300") is True  # within 15 %
    assert units_correct(1200, "1500") is False
    assert units_correct(None, "n/a") is True and units_correct(500, "n/a") is False
    assert units_correct(None, "") is None
    assert units_correct("2.000", "2000") is True


def test_gold_round_trip_and_accuracy(tmp_path):
    rows = [gold_row(comp(1, "A", units=1200, contact={"name": "Max", "role": "GF",
                                                         "email": "m@x.de", "source_url": "u"})),
            gold_row(comp(2, "B", units=None)), gold_row(comp(3, "C", units=800))]
    rows[0].update(true_is_property_manager="y", true_units_managed="1250", true_contact_ok="y")
    rows[1].update(true_is_property_manager="y", true_units_managed="n/a")
    rows[2].update(true_is_property_manager="n", true_units_managed="2000")
    path = tmp_path / "gold.csv"
    write_gold(path, rows)
    acc = accuracy(read_gold(path))
    assert acc["classification"] == {"n": 3, "correct": 2, "accuracy": 0.667}
    assert acc["units_managed"]["correct"] == 2 and acc["units_managed"]["n"] == 3
    assert acc["contact"] == {"n": 1, "correct": 1, "accuracy": 1.0}
    assert acc["classification_by_tier"]["C"]["accuracy"] == 0.0


def test_read_gold_semicolon(tmp_path):
    p = tmp_path / "g.csv"
    p.write_text("id;name;tier;pred_is_property_manager;true_is_property_manager\n"
                 "c1;C1;A;y;y\n", encoding="utf-8-sig")
    rows = read_gold(p)
    assert rows[0]["true_is_property_manager"] == "y"
    assert accuracy(rows)["classification"]["accuracy"] == 1.0
