"""Export (pure code): leads.xlsx + report.json under outputs/<run_id>/."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

KPI_COLUMNS = [
    "units_managed", "management_types", "residential_focus", "portfolio_scope",
    "tenant_portal_or_app", "software_mentioned", "metering_provider_mentioned",
    "remote_reading_or_uvi_mentioned", "association_membership", "employee_count",
]
CONTACT_SUMMARY = ["top_contact_name", "top_contact_role", "top_contact_email",
                   "top_contact_email_type", "top_contact_linkedin", "contacts_count"]
LEAD_COLUMNS = [
    "name", "tier", "fit_score", "data_completeness", "website", "phone", "address", "postcode",
    *CONTACT_SUMMARY, *KPI_COLUMNS, "is_property_manager", "classification_category",
    "classification_reason", "google_rating", "google_review_count", "google_place_id",
    "discovery_sources", "needs_review", "review_reasons", "score_breakdown", "id",
]
REVIEW_COLUMNS = ["name", "tier", "fit_score", "review_reasons", "website", "top_contact_email",
                  "is_property_manager", "classification_category", "classification_reason",
                  "id"]
EVIDENCE_COLUMNS = ["name", "field", "value", "source_url", "evidence", "confidence", "id"]
CONTACT_COLUMNS = ["company", "name", "role", "email", "email_type", "linkedin_url",
                   "linkedin_verified", "source_url", "company_id"]
KPI_DEF_COLUMNS = ["column", "definition", "why", "source", "weight"]
TIER_FILLS = {"A": PatternFill("solid", fgColor="C6EFCE"),
              "B": PatternFill("solid", fgColor="FFEB9C"),
              "C": PatternFill("solid", fgColor="E7E6E6")}
TIER_ORDER = {"A": 0, "B": 1, "C": 2}
EXCLUDED_COLUMNS = ["name", "reason", "address", "postcode", "website", "google_place_id", "source"]

HEADER_FONT = Font(bold=True, color="FFFFFF")
HEADER_FILL = PatternFill("solid", fgColor="305496")
MAX_WIDTH = 60


def _cell(value: Any) -> Any:
    if isinstance(value, list):
        return "; ".join(str(v) for v in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return value


def _write_sheet(wb: Workbook, title: str, columns: list[str], rows: list[dict[str, Any]]) -> None:
    ws = wb.create_sheet(title)
    ws.append(columns)
    for row in rows:
        ws.append([_cell(row.get(c)) for c in columns])
    for i, col in enumerate(columns, start=1):
        cell = ws.cell(row=1, column=i)
        cell.font, cell.fill = HEADER_FONT, HEADER_FILL
        width = max([len(col)] + [len(str(_cell(r.get(col)) or "")) for r in rows[:500]])
        ws.column_dimensions[get_column_letter(i)].width = min(width + 2, MAX_WIDTH)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions


def flatten(company: dict[str, Any]) -> dict[str, Any]:
    """Company dict with KPI values and the top contact lifted to top-level columns."""
    row = dict(company)
    for f in KPI_COLUMNS:
        row[f] = ((company.get("kpis") or {}).get(f) or {}).get("value")
    contacts = company.get("contacts") or []
    top = contacts[0] if contacts else {}
    row.update({"top_contact_name": top.get("name"), "top_contact_role": top.get("role"),
                "top_contact_email": top.get("email"),
                "top_contact_email_type": top.get("email_type"),
                "top_contact_linkedin": top.get("linkedin_url"),
                "contacts_count": len(contacts)})
    return row


def contact_rows(companies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"company": c["name"], "company_id": c["id"], **p}
            for c in companies for p in c.get("contacts") or []]


def sort_leads(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda r: (TIER_ORDER.get(r.get("tier") or "", 3),
                                       -(r.get("fit_score") or 0), r.get("name") or ""))


def evidence_rows(companies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for c in companies:
        for f in KPI_COLUMNS:
            item = (c.get("kpis") or {}).get(f) or {}
            if item.get("value") not in (None, []):
                rows.append({"name": c["name"], "field": f, "id": c["id"], **item})
    return rows


def leads_and_excluded(companies: list[dict[str, Any]], excluded: list[dict[str, Any]]
                       ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """leads = classified property managers, best first; excluded = filtered + rejected."""
    leads = sort_leads([flatten(c) for c in companies if c.get("is_property_manager") is True])
    return leads, excluded


def review_rows(companies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Review queue: flagged leads plus companies whose classification failed."""
    rows = [flatten(c) for c in companies
            if c.get("is_property_manager") is None
            or (c.get("is_property_manager") and c.get("needs_review"))]
    return sort_leads(rows)


