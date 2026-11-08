"""Export (pure code): leads.xlsx + report.json under outputs/<run_id>/."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

LEAD_COLUMNS = [
    "name", "address", "postcode", "website", "phone", "google_place_id", "google_rating",
    "google_review_count", "is_property_manager", "classification_category",
    "classification_reason", "discovery_sources", "needs_review", "review_reasons", "id",
]
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


def leads_and_excluded(companies: list[dict[str, Any]], excluded: list[dict[str, Any]]
                       ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    leads = [c for c in companies if c.get("is_property_manager") is not False]
    return leads, excluded


def write_xlsx(path: Path, companies: list[dict[str, Any]], excluded: list[dict[str, Any]]) -> None:
    leads, excl = leads_and_excluded(companies, excluded)
    wb = Workbook()
    wb.remove(wb.active)
    _write_sheet(wb, "leads", LEAD_COLUMNS, leads)
    _write_sheet(wb, "excluded", EXCLUDED_COLUMNS, excl)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
