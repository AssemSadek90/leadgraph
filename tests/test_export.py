"""Export edge cases seen on live data."""

from openpyxl import load_workbook

from leadgraph.export import KPI_COLUMNS, _cell, write_xlsx


def test_cell_strips_control_characters_and_caps_length():
    assert _cell("WEG Verwaltung\x0bIhrer Immobilie") == "WEG Verwaltung Ihrer Immobilie"
    assert _cell(["a\x01", "b"]) == "a ; b"
    assert len(_cell("x" * 40_000)) == 32_767
    assert _cell(3) == 3 and _cell(None) is None


def test_write_xlsx_with_scraped_control_characters(tmp_path):
    quote = "Unser Team bietet WEG Verwaltung\x0b\x0bIhrer Immobilie im Berlin"
    kpis = {f: {"value": None, "source_url": None, "evidence": None, "confidence": None}
            for f in KPI_COLUMNS}
    kpis["management_types"] = {"value": ["WEG"], "source_url": "https://hv.example/",
                                "evidence": quote, "confidence": "high"}
    company = {"id": "hv.example", "name": "HV\x0c Example", "website": "https://hv.example/",
               "is_property_manager": True, "kpis": kpis, "contacts": [], "tier": "B",
               "fit_score": 50.0, "review_reasons": [], "discovery_sources": ["places:x"]}
    path = tmp_path / "leads.xlsx"
    write_xlsx(path, [company], [])
    wb = load_workbook(path)
    names = [c.value for row in wb["leads"].iter_rows(min_row=2) for c in row]
    assert "HV  Example" in names
    evidence = [c.value for row in wb["evidence"].iter_rows() for c in row]
    assert "Unser Team bietet WEG Verwaltung  Ihrer Immobilie im Berlin" in evidence
