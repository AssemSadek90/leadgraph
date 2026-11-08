"""Evidence verification (pure code, BUILD_PLAN P2).

Every non-null KPI must carry a source_url and a verbatim evidence snippet that appears in the
cached text of that page. Matching ignores case, whitespace, quote/dash variants and soft
hyphens, and tolerates small differences (≥ 90 % of the snippet as one contiguous match).
Numbers must also appear in the evidence (units 1200 needs "1.200" or "1200" in the quote),
and list items must be named in it. Anything that fails is set to null with a review reason.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from difflib import SequenceMatcher
from typing import Any

from leadgraph.schema import CompanyKPIs, Sourced

KPI_FIELDS = list(CompanyKPIs.model_fields)
INT_FIELDS = {"units_managed", "employee_count"}
NAMED_LIST_FIELDS = {"software_mentioned", "metering_provider_mentioned",
                     "association_membership"}
EVIDENCE_MAX = 300
FUZZY_MIN = 0.9

_TRANSLATE = str.maketrans({
    "­": "", "​": "", " ": " ", " ": " ", " ": " ",
    "‘": "'", "’": "'", "‚": "'", "“": '"', "”": '"', "„": '"',
    "«": '"', "»": '"', "–": "-", "—": "-", "−": "-", "…": "...",
})


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_TRANSLATE).lower()
    return re.sub(r"\s+", " ", text).strip()


def evidence_in_text(evidence: str, page_text: str) -> bool:
    ev, page = normalize(evidence), normalize(page_text)
    if not ev:
        return False
    if ev in page:
        return True
    if len(ev) < 20:  # short snippets must match exactly
        return False
    m = SequenceMatcher(None, page, ev, autojunk=False).find_longest_match(
        0, len(page), 0, len(ev))
    return m.size >= FUZZY_MIN * len(ev)


def numbers_in(text: str) -> set[int]:
    """'über 1.200 Einheiten, ca. 2 500 Whg., 3,000+' → {1200, 2500, 3000}."""
    out: set[int] = set()
    for m in re.finditer(r"\d{1,3}(?:[.\s  ,']\d{3})+|\d+", text):
        digits = re.sub(r"\D", "", m.group())
        if digits:
            out.add(int(digits))
    return out


def _empty(value: Any) -> bool:
    return value is None or (isinstance(value, list) and not value)


def verify_kpis(raw: dict[str, dict[str, Any]], page_text: Callable[[str], str | None]
                ) -> tuple[CompanyKPIs, list[str]]:
    """Check each field of `raw` (field → {value, source_url, evidence, confidence}).

    `page_text(url)` returns the cached text of a fetched page, or None if it was never
    fetched. Returns the verified KPIs (contract model) and review reasons.
    """
    reasons: list[str] = []
    fields: dict[str, Sourced] = {}
    for name in KPI_FIELDS:
        item = raw.get(name) or {}
        value, url, ev = item.get("value"), item.get("source_url"), item.get("evidence")
        if _empty(value):
            fields[name] = Sourced()
            continue
        text = page_text(url) if url else None
        if not ev or text is None or not evidence_in_text(ev, text):
            reasons.append(f"evidence_not_found:{name}")
            fields[name] = Sourced()
            continue
        if name in INT_FIELDS and int(value) not in numbers_in(ev):
            reasons.append(f"value_not_in_evidence:{name}")
            fields[name] = Sourced()
            continue
        if name in NAMED_LIST_FIELDS:
            kept = [v for v in value if normalize(str(v)) in normalize(ev)]
            if not kept:
                reasons.append(f"value_not_in_evidence:{name}")
                fields[name] = Sourced()
                continue
            value = kept
        fields[name] = Sourced(value=value, source_url=url, evidence=ev[:EVIDENCE_MAX],
                               confidence=item.get("confidence"))
    return CompanyKPIs(**fields), reasons


def non_null_count(kpis: CompanyKPIs | None) -> int:
    if kpis is None:
        return 0
    return sum(not _empty(getattr(kpis, f).value) for f in KPI_FIELDS)
