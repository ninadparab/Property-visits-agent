# agent/graph.py

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from state import AgentState
from nodes import (
    parse_node,
    input_node,
    scraper_node,
    filter_update_node,
    distance_node,
    scheduler_node,
    replan_node,
    output_node,
    feedback_node,
)


def _route(state: AgentState) -> str:
    """Single routing function — all nodes write next_step, this reads it."""
    return state.get("next_step", "end")


def build_graph():
    graph = StateGraph(AgentState)

    # ── Nodes ─────────────────────────────────────────────────────────────────
    graph.add_node("parse",         parse_node)
    graph.add_node("input",         input_node)
    graph.add_node("scraper",       scraper_node)
    graph.add_node("filter_update", filter_update_node)   # mid-flow human input
    graph.add_node("distance",      distance_node)
    graph.add_node("scheduler",     scheduler_node)
    graph.add_node("replan",        replan_node)
    graph.add_node("output",        output_node)
    graph.add_node("feedback",      feedback_node)        # multi-turn state update

    # ── Entry ──────────────────────────────────────────────────────────────────
    graph.set_entry_point("parse")
    graph.add_edge("parse", "input")

    # ── Conditional edges ──────────────────────────────────────────────────────
    graph.add_conditional_edges("input", _route, {
        "scrape": "scraper",
        "end":    END,
    })

    # Scraper can go to distance (results found), filter_update (0 results), or end
    graph.add_conditional_edges("scraper", _route, {
        "distance":      "distance",
        "filter_update": "filter_update",
        "end":           END,
    })

    # filter_update loops back to scraper after user relaxes filters
    graph.add_conditional_edges("filter_update", _route, {
        "scrape": "scraper",
        "end":    END,
    })

    graph.add_conditional_edges("distance", _route, {
        "schedule": "scheduler",
        "end":      END,
    })

    graph.add_conditional_edges("scheduler", _route, {
        "replan": "replan",
        "output": "output",
    })

    # ── Fixed edges ────────────────────────────────────────────────────────────
    graph.add_edge("replan",  "output")
    graph.add_edge("output",  "feedback")   # always ask for feedback after showing schedule

    # feedback routes back to whichever node needs to re-run
    graph.add_conditional_edges("feedback", _route, {
        "filters":  "scraper",    # filter change → re-scrape
        "time":     "scheduler",  # timing change → re-schedule only
        "location": "distance",   # new home address → new travel matrix + re-schedule
        "restart":  "parse",      # start over
        "end":      END,
    })

    # ── Compile with in-memory checkpointer (required for interrupt()) ─────────
    return graph.compile(checkpointer=MemorySaver())


agent = build_graph()
