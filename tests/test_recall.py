from leadgraph.recall import recall

COMPANIES = [
    {"name": "Hausverwaltung Müller GmbH", "website": "https://www.mueller-hv.de",
     "discovery_sources": ["places:Hausverwaltung"], "is_property_manager": True},
    {"name": "Kiez Immobilienverwaltung", "website": None,
     "discovery_sources": ["list:x.de"], "is_property_manager": True},
]


def test_recall_by_domain_and_name():
    probes = [
        {"name": "Müller & Partner", "website": "mueller-hv.de"},  # domain match
        {"name": "Kiez Hausverwaltung", "website": ""},  # name match on distinctive token
        {"name": "Hausverwaltung Schmidt", "website": "https://schmidt.de"},  # missing
    ]
    r = recall(probes, COMPANIES)
    assert (r["probes"], r["found"]) == (3, 2) and r["recall"] == round(2 / 3, 4)
    assert [x["found"] for x in r["rows"]] == [True, True, False]
