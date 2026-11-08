"""M7 evaluation harness: run classify → research → verify → contacts on the hand-labelled
gold set for several models and compare accuracy, evidence validity, cost and latency.

Every call goes through the shared disk cache (keys include provider and model), so a rerun
reproduces the same table from cache. Cost and latency of a lead are stored the first time
it runs uncached and reused on cached reruns.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any

from leadgraph import tracing
from leadgraph.cache import Cache
from leadgraph.config import (
    OUTPUTS_DIR,
    ModelsConfig,
    load_city,
    load_models,
    load_segment,
    load_settings,
)
from leadgraph.dedupe import normalize_domain
from leadgraph.evidence import KPI_FIELDS, verify_kpis
from leadgraph.graph.nodes.classify import classify_company
from leadgraph.graph.nodes.contacts import contacts_for
from leadgraph.graph.nodes.research import _page_text_lookup, research_company
from leadgraph.graph.state import Deps, Mode, RunContext
from leadgraph.llm import LLM, get_model
from leadgraph.people import fold
from leadgraph.recall import load_companies
from leadgraph.runner import resolve_key
from leadgraph.schema import Company
from leadgraph.usage import Usage
from leadgraph.validation_sample import _yes, read_gold, units_correct
from leadgraph.web.fetch import Fetcher

METRICS = [("classification", "Classification acc."), ("units_managed", "Units acc."),
           ("contact", "Contact acc."), ("evidence_valid", "Evidence-valid")]


@dataclass
class ModelSpec:
    provider: str
    model: str

    @classmethod
    def parse(cls, spec: str) -> ModelSpec:
        provider, _, model = spec.partition(":")
        if not model:
            raise ValueError(f"Model spec must be provider:model, got '{spec}'")
        return cls(provider, model)

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model}"


def contact_correct(pred_name: str | None, row: dict[str, str]) -> bool | None:
    """Compare the model's top contact with the labelled truth.

    true_contact_name (if given) is the right person. Otherwise true_contact_ok == y means the
    original run's contact is right (match it); == n means it is wrong (must not repeat it).
    """
    truth_name = (row.get("true_contact_name") or "").strip()
    orig = (row.get("pred_contact_name") or "").strip()
    ok = _yes(row.get("true_contact_ok"))

    def same(a: str | None, b: str | None) -> bool:
        return bool(a) and bool(b) and fold(a) == fold(b)

    if truth_name:
        return same(pred_name, truth_name)
    if ok is True:
        return same(pred_name, orig)
    if ok is False:
        return not same(pred_name, orig)
    return None


async def evaluate_lead(company: Company, row: dict[str, str], ctx: RunContext,
                        cache: Cache, spec: ModelSpec) -> dict[str, Any]:
    meter: dict[str, float] = {}
    t0 = time.perf_counter()
    with tracing.company_trace(company.name, company_id=company.id, model=spec.label,
                               step="eval"):
        company = await classify_company(company, ctx, meter)
        research: dict[str, Any] | None = None
        raw_non_null = valid_non_null = 0
        if company.is_property_manager and normalize_domain(company.website):
            research = await research_company(company, ctx)
            for k, v in (research.get("meter") or {}).items():
                meter[k] = meter.get(k, 0) + v
            if research.get("raw"):
                raw = research["raw"]
                raw_non_null = sum(raw.get(f, {}).get("value") not in (None, [])
                                   for f in KPI_FIELDS)
                company.kpis, _ = verify_kpis(raw, _page_text_lookup(ctx.deps.fetcher))
                valid_non_null = sum(getattr(company.kpis, f).value not in (None, [])
                                     for f in KPI_FIELDS)
            people, _ = await contacts_for(company, ctx, research, meter)
            company.contacts = people
    latency = time.perf_counter() - t0
    key = (spec.label, company.id)
    if meter.get("llm_calls", 0) > 0:  # at least one uncached call: record cost + latency
        cache.set("eval_meta", *key, value={"latency_s": latency,
                                            "cost_usd": meter.get("cost_usd", 0.0)})
    meta = cache.get("eval_meta", *key) or {"latency_s": latency, "cost_usd": 0.0}
    units = company.kpis.units_managed.value if company.kpis else None
    top = company.contacts[0].name if company.contacts else None
    true_pm = _yes(row.get("true_is_property_manager"))
    return {
        "company_id": company.id, "name": company.name,
        "pred_is_property_manager": company.is_property_manager,
        "classification_ok": None if true_pm is None else company.is_property_manager == true_pm,
        "pred_units": units, "units_ok": units_correct(units, row.get("true_units_managed", "")),
        "pred_contact": top, "contact_ok": contact_correct(top, row),
        "raw_non_null": raw_non_null, "valid_non_null": valid_non_null,
        "tool_calls": len(((research or {}).get("trace") or {}).get("tool_calls", [])),
        "researched": research is not None and research.get("raw") is not None,
        "latency_s": round(meta["latency_s"], 2), "cost_usd": round(meta["cost_usd"], 6),
        "cached": meter.get("llm_calls", 0) == 0,
    }


def summarize(spec: ModelSpec, leads: list[dict[str, Any]]) -> dict[str, Any]:
    def acc(key: str) -> dict[str, Any]:
        vals = [x[key] for x in leads if x[key] is not None]
        return {"n": len(vals), "accuracy": round(sum(vals) / len(vals), 3) if vals else None}

    raw = sum(x["raw_non_null"] for x in leads)
    researched = [x for x in leads if x["researched"]]
    return {
        "model": spec.label, "leads": len(leads),
        "classification": acc("classification_ok"), "units_managed": acc("units_ok"),
        "contact": acc("contact_ok"),
        "evidence_valid": {"n": raw, "accuracy": round(sum(x["valid_non_null"] for x in leads)
                                                       / raw, 3) if raw else None},
        "cost_per_lead_usd": round(mean(x["cost_usd"] for x in leads), 5) if leads else None,
        "latency_per_lead_s": round(mean(x["latency_s"] for x in leads), 2) if leads else None,
        "avg_tool_calls": round(mean(x["tool_calls"] for x in researched), 2)
        if researched else None,
        "cached_share": round(mean(x["cached"] for x in leads), 2) if leads else None,
    }


async def evaluate_model(spec: ModelSpec, rows: list[dict[str, str]],
                         companies: dict[str, dict[str, Any]], *, city: str, segment: str,
                         models: ModelsConfig, cache: Cache, max_llm_cost: float,
                         concurrency: int = 4, llm_key: str | None = None,
                         chat: Any = None) -> dict[str, Any]:
    settings = load_settings()
    usage = Usage(max_places_requests=0, max_llm_cost_usd=max_llm_cost)
    prov = models.providers[spec.provider]
    if chat is None:  # tests inject a fake chat model
        key = resolve_key(llm_key, prov.env_key, spec.provider)
        temperature = settings.llm.temperature if prov.send_temperature else None
        chat = get_model(spec.provider, spec.model, key, temperature=temperature,
                         max_retries=settings.llm.max_retries)
    llm = LLM(chat, provider=spec.provider, model=spec.model, cache=cache, usage=usage,
              models_cfg=models, concurrency=concurrency)
    fetcher = Fetcher(settings.fetch, cache, usage)
    ctx = RunContext(run_id="eval", city=load_city(city), segment=load_segment(segment),
                     settings=settings, models=models, provider=spec.provider, model=spec.model,
                     mode=Mode(command="eval", research=True),
                     deps=Deps(cache=cache, usage=usage, places=None, fetcher=fetcher, llm=llm))
    sem = asyncio.Semaphore(concurrency)

    async def one(row: dict[str, str]) -> dict[str, Any] | None:
        rec = companies.get(row["id"])
        if rec is None:
            return None
        fresh = Company(**{**rec, "is_property_manager": None, "classification_category": None,
                           "classification_reason": None, "kpis": None, "contacts": [],
                           "review_reasons": [], "needs_review": False})
        async with sem:
            return await evaluate_lead(fresh, row, ctx, cache, spec)

    try:
        leads = [x for x in await asyncio.gather(*(one(r) for r in rows)) if x]
    finally:
        await fetcher.aclose()
    return {"summary": summarize(spec, leads), "leads": leads,
            "usage": usage.to_dict()}


def results_markdown(summaries: list[dict[str, Any]], meta: dict[str, Any]) -> str:
    def pct(m: dict[str, Any]) -> str:
        return "—" if m["accuracy"] is None else f"{m['accuracy']:.0%} (n={m['n']})"

    lines = [
        "# Evaluation results", "",
        f"Gold set: `{meta['gold']}` ({meta['rows']} labelled leads) · source run "
        f"`{meta['run_id']}` · {meta['created']}", "",
        "| Model | Classification acc. | Units acc. (±15 %) | Contact acc. | Evidence-valid "
        "| Cost / lead | Latency / lead | Avg tool calls |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for s in summaries:
        lines.append(
            f"| {s['model']} | {pct(s['classification'])} | {pct(s['units_managed'])} | "
            f"{pct(s['contact'])} | {pct(s['evidence_valid'])} | "
            f"${s['cost_per_lead_usd']:.4f} | {s['latency_per_lead_s']:.1f} s | "
            f"{s['avg_tool_calls'] if s['avg_tool_calls'] is not None else '—'} |")
    lines += [
        "", "![Model comparison](results.png)", "",
        "- **Evidence-valid**: share of non-null KPI values the model returned that passed "
        "verify_evidence (quote found on the cited page, number/list items in the quote).",
        "- **Contact acc.**: top contact vs. the labelled decision maker "
        "(`true_contact_name`, or the original prediction marked correct/incorrect).",
        "- Cost and latency come from the first uncached run of each lead; reruns read them "
        "from the cache, so the table is reproducible.",
    ]
    return "\n".join(lines) + "\n"


def results_chart(summaries: list[dict[str, Any]], path: Path) -> None:
    """Small multiples, one panel per metric (no dual axes); models labelled on the y axis."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import PercentFormatter

    ink, muted, grid, bar = "#1f1f1e", "#6b6a64", "#e4e2dc", "#2a78d6"
    panels = [(label, [s[key]["accuracy"] for s in summaries], "pct") for key, label in METRICS]
    panels += [("Cost / lead (USD)", [s["cost_per_lead_usd"] for s in summaries], "usd"),
               ("Latency / lead (s)", [s["latency_per_lead_s"] for s in summaries], "s")]
    names = [s["model"] for s in summaries]
    fig, axes = plt.subplots(2, 3, figsize=(12, 1.6 + 0.55 * len(names) * 2), dpi=200)
    fig.patch.set_facecolor("white")
    for ax, (title, values, kind) in zip(axes.flat, panels, strict=True):
        vals = [v if v is not None else 0 for v in values]
        y = range(len(names))
        ax.barh(list(y), vals, color=bar, height=0.55)
        ax.set_yticks(list(y), names, fontsize=8, color=ink)
        ax.invert_yaxis()
        ax.set_title(title, fontsize=10, color=ink, loc="left")
        top = 1.0 if kind == "pct" else (max(vals) * 1.25 or 1)
        ax.set_xlim(0, top)
        for i, (v, raw) in enumerate(zip(vals, values, strict=True)):
            text = "—" if raw is None else (f"{v:.0%}" if kind == "pct" else
                                             f"${v:.4f}" if kind == "usd" else f"{v:.1f}")
            ax.text(v + top * 0.02, i, text, va="center", fontsize=8, color=ink)
        if kind == "pct":
            ax.xaxis.set_major_formatter(PercentFormatter(1.0))
        ax.tick_params(axis="x", labelsize=7, colors=muted)
        ax.grid(axis="x", color=grid, linewidth=0.6)
        ax.set_axisbelow(True)
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.spines["bottom"].set_color(grid)
    fig.suptitle("LeadGraph — model comparison on the gold set", fontsize=12, color=ink,
                 x=0.01, ha="left")
    fig.tight_layout()
    fig.savefig(path, facecolor="white")
    plt.close(fig)


