"""export node (code): writes leads.xlsx and report.json."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from langgraph.runtime import Runtime

from leadgraph.export import leads_and_excluded, write_report, write_xlsx
from leadgraph.graph.nodes.common import timed
from leadgraph.graph.state import RunContext, RunState


def build_report(state: RunState, ctx: RunContext) -> dict[str, Any]:
    usage = ctx.deps.usage.to_dict()
    leads, excluded = leads_and_excluded(state.get("companies", []), state.get("excluded", []))
    started = ctx.run_meta.get("started_monotonic")
    return {
        "run_id": ctx.run_id,
        **{k: v for k, v in ctx.run_meta.items() if k != "started_monotonic"},
        "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "duration_s": round(time.monotonic() - started, 2) if started else None,
        "config": {
            "city": ctx.city.name, "area": ctx.area_name, "segment": ctx.segment.name,
            "provider": ctx.provider, "model": ctx.model, "limit": ctx.limit,
        },
        "counts": {**(state.get("counts") or {}), "leads": len(leads), "excluded": len(excluded)},
        "timings_s": state.get("timings") or {},
        "cache": ctx.deps.cache.stats(),
        "usage": usage,
        "cost_estimate_usd": {
            "llm": usage["llm_cost_estimate_usd"],
            "places": usage["places_cost_estimate_usd"],
            "total": round(usage["llm_cost_estimate_usd"] + usage["places_cost_estimate_usd"], 4),
        },
        "stopped_reason": state.get("stopped_reason"),
    }


@timed("export")
async def export(state: RunState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    ctx = runtime.context
    out_dir = ctx.outputs_dir / ctx.run_id
    xlsx, report_path = out_dir / "leads.xlsx", out_dir / "report.json"
    write_xlsx(xlsx, state.get("companies", []), state.get("excluded", []))
    write_report(report_path, build_report(state, ctx))
    return {"outputs": {"xlsx": str(xlsx), "report": str(report_path)}}
