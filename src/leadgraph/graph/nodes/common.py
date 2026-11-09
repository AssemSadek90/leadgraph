"""Shared helpers for graph nodes."""

from __future__ import annotations

import functools
import sys
import time
from collections.abc import Awaitable, Callable
from typing import Any

from langgraph.runtime import Runtime

from leadgraph.graph.state import RunContext, RunState

NodeFn = Callable[[RunState, Runtime[RunContext]], Awaitable[dict[str, Any]]]


def progress(msg: str) -> None:
    """Progress line on stderr (keeps stdout for the summary table)."""
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def _counts(out: dict[str, Any]) -> str:
    c = out.get("counts") or {}
    keys = ("unique_companies", "final_unique_companies", "property_managers", "places_results")
    return " ".join(f"{k}={c[k]}" for k in keys if k in c)


def timed(name: str) -> Callable[[NodeFn], NodeFn]:
    """Record the node's wall time under state['timings'][name] (accumulates)."""

    def deco(fn: NodeFn) -> NodeFn:
        @functools.wraps(fn)
        async def wrapper(state: RunState, runtime: Runtime[RunContext]) -> dict[str, Any]:
            t0 = time.perf_counter()
            ctx = runtime.context
            progress(f"> {name}" + (f" (round {state.get('round')})" if state.get("round") else ""))
            ctx.emit({"type": "node_start", "node": name, "round": state.get("round")})
            out = await fn(state, runtime) or {}
            prev = (state.get("timings") or {}).get(name, 0.0)
            dt = time.perf_counter() - t0
            out["timings"] = {name: round(prev + dt, 3)}
            ctx.save_usage()  # budget counters survive a kill
            u = ctx.deps.usage
            progress(f"ok {name} {dt:.0f}s | places {u.places_requests} | "
                     f"LLM ${u.llm_cost_usd:.3f} | {_counts(out)}")
            ctx.emit({
                "type": "node_end", "node": name, "round": state.get("round"),
                "seconds": round(dt, 2), "counts": out.get("counts") or {},
                "round_result": (out.get("rounds") or [None])[-1],
                "usage": {"places_requests": u.places_requests, "llm_calls": u.llm_calls,
                          "llm_cost_usd": round(u.llm_cost_usd, 5),
                          "search_requests": u.search_requests},
            })
            return out

        return wrapper

    return deco
