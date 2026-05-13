"""
Scheduler tool for open house visit planning.

Uses Google OR-Tools VRPTW (Vehicle Routing Problem with Time Windows)
with a single vehicle to find the optimal visit order that maximises the
number of open houses visited within their time windows.

All open house nodes are optional — OR-Tools skips ones it cannot fit
rather than declaring the whole problem infeasible.
"""

from datetime import datetime, timedelta

from langchain_core.tools import tool

try:
    from ortools.constraint_solver import routing_enums_pb2, pywrapcp
    _ORTOOLS_AVAILABLE = True
except ImportError:
    _ORTOOLS_AVAILABLE = False

# Redfin open house times arrive in formats like "Sat, May 17 10:00 AM"
_TIME_FORMATS = [
    "%a, %b %d %I:%M %p",
    "%a, %b %d %I:%M%p",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
]


def _parse_time(time_str: str, year: int) -> datetime | None:
    if not time_str or str(time_str).lower() in ("nan", "none", ""):
        return None
    for fmt in _TIME_FORMATS:
        try:
            dt = datetime.strptime(str(time_str).strip(), fmt)
            return dt.replace(year=year) if dt.year == 1900 else dt
        except ValueError:
            continue
    return None


def _full_address(prop: dict) -> str:
    parts = [prop.get("address", ""), prop.get("city", ""),
             prop.get("state", ""), str(prop.get("zip", ""))]
    return ", ".join(p for p in parts if p and p not in ("nan", "None", ""))


