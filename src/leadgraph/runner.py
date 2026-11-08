"""Wires config, clients and the graph together for one run (used by the CLI)."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from leadgraph.cache import Cache
from leadgraph.config import (
    OUTPUTS_DIR,
    load_city,
    load_models,
    load_segment,
    load_settings,
)
from leadgraph.graph.build import build_graph
from leadgraph.graph.state import Deps, RunContext
from leadgraph.llm import LLM, get_model
from leadgraph.sources.places import PlacesClient
from leadgraph.usage import Usage
from leadgraph.web.fetch import Fetcher


class MissingKey(RuntimeError):
    pass


@dataclass
class RunOptions:
    city: str
    segment: str = "hausverwaltung"
    area: str | None = None
    limit: int | None = None
    provider: str = "anthropic"
    model: str | None = None
    llm_key: str | None = None
    places_key: str | None = None
    max_places_requests: int | None = None
    max_llm_cost: float | None = None
    run_id: str | None = None
    no_cache: bool = False
    outputs_dir: Path = OUTPUTS_DIR


def resolve_key(explicit: str | None, env_var: str, what: str) -> str:
    key = explicit or os.environ.get(env_var)
    if not key:
        raise MissingKey(f"{what} key missing: set the {env_var} environment variable "
                         f"or pass it on the command line.")
    return key


def make_run_id(city: str, area: str | None) -> str:
    return f"{city}-{area or 'all'}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"


async def execute(opts: RunOptions, command: str = "run") -> dict[str, Any]:
    models = load_models()
    settings = load_settings()
    city = load_city(opts.city)
    segment = load_segment(opts.segment)
    city.area(opts.area)  # validate the area name early

    provider_cfg = models.providers.get(opts.provider)
    if provider_cfg is None:
        raise ValueError(f"Unknown provider '{opts.provider}'. Known: {sorted(models.providers)}")
    model = opts.model or provider_cfg.default_model
    llm_key = resolve_key(opts.llm_key, provider_cfg.env_key, opts.provider)
    places_key = resolve_key(opts.places_key, models.places.env_key, "Google Places")

    if opts.max_places_requests is not None:
        settings.budget.max_places_requests = opts.max_places_requests
    if opts.max_llm_cost is not None:
        settings.budget.max_llm_cost_usd = opts.max_llm_cost

    run_id = opts.run_id or make_run_id(opts.city, opts.area)
    cache = Cache(opts.outputs_dir / ".cache", enabled=not opts.no_cache)
    usage = Usage(
        max_places_requests=settings.budget.max_places_requests,
        max_llm_cost_usd=settings.budget.max_llm_cost_usd,
        places_usd_per_request=models.places.usd_per_request,
    )
    temperature = settings.llm.temperature if provider_cfg.send_temperature else None
    chat = get_model(opts.provider, model, llm_key,
                     temperature=temperature, max_retries=settings.llm.max_retries)
    llm = LLM(chat, provider=opts.provider, model=model, cache=cache, usage=usage,
              models_cfg=models, concurrency=settings.llm.concurrency)

    async with httpx.AsyncClient() as places_http:
        fetcher = Fetcher(settings.fetch, cache, usage)
        try:
            places = PlacesClient(places_key, places_http, cache, usage, settings.places)
            ctx = RunContext(
                run_id=run_id, city=city, segment=segment, settings=settings, models=models,
                provider=opts.provider, model=model, area_name=opts.area, limit=opts.limit,
                outputs_dir=opts.outputs_dir,
                deps=Deps(cache=cache, usage=usage, places=places, fetcher=fetcher, llm=llm),
                run_meta={
                    "command": command,
                    "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
                    "started_monotonic": time.monotonic(),
                },
            )
            graph = build_graph()
            final = await graph.ainvoke({"run_id": run_id}, context=ctx)
        finally:
            await fetcher.aclose()
            cache.close()
    return final
