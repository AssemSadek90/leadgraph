"""Graph assembly."""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from leadgraph.graph.nodes.classify import classify
from leadgraph.graph.nodes.discovery import merge_dedupe, places_sweep, plan_sweep
from leadgraph.graph.nodes.export import export
from leadgraph.graph.state import RunContext, RunState


def build_graph(checkpointer=None):
    g = StateGraph(RunState, context_schema=RunContext)
    g.add_node("plan_sweep", plan_sweep)
    g.add_node("places_sweep", places_sweep)
    g.add_node("merge_dedupe", merge_dedupe)
    g.add_node("classify", classify)
    g.add_node("export", export)
    g.add_edge(START, "plan_sweep")
    g.add_edge("plan_sweep", "places_sweep")
    g.add_edge("places_sweep", "merge_dedupe")
    g.add_edge("merge_dedupe", "classify")
    g.add_edge("classify", "export")
    g.add_edge("export", END)
    return g.compile(checkpointer=checkpointer)