@tool
def schedule_open_house_visits(
    home_address: str,
    open_houses: list[dict],
    travel_matrix: dict[str, int],
    visit_duration_minutes: int = 30,
    start_time: str | None = None,
) -> dict:
    """
    Build an optimised open house visit schedule using OR-Tools VRPTW.

    Finds the maximum number of open houses that can be visited given
    travel times and open house time windows. Open houses that cannot
    be fit in without violating a window are skipped and reported.

    Call this after:
      1. scrape_open_houses returns listings
      2. build_travel_time_matrix returns the travel matrix

    Args:
        home_address:           User's starting address — must be the first
                                entry used when building the travel matrix.
        open_houses:            List of dicts from scrape_open_houses.
                                Each needs: address, city, state, zip,
                                open_house_start, open_house_end.
        travel_matrix:          Dict from build_travel_time_matrix.
                                Keys: "ORIGIN -> DESTINATION", values: seconds.
        visit_duration_minutes: Minutes to spend at each property (default 30).
        start_time:             ISO-8601 departure time from home, e.g.
                                "2025-05-17T09:00:00". Defaults to 30 minutes
                                before the earliest open house.

    Returns:
        {
          "itinerary": [
            {
              "order": 1,
              "address": "123 Main St, Bellevue, WA, 98004",
              "arrive_at": "10:15 AM",
              "open_house_window": "10:00 AM - 12:00 PM",
              "depart_at": "10:45 AM",
              "travel_to_next_minutes": 12
            },
            ...
          ],
          "total_visits": 3,
          "skipped": ["456 Oak Ave ... - could not fit within open house window"]
        }
    """
    if not _ORTOOLS_AVAILABLE:
        return {"error": "ortools is not installed. Run: pip install ortools"}

    year = datetime.now().year

    # ── Parse and validate candidates ────────────────────────────────────────
    valid, skipped = [], []
    for prop in open_houses:
        start = _parse_time(prop.get("open_house_start"), year)
        end   = _parse_time(prop.get("open_house_end"),   year)
        addr  = _full_address(prop)
        if not addr.strip(", ") or start is None or end is None:
            skipped.append(f"{addr or 'Unknown'} - missing or unparseable time window")
            continue
        if (end - start) < timedelta(minutes=visit_duration_minutes):
            skipped.append(f"{addr} - open house window shorter than {visit_duration_minutes} min visit")
            continue
        valid.append({"address": addr, "start": start, "end": end})

    if not valid:
        return {"itinerary": [], "total_visits": 0,
                "skipped": skipped or ["No open houses with valid time windows"]}

    # ── Validate home address is present in travel matrix ────────────────────
    home_keys = [k for k in travel_matrix if k.startswith(f"{home_address} ->")]
    if not home_keys:
        return {
            "error": (
                f"home_address '{home_address}' was not found as an origin in the "
                "travel matrix. Make sure you pass the exact same home address string "
                "as the first entry in build_travel_time_matrix."
            ),
            "itinerary": [], "total_visits": 0, "skipped": skipped,
        }

    # Warn about any open house addresses missing from the matrix
    for c in valid:
        if f"{home_address} -> {c['address']}" not in travel_matrix:
            skipped.append(
                f"{c['address']} - not found in travel matrix "
                "(address string may differ from the one used in build_travel_time_matrix)"
            )
    valid = [c for c in valid if f"{home_address} -> {c['address']}" in travel_matrix]

    if not valid:
        return {"itinerary": [], "total_visits": 0,
                "skipped": skipped or ["No open houses matched travel matrix entries"]}

    # ── Reference time: t=0 in the VRP ───────────────────────────────────────
    earliest = min(c["start"] for c in valid)
    ref_dt = datetime.fromisoformat(start_time) if start_time else earliest - timedelta(minutes=30)

    # ── Nodes: 0 = home depot, 1..N = open houses ────────────────────────────
    addresses = [home_address] + [c["address"] for c in valid]
    n = len(addresses)
    visit_dur = visit_duration_minutes
    horizon   = 24 * 60  # minutes — wide enough for a full day

    # Raw travel times in minutes (without service time)
    raw = [[0] * n for _ in range(n)]
    for i, orig in enumerate(addresses):
        for j, dest in enumerate(addresses):
            if i == j:
                continue
            secs = travel_matrix.get(f"{orig} -> {dest}")
            raw[i][j] = (secs // 60) if secs is not None else 9999

    # Transit callback includes service time at the origin node so OR-Tools
    # accounts for time spent at each stop when checking the next window.
    service = [0] + [visit_dur] * (n - 1)
    time_mat = [[service[i] + raw[i][j] for j in range(n)] for i in range(n)]

    # Time windows in minutes from ref_dt
    # Depot: [0, 0] if fixed departure, [0, horizon] if flexible
    # Open house node: must ARRIVE between [tw_start, tw_end - visit_dur]
    depot_tw = (0, 0) if start_time else (0, horizon)
    time_windows = [depot_tw]
    for c in valid:
        tw_s = max(0, int((c["start"] - ref_dt).total_seconds() // 60))
        tw_e = int((c["end"]   - ref_dt).total_seconds() // 60) - visit_dur
        time_windows.append((tw_s, max(tw_s, tw_e)))

    # ── OR-Tools setup ────────────────────────────────────────────────────────
    manager = pywrapcp.RoutingIndexManager(n, 1, 0)  # n nodes, 1 vehicle, depot=0
    routing = pywrapcp.RoutingModel(manager)

    def _time_cb(from_idx, to_idx):
        return time_mat[manager.IndexToNode(from_idx)][manager.IndexToNode(to_idx)]

    cb = routing.RegisterTransitCallback(_time_cb)
    routing.SetArcCostEvaluatorOfAllVehicles(cb)

    routing.AddDimension(cb, horizon, horizon, False, "Time")
    time_dim = routing.GetDimensionOrDie("Time")

    # Apply time windows
    for node, (tw_s, tw_e) in enumerate(time_windows):
        time_dim.CumulVar(manager.NodeToIndex(node)).SetRange(tw_s, tw_e)

    # All open house nodes are optional — skipped ones incur a large penalty
    # so the solver maximises visits without declaring the problem infeasible
    penalty = 100_000
    for node in range(1, n):
        routing.AddDisjunction([manager.NodeToIndex(node)], penalty)

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    )
    params.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    )
    params.time_limit.seconds = 10

    solution = routing.SolveWithParameters(params)

    if not solution:
        return {"itinerary": [], "total_visits": 0,
                "skipped": skipped + ["OR-Tools found no feasible solution"]}

    # ── Extract route ─────────────────────────────────────────────────────────
    itinerary, visited = [], set()
    idx = routing.Start(0)

    while not routing.IsEnd(idx):
        node = manager.IndexToNode(idx)
        if node != 0:
            arrive_min = solution.Min(time_dim.CumulVar(idx))
            arrive_dt  = ref_dt + timedelta(minutes=arrive_min)
            depart_dt  = arrive_dt + timedelta(minutes=visit_dur)
            c = valid[node - 1]

            next_idx  = solution.Value(routing.NextVar(idx))
            next_node = manager.IndexToNode(next_idx)
            travel_next = (
                raw[node][next_node]
                if not routing.IsEnd(next_idx) and next_node != 0
                else None
            )

            itinerary.append({
                "order":                  len(itinerary) + 1,
                "address":                c["address"],
                "arrive_at":              arrive_dt.strftime("%I:%M %p").lstrip("0"),
                "open_house_window":      f"{c['start'].strftime('%I:%M %p').lstrip('0')} - {c['end'].strftime('%I:%M %p').lstrip('0')}",
                "depart_at":              depart_dt.strftime("%I:%M %p").lstrip("0"),
                "travel_to_next_minutes": travel_next,
            })
            visited.add(node)
        idx = solution.Value(routing.NextVar(idx))

    for node, c in enumerate(valid, start=1):
        if node not in visited:
            skipped.append(f"{c['address']} - could not fit within open house window")

    return {"itinerary": itinerary, "total_visits": len(itinerary), "skipped": skipped}
