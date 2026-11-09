"""Wires config, clients and the graph together for one run (used by the CLI).

Each run lives in outputs/<run_id>/: run.json (options, never keys), checkpoints.sqlite
(LangGraph state, thread_id = run_id), usage.json (paid-call counters, so the budget
holds across resumes), tiles.jsonl, leads.xlsx and report.json.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from leadgraph.cache import Cache
from leadgraph.config import (
    OUTPUTS_DIR,
    load_city,
    load_models,
    load_segment,
    load_settings,
)
from leadgraph.graph.build import build_graph
from leadgraph.graph.state import Deps, Mode, RunContext
from leadgraph.llm import LLM, get_model
from leadgraph.sources.places import PlacesClient
from leadgraph.sources.search import SerperClient
from leadgraph.usage import Usage
from leadgraph.web.fetch import Fetcher

SECRET_FIELDS = ("llm_key", "places_key", "search_key")
Approver = Callable[[dict[str, Any]], dict[str, Any]]


class MissingKey(RuntimeError):
    pass


class NeedsApproval(RuntimeError):
    pass


@dataclass
class RunOptions:
    city: str
    command: str = "run"
    segment: str = "hausverwaltung"
    area: str | None = None
    limit: int | None = None
    provider: str = "anthropic"
    model: str | None = None
    llm_key: str | None = field(default=None, repr=False)  # keys never appear in reprs/logs
    places_key: str | None = field(default=None, repr=False)
    search_key: str | None = field(default=None, repr=False)
    max_places_requests: int | None = None
    max_llm_cost: float | None = None
    max_search_requests: int | None = None
    places_tier: str | None = None
    max_rounds: int | None = None
    use_scout: bool = False
    approve: str | None = None
    extra_sources: list[str] = field(default_factory=list)
    source_run: str | None = None  # enrich: the discovery run to start from
    run_id: str | None = None
    no_cache: bool = False
    outputs_dir: Path = OUTPUTS_DIR

    def public(self) -> dict[str, Any]:
        """Options safe to write to disk (no keys)."""
        d = asdict(self)
        for k in SECRET_FIELDS:
            d.pop(k, None)
        d["outputs_dir"] = str(self.outputs_dir)
        return d

    @classmethod
    def from_public(cls, d: dict[str, Any], **overrides: Any) -> RunOptions:
        d = {**d, "outputs_dir": Path(d["outputs_dir"]), **overrides}
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def resolve_key(explicit: str | None, env_var: str, what: str) -> str:
    key = explicit or os.environ.get(env_var)
    if not key:
        raise MissingKey(f"{what} key missing: set the {env_var} environment variable "
                         f"or pass it on the command line.")
    return key


def make_run_id(city: str, area: str | None, command: str = "run") -> str:
    prefix = {"discover": "disc", "enrich": "enr"}.get(command, "run")
    return f"{prefix}-{city}-{area or 'all'}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"


def run_dir(opts: RunOptions) -> Path:
    assert opts.run_id
    return opts.outputs_dir / opts.run_id


def load_run_options(run_id: str, outputs_dir: Path = OUTPUTS_DIR, **overrides: Any) -> RunOptions:
    path = outputs_dir / run_id / "run.json"
    if not path.exists():
        raise FileNotFoundError(f"No run.json for run '{run_id}' in {outputs_dir}")
    return RunOptions.from_public(json.loads(path.read_text(encoding="utf-8")), **overrides)


@asynccontextmanager
async def run_context(opts: RunOptions, *, resumed: bool = False,
                      patch_ctx: Callable[[RunContext], None] | None = None
                      ) -> AsyncIterator[RunContext]:
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
    places_key = (opts.places_key or os.environ.get(models.places.env_key) or ""
                  if opts.command == "enrich"  # enrich makes no Places calls
                  else resolve_key(opts.places_key, models.places.env_key, "Google Places"))
    search_key = opts.search_key or os.environ.get(models.search.env_key)
    if opts.use_scout and not search_key:
        raise MissingKey(f"Search key missing for the scout: set {models.search.env_key} "
                         f"or pass --no-scout.")

    b = settings.budget
    if opts.max_places_requests is not None:
        b.max_places_requests = opts.max_places_requests
    if opts.max_llm_cost is not None:
        b.max_llm_cost_usd = opts.max_llm_cost
    if opts.max_search_requests is not None:
        b.max_search_requests = opts.max_search_requests
    if opts.places_tier:
        settings.places.tier = opts.places_tier  # type: ignore[assignment]

    out = run_dir(opts)
    out.mkdir(parents=True, exist_ok=True)
    (out / "run.json").write_text(json.dumps(opts.public(), indent=2), encoding="utf-8")

    cache = Cache(opts.outputs_dir / ".cache", enabled=not opts.no_cache)
    usage = Usage(max_places_requests=b.max_places_requests, max_llm_cost_usd=b.max_llm_cost_usd,
                  max_search_requests=b.max_search_requests,
                  places_sku_usd_per_1000=models.places.sku_usd_per_1000)
    usage_path = out / "usage.json"
    if resumed and usage_path.exists():
        usage.load_counters(json.loads(usage_path.read_text(encoding="utf-8")))

    temperature = settings.llm.temperature if provider_cfg.send_temperature else None
    chat = get_model(opts.provider, model, llm_key,
                     temperature=temperature, max_retries=settings.llm.max_retries)
    llm = LLM(chat, provider=opts.provider, model=model, cache=cache, usage=usage,
              models_cfg=models, concurrency=settings.llm.concurrency)
    mode = Mode(
        command=opts.command,
        queries=[] if opts.command == "discover" else segment.queries[:1],
        quadtree=opts.command == "discover",
        use_scout=opts.use_scout,
        max_rounds=(opts.max_rounds or settings.discovery.max_rounds)
        if opts.command == "discover" else 1,
        approve=opts.approve,
        extra_sources=list(opts.extra_sources),
        research=opts.command in ("run", "enrich"),
    )

    async with httpx.AsyncClient() as api_http:
        fetcher = Fetcher(settings.fetch, cache, usage)
        try:
            ctx = RunContext(
                run_id=opts.run_id or "", city=city, segment=segment, settings=settings,
                models=models, provider=opts.provider, model=model, mode=mode,
                area_name=opts.area, limit=opts.limit, outputs_dir=opts.outputs_dir,
                deps=Deps(
                    cache=cache, usage=usage, fetcher=fetcher, llm=llm,
                    places=PlacesClient(places_key, api_http, cache, usage, settings.places),
                    search=SerperClient(search_key, api_http, cache, usage) if search_key else None,
                ),
                run_meta={
                    "command": opts.command,
                    "resumed": resumed,
                    "places_tier": settings.places.tier,
                    "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
                    "started_monotonic": time.monotonic(),
                },
                usage_path=usage_path,
            )
            if patch_ctx is not None:  # tests swap in fakes here
                patch_ctx(ctx)
            yield ctx
        finally:
            usage_path.write_text(json.dumps(usage.to_counters(), indent=2), encoding="utf-8")
            await fetcher.aclose()
            cache.close()


async def execute(opts: RunOptions, *, resume: bool = False, approver: Approver | None = None,
                  patch_ctx: Callable[[RunContext], None] | None = None,
                  events: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    """Run (or resume) a graph thread until it finishes, asking `approver` at interrupts."""
    if not opts.run_id:
        opts.run_id = make_run_id(opts.city, opts.area, opts.command)
    async with run_context(opts, resumed=resume, patch_ctx=patch_ctx) as ctx:
        ctx.events = events
        db = run_dir(opts) / "checkpoints.sqlite"
        async with AsyncSqliteSaver.from_conn_string(str(db)) as saver:
            graph = build_graph(saver)
            config = {"configurable": {"thread_id": opts.run_id}, "recursion_limit": 500,
                      "max_concurrency": ctx.settings.agents.research_concurrency}
            inp: Any = {"run_id": opts.run_id}
            if opts.command == "enrich" and not resume:
                inp = {**await load_final_state(opts.source_run or "", opts.outputs_dir),
                       "run_id": opts.run_id}
            if resume:
                snap = await graph.aget_state(config)
                if not snap.next and not snap.interrupts:
                    return {**snap.values, "_already_finished": True}
                inp = None
            while True:
                if inp is None:
                    snap = await graph.aget_state(config)
                    if snap.interrupts:
                        inp = _resume_command(snap.interrupts[0].value, approver)
                await graph.ainvoke(inp, config, context=ctx)
                snap = await graph.aget_state(config)
                if snap.interrupts:
                    inp = _resume_command(snap.interrupts[0].value, approver)
                    continue
                return dict(snap.values)


CARRY_OVER = ("companies", "filtered", "rejected", "rounds", "saturation", "coverage",
              "query_stats", "queries_tried", "source_candidates", "approved_sources",
              "source_stats", "scout_trace", "counts", "stopped_reason", "tiles")


async def load_final_state(run_id: str, outputs_dir: Path = OUTPUTS_DIR) -> dict[str, Any]:
    """Final graph state of a finished run (from its checkpoint), for `enrich`."""
    db = outputs_dir / run_id / "checkpoints.sqlite"
    if not db.exists():
        raise FileNotFoundError(f"No checkpoints for run '{run_id}' in {outputs_dir}")
    async with AsyncSqliteSaver.from_conn_string(str(db)) as saver:
        snap = await build_graph(saver).aget_state({"configurable": {"thread_id": run_id}})
    if snap.next or snap.interrupts:
        raise RuntimeError(f"Run '{run_id}' has not finished; resume it first.")
    values = dict(snap.values)
    out = {k: values[k] for k in CARRY_OVER if k in values}
    out["counts"] = {**(out.get("counts") or {}), "source_run": run_id}
    return out


def _resume_command(value: dict[str, Any], approver: Approver | None) -> Command:
    if approver is None:
        raise NeedsApproval(value)
    return Command(resume=approver(value))