SOURCE_COLUMNS = [
    "url", "source", "source_type", "estimated_count", "approved_by", "pages", "entries",
    "looked_up", "matched_on_places", "companies_found", "only_found_here", "errors", "notes",
]
TITLE_FONT = Font(bold=True, size=12)

Block = tuple[str, list[str], list[dict[str, Any]]]


def _write_blocks(wb: Workbook, title: str, blocks: list[Block]) -> None:
    """Several titled tables stacked on one sheet."""
    ws = wb.create_sheet(title)
    widths: dict[int, int] = {}
    row = 1
    for block_title, columns, rows in blocks:
        ws.cell(row=row, column=1, value=block_title).font = TITLE_FONT
        row += 1
        for i, col in enumerate(columns, start=1):
            cell = ws.cell(row=row, column=i, value=col)
            cell.font, cell.fill = HEADER_FONT, HEADER_FILL
            widths[i] = max(widths.get(i, 0), len(col))
        for r in rows:
            row += 1
            for i, col in enumerate(columns, start=1):
                v = _cell(r.get(col))
                ws.cell(row=row, column=i, value=v)
                widths[i] = max(widths.get(i, 0), len(str(v or "")))
        row += 2
    for i, w in widths.items():
        ws.column_dimensions[get_column_letter(i)].width = min(w + 2, MAX_WIDTH)


def coverage_blocks(cov: dict[str, Any]) -> list[Block]:
    stats = cov.get("stats") or {}
    matrix = stats.get("overlap_matrix") or {}
    groups = list(matrix)
    kv = [{"metric": k, "value": stats.get(k)} for k in
          ("n1_places", "n2_lists", "m_both", "union", "total_unique", "chapman_estimate",
           "coverage", "caveat")]
    return [
        ("Rounds (yield = new unique / cumulative before)",
         ["round", "queries", "new_unique", "cumulative_before", "cumulative", "yield"],
         cov.get("rounds") or []),
        ("Saturation: cumulative unique companies by discovery step",
         ["round", "step", "new", "cumulative"], cov.get("saturation") or []),
        ("Coverage estimate (Chapman, Places vs. list sources), all companies",
         ["metric", "value"], kv),
        ("Coverage estimate, classified property managers only", ["metric", "value"],
         [{"metric": k, "value": (cov.get("stats_pm") or {}).get(k)} for k in
          ("n1_places", "n2_lists", "m_both", "union", "chapman_estimate", "coverage")]),
        ("Source overlap (companies found by both)", ["group", *groups],
         [{"group": g, **matrix[g]} for g in groups]),
        ("Places queries", ["round", "query", "tiles", "pages", "paid_requests", "results",
                            "capped_at_min_size"], cov.get("query_stats") or []),
        ("Stop", ["stopped_reason"], [{"stopped_reason": cov.get("stopped_reason")}]),
    ]


def _color_tiers(wb: Workbook, sheet: str, columns: list[str]) -> None:
    ws = wb[sheet]
    col = columns.index("tier") + 1
    for row in range(2, ws.max_row + 1):
        cell = ws.cell(row=row, column=col)
        if cell.value in TIER_FILLS:
            cell.fill = TIER_FILLS[cell.value]


def write_xlsx(path: Path, companies: list[dict[str, Any]], excluded: list[dict[str, Any]],
               coverage: dict[str, Any] | None = None,
               sources: list[dict[str, Any]] | None = None,
               kpi_definitions: list[dict[str, Any]] | None = None) -> None:
    leads, excl = leads_and_excluded(companies, excluded)
    wb = Workbook()
    wb.remove(wb.active)
    _write_sheet(wb, "leads", LEAD_COLUMNS, leads)
    _color_tiers(wb, "leads", LEAD_COLUMNS)
    _write_sheet(wb, "review", REVIEW_COLUMNS, review_rows(companies))
    _color_tiers(wb, "review", REVIEW_COLUMNS)
    _write_sheet(wb, "excluded", EXCLUDED_COLUMNS, excl)
    _write_blocks(wb, "coverage", coverage_blocks(coverage or {}))
    _write_sheet(wb, "sources", SOURCE_COLUMNS, sources or [])
    _write_sheet(wb, "kpi_definitions", KPI_DEF_COLUMNS, kpi_definitions or [])
    _write_sheet(wb, "evidence", EVIDENCE_COLUMNS, evidence_rows(leads))
    _write_sheet(wb, "contacts", CONTACT_COLUMNS, contact_rows(leads))
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
