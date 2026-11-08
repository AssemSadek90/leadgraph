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
LEAD_COLUMNS = [
    "name", "address", "postcode", "website", "phone", "google_place_id", "google_rating",
    "google_review_count", "is_property_manager", "classification_category",
    "classification_reason", *KPI_COLUMNS, "data_completeness", "discovery_sources",
    "needs_review", "review_reasons", "id",
]
EVIDENCE_COLUMNS = ["name", "field", "value", "source_url", "evidence", "confidence", "id"]
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
    """Company dict with KPI values lifted to top-level columns."""
    row = dict(company)
    for f in KPI_COLUMNS:
        row[f] = ((company.get("kpis") or {}).get(f) or {}).get("value")
    return row


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
    leads = [flatten(c) for c in companies if c.get("is_property_manager") is not False]
    return leads, excluded


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
        ("Coverage estimate (Chapman, Places vs. list sources)", ["metric", "value"], kv),
        ("Source overlap (companies found by both)", ["group", *groups],
         [{"group": g, **matrix[g]} for g in groups]),
        ("Places queries", ["round", "query", "tiles", "pages", "paid_requests", "results",
                            "capped_at_min_size"], cov.get("query_stats") or []),
        ("Stop", ["stopped_reason"], [{"stopped_reason": cov.get("stopped_reason")}]),
    ]


def write_xlsx(path: Path, companies: list[dict[str, Any]], excluded: list[dict[str, Any]],
               coverage: dict[str, Any] | None = None,
               sources: list[dict[str, Any]] | None = None) -> None:
    leads, excl = leads_and_excluded(companies, excluded)
    wb = Workbook()
    wb.remove(wb.active)
    _write_sheet(wb, "leads", LEAD_COLUMNS, leads)
    _write_sheet(wb, "excluded", EXCLUDED_COLUMNS, excl)
    _write_blocks(wb, "coverage", coverage_blocks(coverage or {}))
    _write_sheet(wb, "sources", SOURCE_COLUMNS, sources or [])
    _write_sheet(wb, "evidence", EVIDENCE_COLUMNS, evidence_rows(leads))
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
