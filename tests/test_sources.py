from leadgraph.graph.nodes.sources import (
    ListEntry,
    chunk_text,
    clean_website,
    entry_to_candidate,
    parse_selection,
)


def test_parse_selection():
    assert parse_selection("all", 3) == [0, 1, 2]
    assert parse_selection("", 3) == [] and parse_selection("none", 3) == []
    assert parse_selection("1,3", 3) == [0, 2]
    assert parse_selection("2-3", 5) == [1, 2]
    assert parse_selection("1, 9", 3) == [0]  # out of range ignored


def test_chunk_text_respects_size_and_max():
    text = "\n".join(f"line {i}" for i in range(1000))
    chunks = chunk_text(text, 500, 3)
    assert len(chunks) == 3 and all(len(c) <= 500 for c in chunks)
    assert "".join(chunk_text(text, 10_000, 5)) == text


def test_clean_website_rejects_invented_domains():
    chunk = "Muster HV <https://muster-hv.de/> Str. 1"
    assert clean_website("https://muster-hv.de/", chunk, "verzeichnis.de") == "https://muster-hv.de/"
    assert clean_website("www.muster-hv.de", chunk, "verzeichnis.de") == "https://www.muster-hv.de"
    assert clean_website("https://invented.de", chunk, "verzeichnis.de") is None
    assert clean_website("https://verzeichnis.de/firma/1", chunk + " verzeichnis.de", "verzeichnis.de") is None
    assert clean_website(None, chunk, "x.de") is None


def test_entry_to_candidate_postcode_from_address():
    e = ListEntry(name=" Kiez HV ", website=None, address="Kiezstr. 1, 10437 Berlin", postcode=None)
    c = entry_to_candidate(e, "", "list:x.de", "x.de", 1)
    assert c.name == "Kiez HV" and c.postcode == "10437" and c.source == "list:x.de"
    bad = ListEntry(name="A", website=None, address=None, postcode="1043")
    assert entry_to_candidate(bad, "", "list:x.de", "x.de", 1).postcode is None
