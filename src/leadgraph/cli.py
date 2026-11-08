"""Command-line interface: `leadgraph run ...`."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from leadgraph.runner import MissingKey, RunOptions, execute

app = typer.Typer(add_completion=False, no_args_is_help=True,
                  help="LeadGraph — agentic lead research pipeline.")
console = Console()


@app.callback()
def _main() -> None:
    """LeadGraph — agentic lead research pipeline."""


def _summary(final: dict) -> None:
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
    run_id: Annotated[str | None, typer.Option(help="Run id (default: generated).")] = None,
    no_cache: Annotated[bool, typer.Option("--no-cache", help="Bypass the disk cache.")] = False,
) -> None:
    """Thin end-to-end run: Places search → dedupe → classify → export."""
    opts = RunOptions(
        city=city, area=area, segment=segment, limit=limit, provider=provider, model=model,
        llm_key=llm_key, places_key=places_key, max_places_requests=max_places_requests,
        max_llm_cost=max_llm_cost, run_id=run_id, no_cache=no_cache,
    )
    try:
        final = asyncio.run(execute(opts, command="run"))
    except MissingKey as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from None
    _summary(final)


def main() -> None:  # pragma: no cover
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
