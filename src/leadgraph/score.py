"""Scoring v1 (pure code, BUILD_PLAN Section 3.3): weighted, explainable, config-driven.

Unknown values get partial credit so missing data doesn't masquerade as a bad fit;
data_completeness (share of non-null KPIs) is reported next to the score.
"""

from __future__ import annotations

from leadgraph.config import SegmentConfig
from leadgraph.evidence import KPI_FIELDS, non_null_count
from leadgraph.schema import Company


def units_bucket(units: int | None) -> str:
    if units is None:
        return "unknown"
    if units > 3000:
        return ">3000"
    if units >= 1000:
        return "1000-3000"
    if units >= 300:
        return "300-999"
    return "<300"


def component_buckets(c: Company) -> dict[str, str]:
    k = c.kpis
    units = k.units_managed.value if k else None
    residential = k.residential_focus.value if k else None
    portal = bool(k and k.tenant_portal_or_app.value)
    software = bool(k and k.software_mentioned.value)
    scope = k.portfolio_scope.value if k else None
    member = bool(k and k.association_membership.value)
    if any(x.email_type == "personal" for x in c.contacts):
        reach = "personal_email"
    elif c.contacts:
        reach = "named_person"
    else:
        reach = "none"
    return {
        "units_managed": units_bucket(units),
        "residential_focus": "unknown" if residential is None else str(residential).lower(),
        "digital_maturity": "portal_or_software" if portal or software else "none_found",
        "reachable_decision_maker": reach,
        "berlin_focus": scope or "unknown",
        "association_member": "member" if member else "unknown",
    }


def tier_for(score: float, tiers: dict[str, float]) -> str:
    if score >= tiers.get("A", 70):
        return "A"
    if score >= tiers.get("B", 45):
        return "B"
    return "C"


def score_company(c: Company, seg: SegmentConfig) -> Company:
    """Set fit_score, tier, score_breakdown (points per component) and data_completeness."""
    buckets = component_buckets(c)
    breakdown = {comp: round(seg.weights[comp] * seg.buckets[comp][bucket], 2)
                 for comp, bucket in buckets.items()}
    c.score_breakdown = breakdown
    c.fit_score = round(sum(breakdown.values()), 1)
    c.tier = tier_for(c.fit_score, seg.tiers)  # type: ignore[assignment]
    c.data_completeness = round(non_null_count(c.kpis) / len(KPI_FIELDS), 2)
    return c
