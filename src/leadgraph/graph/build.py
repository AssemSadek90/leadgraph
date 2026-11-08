"""Graph assembly."""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from leadgraph.graph.nodes.classify import classify
from leadgraph.graph.nodes.coverage_loop import (
    expand_queries,
    measure_yield,
    place_details,
    route_after_expand,
    route_after_yield,
)
from leadgraph.graph.nodes.discovery import (
    merge_dedupe,
    places_sweep,
    plan_sweep,
    route_after_plan,
)
from leadgraph.graph.nodes.export import export
from leadgraph.graph.nodes.research import (
    ResearchTask,
    research_agent,
    route_research,
    verify_evidence,
)
from leadgraph.graph.nodes.sources import approve_sources, extract_lists, scout_sources
from leadgraph.graph.state import RunContext, RunState


def route_start(state: RunState, runtime: Runtime[RunContext]) -> str:
    """`enrich` starts from a finished discovery run's companies; everything else discovers."""
    return "prepare_enrich" if runtime.context.mode.command == "enrich" else "plan_sweep"


async def prepare_enrich(state: RunState, runtime: Runtime[RunContext]) -> dict:
    return {}


def build_graph(checkpointer=None):
    """Discovery loop → place_details → classify → export (BUILD_PLAN Section 2).

    Round 1:  plan_sweep → scout_sources → approve_sources → [places_sweep ∥ extract_lists]
    Round 2+: plan_sweep → [places_sweep ∥ extract_lists]
    Then:     merge_dedupe → measure_yield → expand_queries → plan_sweep … or → place_details
    Finally:  place_details → classify → Send(research_agent) × N → verify_evidence → export
    `enrich`: START → prepare_enrich → Send(research_agent) × N → verify_evidence → export
    """
    g = StateGraph(RunState, context_schema=RunContext)
    g.add_node("plan_sweep", plan_sweep)
    g.add_node("scout_sources", scout_sources)
    g.add_node("approve_sources", approve_sources)
    g.add_node("places_sweep", places_sweep)
    g.add_node("extract_lists", extract_lists)
    g.add_node("merge_dedupe", merge_dedupe)
    g.add_node("measure_yield", measure_yield)
    g.add_node("expand_queries", expand_queries)
    g.add_node("place_details", place_details)
    g.add_node("classify", classify)
    g.add_node("prepare_enrich", prepare_enrich)
    g.add_node("research_agent", research_agent, input_schema=ResearchTask)
    g.add_node("verify_evidence", verify_evidence)
    g.add_node("export", export)

    g.add_conditional_edges(START, route_start, ["plan_sweep", "prepare_enrich"])
    g.add_conditional_edges("plan_sweep", route_after_plan,
                            ["scout_sources", "places_sweep", "extract_lists"])
    g.add_edge("scout_sources", "approve_sources")
    g.add_edge("approve_sources", "places_sweep")
    g.add_edge("approve_sources", "extract_lists")
    g.add_edge(["places_sweep", "extract_lists"], "merge_dedupe")  # waits for both
    g.add_edge("merge_dedupe", "measure_yield")
    g.add_conditional_edges("measure_yield", route_after_yield,
                            ["expand_queries", "place_details"])
    g.add_conditional_edges("expand_queries", route_after_expand,
                            ["plan_sweep", "place_details"])
    g.add_edge("place_details", "classify")
    g.add_conditional_edges("classify", route_research,
                            ["research_agent", "verify_evidence", "export"])
    g.add_conditional_edges("prepare_enrich", route_research,
                            ["research_agent", "verify_evidence", "export"])
    g.add_edge("research_agent", "verify_evidence")
    g.add_edge("verify_evidence", "export")
    g.add_edge("export", END)
    return g.compile(checkpointer=checkpointer)