async def run_eval(run_id: str, gold: Path, specs: list[ModelSpec], *, max_llm_cost: float,
                   outputs_dir: Path = OUTPUTS_DIR, eval_dir: Path = Path("eval"),
                   chats: dict[str, Any] | None = None) -> dict[str, Any]:
    run_dir = outputs_dir / run_id
    run_opts = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    rows = read_gold(gold)
    companies = {c["id"]: c for c in load_companies(run_dir)}
    models = load_models()
    cache = Cache(outputs_dir / ".cache")
    results = []
    try:
        for spec in specs:
            results.append(await evaluate_model(
                spec, rows, companies, city=run_opts["city"], segment=run_opts["segment"],
                models=models, cache=cache, max_llm_cost=max_llm_cost,
                chat=(chats or {}).get(spec.label)))
    finally:
        cache.close()
        tracing.flush()
    meta = {"run_id": run_id, "gold": str(gold), "rows": len(rows),
            "created": datetime.now().strftime("%Y-%m-%d %H:%M")}
    summaries = [r["summary"] for r in results]
    out_dir = eval_dir / "results" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(json.dumps({"meta": meta, "results": results},
                                                     indent=2, ensure_ascii=False),
                                          encoding="utf-8")
    (eval_dir / "results.md").write_text(results_markdown(summaries, meta), encoding="utf-8")
    results_chart(summaries, eval_dir / "results.png")
    return {"meta": meta, "summaries": summaries, "detail": str(out_dir / "results.json")}


def tracing_status() -> str:
    return "on (Langfuse)" if tracing.enabled() else "off (set LANGFUSE_PUBLIC_KEY/SECRET_KEY)"


__all__ = ["ModelSpec", "run_eval", "results_markdown", "summarize", "contact_correct",
           "tracing_status"]
