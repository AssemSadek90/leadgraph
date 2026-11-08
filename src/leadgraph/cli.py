"""Command-line interface: `leadgraph run | discover | resume`."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from leadgraph.graph.nodes.sources import parse_selection
from leadgraph.runner import (
    MissingKey,
    NeedsApproval,
    RunOptions,
    execute,
    load_run_options,
)

app = typer.Typer(add_completion=False, no_args_is_help=True,
                  help="LeadGraph — agentic lead research pipeline.")
console = Console()


@app.callback()
def _main() -> None:
    """LeadGraph — agentic lead research pipeline."""


def approve_interactively(value: dict) -> dict:
    """Show scouted sources and ask which to use (LangGraph interrupt → CLI → resume)."""
    cands = value.get("candidates", [])
    t = Table(title="Candidate list sources (scout agent)")
    for col in ("#", "type", "est.", "url", "notes"):
        t.add_column(col, overflow="fold")
    for i, c in enumerate(cands, start=1):
        t.add_row(str(i), c.get("source_type") or "", str(c.get("estimated_count") or ""),
                  c["url"], (c.get("notes") or "")[:80])
    console.print(t)
    sel = typer.prompt("Approve which sources? (e.g. 1,3 / all / none)", default="all")
    extra = typer.prompt("Extra list URLs to add (comma-separated, Enter to skip)",
                         default="", show_default=False)
    return {"approved": parse_selection(sel, len(cands)),
            "extra": [u.strip() for u in extra.split(",") if u.strip()]}


def _execute(opts: RunOptions, resume: bool = False) -> dict:
    approver = approve_interactively if opts.approve is None else None
    try:
        return asyncio.run(execute(opts, resume=resume, approver=approver))
    except MissingKey as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from None
    except NeedsApproval:
        console.print("[yellow]Run paused for source approval.[/yellow]")
    except KeyboardInterrupt:
        console.print("[yellow]Interrupted.[/yellow]")
    console.print(f"Resume with: [bold]uv run leadgraph resume {opts.run_id}[/bold]")
    raise typer.Exit(130)


def _summary(final: dict) -> None:
    if final.get("_already_finished"):
        console.print("[green]This run already finished.[/green]")
    report = json.loads(Path(final["outputs"]["report"]).read_text(encoding="utf-8"))
    t = Table(title=f"Run {report['run_id']}", show_header=False)
    for k, v in report["counts"].items():
        t.add_row(k, str(v))
    u = report["usage"]
    t.add_row("places requests (paid)", str(u["places_requests"]))
    t.add_row("LLM calls (paid)", str(u["llm_calls"]))
    t.add_row("tokens in / out", f"{u['input_tokens']} / {u['output_tokens']}")
    t.add_row("cost estimate (USD)", json.dumps(report["cost_estimate_usd"]))
    t.add_row("cache", json.dumps(report["cache"]))
    t.add_row("duration (s)", str(report["duration_s"]))
    for r in report.get("rounds") or []:
        y = "—" if r["yield"] is None else f"{r['yield']:.1%}"
        t.add_row(f"round {r['round']}", f"+{r['new_unique']} → {r['cumulative']} (yield {y})")
    cov = report.get("coverage") or {}
    if cov.get("chapman_estimate"):
        t.add_row("Chapman estimate / coverage", f"{cov['chapman_estimate']} / {cov['coverage']:.1%}")
    if report.get("stopped_reason"):
        t.add_row("stopped", report["stopped_reason"])
    console.print(t)
    console.print(f"[green]xlsx:[/green] {final['outputs']['xlsx']}")
    console.print(f"[green]report:[/green] {final['outputs']['report']}")


@app.command()
def run(
    city: Annotated[str, typer.Option(help="City config name (config/cities/<city>.yaml).")],
    area: Annotated[str | None, typer.Option(help="Test area from the city config.")] = None,
    segment: Annotated[str, typer.Option(help="Segment preset.")] = "hausverwaltung",
    limit: Annotated[int | None, typer.Option(help="Max companies to classify/export.")] = None,
    provider: Annotated[str, typer.Option(help="LLM provider: anthropic | openai.")] = "anthropic",
    model: Annotated[str | None, typer.Option(help="Model id (default from models.yaml).")] = None,
    llm_key: Annotated[str | None, typer.Option(
        help="LLM API key (default: ANTHROPIC_API_KEY / OPENAI_API_KEY env var).")] = None,
    places_key: Annotated[str | None, typer.Option(
        help="Google Places key (default: GOOGLE_PLACES_API_KEY env var).")] = None,
    max_places_requests: Annotated[int | None, typer.Option(
        help="Budget: paid Places requests.")] = None,
    max_llm_cost: Annotated[float | None, typer.Option(help="Budget: LLM cost in USD.")] = None,
    places_tier: Annotated[str | None, typer.Option(
        help="pro (cheap sweep + Place Details) or enterprise (plan's field mask).")] = None,
    run_id: Annotated[str | None, typer.Option(help="Run id (default: generated).")] = None,
    no_cache: Annotated[bool, typer.Option("--no-cache", help="Bypass the disk cache.")] = False,
) -> None:
    """Thin end-to-end run: one Places search of the area → dedupe → classify → export."""
    opts = RunOptions(
        city=city, command="run", area=area, segment=segment, limit=limit, provider=provider,
        model=model, llm_key=llm_key, places_key=places_key,
        max_places_requests=max_places_requests, max_llm_cost=max_llm_cost,
        places_tier=places_tier, run_id=run_id, no_cache=no_cache, approve="none",
    )
    _summary(_execute(opts))


@app.command()
def discover(
    city: Annotated[str, typer.Option(help="City config name (config/cities/<city>.yaml).")],
    area: Annotated[str | None, typer.Option(help="Test area instead of the whole city.")] = None,
    segment: Annotated[str, typer.Option(help="Segment preset.")] = "hausverwaltung",
    provider: Annotated[str, typer.Option(help="LLM provider: anthropic | openai.")] = "anthropic",
    model: Annotated[str | None, typer.Option(help="Model id (default from models.yaml).")] = None,
    max_rounds: Annotated[int | None, typer.Option(help="Max discovery rounds.")] = None,
    scout: Annotated[bool, typer.Option("--scout/--no-scout",
                                        help="Run the source-scout agent.")] = True,
    approve: Annotated[str | None, typer.Option(
        help="Non-interactive approval: all | none | 1,3. Default: ask.")] = None,
    source: Annotated[list[str] | None, typer.Option(
        help="Extra list URL to extract (repeatable).")] = None,
    limit: Annotated[int | None, typer.Option(help="Max companies to detail/classify.")] = None,
    llm_key: Annotated[str | None, typer.Option(help="LLM API key (default: env var).")] = None,
    places_key: Annotated[str | None, typer.Option(help="Places key (default: env var).")] = None,
    search_key: Annotated[str | None, typer.Option(
        help="Serper key (default: SERPER_API_KEY).")] = None,
    max_places_requests: Annotated[int | None, typer.Option(
        help="Budget: Places requests (search pages + lookups + details).")] = None,
    max_llm_cost: Annotated[float | None, typer.Option(help="Budget: LLM cost in USD.")] = None,
    max_search_requests: Annotated[int | None, typer.Option(
        help="Budget: Serper queries.")] = None,
    places_tier: Annotated[str | None, typer.Option(help="pro | enterprise.")] = None,
    run_id: Annotated[str | None, typer.Option(help="Run id (default: generated).")] = None,
) -> None:
    """Discovery loop: quadtree Places sweep + approved list sources, rounds until saturation,
    then classification. Pauses once to approve list sources (unless --approve is given)."""
    opts = RunOptions(
        city=city, command="discover", area=area, segment=segment, provider=provider,
        model=model, max_rounds=max_rounds, use_scout=scout, approve=approve,
        extra_sources=source or [], limit=limit, llm_key=llm_key, places_key=places_key,
        search_key=search_key, max_places_requests=max_places_requests,
        max_llm_cost=max_llm_cost, max_search_requests=max_search_requests,
        places_tier=places_tier, run_id=run_id,
    )
    _summary(_execute(opts))


@app.command()
def resume(
    run_id: Annotated[str, typer.Argument(help="Run id (folder name under outputs/).")],
    approve: Annotated[str | None, typer.Option(help="all | none | 1,3. Default: ask.")] = None,
    llm_key: Annotated[str | None, typer.Option(help="LLM API key (default: env var).")] = None,
    places_key: Annotated[str | None, typer.Option(help="Places key (default: env var).")] = None,
    search_key: Annotated[str | None, typer.Option(help="Serper key (default: env var).")] = None,
    max_places_requests: Annotated[int | None, typer.Option(
        help="Raise the Places budget for the rest of the run.")] = None,
    max_llm_cost: Annotated[float | None, typer.Option(help="Raise the LLM budget (USD).")] = None,
) -> None:
    """Continue a paused, interrupted or crashed run from its last checkpoint."""
    overrides = {k: v for k, v in {
        "approve": approve, "llm_key": llm_key, "places_key": places_key,
        "search_key": search_key, "max_places_requests": max_places_requests,
        "max_llm_cost": max_llm_cost}.items() if v is not None}
    try:
        opts = load_run_options(run_id, **overrides)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from None
    _summary(_execute(opts, resume=True))


@app.command("recall")
def recall_cmd(
    run_id: Annotated[str, typer.Argument(help="Run id (folder name under outputs/).")],
    probes: Annotated[Path, typer.Option(help="CSV with name,website,notes.")] = Path(
        "eval/recall_probes.csv"),
) -> None:
    """Recall probes: how many hand-found companies did the run discover?"""
    from leadgraph.config import OUTPUTS_DIR
    from leadgraph.recall import load_companies, load_probes, recall

    run_path = OUTPUTS_DIR / run_id
    rows = load_probes(probes)
    if not rows:
        console.print(f"[red]No probes in {probes}. Add 10 companies found by hand.[/red]")
        raise typer.Exit(2)
    result = recall(rows, load_companies(run_path))
    t = Table(title=f"Recall probes — {result['found']}/{result['probes']} found "
                    f"({result['recall']:.0%})")
    for col in ("probe", "found", "matched as", "sources"):
        t.add_column(col, overflow="fold")
    for r in result["rows"]:
        t.add_row(r["probe"], "yes" if r["found"] else "[red]no[/red]", r["matched_name"] or "",
                  ", ".join(r["matched_sources"] or []))
    console.print(t)
    (run_path / "recall.json").write_text(json.dumps(result, indent=2, ensure_ascii=False),
                                          encoding="utf-8")
    console.print(f"[green]saved:[/green] {run_path / 'recall.json'}")


def main() -> None:  # pragma: no cover
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
