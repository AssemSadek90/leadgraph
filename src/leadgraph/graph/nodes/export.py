"""export node (code): writes leads.xlsx and report.json."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from typing import Any

from langgraph.runtime import Runtime

from leadgraph.coverage import coverage_stats
from leadgraph.export import leads_and_excluded, review_rows, write_report, write_xlsx
from leadgraph.graph.nodes.common import timed
from leadgraph.graph.state import RunContext, RunState


def pm_coverage(state: RunState) -> dict[str, Any]:
    """Chapman on classified property managers only (list sources are PM directories)."""
    pms = [c for c in state.get("companies") or [] if c.get("is_property_manager")]
    if not pms:
        return {}
    stats = coverage_stats([c["discovery_sources"] for c in pms])
    stats.pop("overlap_matrix", None)
    return stats


def excluded_rows(state: RunState) -> list[dict[str, Any]]:
    return list(state.get("filtered") or []) + list(state.get("rejected") or [])


def coverage_sheet_data(state: RunState) -> dict[str, Any]:
    return {
        "rounds": state.get("rounds") or [],
        "saturation": state.get("saturation") or [],
        "stats": state.get("coverage") or {},
        "stats_pm": pm_coverage(state),
        "query_stats": state.get("query_stats") or [],
        "stopped_reason": state.get("stopped_reason"),
    }


def sources_sheet_rows(state: RunState) -> list[dict[str, Any]]:
    """Every scouted or added source, approved or not, with what it contributed."""
    companies = state.get("companies") or []
    stats = state.get("source_stats") or {}
    approved = {s["url"]: s for s in state.get("approved_sources") or []}
    rows: list[dict[str, Any]] = []
    urls = list(approved) + [c["url"] for c in state.get("source_candidates") or []
                             if c["url"] not in approved]
    cands = {c["url"]: c for c in state.get("source_candidates") or []}
    for url in urls:
        meta = approved.get(url) or {**cands.get(url, {}), "approved_by": "not approved"}
        st = stats.get(url, {})
        src = st.get("source")
        found = [c for c in companies if src and src in c.get("discovery_sources", [])]
        only = [c for c in found if all(s == src for s in c.get("discovery_sources", []))]
        rows.append({
            **{k: meta.get(k) for k in ("url", "source_type", "estimated_count", "approved_by",
                                        "notes")},
            **{k: st.get(k) for k in ("source", "pages", "entries", "looked_up",
                                      "matched_on_places", "errors")},
            "companies_found": len(found) if src else None,
            "only_found_here": len(only) if src else None,
        })
    return rows


def build_report(state: RunState, ctx: RunContext) -> dict[str, Any]:
    usage = ctx.deps.usage.to_dict()
    leads, excluded = leads_and_excluded(state.get("companies", []), excluded_rows(state))
    started = ctx.run_meta.get("started_monotonic")
    return {
        "run_id": ctx.run_id,
        **{k: v for k, v in ctx.run_meta.items() if k != "started_monotonic"},
        "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "duration_s": round(time.monotonic() - started, 2) if started else None,
        "config": {
            "city": ctx.city.name, "city_label": ctx.city.label,
            "city_resolved_from": ctx.city.resolved_from,
            "area": ctx.area_name, "segment": ctx.segment.name,
            "provider": ctx.provider, "model": ctx.model, "limit": ctx.limit,
        },
        "counts": {**(state.get("counts") or {}), "leads": len(leads), "excluded": len(excluded),
                   "review": len(review_rows(state.get("companies", [])))},
        "timings_s": state.get("timings") or {},
        "cache": ctx.deps.cache.stats(),
        "usage": usage,
        "cost_estimate_usd": {
            "llm": usage["llm_cost_estimate_usd"],
            "places": usage["places_cost_estimate_usd"],
            "total": round(usage["llm_cost_estimate_usd"] + usage["places_cost_estimate_usd"], 4),
        },
        "stopped_reason": state.get("stopped_reason"),
        "rounds": state.get("rounds") or [],
        "queries_tried": state.get("queries_tried") or [],
        "query_stats": state.get("query_stats") or [],
        "tiles": {
            "count": len(state.get("tiles") or []),
            "capped_at_min_size": sum(t["capped"] and not t["split"]
                                      for t in state.get("tiles") or []),
            "max_depth": max((t["depth"] for t in state.get("tiles") or []), default=None),
        },
        "coverage_property_managers": pm_coverage(state),
        "research": state.get("research_stats") or {},
        "contacts": state.get("contact_stats") or {},
        "scoring": state.get("score_stats") or {},
        "validation": state.get("validation_stats") or {},
        "coverage": state.get("coverage") or {},
        "saturation": state.get("saturation") or [],
        "sources": {
            "scouted": state.get("source_candidates") or [],
            "approved": state.get("approved_sources") or [],
            "per_source": state.get("source_stats") or {},
            "scout_trace": state.get("scout_trace") or {},
        },
    }


@timed("export")
async def export(state: RunState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    ctx = runtime.context
    out_dir = ctx.outputs_dir / ctx.run_id
    xlsx, report_path = out_dir / "leads.xlsx", out_dir / "report.json"
    write_xlsx(xlsx, state.get("companies", []), excluded_rows(state),
               coverage=coverage_sheet_data(state), sources=sources_sheet_rows(state),
               kpi_definitions=ctx.segment.kpi_definitions)
    write_report(report_path, build_report(state, ctx))
    with (out_dir / "companies.jsonl").open("w", encoding="utf-8") as f:  # all discovered
        for c in state.get("companies", []):
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    if state.get("researched"):  # raw agent output + trace, for debugging and evaluation
        with (out_dir / "research.jsonl").open("w", encoding="utf-8") as f:
            for r in state["researched"]:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return {"outputs": {"xlsx": str(xlsx), "report": str(report_path),
                        "companies": str(out_dir / "companies.jsonl")}}
