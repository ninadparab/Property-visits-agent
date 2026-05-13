# workflow.py
#
# Simple deterministic pipeline — no agent framework, no LangGraph.
# Runs: scrape → travel matrix → schedule → print results.
# Use this when you don't need mid-flow human input or multi-turn updates.

from dotenv import load_dotenv
load_dotenv()

from redfin_tool import scrape_open_houses
from google_maps_tool import build_travel_time_matrix
from scheduler_tool import schedule_open_house_visits


def _full_address(prop: dict) -> str:
    parts = [prop.get("address", ""), prop.get("city", ""),
             prop.get("state", ""), str(prop.get("zip", ""))]
    return ", ".join(p for p in parts if p and p not in ("nan", "None", ""))


def run_workflow(
    user_location:  str,
    city:           str,
    state:          str,
    time_per_house: int = 30,
    start_time:     str | None = None,
    filters:        dict | None = None,
) -> dict:
    """
    Run the full open house planning pipeline in a straight line.

    Args:
        user_location:  Home address (starting point).
        city:           City to search.
        state:          Two-letter state abbreviation e.g. "WA".
        time_per_house: Minutes to spend at each property.
        start_time:     ISO-8601 departure time e.g. "2025-05-17T09:00:00".
        filters:        Dict of search filters (min_beds, max_price, etc.).

    Returns:
        Schedule dict with "itinerary", "total_visits", and "skipped" keys.
    """
    filters = filters or {}

    # ── Step 1: Scrape open houses ────────────────────────────────────────────
    print(f"Scraping open houses in {city}, {state}...")
    houses = scrape_open_houses.invoke({
        "city":  city,
        "state": state,
        **{k: v for k, v in filters.items() if v is not None},
    })

    if not houses:
        return {"error": f"No open houses found in {city}, {state}"}
    if "error" in houses[0]:
        return {"error": houses[0]["error"]}
    print(f"  Found {len(houses)} open houses")

    # ── Step 2: Build travel time matrix ──────────────────────────────────────
    print("Computing travel times...")
    locations = [user_location] + [_full_address(h) for h in houses]
    matrix = build_travel_time_matrix.invoke({
        "locations":      locations,
        "mode":           "driving",
        "departure_time": start_time,
    })

    if not matrix:
        return {"error": "Could not compute travel times"}
    print(f"  Computed {len(matrix)} travel time pairs")

    # ── Step 3: Build optimised schedule ──────────────────────────────────────
    print("Building schedule...")
    result = schedule_open_house_visits.invoke({
        "home_address":           user_location,
        "open_houses":            houses,
        "travel_matrix":          matrix,
        "visit_duration_minutes": time_per_house,
        "start_time":             start_time,
    })

    if "error" in result:
        return {"error": result["error"]}

    return result


def _print_result(result: dict) -> None:
    if "error" in result:
        print(f"\nError: {result['error']}")
        return

    print(f"\nSchedule ({result['total_visits']} visits):\n")
    for stop in result.get("itinerary", []):
        travel = f"  → {stop['travel_to_next_minutes']} min drive" if stop.get("travel_to_next_minutes") else ""
        print(f"  {stop['order']}. {stop['address']}")
        print(f"     Open: {stop['open_house_window']}")
        print(f"     Arrive: {stop['arrive_at']}  |  Depart: {stop['depart_at']}{travel}")
        print()

    if result.get("skipped"):
        print("Could not fit:")
        for msg in result["skipped"]:
            print(f"  - {msg}")


if __name__ == "__main__":
    result = run_workflow(
        user_location  = "500 108th Ave NE, Bellevue, WA",
        city           = "Redmond",
        state          = "WA",
        time_per_house = 30,
        start_time     = "2025-05-17T09:00:00",
        filters        = {
            "min_beds":       3,
            "max_price":      1_500_000,
            "min_baths":      1.5,
            "min_year_built": 1980,
            "property_types": ["house", "condo", "townhouse"],
        },
    )
    _print_result(result)
