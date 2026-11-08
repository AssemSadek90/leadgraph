"""Graph state (checkpointed) and run context (not checkpointed).

State holds only plain, JSON-friendly data. Clients, caches and anything holding an API key
live in RunContext, which is passed per invocation and never written to a checkpoint.
"""

from __future__ import annotations

import operator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, TypedDict

from leadgraph.cache import Cache
from leadgraph.config import OUTPUTS_DIR, CityConfig, ModelsConfig, Rect, SegmentConfig, Settings
from leadgraph.usage import Usage


def merge_dicts(a: dict | None, b: dict | None) -> dict:
    return {**(a or {}), **(b or {})}


class RunState(TypedDict, total=False):
    run_id: str
    round: int
    work_items: list[dict[str, Any]]
    candidates: Annotated[list[dict[str, Any]], operator.add]
    companies: list[dict[str, Any]]
    excluded: Annotated[list[dict[str, Any]], operator.add]
    counts: Annotated[dict[str, Any], merge_dicts]
    timings: Annotated[dict[str, float], merge_dicts]
    stopped_reason: str | None
    outputs: dict[str, str]


@dataclass
class Deps:
    cache: Cache
    usage: Usage
    places: Any  # PlacesClient (or a fake in tests)
    fetcher: Any  # Fetcher
    llm: Any  # LLM


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
    area_name: str | None = None
    limit: int | None = None
    outputs_dir: Path = OUTPUTS_DIR
    run_meta: dict[str, Any] = field(default_factory=dict)

    @property
    def area(self) -> Rect:
        return self.city.area(self.area_name)
