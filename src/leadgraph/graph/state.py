"""Graph state (checkpointed) and run context (not checkpointed).

State holds only plain, JSON-friendly data. Clients, caches and anything holding an API key
live in RunContext, which is passed per invocation and never written to a checkpoint.
"""

from __future__ import annotations

import json
import operator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, TypedDict

from leadgraph.cache import Cache
from leadgraph.config import OUTPUTS_DIR, CityConfig, ModelsConfig, Rect, SegmentConfig, Settings
from leadgraph.usage import Usage


def merge_dicts(a: dict | None, b: dict | None) -> dict:
    return {**(a or {}), **(b or {})}


def keep_first(a: str | None, b: str | None) -> str | None:
    """First stop reason wins; parallel branches may both report one."""
    return a or b


class RunState(TypedDict, total=False):
    run_id: str
    round: int
    work_items: list[dict[str, Any]]
    queries_tried: list[str]
    next_queries: list[str]
    candidates: Annotated[list[dict[str, Any]], operator.add]
    tiles: Annotated[list[dict[str, Any]], operator.add]
    query_stats: Annotated[list[dict[str, Any]], operator.add]
    source_candidates: list[dict[str, Any]]
    approved_sources: list[dict[str, Any]]
    source_stats: Annotated[dict[str, dict[str, Any]], merge_dicts]
    scout_trace: dict[str, Any]
    companies: list[dict[str, Any]]
    filtered: list[dict[str, Any]]  # dropped before dedupe (closed, outside city)
    rejected: list[dict[str, Any]]  # classified as not a property manager
    rounds: list[dict[str, Any]]
    saturation: list[dict[str, Any]]
    coverage: dict[str, Any]
    counts: Annotated[dict[str, Any], merge_dicts]
    timings: Annotated[dict[str, float], merge_dicts]
    stopped_reason: Annotated[str | None, keep_first]
    outputs: dict[str, str]


@dataclass
class Deps:
    cache: Cache
    usage: Usage
    places: Any  # PlacesClient (or a fake in tests)
    fetcher: Any  # Fetcher
    llm: Any  # LLM
    search: Any = None  # SerperClient; None disables the scout's web_search tool


@dataclass
class Mode:
    """What a command runs. `run` = M1 thin slice; `discover` = full discovery loop."""

    command: str = "run"
    queries: list[str] = field(default_factory=list)  # round-1 queries (empty → segment's)
    quadtree: bool = False
    use_scout: bool = False
    max_rounds: int = 1
    approve: str | None = None  # None → interactive interrupt; "all" | "none" | "1,3"
    extra_sources: list[str] = field(default_factory=list)


@dataclass
class RunContext:
    run_id: str
    city: CityConfig
    segment: SegmentConfig
    settings: Settings
    models: ModelsConfig
    provider: str
    model: str
    deps: Deps
    mode: Mode = field(default_factory=Mode)
    area_name: str | None = None
    limit: int | None = None
    outputs_dir: Path = OUTPUTS_DIR
    run_meta: dict[str, Any] = field(default_factory=dict)
    usage_path: Path | None = None

    def save_usage(self) -> None:
        if self.usage_path is not None:
            self.usage_path.write_text(json.dumps(self.deps.usage.to_counters(), indent=2),
                                       encoding="utf-8")

    @property
    def area(self) -> Rect:
        return self.city.area(self.area_name)
