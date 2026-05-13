# agent/state.py

from typing import TypedDict, Annotated
from langgraph.graph.message import add_messages

class AgentState(TypedDict):

    # ── Conversation ──────────────────────────────
    # add_messages means append new messages, not overwrite
    messages: Annotated[list, add_messages]

    # ── Raw natural language input ────────────────
    raw_input: str              # "Find open houses in Redmond WA this Saturday..."

    # ── User Inputs (populated by parse_node) ─────
    user_location: str          # "500 108th Ave NE, Bellevue WA"
    city: str                   # "Redmond"
    state: str                  # "WA"
    time_per_house: int         # minutes user wants at each house e.g. 30
    start_time: str             # "09:00"
    filters: dict               # {"min_beds": 3, "max_price": 1500000, ...}

    # ── Tool Outputs ──────────────────────────────
    open_houses: list           # output from scraper tool
    travel_matrix: dict         # output from distance tool
    schedules: list             # output from scheduler tool

    # ── Control Flow ──────────────────────────────
    unschedulable: list         # houses that didn't fit any schedule
    error: str                  # error message if something fails
    next_step: str              # used by conditional edges to decide routing