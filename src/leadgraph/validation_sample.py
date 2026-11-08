"""M5 validation (pure code): stratified sample → hand labels in eval/gold.csv → accuracy.

Truth columns to fill by hand:
  true_is_property_manager  y / n
  true_units_managed        a number, or n/a if the website does not state it
  true_contact_ok           y / n (leave blank when no contact was predicted)
  true_contact_name         optional: the right decision maker's name if the prediction is wrong
"""

from __future__ import annotations

import csv
import random
from pathlib import Path
from typing import Any

GOLD_COLUMNS = [
    "id", "name", "website", "tier", "fit_score", "pred_is_property_manager", "pred_category",
    "pred_units_managed", "units_source_url", "units_evidence", "pred_contact_name",
    "pred_contact_role", "pred_contact_email", "contact_source_url",
    "true_is_property_manager", "true_units_managed", "true_contact_ok", "true_contact_name",
    "notes",
]
UNITS_TOLERANCE = 0.15


def stratified_sample(companies: list[dict[str, Any]], per_tier: int = 10, rejected: int = 0,
                      seed: int = 42) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    out: list[dict[str, Any]] = []
    for tier in ("A", "B", "C"):
        pool = [c for c in companies if c.get("is_property_manager") and c.get("tier") == tier]
        out += rng.sample(pool, min(per_tier, len(pool)))
    if rejected:
        pool = [c for c in companies if c.get("is_property_manager") is False]
        out += rng.sample(pool, min(rejected, len(pool)))
    return out


def gold_row(c: dict[str, Any]) -> dict[str, Any]:
    units = ((c.get("kpis") or {}).get("units_managed") or {})
    contact = (c.get("contacts") or [{}])[0]
    return {
        "id": c["id"], "name": c["name"], "website": c.get("website"), "tier": c.get("tier"),
        "fit_score": c.get("fit_score"),
        "pred_is_property_manager": {True: "y", False: "n"}.get(c.get("is_property_manager"), ""),
        "pred_category": c.get("classification_category"),
        "pred_units_managed": units.get("value"), "units_source_url": units.get("source_url"),
        "units_evidence": units.get("evidence"),
        "pred_contact_name": contact.get("name"), "pred_contact_role": contact.get("role"),
        "pred_contact_email": contact.get("email"), "contact_source_url": contact.get("source_url"),
        "true_is_property_manager": "", "true_units_managed": "", "true_contact_ok": "",
        "true_contact_name": "", "notes": "",
    }


def write_gold(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:  # BOM: Excel opens UTF-8
        w = csv.DictWriter(f, fieldnames=GOLD_COLUMNS)
        w.writeheader()
        w.writerows(rows)


def read_gold(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        header = f.readline()
        f.seek(0)
        # German Excel saves CSV with ';' — pick the delimiter the header uses most
        delimiter = ";" if header.count(";") > header.count(",") else ","
        return list(csv.DictReader(f, delimiter=delimiter))


def _yes(v: str | None) -> bool | None:
    v = (v or "").strip().lower()
    if v in ("y", "yes", "j", "ja", "1", "true"):
        return True
    if v in ("n", "no", "nein", "0", "false"):
        return False
    return None


def _num(v: Any) -> float | None:
    try:
        return float(str(v).replace(".", "").replace(",", ".")) if str(v).strip() else None
    except ValueError:
        return None


def units_correct(pred: Any, truth: str) -> bool | None:
    t = (truth or "").strip().lower()
    if not t:
        return None
    p = _num(pred) if pred not in (None, "") else None
    if t in ("n/a", "na", "none", "-", "keine"):
        return p is None
    tv = _num(t)
    if tv is None:
        return None
    return p is not None and abs(p - tv) <= UNITS_TOLERANCE * tv


def accuracy(rows: list[dict[str, str]]) -> dict[str, Any]:
    def summary(results: list[bool]) -> dict[str, Any]:
        n = len(results)
        return {"n": n, "correct": sum(results),
                "accuracy": round(sum(results) / n, 3) if n else None}

    cls = [(_yes(r.get("pred_is_property_manager")) == t)
           for r in rows if (t := _yes(r.get("true_is_property_manager"))) is not None]
    units = [ok for r in rows if (ok := units_correct(r.get("pred_units_managed"),
                                                      r.get("true_units_managed", ""))) is not None]
    contact = [t for r in rows if r.get("pred_contact_name")
               and (t := _yes(r.get("true_contact_ok"))) is not None]
    by_tier: dict[str, Any] = {}
    for tier in ("A", "B", "C"):
        tr = [r for r in rows if r.get("tier") == tier]
        by_tier[tier] = summary([(_yes(r.get("pred_is_property_manager")) == t) for r in tr
                                 if (t := _yes(r.get("true_is_property_manager"))) is not None])
    return {"rows": len(rows), "classification": summary(cls),
            "units_managed": {**summary(units), "tolerance": UNITS_TOLERANCE},
            "contact": summary(contact), "classification_by_tier": by_tier}
