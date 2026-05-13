# agent/nodes.py

from datetime import datetime

from pydantic import BaseModel, Field
from langchain_anthropic import ChatAnthropic
from langgraph.types import interrupt

from state import AgentState
from redfin_tool import scrape_open_houses
from google_maps_tool import build_travel_time_matrix
from scheduler_tool import schedule_open_house_visits


# ── Pydantic models for LLM structured extraction ────────────────────────────
# Field() descriptions are sent to Claude as part of the JSON schema —
# no need to repeat them in the system prompt.

class _Filters(BaseModel):
    property_types: list[str] | None = Field(None, description="Property types to include: house, condo, townhouse, land, other")
    min_price:      int   | None     = Field(None, description="Minimum listing price in dollars")
    max_price:      int   | None     = Field(None, description="Maximum listing price in dollars")
    min_beds:       int   | None     = Field(None, description="Minimum number of bedrooms")
    max_beds:       int   | None     = Field(None, description="Maximum number of bedrooms")
    min_baths:      float | None     = Field(None, description="Minimum number of bathrooms e.g. 1.5")
    max_baths:      float | None     = Field(None, description="Maximum number of bathrooms")
    min_year_built: int   | None     = Field(None, description="Earliest year the property was built")
    max_year_built: int   | None     = Field(None, description="Latest year the property was built")
    min_sqft:       int   | None     = Field(None, description="Minimum square footage")
    max_sqft:       int   | None     = Field(None, description="Maximum square footage")


class _ParsedInput(BaseModel):
    user_location:  str | None = Field(None, description="User's full home address including street, city, and state")
    city:           str | None = Field(None, description="City to search for open houses")
    state:          str | None = Field(None, description="Two-letter US state abbreviation e.g. WA, CA")
    time_per_house: int        = Field(30,   description="Minutes to spend at each property, default 30")
    start_time:     str | None = Field(None, description="ISO-8601 departure time from home e.g. 2025-05-17T09:00:00. Infer the actual date when user says Saturday, Sunday, or this weekend.")
    filters:        _Filters   = Field(default_factory=_Filters)


_SYSTEM_PROMPT = (
    "Extract open house visit planning details from the user message. "
    "Today is {today}. Only populate fields explicitly mentioned by the user."
)


class _FeedbackResult(BaseModel):
    action: str = Field(description=(
        "What the user wants to change: "
        "'filters' if property type, price, beds, baths, sqft, or year built changed; "
        "'time' if departure time or minutes per visit changed; "
        "'location' if home address changed; "
        "'restart' to start completely over with a new request; "
        "'done' if the user is satisfied and wants to exit"
    ))
    # Filter changes — used when action='filters'
    property_types: list[str] | None = Field(None, description="Updated property types")
    min_price:      int   | None     = Field(None, description="Updated min price in dollars")
    max_price:      int   | None     = Field(None, description="Updated max price in dollars")
    min_beds:       int   | None     = Field(None, description="Updated min bedrooms")
    max_beds:       int   | None     = Field(None, description="Updated max bedrooms")
    min_baths:      float | None     = Field(None, description="Updated min bathrooms")
    max_baths:      float | None     = Field(None, description="Updated max bathrooms")
    min_year_built: int   | None     = Field(None, description="Updated min year built")
    max_year_built: int   | None     = Field(None, description="Updated max year built")
    min_sqft:       int   | None     = Field(None, description="Updated min sqft")
    max_sqft:       int   | None     = Field(None, description="Updated max sqft")
    # Time changes — used when action='time'
    new_start_time:     str | None   = Field(None, description="New ISO-8601 departure time e.g. 2025-05-17T10:00:00")
    new_visit_duration: int | None   = Field(None, description="New minutes per visit")
    # Location change — used when action='location'
    new_home_address:   str | None   = Field(None, description="New full home address")


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

    result: _ParsedInput = llm.invoke([
        {"role": "system", "content": _SYSTEM_PROMPT.format(today=today)},
        {"role": "user",   "content": raw},
    ])

    updates: dict = {}
    if result.user_location:  updates["user_location"]  = result.user_location
    if result.city:           updates["city"]            = result.city
    if result.state:          updates["state"]           = result.state
    if result.start_time:     updates["start_time"]      = result.start_time
    updates["time_per_house"] = result.time_per_house

    filters = result.filters.model_dump(exclude_none=True)
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
        # Route to filter_update so the user can relax filters interactively
        return {"next_step": "filter_update"}

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


