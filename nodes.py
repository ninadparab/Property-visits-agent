# agent/nodes.py

from datetime import datetime
from typing import TypedDict

from langchain_anthropic import ChatAnthropic

from state import AgentState
from redfin_tool import scrape_open_houses
from google_maps_tool import build_travel_time_matrix
from scheduler_tool import schedule_open_house_visits


# ── TypedDict schema for LLM structured extraction ───────────────────────────
# total=False makes all keys optional — user may not mention every field.
# Field descriptions live in the system prompt since TypedDict has no Field().

class _ParsedInput(TypedDict, total=False):
    user_location:  str
    city:           str
    state:          str
    time_per_house: int
    start_time:     str
    property_types: list[str]
    min_price:      int
    max_price:      int
    min_beds:       int
    max_beds:       int
    min_baths:      float
    max_baths:      float
    min_year_built: int
    max_year_built: int
    min_sqft:       int
    max_sqft:       int


_FILTER_KEYS = {
    "property_types", "min_price", "max_price",
    "min_beds", "max_beds", "min_baths", "max_baths",
    "min_year_built", "max_year_built", "min_sqft", "max_sqft",
}

_SYSTEM_PROMPT = """You are a data extraction assistant. Extract open house visit \
planning details from the user message and return them as a structured object. \
Only include fields that are explicitly mentioned — omit everything else.

Field definitions:
- user_location: Full home address (street number, street, city, state)
- city: City to search for open houses
- state: Two-letter US state abbreviation e.g. WA, CA
- time_per_house: Minutes to spend at each property (default 30 if not mentioned)
- start_time: ISO-8601 departure time from home e.g. 2025-05-17T09:00:00. \
Today is {today}. If the user says "Saturday" or "this weekend" compute the \
upcoming Saturday's date. If "Sunday", use the upcoming Sunday.
- property_types: List from: house, condo, townhouse, land, other
- min_beds / max_beds: Bedroom count (integers)
- min_price / max_price: Price in dollars (integers, no commas or $ signs)
- min_baths / max_baths: Bathroom count (floats e.g. 1.5)
- min_year_built / max_year_built: Year as integer
- min_sqft / max_sqft: Square footage as integer"""


def parse_node(state: AgentState) -> dict:
    """
    Converts a raw natural language message into structured AgentState fields.
    Reads:  raw_input
    Writes: user_location, city, state, time_per_house, start_time, filters
    """
    raw = state.get("raw_input", "").strip()
    if not raw:
        return {}  # nothing to parse — input_node will catch missing fields

    today = datetime.now().strftime("%Y-%m-%d")
    llm = ChatAnthropic(model="claude-haiku-4-5-20251001").with_structured_output(_ParsedInput)

    parsed: _ParsedInput = llm.invoke([
        {"role": "system", "content": _SYSTEM_PROMPT.format(today=today)},
        {"role": "user",   "content": raw},
    ])

    updates: dict = {}
    for key in ("user_location", "city", "state", "time_per_house", "start_time"):
        if key in parsed:
            updates[key] = parsed[key]

    # Collect all filter fields into the filters dict
    filters = {k: parsed[k] for k in _FILTER_KEYS if k in parsed}
    if filters:
        updates["filters"] = filters

    return updates


def _full_address(prop: dict) -> str:
    parts = [prop.get("address", ""), prop.get("city", ""),
             prop.get("state", ""), str(prop.get("zip", ""))]
    return ", ".join(p for p in parts if p and p not in ("nan", "None", ""))


def input_node(state: AgentState) -> dict:
    """Validates required user inputs before any tool runs."""
    errors = []
    if not state.get("user_location"):
        errors.append("user_location is required")
    if not state.get("city"):
        errors.append("city is required")
    if not state.get("state"):
        errors.append("state is required")
    if not state.get("time_per_house"):
        errors.append("time_per_house is required")

    if errors:
        return {"error": ", ".join(errors), "next_step": "end"}

    return {"next_step": "scrape"}


