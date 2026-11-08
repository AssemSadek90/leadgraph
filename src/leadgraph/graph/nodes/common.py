"""Shared helpers for graph nodes."""

from __future__ import annotations

import functools
import time
from collections.abc import Awaitable, Callable
from typing import Any

from langgraph.runtime import Runtime

from leadgraph.graph.state import RunContext, RunState

NodeFn = Callable[[RunState, Runtime[RunContext]], Awaitable[dict[str, Any]]]


def timed(name: str) -> Callable[[NodeFn], NodeFn]:
    """Record the node's wall time under state['timings'][name] (accumulates)."""

    def deco(fn: NodeFn) -> NodeFn:
        @functools.wraps(fn)
        async def wrapper(state: RunState, runtime: Runtime[RunContext]) -> dict[str, Any]:
            t0 = time.perf_counter()
            out = await fn(state, runtime) or {}
            prev = (state.get("timings") or {}).get(name, 0.0)
            out["timings"] = {name: round(prev + time.perf_counter() - t0, 3)}
            return out

        return wrapper

    return deco