def filter_update_node(state: AgentState) -> dict:
    """
    Mid-flow human input: pauses when scraper returns 0 results and asks
    the user to relax their filters. Loops back to scraper on retry.
    Reads:  city, state, filters
    Writes: filters, next_step
    """
    current = state.get("filters", {})
    question = (
        f"No open houses found in {state.get('city')}, {state.get('state')} "
        f"with your current filters: {current or 'none'}.\n"
        "What would you like to change? "
        "(e.g. 'increase max price to $2M', 'remove the year built filter', 'try condos too')\n"
        "Or say 'stop' to exit."
    )

    user_response = interrupt(question)

    if user_response.strip().lower() in ("stop", "exit", "quit", "no"):
        return {"error": "No matching open houses found.", "next_step": "end"}

    llm = ChatAnthropic(model="claude-haiku-4-5-20251001").with_structured_output(_Filters)
    updated: _Filters = llm.invoke([
        {"role": "system", "content": (
            f"The user wants to update their open house search filters. "
            f"Current filters: {current}. "
            "Extract only the fields they mentioned changing. Omit everything else."
        )},
        {"role": "user", "content": user_response},
    ])

    merged = {**current, **updated.model_dump(exclude_none=True)}
    return {"filters": merged, "next_step": "scrape"}


def feedback_node(state: AgentState) -> dict:
    """
    Multi-turn state update: pauses after output and asks the user if they
    want to change anything. Routes back to the right node based on what changed.
    Reads:  schedules, unschedulable
    Writes: varies by action (filters / start_time / user_location / next_step)
    """
    question = (
        "Would you like to change anything?\n"
        "  • Filters   — e.g. 'remove condos', 'max price $2M'\n"
        "  • Timing    — e.g. 'start at 10am', '45 minutes per house'\n"
        "  • Location  — e.g. 'leave from downtown Seattle instead'\n"
        "  • 'restart' — start over with a new search\n"
        "  • 'done'    — exit\n"
    )

    user_response = interrupt(question)

    if user_response.strip().lower() in ("done", "exit", "quit", "no", "looks good", "thanks"):
        return {"next_step": "end"}

    llm = ChatAnthropic(model="claude-haiku-4-5-20251001").with_structured_output(_FeedbackResult)
    result: _FeedbackResult = llm.invoke([
        {"role": "system", "content": (
            f"The user has seen their open house schedule and wants to make changes. "
            f"Current state — city: {state.get('city')}, filters: {state.get('filters', {})}, "
            f"start_time: {state.get('start_time')}, home: {state.get('user_location')}. "
            "Parse what they want to change."
        )},
        {"role": "user", "content": user_response},
    ])

    updates: dict = {"next_step": result.action}

    if result.action == "filters":
        filter_changes = result.model_dump(
            include={"property_types","min_price","max_price","min_beds","max_beds",
                     "min_baths","max_baths","min_year_built","max_year_built","min_sqft","max_sqft"},
            exclude_none=True,
        )
        updates["filters"] = {**state.get("filters", {}), **filter_changes}

    elif result.action == "time":
        if result.new_start_time:
            updates["start_time"] = result.new_start_time
        if result.new_visit_duration:
            updates["time_per_house"] = result.new_visit_duration

    elif result.action == "location":
        if result.new_home_address:
            updates["user_location"] = result.new_home_address

    return updates
