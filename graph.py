# agent/graph.py

from langgraph.graph import StateGraph, END
from state import AgentState
from nodes import (
    parse_node,
    input_node,
    scraper_node,
    distance_node,
    scheduler_node,
    replan_node,
    output_node,
)


# ── Conditional edge functions ────────────────────────────────
# These functions read state and return the name of the next node

def route_after_input(state: AgentState) -> str:
    """After input validation — go to scraper or end."""
    return state.get("next_step", "end")


def route_after_scraper(state: AgentState) -> str:
    """After scraping — go to distance or end."""
    return state.get("next_step", "end")


def route_after_distance(state: AgentState) -> str:
    """After distance matrix — go to scheduler or end."""
    return state.get("next_step", "end")


def route_after_scheduler(state: AgentState) -> str:
    """After scheduling — go to replan or output."""
    return state.get("next_step", "output")


# ── Build the graph ───────────────────────────────────────────

def build_graph():

    # initialise graph with your state
    graph = StateGraph(AgentState)

    # ── Add nodes ─────────────────────────────────────────────
    graph.add_node("parse",     parse_node)
    graph.add_node("input",     input_node)
    graph.add_node("scraper",   scraper_node)
    graph.add_node("distance",  distance_node)
    graph.add_node("scheduler", scheduler_node)
    graph.add_node("replan",    replan_node)
    graph.add_node("output",    output_node)

    # ── Set entry point ───────────────────────────────────────
    graph.set_entry_point("parse")
    graph.add_edge("parse", "input")

    # ── Add edges ─────────────────────────────────────────────

    # conditional edges — function decides next node
    graph.add_conditional_edges(
        "input",                    # from this node
        route_after_input,          # call this function
        {                           # map return value to next node
            "scrape": "scraper",
            "end":    END,
        }
    )

    graph.add_conditional_edges(
        "scraper",
        route_after_scraper,
        {
            "distance": "distance",
            "end":      END,
        }
    )

    graph.add_conditional_edges(
        "distance",
        route_after_distance,
        {
            "schedule": "scheduler",
            "end":      END,
        }
    )

    graph.add_conditional_edges(
        "scheduler",
        route_after_scheduler,
        {
            "replan": "replan",
            "output": "output",
        }
    )

    # simple fixed edges — always go to next node
    graph.add_edge("replan", "output")
    graph.add_edge("output", END)

    # ── Compile ───────────────────────────────────────────────
    return graph.compile()


# expose compiled graph
agent = build_graph()