def scraper_node(state: AgentState) -> dict:
    """
    Calls scrape_open_houses.
    Reads:  city, state, filters
    Writes: open_houses
    """
    filters = state.get("filters", {})

    try:
        houses = scrape_open_houses.invoke({
            "city":           state["city"],
            "state":          state["state"],
            "property_types": filters.get("property_types"),
            "min_price":      filters.get("min_price"),
            "max_price":      filters.get("max_price"),
            "min_beds":       filters.get("min_beds"),
            "max_beds":       filters.get("max_beds"),
            "min_baths":      filters.get("min_baths"),
            "max_baths":      filters.get("max_baths"),
            "min_year_built": filters.get("min_year_built"),
            "max_year_built": filters.get("max_year_built"),
            "min_sqft":       filters.get("min_sqft"),
            "max_sqft":       filters.get("max_sqft"),
        })
    except Exception as e:
        return {"error": f"Scraper failed: {e}", "next_step": "end"}

    # Tool returns [{"error": "..."}] on failure
    if houses and "error" in houses[0]:
        return {"error": houses[0]["error"], "next_step": "end"}

    if not houses:
        return {"error": "No open houses found matching your filters",
                "next_step": "end"}

    return {"open_houses": houses, "next_step": "distance"}


def distance_node(state: AgentState) -> dict:
    """
    Calls build_travel_time_matrix.
    Reads:  user_location, open_houses, start_time
    Writes: travel_matrix
    """
    home = state["user_location"]
    house_addresses = [_full_address(h) for h in state["open_houses"]]

    # Home must be first so the scheduler can use it as the depot
    locations = [home] + house_addresses

    try:
        matrix = build_travel_time_matrix.invoke({
            "locations":      locations,
            "mode":           "driving",
            "departure_time": state.get("start_time") or None,
        })
    except Exception as e:
        return {"error": f"Distance calculation failed: {e}", "next_step": "end"}

    if not matrix:
        return {"error": "Could not compute travel times", "next_step": "end"}

    return {"travel_matrix": matrix, "next_step": "schedule"}


def scheduler_node(state: AgentState) -> dict:
    """
    Calls schedule_open_house_visits.
    Reads:  user_location, open_houses, travel_matrix, time_per_house, start_time
    Writes: schedules, unschedulable
    """
    try:
        result = schedule_open_house_visits.invoke({
            "home_address":           state["user_location"],
            "open_houses":            state["open_houses"],
            "travel_matrix":          state["travel_matrix"],
            "visit_duration_minutes": state["time_per_house"],
            "start_time":             state.get("start_time") or None,
        })
    except Exception as e:
        return {"error": f"Scheduler failed: {e}", "next_step": "end"}

    if "error" in result:
        return {"error": result["error"], "next_step": "end"}

    itinerary     = result.get("itinerary", [])
    unschedulable = result.get("skipped", [])

    next_step = "replan" if unschedulable else "output"

    return {
        "schedules":     itinerary,
        "unschedulable": unschedulable,
        "next_step":     next_step,
    }


def replan_node(state: AgentState) -> dict:
    """
    Handles houses that didn't fit the schedule.
    Currently flags them and passes through to output.
    Future: could ask user which to drop or try alternate days.
    """
    for msg in state.get("unschedulable", []):
        print(f"  Skipped: {msg}")

    return {"next_step": "output"}


def output_node(state: AgentState) -> dict:
    """Formats the final itinerary for the user."""
    itinerary     = state.get("schedules", [])
    unschedulable = state.get("unschedulable", [])
    error         = state.get("error")

    if error:
        output = f"Sorry, something went wrong: {error}"

    else:
        lines = ["Here is your open house schedule:\n"]

        for stop in itinerary:
            travel_note = (
                f"  ({stop['travel_to_next_minutes']} min drive to next)"
                if stop.get("travel_to_next_minutes") else ""
            )
            lines.append(
                f"  {stop['order']}. {stop['address']}\n"
                f"     Open: {stop['open_house_window']}\n"
                f"     Arrive: {stop['arrive_at']}  |  Depart: {stop['depart_at']}"
                f"{travel_note}"
            )

        if unschedulable:
            lines.append("\nCould not fit these into the schedule:")
            for msg in unschedulable:
                lines.append(f"  - {msg}")

        output = "\n".join(lines)

    return {"messages": [{"role": "assistant", "content": output}]}
